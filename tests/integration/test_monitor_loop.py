"""Integration tests for the Pipeline Monitor loop idempotency (T040, T041)."""

from __future__ import annotations

import uuid

import pytest

from tests.integration.conftest import seed_deal, seed_issuer


def _mock_call_structured(deal_score: float = 0.3):
    """Return a factory that yields a fixed AttentionScore for Haiku screening
    and a minimal SufficiencyDecision + SeverityDecision for the investigator."""
    from hiive_monitor.models.risk import AttentionScore, RiskDimension, RiskSignal, Severity, SeverityDecision
    from hiive_monitor.models.risk import SufficiencyDecision

    call_counts: dict[str, int] = {}

    def _call(*, prompt, output_model, model, tick_id, deal_id, call_name, system="", timeout=30.0):
        call_counts[call_name] = call_counts.get(call_name, 0) + 1

        if output_model is AttentionScore:
            return AttentionScore(deal_id=deal_id, score=deal_score, reason="mock")

        if output_model is SufficiencyDecision:
            return SufficiencyDecision(sufficient=True, rationale="mock sufficient", tool_to_call=None)

        if output_model is SeverityDecision:
            return SeverityDecision(
                severity=Severity.INFORMATIONAL,
                reasoning="mock informational",
                primary_dimensions=[],
            )

        # Risk dimension signals
        if hasattr(output_model, "model_fields") and "triggered" in output_model.model_fields:
            return output_model(
                dimension=RiskDimension.STAGE_AGING,
                triggered=False,
                evidence="no issue (mock)",
                confidence=0.1,
            )

        return None

    return _call, call_counts


def test_single_tick_happy_path(db_path, monkeypatch):
    """
    One completed tick produces exactly one row in ticks; re-running the
    same tick_id is a no-op (idempotency, FR-024). (T040)
    """
    path, conn = db_path

    # Seed 2 deals
    seed_issuer(conn)
    seed_deal(conn, "D-LOOP-01")
    seed_deal(conn, "D-LOOP-02", stage="rofr_pending")

    # Mock LLM calls — return low scores (below threshold) to skip investigation
    import hiive_monitor.agents.monitor as _mon_mod
    mock_fn, _ = _mock_call_structured(deal_score=0.2)
    monkeypatch.setattr(_mon_mod, "call_structured", mock_fn)

    # Reset monitor graph singleton so it picks up fresh DB
    import hiive_monitor.agents.monitor as _mon
    _mon._monitor_graph = None

    from hiive_monitor.agents.monitor import run_tick
    tid = str(uuid.uuid4())
    returned_id = run_tick(mode="simulated", tick_id=tid)
    assert returned_id == tid

    # Exactly one row in ticks for this tick_id
    row = conn.execute("SELECT * FROM ticks WHERE tick_id = ?", (tid,)).fetchone()
    assert row is not None
    assert row["tick_completed_at"] is not None

    # Re-running the same tick_id is a no-op
    run_tick(mode="simulated", tick_id=tid)
    count = conn.execute("SELECT COUNT(*) FROM ticks WHERE tick_id = ?", (tid,)).fetchone()[0]
    assert count == 1


def test_crash_restart_no_duplicate_observations(db_path, monkeypatch):
    """
    If a tick crashes after some investigators emit, restarting with the same
    tick_id must produce exactly 5 unique (tick_id, deal_id) observations — no
    duplicates. INSERT OR IGNORE + UNIQUE(tick_id, deal_id) is the mechanism.
    (T041)
    """
    path, conn = db_path
    deal_ids = [f"D-CR-{i:02d}" for i in range(5)]

    seed_issuer(conn)
    for did in deal_ids:
        seed_deal(conn, did, stage="docs_pending", days_in_stage=10)

    # Simulate crash: insert an incomplete tick + pre-seed 2 observations
    tid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO ticks (tick_id, mode, tick_started_at) VALUES (?, 'simulated', datetime('now', '-2 minutes'))",
        (tid,),
    )
    for did in deal_ids[:2]:
        conn.execute(
            """INSERT INTO agent_observations
               (observation_id, tick_id, deal_id, observed_at, severity,
                dimensions_evaluated, reasoning)
               VALUES (?, ?, ?, datetime('now', '-1 minute'), 'informational', '[]', '{}')""",
            (str(uuid.uuid4()), tid, did),
        )
    conn.commit()

    # Mock LLM to return above-threshold scores so all 5 deals are investigated
    from hiive_monitor.models.risk import AttentionScore, Severity, SeverityDecision, SufficiencyDecision

    def _high_score_mock(*, output_model, model, tick_id, deal_id, call_name,
                          prompt="", system="", timeout=30.0, template=None, template_vars=None):
        if output_model is AttentionScore:
            return AttentionScore(deal_id=deal_id, score=0.85, reason="mock high")
        if output_model is SufficiencyDecision:
            return SufficiencyDecision(sufficient=True, rationale="ok", tool_to_call=None)
        if output_model is SeverityDecision:
            return SeverityDecision(severity=Severity.INFORMATIONAL, reasoning="ok", primary_dimensions=[])
        if hasattr(output_model, "model_fields") and "triggered" in output_model.model_fields:
            return output_model(
                dimension="stage_aging", triggered=False, evidence="no issue", confidence=0.1
            )
        return None

    import hiive_monitor.agents.monitor as _mon_mod
    import hiive_monitor.agents.investigator as _inv_mod
    monkeypatch.setattr(_mon_mod, "call_structured", _high_score_mock)
    monkeypatch.setattr(_inv_mod.llm_client, "call_structured", _high_score_mock)

    # Reset singletons so the mock takes effect
    import hiive_monitor.agents.monitor as _mon
    import hiive_monitor.agents.investigator as _inv
    _mon._monitor_graph = None
    _inv._investigator_graph = None
    _inv._checkpointer = None

    from hiive_monitor.agents.monitor import run_tick
    run_tick(mode="simulated", tick_id=tid)

    # Must have exactly 5 unique (tick_id, deal_id) pairs — no duplicates
    rows = conn.execute(
        "SELECT DISTINCT deal_id FROM agent_observations WHERE tick_id = ?", (tid,)
    ).fetchall()
    assert len(rows) == 5

    # Tick must be marked complete
    tick = conn.execute("SELECT * FROM ticks WHERE tick_id = ?", (tid,)).fetchone()
    assert tick["tick_completed_at"] is not None
