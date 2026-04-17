"""
Tier 1 evaluation harness — deterministic golden-scenario runner (FR-038, US7).

Usage: make eval  (calls python -m hiive_monitor.eval.runner)

Each scenario is a YAML file in eval/fixtures/. The runner:
  1. Seeds an isolated in-memory DB with the scenario's setup block
  2. Sets the simulated clock to scenario.setup.now
  3. Runs one monitoring tick (which invokes the Deal Investigator)
  4. Evaluates assertions against the persisted observations + interventions
  5. Writes a scorecard to eval_results/scorecard_<timestamp>.md
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any

import yaml

from hiive_monitor import clock as clk
from hiive_monitor.models.risk import Severity

_SEVERITY_ORDER = [s.value for s in (Severity.INFORMATIONAL, Severity.WATCH, Severity.ACT, Severity.ESCALATE)]

_FIXTURES_DIR = pathlib.Path(__file__).parent.parent.parent.parent.parent / "eval" / "fixtures"
_RESULTS_DIR = pathlib.Path(__file__).parent.parent.parent.parent.parent / "eval_results"

# Fallback: look relative to this file
_ALT_FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _find_fixtures_dir() -> pathlib.Path:
    if _FIXTURES_DIR.exists():
        return _FIXTURES_DIR
    if _ALT_FIXTURES.exists():
        return _ALT_FIXTURES
    # Search up from cwd
    cwd = pathlib.Path.cwd()
    for candidate in [cwd / "eval" / "fixtures", cwd / "src" / "hiive_monitor" / "eval" / "fixtures"]:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Cannot find fixtures directory (tried {_FIXTURES_DIR}, {_ALT_FIXTURES})")


def _find_results_dir() -> pathlib.Path:
    if _RESULTS_DIR.parent.exists():
        _RESULTS_DIR.mkdir(exist_ok=True)
        return _RESULTS_DIR
    # Use cwd
    d = pathlib.Path.cwd() / "eval_results"
    d.mkdir(exist_ok=True)
    return d


# ── Scenario loader ───────────────────────────────────────────────────────────


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


# ── DB seeder from scenario setup block ──────────────────────────────────────


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

        # Seed events (comm history)
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

    # Seed prior observations for enrichment scenarios
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


# ── Assertion evaluator ───────────────────────────────────────────────────────


def evaluate_assertions(assertions: dict, observations: list[dict], interventions: list[dict], state: dict) -> list[tuple[str, bool, str]]:
    """
    Returns list of (assertion_name, passed, detail) tuples.
    """
    results = []
    obs = observations[0] if observations else {}
    reasoning = json.loads(obs.get("reasoning", "{}")) if obs.get("reasoning") else {}

    def check(name: str, passed: bool, detail: str = ""):
        results.append((name, passed, detail))

    # severity assertions
    if "severity" in assertions:
        expected = assertions["severity"]
        # No observation → deal was healthy enough to skip investigation → informational
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

    # dimensions triggered
    if "dimensions_triggered" in assertions:
        triggered = set(reasoning.get("dimensions_triggered", []))
        for dim in assertions["dimensions_triggered"]:
            check(f"dimension:{dim}", dim in triggered, f"triggered={sorted(triggered)}")

    if "dimensions_not_triggered" in assertions:
        triggered = set(reasoning.get("dimensions_triggered", []))
        for dim in assertions["dimensions_not_triggered"]:
            check(f"dimension_not:{dim}", dim not in triggered, f"triggered={sorted(triggered)}")

    # intervention assertions
    if "intervention_type" in assertions:
        expected = assertions["intervention_type"]
        actual = interventions[0]["intervention_type"] if interventions else None
        check("intervention_type", actual == expected, f"expected={expected}, actual={actual}")

    if "intervention_drafted" in assertions:
        check("intervention_drafted", bool(interventions), f"count={len(interventions)}")

    if "no_intervention" in assertions:
        check("no_intervention", not interventions, f"count={len(interventions)}")

    # intervention body content
    if "intervention_body_contains" in assertions and interventions:
        body = interventions[0].get("draft_body", "")
        for phrase in assertions["intervention_body_contains"]:
            check(f"body_contains:{phrase[:30]}", phrase.lower() in body.lower(), f"not found in: {body[:100]}")

    # enrichment assertions
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
        check("trigger_matched", len(chain) > 0, f"no enrichment triggered; label: {assertions['trigger_matched']}")

    return results


# ── Per-scenario runner ───────────────────────────────────────────────────────


def run_scenario(scenario: dict) -> dict:
    """Run one scenario in an isolated temp DB. Returns result dict."""
    scenario_id = scenario.get("id", scenario.get("_file", "unknown"))
    category = scenario.get("category", "unknown")
    setup = scenario.get("setup", {})
    assertions = scenario.get("assertions", {})

    # Isolated temp DB
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    tmp_ckpt = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_ckpt.close()

    try:
        os.environ["DOMAIN_DB_PATH"] = tmp.name
        os.environ["CHECKPOINT_DB_PATH"] = tmp_ckpt.name

        # Reset singletons so they pick up the new env vars
        import hiive_monitor.config as _cfg
        _cfg._settings = None
        import hiive_monitor.db.connection as _conn_mod  # no singleton, but flush settings reference
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

        # Set simulated clock
        now_str = setup.get("now", "2026-04-16T09:00:00Z")
        from hiive_monitor import clock as clk
        from hiive_monitor.clock import SimulatedClock
        now_dt = datetime.fromisoformat(now_str.replace("Z", "+00:00"))
        clk.set_clock(SimulatedClock(start=now_dt))

        # Run tick (will invoke Investigator for the seeded deal)
        from hiive_monitor.llm.client import clear_cache
        clear_cache()

        from hiive_monitor.agents.monitor import run_tick
        tick_id = run_tick(mode="simulated")

        # Collect results
        conn = get_domain_conn()
        deal_id = setup.get("deal", {}).get("deal_id", "")
        from hiive_monitor.db import dao
        observations = dao.get_observations(conn, deal_id)
        interventions = dao.get_interventions(conn, deal_id)
        conn.close()

        assertion_results = evaluate_assertions(assertions, observations, interventions, {})
        passed = all(r[1] for r in assertion_results)

        return {
            "id": scenario_id,
            "category": category,
            "passed": passed,
            "assertion_results": assertion_results,
            "observations": len(observations),
            "interventions": len(interventions),
            "severity": observations[0].get("severity") if observations else None,
            "error": None,
        }

    except Exception as e:
        import traceback
        return {
            "id": scenario_id,
            "category": category,
            "passed": False,
            "assertion_results": [("run_error", False, str(e))],
            "observations": 0,
            "interventions": 0,
            "severity": None,
            "error": str(e),
        }
    finally:
        try:
            os.unlink(tmp.name)
            os.unlink(tmp_ckpt.name)
        except OSError:
            pass


# ── Scorecard writer ──────────────────────────────────────────────────────────


def write_scorecard(results: list[dict], output_dir: pathlib.Path) -> pathlib.Path:
    ts = clk.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"scorecard_{ts}.md"

    total = len(results)
    passed = sum(1 for r in results if r["passed"])

    by_category: dict[str, list[dict]] = {}
    for r in results:
        by_category.setdefault(r["category"], []).append(r)

    lines = [
        "# Evaluation Scorecard",
        f"",
        f"**Generated**: {clk.now().isoformat()}",
        f"**Result**: {passed}/{total} scenarios passed ({100*passed//total}%)",
        f"",
        "## By Category",
        "",
    ]

    for cat, cat_results in sorted(by_category.items()):
        cat_passed = sum(1 for r in cat_results if r["passed"])
        lines.append(f"### {cat.replace('_', ' ').title()} ({cat_passed}/{len(cat_results)})")
        lines.append("")
        for r in cat_results:
            status = "✓ PASS" if r["passed"] else "✗ FAIL"
            lines.append(f"**{r['id']}** — {status}")
            if r.get("severity"):
                lines.append(f"  - Severity: `{r['severity']}`")
            for (name, ok, detail) in r["assertion_results"]:
                mark = "✓" if ok else "✗"
                lines.append(f"  - {mark} `{name}`: {detail}")
            if r.get("error"):
                lines.append(f"  - Error: {r['error'][:200]}")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
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

    print(f"Running {len(scenarios)} scenarios from {fixtures_dir}...")
    print()

    results = []
    for scenario in scenarios:
        sid = scenario.get("id", scenario.get("_file", "?"))
        print(f"  [{sid}] ", end="", flush=True)
        result = run_scenario(scenario)
        status = "PASS" if result["passed"] else "FAIL"
        print(status)
        results.append(result)

    passed = sum(1 for r in results if r["passed"])
    total = len(results)

    print()
    print(f"Result: {passed}/{total} passed")

    results_dir = _find_results_dir()
    scorecard_path = write_scorecard(results, results_dir)
    print(f"Scorecard: {scorecard_path}")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
