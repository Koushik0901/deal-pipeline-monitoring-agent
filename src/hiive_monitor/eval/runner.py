"""
Evaluation harness — deterministic Tier 1 runner + optional Tier 2 LLM-as-judge.

Usage:
  make eval           → Tier 1 only (deterministic assertions + aggregate metrics)
  make eval-deep      → Tier 1 + Tier 2 LLM-as-judge + Langfuse traces

Each scenario is a YAML file in eval/fixtures/. The runner:
  1. Seeds an isolated temp SQLite DB with the scenario's setup block
  2. Sets the simulated clock to scenario.setup.now
  3. Runs one monitoring tick (which invokes the Deal Investigator)
  4. Evaluates assertions against the persisted observations + interventions
  5. Computes aggregate metrics: Task Completion, Answer Correctness, Tool
     Correctness, Factual Grounding, per-dimension Precision/Recall, and the
     4x4 Severity Confusion Matrix
  6. Writes a scorecard to eval_results/scorecard_<timestamp>.md
  7. (--deep only) Runs G-Eval LLM-as-judge on intervention_quality scenarios
     and emits all scores to Langfuse
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime

import yaml

from hiive_monitor import clock as clk
from hiive_monitor.models.risk import Severity

_SEVERITY_ORDER = [s.value for s in (Severity.INFORMATIONAL, Severity.WATCH, Severity.ACT, Severity.ESCALATE)]

_ALL_DIMENSIONS = [
    "stage_aging",
    "deadline_proximity",
    "communication_silence",
    "missing_prerequisites",
    "counterparty_nonresponsiveness",
    "unusual_characteristics",
]

_FIXTURES_DIR = pathlib.Path(__file__).parent.parent.parent.parent.parent / "eval" / "fixtures"
_RESULTS_DIR = pathlib.Path(__file__).parent.parent.parent.parent.parent / "eval_results"
_ALT_FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _find_fixtures_dir() -> pathlib.Path:
    if _FIXTURES_DIR.exists():
        return _FIXTURES_DIR
    if _ALT_FIXTURES.exists():
        return _ALT_FIXTURES
    cwd = pathlib.Path.cwd()
    for candidate in [cwd / "eval" / "fixtures", cwd / "src" / "hiive_monitor" / "eval" / "fixtures"]:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Cannot find fixtures directory (tried {_FIXTURES_DIR}, {_ALT_FIXTURES})")


def _find_results_dir() -> pathlib.Path:
    if _RESULTS_DIR.parent.exists():
        _RESULTS_DIR.mkdir(exist_ok=True)
        return _RESULTS_DIR
    d = pathlib.Path.cwd() / "eval_results"
    d.mkdir(exist_ok=True)
    return d


# -- Scenario loader -----------------------------------------------------------


def load_scenarios(fixtures_dir: pathlib.Path) -> list[dict]:
    scenarios = []
    for f in sorted(fixtures_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            data["_file"] = f.name
            scenarios.append(data)
        except Exception as e:
            print(f"  WARN: could not load {f.name}: {e}")
    return scenarios


# -- DB seeder -----------------------------------------------------------------


def _seed_from_scenario(conn: sqlite3.Connection, setup: dict) -> None:
    from hiive_monitor.db import dao

    for issuer in setup.get("issuers", []):
        row = {
            "issuer_id": issuer["issuer_id"],
            "name": issuer.get("name", issuer["issuer_id"]),
            "typical_response_days": issuer.get("typical_response_days", 5),
            "rofr_window_days": issuer.get("rofr_window_days", 30),
            "multi_layer_rofr": 1 if issuer.get("multi_layer_rofr") else 0,
            "sector": issuer.get("sector", ""),
            "created_at": setup.get("now", "2026-04-16T09:00:00Z"),
        }
        dao.insert_issuer(conn, row)

    for party in setup.get("parties", []):
        row = {
            "party_id": party["party_id"],
            "party_type": party.get("party_type", "buyer"),
            "display_name": party.get("display_name", party["party_id"]),
            "is_first_time": 1 if party.get("is_first_time") else 0,
            "prior_breakage_count": party.get("prior_breakage_count", 0),
            "created_at": setup.get("now", "2026-04-16T09:00:00Z"),
        }
        dao.insert_party(conn, row)

    deal = setup.get("deal", {})
    if deal:
        now_str = setup.get("now", "2026-04-16T09:00:00Z")
        now = datetime.fromisoformat(now_str.replace("Z", "+00:00"))

        stage_entered = now
        stage_entered_days = deal.get("stage_entered_days_ago", 0)
        if stage_entered_days:
            from datetime import timedelta
            stage_entered = now - timedelta(days=stage_entered_days)

        rofr_deadline = None
        rofr_days = deal.get("rofr_deadline_days_from_now")
        if rofr_days is not None:
            from datetime import timedelta
            rofr_deadline = (now + timedelta(days=rofr_days)).isoformat()

        row = {
            "deal_id": deal["deal_id"],
            "issuer_id": deal["issuer_id"],
            "buyer_id": deal.get("buyer_id", "buyer_a1"),
            "seller_id": deal.get("seller_id", "seller_s1"),
            "shares": deal.get("shares", 1000),
            "price_per_share": deal.get("price_per_share", 100.0),
            "stage": deal["stage"],
            "stage_entered_at": stage_entered.isoformat(),
            "rofr_deadline": rofr_deadline,
            "responsible_party": deal.get("responsible_party", "hiive_ts"),
            "blockers": json.dumps(deal.get("blockers", [])),
            "risk_factors": json.dumps(deal.get("risk_factors", {})),
            "created_at": now_str,
            "updated_at": now_str,
        }
        dao.insert_deal(conn, row)

        for ev in deal.get("events", []):
            days_ago = ev.get("days_ago", 0)
            from datetime import timedelta
            occ = now - timedelta(days=days_ago)
            dao.insert_event(
                conn,
                deal_id=deal["deal_id"],
                event_type=ev["event_type"],
                occurred_at=occ,
                summary=ev.get("summary", ""),
                payload=ev.get("payload", {}),
            )

    for obs in setup.get("prior_observations_to_seed", []):
        tick_id = obs.get("tick_id", str(uuid.uuid4()))
        _seed_tick(conn, tick_id, setup.get("now", "2026-04-16T09:00:00Z"))
        dao.insert_observation(
            conn,
            tick_id=tick_id,
            deal_id=deal.get("deal_id", obs.get("deal_id", "")),
            severity=obs.get("severity", "watch"),
            dimensions_evaluated=obs.get("dimensions_evaluated", []),
            reasoning=obs.get("reasoning", {}),
            observation_id=obs.get("observation_id"),
        )


def _seed_tick(conn: sqlite3.Connection, tick_id: str, now_str: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO ticks (tick_id, mode, tick_started_at, tick_completed_at) VALUES (?, ?, ?, ?)",
        (tick_id, "simulated", now_str, now_str),
    )
    conn.commit()


# -- Ground truth extraction ---------------------------------------------------


def _extract_ground_truth(scenario: dict) -> dict:
    """Pull ground_truth block; return empty defaults if absent."""
    gt = scenario.get("ground_truth", {})
    return {
        "severity": gt.get("severity"),
        "triggered": gt.get("dimensions_triggered", []),
        "not_triggered": gt.get("dimensions_not_triggered", []),
        "expected_tools": gt.get("expected_tools", []),
    }


# -- Factual grounding (Tier 1.5 string-match) ---------------------------------


def _check_factual_grounding(setup: dict, interventions: list[dict]) -> tuple[bool | None, str]:
    """
    Verify financial figures from deal setup appear verbatim in the drafted body.

    Assumption: share counts may appear with or without comma formatting (5000 or
    5,000). Prices may appear as 185.50 or $185.50. We check common formats.
    Returns (None, reason) when there is no intervention or no figures to check.
    """
    if not interventions:
        return None, "no intervention drafted"

    body = interventions[0].get("draft_body", "")
    deal = setup.get("deal", {})
    checks: list[str] = []
    all_pass = True

    shares = deal.get("shares")
    if shares:
        share_variants = {str(shares), f"{shares:,}"}
        found = any(v in body for v in share_variants)
        checks.append(f"shares({shares:,})={'ok' if found else 'MISSING'}")
        if not found:
            all_pass = False

    price = deal.get("price_per_share")
    if price:
        price_variants = {
            f"{price:.2f}",
            f"${price:.2f}",
            f"{price:.1f}",
            f"${price:.1f}",
        }
        found = any(v in body for v in price_variants)
        checks.append(f"price({price:.2f})={'ok' if found else 'MISSING'}")
        if not found:
            all_pass = False

    if not checks:
        return None, "no financial figures in setup"

    return all_pass, "; ".join(checks)


# -- Assertion evaluator -------------------------------------------------------


def evaluate_assertions(
    assertions: dict,
    observations: list[dict],
    interventions: list[dict],
    state: dict,
) -> list[tuple[str, bool, str]]:
    """Returns list of (assertion_name, passed, detail) tuples."""
    results = []
    obs = observations[0] if observations else {}
    reasoning = json.loads(obs.get("reasoning", "{}")) if obs.get("reasoning") else {}

    def check(name: str, passed: bool, detail: str = ""):
        results.append((name, passed, detail))

    if "severity" in assertions:
        expected = assertions["severity"]
        actual = obs.get("severity", "informational")
        check("severity", actual == expected, f"expected={expected}, actual={actual}")

    if "severity_gte" in assertions:
        expected_min = assertions["severity_gte"]
        actual = obs.get("severity", "informational")
        passed = _SEVERITY_ORDER.index(actual) >= _SEVERITY_ORDER.index(expected_min)
        check("severity_gte", passed, f"expected>={expected_min}, actual={actual}")

    if "severity_lte" in assertions:
        expected_max = assertions["severity_lte"]
        actual = obs.get("severity", "informational")
        passed = _SEVERITY_ORDER.index(actual) <= _SEVERITY_ORDER.index(expected_max)
        check("severity_lte", passed, f"expected<={expected_max}, actual={actual}")

    if "dimensions_triggered" in assertions:
        triggered = set(reasoning.get("dimensions_triggered", []))
        for dim in assertions["dimensions_triggered"]:
            check(f"dimension:{dim}", dim in triggered, f"triggered={sorted(triggered)}")

    if "dimensions_not_triggered" in assertions:
        triggered = set(reasoning.get("dimensions_triggered", []))
        for dim in assertions["dimensions_not_triggered"]:
            check(f"dimension_not:{dim}", dim not in triggered, f"triggered={sorted(triggered)}")

    if "intervention_type" in assertions:
        expected = assertions["intervention_type"]
        actual = interventions[0]["intervention_type"] if interventions else None
        check("intervention_type", actual == expected, f"expected={expected}, actual={actual}")

    if "intervention_drafted" in assertions:
        check("intervention_drafted", bool(interventions), f"count={len(interventions)}")

    if "no_intervention" in assertions:
        check("no_intervention", not interventions, f"count={len(interventions)}")

    if "intervention_body_contains" in assertions and interventions:
        body = interventions[0].get("draft_body", "")
        for phrase in assertions["intervention_body_contains"]:
            check(
                f"body_contains:{phrase[:30]}",
                phrase.lower() in body.lower(),
                f"not found in: {body[:100]}",
            )

    if "agent_triggers_enrichment" in assertions:
        chain = reasoning.get("enrichment_chain", [])
        check("agent_triggers_enrichment", len(chain) > 0, f"enrichment_rounds={len(chain)}")

    if "enrichment_tool_called" in assertions:
        chain = reasoning.get("enrichment_chain", [])
        tools_called = [step.get("tool_called", "") for step in chain]
        expected_tool = assertions["enrichment_tool_called"]
        check("enrichment_tool_called", expected_tool in tools_called, f"called={tools_called}")

    if "severity_after_enrichment_gte" in assertions:
        expected_min = assertions["severity_after_enrichment_gte"]
        actual = obs.get("severity", "informational")
        passed = _SEVERITY_ORDER.index(actual) >= _SEVERITY_ORDER.index(expected_min)
        check("severity_after_enrichment_gte", passed, f"expected>={expected_min}, actual={actual}")

    if "severity_after_enrichment_lte" in assertions:
        expected_max = assertions["severity_after_enrichment_lte"]
        actual = obs.get("severity", "informational")
        passed = _SEVERITY_ORDER.index(actual) <= _SEVERITY_ORDER.index(expected_max)
        check("severity_after_enrichment_lte", passed, f"expected<={expected_max}, actual={actual}")

    if "trigger_matched" in assertions:
        chain = reasoning.get("enrichment_chain", [])
        check(
            "trigger_matched",
            len(chain) > 0,
            f"no enrichment triggered; label: {assertions['trigger_matched']}",
        )

    return results


# -- Per-scenario runner -------------------------------------------------------


def run_scenario(scenario: dict) -> dict:
    """Run one scenario in an isolated temp DB. Returns enriched result dict."""
    scenario_id = scenario.get("id", scenario.get("_file", "unknown"))
    category = scenario.get("category", "unknown")
    setup = scenario.get("setup", {})
    assertions = scenario.get("assertions", {})
    gt = _extract_ground_truth(scenario)

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    tmp_ckpt = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_ckpt.close()

    try:
        os.environ["DOMAIN_DB_PATH"] = tmp.name
        os.environ["CHECKPOINT_DB_PATH"] = tmp_ckpt.name

        import hiive_monitor.config as _cfg
        _cfg._settings = None
        import hiive_monitor.agents.investigator as _inv
        _inv._investigator_graph = None
        _inv._checkpointer = None
        import hiive_monitor.agents.monitor as _mon
        _mon._monitor_graph = None

        from hiive_monitor.db.init import init_domain_db
        init_domain_db()

        from hiive_monitor.db.connection import get_domain_conn
        conn = get_domain_conn()
        _seed_from_scenario(conn, setup)
        conn.commit()
        conn.close()

        now_str = setup.get("now", "2026-04-16T09:00:00Z")
        from hiive_monitor import clock as clk
        from hiive_monitor.clock import SimulatedClock
        now_dt = datetime.fromisoformat(now_str.replace("Z", "+00:00"))
        clk.set_clock(SimulatedClock(start=now_dt))

        from hiive_monitor.llm.client import clear_cache
        clear_cache()

        from hiive_monitor.agents.monitor import run_tick
        run_tick(mode="simulated")

        conn = get_domain_conn()
        deal_id = setup.get("deal", {}).get("deal_id", "")
        from hiive_monitor.db import dao
        observations = dao.get_observations(conn, deal_id)
        interventions = dao.get_interventions(conn, deal_id)
        conn.close()

        obs = observations[0] if observations else {}
        reasoning = json.loads(obs.get("reasoning", "{}")) if obs.get("reasoning") else {}
        actual_triggered = reasoning.get("dimensions_triggered", [])
        enrichment_chain = reasoning.get("enrichment_chain", [])
        actual_tools = [step.get("tool_called", "") for step in enrichment_chain]
        actual_severity = obs.get("severity") if observations else None
        task_completed = bool(observations)

        fg_pass, fg_detail = _check_factual_grounding(setup, interventions)

        assertion_results = evaluate_assertions(assertions, observations, interventions, {})
        passed = all(r[1] for r in assertion_results)

        return {
            "id": scenario_id,
            "category": category,
            "passed": passed,
            "assertion_results": assertion_results,
            "observations": len(observations),
            "interventions": len(interventions),
            "severity": actual_severity,
            "error": None,
            # metric fields
            "task_completed": task_completed,
            "gt_severity": gt["severity"],
            "actual_severity": actual_severity,
            "gt_triggered": gt["triggered"],
            "gt_not_triggered": gt["not_triggered"],
            "actual_triggered": actual_triggered,
            "expected_tools": gt["expected_tools"],
            "actual_tools": actual_tools,
            "factual_grounding": fg_pass,
            "factual_grounding_detail": fg_detail,
            # raw data for Tier 2
            "_setup": setup,
            "_observations": observations,
            "_interventions": interventions,
        }

    except Exception as e:
        return {
            "id": scenario_id,
            "category": category,
            "passed": False,
            "assertion_results": [("run_error", False, str(e))],
            "observations": 0,
            "interventions": 0,
            "severity": None,
            "error": str(e),
            "task_completed": False,
            "gt_severity": gt["severity"],
            "actual_severity": None,
            "gt_triggered": gt["triggered"],
            "gt_not_triggered": gt["not_triggered"],
            "actual_triggered": [],
            "expected_tools": gt["expected_tools"],
            "actual_tools": [],
            "factual_grounding": None,
            "factual_grounding_detail": "scenario crashed",
            "_setup": setup,
            "_observations": [],
            "_interventions": [],
        }
    finally:
        try:
            os.unlink(tmp.name)
            os.unlink(tmp_ckpt.name)
        except OSError:
            pass


# -- Aggregate metrics ---------------------------------------------------------


def compute_aggregate_metrics(results: list[dict]) -> dict:
    """
    Compute all aggregate metrics across scenario results.

    Metrics:
      Task Completion    — observation produced (agent ran to completion without crash)
      Answer Correctness — actual severity matches ground_truth.severity exactly
      Tool Correctness   — all expected_tools were present in actual enrichment chain
      Factual Grounding  — financial figures (shares, price) appear verbatim in draft
      Dimension P/R/F1   — per-dimension using ground_truth triggered/not_triggered labels
      Confusion Matrix   — 4x4 expected vs actual severity

    Assumption — Argument Correctness: enrichment tools always receive deal_id from
    LangGraph state, which is set deterministically from the fixture. Tool Correctness
    implicitly covers argument correctness: if the wrong deal_id were passed, the tool
    would return empty data and the scenario would fail enrichment assertions.
    """
    total = len(results)

    task_completed_n = sum(1 for r in results if r.get("task_completed", False))

    exact_sev_pairs = [
        (r["gt_severity"], r["actual_severity"])
        for r in results
        if r.get("gt_severity") and r.get("actual_severity")
    ]
    answer_correct_n = sum(1 for gt, act in exact_sev_pairs if gt == act)

    tool_scenarios = [r for r in results if r.get("expected_tools")]
    tool_correct_n = sum(
        1 for r in tool_scenarios
        if all(t in r.get("actual_tools", []) for t in r["expected_tools"])
    )

    fg_scenarios = [r for r in results if r.get("factual_grounding") is not None]
    fg_passed_n = sum(1 for r in fg_scenarios if r.get("factual_grounding"))

    dim_stats: dict[str, dict] = {}
    for dim in _ALL_DIMENSIONS:
        tp = fp = fn = tn = 0
        for r in results:
            gt_t = r.get("gt_triggered", [])
            gt_nt = r.get("gt_not_triggered", [])
            actual = set(r.get("actual_triggered", []))
            if dim in gt_t:
                if dim in actual:
                    tp += 1
                else:
                    fn += 1
            elif dim in gt_nt:
                if dim in actual:
                    fp += 1
                else:
                    tn += 1
        precision = tp / (tp + fp) if (tp + fp) > 0 else None
        recall = tp / (tp + fn) if (tp + fn) > 0 else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and (precision + recall) > 0
            else None
        )
        dim_stats[dim] = {
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall, "f1": f1,
        }

    matrix = {e: {a: 0 for a in _SEVERITY_ORDER} for e in _SEVERITY_ORDER}
    matrix_total = 0
    for r in results:
        exp = r.get("gt_severity")
        act = r.get("actual_severity")
        if exp and act and exp in _SEVERITY_ORDER and act in _SEVERITY_ORDER:
            matrix[exp][act] += 1
            matrix_total += 1
    sev_correct = sum(matrix[s][s] for s in _SEVERITY_ORDER)

    return {
        "total": total,
        "task_completion": (task_completed_n, total),
        "answer_correctness": (answer_correct_n, len(exact_sev_pairs)) if exact_sev_pairs else None,
        "tool_correctness": (tool_correct_n, len(tool_scenarios)) if tool_scenarios else None,
        "factual_grounding": (fg_passed_n, len(fg_scenarios)) if fg_scenarios else None,
        "dim_stats": dim_stats,
        "matrix": matrix,
        "matrix_total": matrix_total,
        "sev_correct": sev_correct,
    }


def _pct(num: int, den: int) -> str:
    if den == 0:
        return "n/a"
    return f"{100 * num // den}%"


def _fmt(val: float | None, digits: int = 2) -> str:
    if val is None:
        return "n/a"
    return f"{val:.{digits}f}"


def _format_metrics_markdown(metrics: dict) -> list[str]:
    lines: list[str] = []

    tc_n, tc_d = metrics["task_completion"]
    lines += [
        "## Aggregate Metrics",
        "",
        "| Metric | Score | Notes |",
        "|--------|-------|-------|",
        f"| Task Completion | {tc_n}/{tc_d} ({_pct(tc_n, tc_d)}) | Observation produced for deal |",
    ]

    ac = metrics["answer_correctness"]
    if ac:
        lines.append(
            f"| Answer Correctness | {ac[0]}/{ac[1]} ({_pct(*ac)}) | Exact severity match vs ground_truth |"
        )
    else:
        lines.append("| Answer Correctness | n/a | No ground_truth.severity in fixtures |")

    tl = metrics["tool_correctness"]
    if tl:
        lines.append(
            f"| Tool Correctness | {tl[0]}/{tl[1]} ({_pct(*tl)}) | Expected enrichment tools called |"
        )
    else:
        lines.append("| Tool Correctness | n/a | No expected_tools declared in fixtures |")

    fg = metrics["factual_grounding"]
    if fg:
        lines.append(
            f"| Factual Grounding (Tier 1.5) | {fg[0]}/{fg[1]} ({_pct(*fg)}) | Shares/price verbatim in draft |"
        )
    else:
        lines.append("| Factual Grounding (Tier 1.5) | n/a | No intervention scenarios with financial data |")

    lines += ["", "---", ""]

    lines += [
        "## Dimension Precision / Recall",
        "",
        "| Dimension | TP | FP | FN | TN | Coverage | Precision | Recall | F1 |",
        "|-----------|----|----|----|----|----------|-----------|--------|----|",
    ]
    for dim in _ALL_DIMENSIONS:
        s = metrics["dim_stats"][dim]
        coverage = s["tp"] + s["fp"] + s["fn"] + s["tn"]
        lines.append(
            f"| {dim} | {s['tp']} | {s['fp']} | {s['fn']} | {s['tn']} "
            f"| {coverage} | {_fmt(s['precision'])} | {_fmt(s['recall'])} | {_fmt(s['f1'])} |"
        )

    lines += ["", "---", ""]

    mat = metrics["matrix"]
    mat_total = metrics["matrix_total"]
    sev_correct = metrics["sev_correct"]
    lines += [
        "## Severity Confusion Matrix",
        "",
        f"Scenarios with exact ground_truth severity: **{mat_total}** — "
        f"Accuracy: **{sev_correct}/{mat_total}** ({_pct(sev_correct, mat_total)})",
        "",
        "Rows = expected · Columns = actual",
        "",
        "| expected \\ actual | informational | watch | act | escalate |",
        "|--------------------|---------------|-------|-----|----------|",
    ]
    for exp in _SEVERITY_ORDER:
        row_vals = " | ".join(str(mat[exp][act]) for act in _SEVERITY_ORDER)
        lines.append(f"| **{exp}** | {row_vals} |")

    lines += ["", "---", ""]
    return lines


# -- Tier 2: LLM-as-judge ------------------------------------------------------


def run_tier2_for_scenario(result: dict) -> dict[str, float] | None:
    """Run deepeval G-Eval on an intervention_quality scenario. Returns score dict or None."""
    if result.get("category") != "intervention_quality":
        return None
    try:
        from hiive_monitor.eval.tier2_judge import run_tier2
        return run_tier2(
            scenario_id=result["id"],
            setup=result["_setup"],
            interventions=result["_interventions"],
            observations=result["_observations"],
        )
    except ImportError:
        print("  WARN: tier2_judge not available — install deepeval: uv add deepeval")
        return None
    except Exception as e:
        print(f"  WARN: Tier 2 failed for {result['id']}: {e}")
        return None


def _format_tier2_markdown(tier2_map: dict[str, dict]) -> list[str]:
    if not tier2_map:
        return []
    lines = [
        "## Tier 2: LLM-as-Judge (Intervention Quality)",
        "",
        "| Scenario | Factual Grounding | Tone | Urgency | Actionability | Mean |",
        "|----------|-------------------|------|---------|---------------|------|",
    ]
    for sid, scores in sorted(tier2_map.items()):
        if not scores:
            lines.append(f"| {sid} | — | — | — | — | — |")
            continue
        vals = [scores.get(k) for k in ["Factual Grounding", "Tone Appropriateness", "Urgency Calibration", "Actionability"]]
        numeric = [v for v in vals if v is not None]
        mean = sum(numeric) / len(numeric) if numeric else None
        lines.append(
            f"| {sid} | {_fmt(vals[0])} | {_fmt(vals[1])} | {_fmt(vals[2])} | {_fmt(vals[3])} | {_fmt(mean)} |"
        )
    lines += ["", "---", ""]
    return lines



# -- Scorecard writer ----------------------------------------------------------


def write_scorecard(
    results: list[dict],
    output_dir: pathlib.Path,
    metrics: dict,
    tier2_map: dict[str, dict] | None = None,
) -> pathlib.Path:
    ts = clk.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"scorecard_{ts}.md"

    total = len(results)
    passed = sum(1 for r in results if r["passed"])

    by_category: dict[str, list[dict]] = {}
    for r in results:
        by_category.setdefault(r["category"], []).append(r)

    lines = [
        "# Evaluation Scorecard",
        "",
        f"**Generated**: {clk.now().isoformat()}",
        f"**Tier 1 Result**: {passed}/{total} scenarios passed ({100*passed//total}%)",
        "",
    ]

    lines += _format_metrics_markdown(metrics)

    if tier2_map:
        lines += _format_tier2_markdown(tier2_map)

    lines += ["## By Category", ""]

    for cat, cat_results in sorted(by_category.items()):
        cat_passed = sum(1 for r in cat_results if r["passed"])
        lines.append(f"### {cat.replace('_', ' ').title()} ({cat_passed}/{len(cat_results)})")
        lines.append("")
        for r in cat_results:
            status = "PASS" if r["passed"] else "FAIL"
            lines.append(f"**{r['id']}** — {status}")
            if r.get("severity"):
                gt_sev = r.get("gt_severity")
                gt_tag = f" (expected: `{gt_sev}`)" if gt_sev else ""
                lines.append(f"  - Severity: `{r['severity']}`{gt_tag}")
            if r.get("factual_grounding") is not None:
                fg_mark = "ok" if r["factual_grounding"] else "FAIL"
                lines.append(f"  - Factual Grounding: {fg_mark} — {r.get('factual_grounding_detail', '')}")
            for (name, ok, detail) in r["assertion_results"]:
                mark = "PASS" if ok else "FAIL"
                lines.append(f"  - {mark} `{name}`: {detail}")
            if r.get("error"):
                lines.append(f"  - Error: {r['error'][:200]}")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# -- Main ----------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Run evaluation harness")
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Enable Tier 2 LLM-as-judge (deepeval) + Langfuse trace emission",
    )
    args = parser.parse_args()
    deep_mode = args.deep

    from hiive_monitor import logging as log_module
    log_module.configure_logging()

    try:
        fixtures_dir = _find_fixtures_dir()
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    scenarios = load_scenarios(fixtures_dir)
    if not scenarios:
        print("No fixture files found. Add YAML files to eval/fixtures/.")
        sys.exit(0)

    mode_tag = " [deep: Tier 2 + Langfuse]" if deep_mode else ""
    print(f"Running {len(scenarios)} scenarios from {fixtures_dir}{mode_tag}...")

    # ── Langfuse: sync dataset + open experiment run before loop ──────────────
    from hiive_monitor.eval.langfuse_tracer import EvalTracer
    tracer = EvalTracer()
    run_name = f"eval-{clk.now().strftime('%Y%m%d-%H%M%S')}"
    if tracer._enabled:
        print()
        tracer.setup_experiment(
            scenarios,
            run_name=run_name,
            run_metadata={"mode": "deep" if deep_mode else "tier1"},
        )
    print()

    # ── Scenario loop — each scenario runs inside item.run() if Langfuse up ──
    results = []
    for scenario in scenarios:
        sid = scenario.get("id", scenario.get("_file", "?"))
        print(f"  [{sid}] ", end="", flush=True)
        with tracer.scenario_run(sid) as span:
            result = run_scenario(scenario)
            tracer.score_scenario(span, result)
        print("PASS" if result["passed"] else "FAIL")
        results.append(result)

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    metrics = compute_aggregate_metrics(results)

    print()
    print(f"Tier 1:             {passed}/{total} passed ({_pct(passed, total)})")

    tc_n, tc_d = metrics["task_completion"]
    print(f"Task Completion:    {tc_n}/{tc_d} ({_pct(tc_n, tc_d)})")

    ac = metrics["answer_correctness"]
    if ac:
        print(f"Answer Correctness: {ac[0]}/{ac[1]} ({_pct(*ac)})")

    tl = metrics["tool_correctness"]
    if tl:
        print(f"Tool Correctness:   {tl[0]}/{tl[1]} ({_pct(*tl)})")

    fg = metrics["factual_grounding"]
    if fg:
        print(f"Factual Grounding:  {fg[0]}/{fg[1]} ({_pct(*fg)})")

    print()
    print("Severity Confusion Matrix (expected -> actual):")
    col_w = 14
    header = f"{'':>{col_w}} | " + " | ".join(f"{s[:col_w]:>{col_w}}" for s in _SEVERITY_ORDER)
    print(f"  {header}")
    print(f"  {'-' * len(header)}")
    for exp in _SEVERITY_ORDER:
        row = " | ".join(f"{metrics['matrix'][exp][act]:>{col_w}}" for act in _SEVERITY_ORDER)
        print(f"  {exp:>{col_w}} | {row}")
    print()

    results_dir = _find_results_dir()
    scorecard_path = write_scorecard(results, results_dir, metrics)
    print(f"Scorecard: {scorecard_path}")

    # ── Langfuse: aggregate run scores + flush ────────────────────────────────
    tracer.emit_aggregate_scores(metrics)
    tracer.flush()

    if deep_mode:
        from hiive_monitor.eval.deepeval_runner import run_deep_eval
        run_deep_eval(results, fixtures_dir, results_dir)

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
