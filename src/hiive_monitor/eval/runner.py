"""
Evaluation harness — Tier 1 (deterministic agent runs on fixture golden set).

Two-step eval workflow:
  make eval       → Tier 1: run agents on all fixtures, save results_latest.json
  make eval-deep  → Tier 2: load results_latest.json, run LLM-as-judge (deepeval_runner)

Each scenario is a YAML file in eval/fixtures/. This runner:
  1. Seeds an isolated temp SQLite DB with the scenario's setup block
  2. Sets the simulated clock to scenario.setup.now
  3. Runs one monitoring tick (which invokes the Deal Investigator)
  4. Evaluates assertions against the persisted observations + interventions
  5. Computes aggregate metrics: Task Completion, Answer Correctness, Tool
     Correctness, Factual Grounding, per-dimension Precision/Recall, and the
     4x4 Severity Confusion Matrix
  6. Writes a scorecard to eval_results/scorecard_<timestamp>.md
  7. Saves eval_results/results_<timestamp>.json + results_latest.json for Tier 2
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
from datetime import datetime, timedelta

import yaml
from dotenv import load_dotenv

load_dotenv()

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

_FIXTURES_DIR = pathlib.Path(__file__).parent.parent.parent.parent / "eval" / "fixtures"
_RESULTS_DIR = pathlib.Path(__file__).parent.parent.parent.parent / "eval_results"
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
            stage_entered = now - timedelta(days=stage_entered_days)

        rofr_deadline = None
        rofr_days = deal.get("rofr_deadline_days_from_now")
        if rofr_days is not None:
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
            occ = now - timedelta(days=days_ago)
            dao.insert_event(
                conn,
                deal_id=deal["deal_id"],
                event_type=ev["event_type"],
                occurred_at=occ,
                summary=ev.get("summary", ""),
                payload=ev.get("payload", {}),
            )

    # Seed historical settled deals + approved interventions for outcome tracking (TS08)
    for hist in setup.get("historical_interventions", []):
        hist_now_str = setup.get("now", "2026-04-16T09:00:00Z")
        hist_now = datetime.fromisoformat(hist_now_str.replace("Z", "+00:00"))

        approved_days_ago = hist.get("approved_at_days_ago", 30)
        approved_at = (hist_now - timedelta(days=approved_days_ago)).isoformat()

        # Insert the deal in settled stage
        hist_deal_id = hist["deal_id"]
        hist_issuer_id = hist.get("issuer_id", deal.get("issuer_id", ""))
        conn.execute(
            """INSERT OR IGNORE INTO deals
               (deal_id, issuer_id, buyer_id, seller_id, shares, price_per_share,
                stage, stage_entered_at, rofr_deadline, responsible_party,
                blockers, risk_factors, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'settled', ?, NULL, 'buyer', '[]', '{}', ?, ?)""",
            (
                hist_deal_id, hist_issuer_id,
                hist.get("buyer_id", "buyer_hist"), hist.get("seller_id", "seller_hist"),
                hist.get("shares", 1000), hist.get("price_per_share", 100.0),
                approved_at, approved_at, hist_now_str,
            ),
        )

        # Insert the approved intervention
        hist_iv_id = str(uuid.uuid4())
        conn.execute(
            """INSERT OR IGNORE INTO interventions
               (intervention_id, deal_id, observation_id, intervention_type,
                recipient_type, draft_subject, draft_body, reasoning_ref,
                status, final_text, approved_at, created_at)
               VALUES (?, ?, ?, ?, 'buyer', 'Follow-up', 'Follow-up message', ?,
                       'approved', 'Follow-up message', ?, ?)""",
            (
                hist_iv_id, hist_deal_id, str(uuid.uuid4()),
                hist.get("intervention_type", "outbound_nudge"),
                hist_iv_id, approved_at, approved_at,
            ),
        )

        # Insert follow-on events (within or outside the 7-day response window)
        for fev in hist.get("followed_by", []):
            days_after = fev.get("days_after_approval", 3)
            fev_occ = (hist_now - timedelta(days=approved_days_ago - days_after)).isoformat()
            dao.insert_event(
                conn,
                deal_id=hist_deal_id,
                event_type=fev["event_type"],
                occurred_at=datetime.fromisoformat(fev_occ),
                summary=fev.get("summary", fev["event_type"]),
            )
        conn.commit()

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

    # Additional deals — seeds extra deals alongside the main one, used by
    # portfolio-pattern scenarios that need multiple deals in the same (issuer, stage) cluster.
    now_str = setup.get("now", "2026-04-16T09:00:00Z")
    now = datetime.fromisoformat(now_str.replace("Z", "+00:00"))
    for extra in setup.get("additional_deals", []):
        extra_entered = now - timedelta(days=extra.get("stage_entered_days_ago", 0))
        extra_rofr = None
        if extra.get("rofr_deadline_days_from_now") is not None:
            extra_rofr = (now + timedelta(days=extra["rofr_deadline_days_from_now"])).isoformat()
        row = {
            "deal_id": extra["deal_id"],
            "issuer_id": extra["issuer_id"],
            "buyer_id": extra.get("buyer_id", "buyer_a1"),
            "seller_id": extra.get("seller_id", "seller_s1"),
            "shares": extra.get("shares", 1000),
            "price_per_share": extra.get("price_per_share", 100.0),
            "stage": extra["stage"],
            "stage_entered_at": extra_entered.isoformat(),
            "rofr_deadline": extra_rofr,
            "responsible_party": extra.get("responsible_party", "hiive_ts"),
            "blockers": json.dumps(extra.get("blockers", [])),
            "risk_factors": json.dumps(extra.get("risk_factors", {})),
            "created_at": now_str,
            "updated_at": now_str,
        }
        dao.insert_deal(conn, row)

    # Prior completed ticks with per-cluster observations. Used by portfolio-pattern
    # scenarios to build up a rolling average baseline without running real prior ticks.
    for pt in setup.get("prior_ticks", []):
        tick_id = pt.get("tick_id", str(uuid.uuid4()))
        days_ago = pt.get("days_ago", 1)
        tick_time = (now - timedelta(days=days_ago)).isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO ticks (tick_id, mode, tick_started_at, tick_completed_at) VALUES (?, ?, ?, ?)",
            (tick_id, "simulated", tick_time, tick_time),
        )
        for po in pt.get("observations", []):
            dao.insert_observation(
                conn,
                tick_id=tick_id,
                deal_id=po["deal_id"],
                severity=po.get("severity", "informational"),
                dimensions_evaluated=po.get("dimensions_evaluated", []),
                reasoning=po.get("reasoning", {}),
            )
        conn.commit()

    # Prior pending intervention — simulates a stale draft from an earlier tick
    # that should be superseded when the current tick re-investigates the deal.
    ppi = setup.get("prior_pending_intervention")
    if ppi:
        prior_tick_id = ppi.get("tick_id", "prior-pending-tick")
        days_ago = ppi.get("created_days_ago", 3)
        tick_time = (now - timedelta(days=days_ago)).isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO ticks (tick_id, mode, tick_started_at, tick_completed_at) VALUES (?, ?, ?, ?)",
            (prior_tick_id, "simulated", tick_time, tick_time),
        )
        prior_obs_id = str(uuid.uuid4())
        conn.execute(
            """INSERT OR IGNORE INTO agent_observations
               (observation_id, tick_id, deal_id, observed_at, severity,
                dimensions_evaluated, reasoning, intervention_id)
               VALUES (?, ?, ?, ?, ?, '[]', '{}', NULL)""",
            (
                prior_obs_id, prior_tick_id, ppi["deal_id"], tick_time,
                ppi.get("severity", "act"),
            ),
        )
        prior_iv_id = ppi.get("intervention_id", "prior-pending-iv")
        conn.execute(
            """INSERT OR IGNORE INTO interventions
               (intervention_id, deal_id, observation_id, intervention_type,
                recipient_type, draft_subject, draft_body, reasoning_ref,
                status, created_at)
               VALUES (?, ?, ?, ?, 'external', ?, ?, ?, 'pending', ?)""",
            (
                prior_iv_id, ppi["deal_id"], prior_obs_id,
                ppi.get("intervention_type", "outbound_nudge"),
                ppi.get("draft_subject", "Follow-up"),
                ppi.get("draft_body", ""),
                prior_obs_id, tick_time,
            ),
        )
        conn.commit()


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

    Only checked for outbound_nudge interventions — the outbound prompt explicitly
    mandates shares/price verbatim. Internal escalation and brief_entry prompts do
    not receive these figures so checking them would always fail spuriously.

    Assumption: share counts may appear with or without comma formatting (5000 or
    5,000). Prices may appear as 185.50 or $185.50. We check common formats.
    Returns (None, reason) when there is no intervention or no figures to check.
    """
    nudges = [iv for iv in interventions if iv.get("intervention_type") == "outbound_nudge"]
    if not nudges:
        return None, "no outbound_nudge intervention drafted"

    body = nudges[0].get("draft_body", "")
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

    if "intervention_body_not_contains" in assertions and interventions:
        body = interventions[0].get("draft_body", "")
        for phrase in assertions["intervention_body_not_contains"]:
            check(
                f"body_not_contains:{phrase[:30]}",
                phrase.lower() not in body.lower(),
                f"found in: {body[:120]}",
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

    if "no_portfolio_signal" in assertions and assertions["no_portfolio_signal"]:
        signals = state.get("tick_signals") or []
        check("no_portfolio_signal", len(signals) == 0, f"signals={signals}")

    if "prior_pending_superseded" in assertions and assertions["prior_pending_superseded"]:
        prior = state.get("prior_pending_intervention") or {}
        status = prior.get("status")
        check(
            "prior_pending_superseded",
            status == "dismissed",
            f"prior intervention status={status} (expected dismissed)",
        )

    if "trigger_matched" in assertions:
        chain = reasoning.get("enrichment_chain", [])
        check(
            "trigger_matched",
            len(chain) > 0,
            f"no enrichment triggered; label: {assertions['trigger_matched']}",
        )

    return results


# -- Per-scenario runner -------------------------------------------------------


def run_scenario(scenario: dict, tick_id: str | None = None) -> dict:
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
        # Stretch migrations add signals/documents_received/snoozed columns — needed
        # for portfolio-pattern and doc-tracking scenarios.
        from hiive_monitor.db.migrations import stretch_migrations
        stretch_migrations()

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
        current_tick_id = run_tick(mode="simulated", tick_id=tick_id)

        conn = get_domain_conn()
        deal_id = setup.get("deal", {}).get("deal_id", "")
        from hiive_monitor.db import dao
        observations = dao.get_observations(conn, deal_id)
        interventions = dao.get_interventions(conn, deal_id)

        # Look up portfolio signals on the current tick (may be NULL if column absent).
        tick_signals: list = []
        try:
            row = conn.execute(
                "SELECT signals FROM ticks WHERE tick_id = ?", (current_tick_id,)
            ).fetchone()
            if row and row["signals"]:
                tick_signals = json.loads(row["signals"])
        except sqlite3.OperationalError:
            pass

        # Look up status of a seeded prior pending intervention (for supersession assertion).
        prior_pending = None
        ppi_cfg = setup.get("prior_pending_intervention")
        if ppi_cfg:
            prior_iv_id = ppi_cfg.get("intervention_id", "prior-pending-iv")
            row = conn.execute(
                "SELECT status FROM interventions WHERE intervention_id = ?",
                (prior_iv_id,),
            ).fetchone()
            if row:
                prior_pending = {"status": row["status"]}

        conn.close()

        obs = observations[0] if observations else {}
        reasoning = json.loads(obs.get("reasoning", "{}")) if obs.get("reasoning") else {}
        actual_triggered = reasoning.get("dimensions_triggered", [])
        enrichment_chain = reasoning.get("enrichment_chain", [])
        actual_tools = [step.get("tool_called", "") for step in enrichment_chain]
        actual_severity = obs.get("severity") if observations else None
        task_completed = bool(observations)

        fg_pass, fg_detail = _check_factual_grounding(setup, interventions)

        assertion_results = evaluate_assertions(
            assertions,
            observations,
            interventions,
            {"tick_signals": tick_signals, "prior_pending_intervention": prior_pending},
        )
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
            "_trace_id": current_tick_id,
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



# -- Results JSON serialization -----------------------------------------------


def save_results_json(results: list[dict], output_dir: pathlib.Path) -> pathlib.Path:
    """Persist Tier 1 results to JSON so deepeval_runner can load them without re-running agents."""
    import shutil

    ts = clk.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"results_{ts}.json"
    text = json.dumps([_serialize_result(r) for r in results], indent=2)
    path.write_text(text, encoding="utf-8")
    # Atomic copy to results_latest.json (avoids partial reads mid-write)
    latest = output_dir / "results_latest.json"
    tmp_latest = output_dir / f"results_latest_{ts}.tmp"
    tmp_latest.write_text(text, encoding="utf-8")
    shutil.move(str(tmp_latest), str(latest))
    return path


def load_results_json(path: pathlib.Path) -> list[dict]:
    """Load serialized Tier 1 results from disk. Tuples are stored as lists — restore them."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [_deserialize_result(r) for r in raw]


def _serialize_result(r: dict) -> dict:
    return {
        "id": r["id"],
        "category": r["category"],
        "passed": r["passed"],
        "severity": r.get("severity"),
        "error": r.get("error"),
        "observations": r.get("observations", 0),
        "interventions": r.get("interventions", 0),
        "task_completed": r.get("task_completed", False),
        "actual_severity": r.get("actual_severity"),
        "gt_severity": r.get("gt_severity"),
        "actual_triggered": r.get("actual_triggered", []),
        "gt_triggered": r.get("gt_triggered", []),
        "gt_not_triggered": r.get("gt_not_triggered", []),
        "actual_tools": r.get("actual_tools", []),
        "expected_tools": r.get("expected_tools", []),
        "factual_grounding": r.get("factual_grounding"),
        "factual_grounding_detail": r.get("factual_grounding_detail"),
        # tuples serialized as lists; _deserialize_result restores them
        "assertion_results": [
            [name, ok, detail] for (name, ok, detail) in r.get("assertion_results", [])
        ],
        "_trace_id": r.get("_trace_id"),
    }


def _deserialize_result(r: dict) -> dict:
    r = dict(r)
    r["assertion_results"] = [
        (item[0], item[1], item[2]) for item in r.get("assertion_results", [])
    ]
    return r


# -- Scorecard writer ----------------------------------------------------------


def write_scorecard(
    results: list[dict],
    output_dir: pathlib.Path,
    metrics: dict,
    tier2_map: dict[str, dict] | None = None,
) -> pathlib.Path:
    now = clk.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"scorecard_{ts}.md"

    total = len(results)
    passed = sum(1 for r in results if r["passed"])

    by_category: dict[str, list[dict]] = {}
    for r in results:
        by_category.setdefault(r["category"], []).append(r)

    lines = [
        "# Evaluation Scorecard",
        "",
        f"**Generated**: {now.isoformat()}",
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


# -- Shared progress bar factory -----------------------------------------------


def make_eval_progress(console):
    """Return a standardized rich Progress instance for both eval phases."""
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    return Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description:<44}"),
        BarColumn(bar_width=28),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        TextColumn(" [green]{task.fields[n_pass]}P[/] [red]{task.fields[n_fail]}F[/]"),
        console=console,
        transient=False,
    )


# -- Main ----------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Tier 1 evaluation — agent runs on all fixtures")
    parser.parse_args()

    from hiive_monitor import logging as log_module
    log_module.configure_logging()

    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    console = Console(stderr=False)

    try:
        fixtures_dir = _find_fixtures_dir()
    except FileNotFoundError as e:
        console.print(f"[red]ERROR:[/] {e}")
        sys.exit(1)

    scenarios = load_scenarios(fixtures_dir)
    if not scenarios:
        console.print("No fixture files found. Add YAML files to eval/fixtures/.")
        sys.exit(0)

    console.print()
    console.print(
        f"[bold]Hiive Eval[/]  {len(scenarios)} scenarios  ·  [dim]{fixtures_dir}[/]"
    )

    # ── Langfuse: sync dataset + open experiment run before loop ──────────────
    from hiive_monitor.eval.langfuse_tracer import EvalTracer
    tracer = EvalTracer()
    run_name = f"eval-{clk.now().strftime('%Y%m%d-%H%M%S')}"
    if tracer._enabled:
        console.print(f"[dim]Langfuse run:[/] {run_name}")
        tracer.setup_experiment(
            scenarios,
            run_name=run_name,
            run_metadata={"mode": "tier1"},
        )
    console.print()

    # ── Scenario loop ──────────────────────────────────────────────────────────
    results: list[dict] = []
    n_pass = 0
    n_fail = 0

    with make_eval_progress(console) as progress:
        task = progress.add_task("Starting…", total=len(scenarios), n_pass=0, n_fail=0)

        for scenario in scenarios:
            sid = scenario.get("id", scenario.get("_file", "?"))
            progress.update(task, description=sid)

            # Pre-allocate the Langfuse trace_id and reuse it as the tick_id so all
            # LLM generations + the finalized trace output land on the same trace
            # that the dataset_run_item links to.
            shared_trace_id = tracer.new_trace_id()
            scenario_input = {
                "scenario_id": sid,
                "category": scenario.get("category"),
                "description": scenario.get("description"),
                "setup": scenario.get("setup", {}),
                "assertions": scenario.get("assertions", {}),
                "ground_truth": scenario.get("ground_truth", {}),
            }
            with tracer.scenario_run(
                sid, trace_id=shared_trace_id, input_payload=scenario_input
            ) as span:
                result = run_scenario(scenario, tick_id=shared_trace_id)
                tracer.score_scenario(span, result)
                if span is not None:
                    scenario_output = {
                        "passed": result.get("passed"),
                        "severity": result.get("actual_severity"),
                        "observations": result.get("_observations", []),
                        "interventions": result.get("_interventions", []),
                        "actual_triggered": result.get("actual_triggered", []),
                        "actual_tools": result.get("actual_tools", []),
                        "assertion_results": [
                            {"name": n, "passed": ok, "detail": d}
                            for (n, ok, d) in result.get("assertion_results", [])
                        ],
                    }
                    try:
                        span.update(output=scenario_output)
                        span.update_trace(output=scenario_output)
                    except Exception:
                        pass

            if result["passed"]:
                n_pass += 1
            else:
                n_fail += 1
                # Print failure details above the progress bar
                failed_assertions = [
                    f"[dim]{name}[/]: {detail}"
                    for (name, ok, detail) in result.get("assertion_results", [])
                    if not ok
                ]
                err_lines = "\n  ".join(failed_assertions) if failed_assertions else result.get("error", "unknown")
                progress.console.print(
                    f"  [red]FAIL[/] [bold]{sid}[/]\n  {err_lines}"
                )

            results.append(result)
            progress.update(task, advance=1, n_pass=n_pass, n_fail=n_fail)

    # ── Summary ────────────────────────────────────────────────────────────────
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    metrics = compute_aggregate_metrics(results)

    console.print()

    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold", min_width=22)
    summary.add_column()

    tier1_color = "green" if passed == total else "red" if passed < total * 0.7 else "yellow"
    summary.add_row("Tier 1", f"[{tier1_color}]{passed}/{total} ({_pct(passed, total)})[/]")

    tc_n, tc_d = metrics["task_completion"]
    summary.add_row("Task Completion", f"{tc_n}/{tc_d} ({_pct(tc_n, tc_d)})")

    ac = metrics["answer_correctness"]
    if ac:
        summary.add_row("Answer Correctness", f"{ac[0]}/{ac[1]} ({_pct(*ac)})")

    tl = metrics["tool_correctness"]
    if tl:
        summary.add_row("Tool Correctness", f"{tl[0]}/{tl[1]} ({_pct(*tl)})")

    fg = metrics["factual_grounding"]
    if fg:
        summary.add_row("Factual Grounding", f"{fg[0]}/{fg[1]} ({_pct(*fg)})")

    console.print(Panel(summary, title="[bold]Metrics[/]", expand=False))

    # ── Confusion matrix ───────────────────────────────────────────────────────
    mat_table = Table(title="Severity Confusion Matrix  (rows=expected, cols=actual)", show_header=True)
    mat_table.add_column("expected \\ actual", style="dim")
    for act in _SEVERITY_ORDER:
        mat_table.add_column(act, justify="right")

    _SEV_STYLE = {
        "informational": "green",
        "watch": "blue",
        "act": "yellow",
        "escalate": "red",
    }
    for exp in _SEVERITY_ORDER:
        row_vals = []
        for act in _SEVERITY_ORDER:
            v = metrics["matrix"][exp][act]
            style = "bold green" if (exp == act and v > 0) else ("bold red" if (exp != act and v > 0) else "dim")
            row_vals.append(Text(str(v), style=style))
        mat_table.add_row(Text(exp, style=_SEV_STYLE.get(exp, "")), *row_vals)

    console.print()
    console.print(mat_table)
    console.print()

    results_dir = _find_results_dir()
    scorecard_path = write_scorecard(results, results_dir, metrics)
    results_json_path = save_results_json(results, results_dir)
    console.print(f"[dim]Scorecard:[/]     {scorecard_path}")
    console.print(f"[dim]Results JSON:[/]  {results_json_path}")
    console.print("[dim]              → eval_results/results_latest.json (symlink)[/]")
    console.print()
    console.print("[dim]Run [bold]make eval-deep[/] to score these results with LLM-as-judge.[/]")

    # ── Langfuse: aggregate run scores + flush ────────────────────────────────
    tracer.emit_aggregate_scores(metrics)
    tracer.flush()

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
