"""Integration tests for the Deal Investigator sub-graph (T054, T055, T063, T064)."""

from __future__ import annotations

import uuid

from tests.integration.conftest import seed_deal, seed_issuer, seed_tick


def _make_investigator_state(deal_id: str, snapshot, tick_id: str | None = None) -> dict:
    return {
        "tick_id": tick_id or str(uuid.uuid4()),
        "deal_id": deal_id,
        "deal_snapshot": snapshot,
        "risk_signals": [],
        "severity": None,
        "severity_reasoning": None,
        "enrichment_count": 0,
        "enrichment_chain": [],
        "enrichment_context": {},
        "intervention": None,
        "observation_id": None,
        "error": None,
    }


def _build_snapshot(conn, deal_id: str):
    from hiive_monitor.agents.monitor import _build_snapshot as bs
    from hiive_monitor.db import dao
    deal = dao.get_deal(conn, deal_id)
    return bs(deal, conn)


def _get_fresh_graph(monkeypatch):
    """Reset investigator singleton and return a freshly compiled graph (no checkpointer)."""
    import hiive_monitor.agents.investigator as _inv
    _inv._investigator_graph = None
    _inv._checkpointer = None

    from hiive_monitor.agents.investigator import build_investigator_graph
    return build_investigator_graph(checkpointer=None)


# ── T054: enrichment loop is bounded at 2 rounds ─────────────────────────────


def test_enrichment_loop_bounded(db_path, monkeypatch):
    """
    When enrichment_count is already at _MAX_ENRICHMENT_ROUNDS (2) on entry to N3,
    assess_sufficiency must return _sufficient=True without calling the LLM.
    (FR-AGENT-04, T054)
    """
    from hiive_monitor.agents.investigator import _MAX_ENRICHMENT_ROUNDS, assess_sufficiency

    llm_calls: list[str] = []

    def _no_call(**kwargs):
        llm_calls.append(kwargs.get("call_name", "unknown"))
        raise AssertionError("LLM must not be called when enrichment_count >= max")

    monkeypatch.setattr("hiive_monitor.agents.investigator.llm_client.call_structured", _no_call)

    path, conn = db_path
    seed_issuer(conn)
    seed_deal(conn, "D-INV-001")
    snap = _build_snapshot(conn, "D-INV-001")

    state = _make_investigator_state("D-INV-001", snap)
    state["enrichment_count"] = _MAX_ENRICHMENT_ROUNDS  # already at max

    result = assess_sufficiency(state)

    assert result["_sufficient"] is True
    assert llm_calls == [], "No LLM call should be made when enrichment is maxed"


# ── T055: enrichment fires when comm silence detected ─────────────────────────


def test_enrichment_fires_on_comm_silence(db_path):
    """
    When assess_sufficiency decides to enrich context with fetch_communication_content,
    enrich_context must call the tool and return a non-empty enrichment_chain. (T055)

    Tested by calling the nodes directly rather than through the full graph to avoid
    LangGraph's compiled-graph closure semantics.
    """
    from hiive_monitor.agents.investigator import enrich_context
    from hiive_monitor.models.risk import (
        RiskDimension,
        RiskSignal,
    )

    path, conn = db_path
    seed_issuer(conn)
    seed_deal(conn, "D-INV-002", stage="docs_pending", days_in_stage=14)
    # Insert a 14-day-old comm event to simulate silence
    conn.execute(
        """INSERT INTO events (event_id, deal_id, event_type, occurred_at, created_at, summary, payload)
           VALUES (?, 'D-INV-002', 'comm_outbound', datetime('now', '-14 days'),
                   datetime('now', '-14 days'), 'outreach', '{"body": "Following up on document submission"}')""",
        (str(uuid.uuid4()),),
    )
    conn.commit()

    tick_id = str(uuid.uuid4())
    seed_tick(conn, tick_id)
    snap = _build_snapshot(conn, "D-INV-002")

    comm_signal = RiskSignal(
        dimension=RiskDimension.COMMUNICATION_SILENCE,
        triggered=True,
        evidence="14 days since last comm outbound",
        confidence=0.9,
    )

    # Simulate assess_sufficiency returning "need enrichment"
    state_before_enrich = {
        "tick_id": tick_id,
        "deal_id": "D-INV-002",
        "deal_snapshot": snap,
        "risk_signals": [comm_signal],
        "severity": None,
        "severity_reasoning": None,
        "enrichment_count": 0,
        "enrichment_chain": [],
        "enrichment_context": {},
        "intervention": None,
        "observation_id": None,
        "error": None,
        "_sufficient": False,
        "_next_tool": "fetch_communication_content",
        "_sufficiency_rationale": "Need comm content to assess silence",
    }

    # Call enrich_context directly — it should fetch comm content and return a step
    result = enrich_context(state_before_enrich)

    assert result["enrichment_count"] == 1
    assert len(result["enrichment_chain"]) == 1
    assert result["enrichment_chain"][0]["tool_called"] == "fetch_communication_content"
    # Context should contain the fetched communications
    assert "fetch_communication_content" in result["enrichment_context"]


# ── T063: ROFR deadline 7 days → act severity + outbound_nudge ───────────────


def test_rofr_deadline_7d(db_path, monkeypatch):
    """
    Deal with ROFR deadline in 7 days must produce severity=act and an
    outbound_nudge intervention. (T063)
    """
    path, conn = db_path
    seed_issuer(conn)
    seed_deal(conn, "D-INV-003", stage="rofr_pending", days_in_stage=3, rofr_deadline_days=7)

    from hiive_monitor.models.interventions import OutboundNudge
    from hiive_monitor.models.risk import (
        RiskDimension,
        RiskSignal,
        Severity,
        SeverityDecision,
        SufficiencyDecision,
    )

    def _mock_llm(*, output_model, model, tick_id, deal_id, call_name,
                  prompt="", system="", timeout=30.0, template=None, template_vars=None):
        if output_model is SufficiencyDecision:
            return SufficiencyDecision(sufficient=True, rationale="ok", tool_to_call=None)
        if output_model is SeverityDecision:
            return SeverityDecision(
                severity=Severity.ACT,
                reasoning="ROFR deadline in 7 days",
                primary_dimensions=[RiskDimension.DEADLINE_PROXIMITY],
            )
        if output_model is OutboundNudge:
            return OutboundNudge(
                recipient_type="issuer",
                recipient_name="TestCo",
                subject="ROFR deadline approaching",
                body="Your ROFR deadline is in 7 days. Please confirm your decision.",
                referenced_deadline="2026-04-24",
            )
        # Risk signals: trigger deadline_proximity, all others clear
        from hiive_monitor.models.risk import AllRiskSignals
        if output_model is AllRiskSignals:
            def _sig(dim, triggered, evidence):
                return RiskSignal(dimension=dim, triggered=triggered, evidence=evidence, confidence=0.9)
            return AllRiskSignals(
                stage_aging=_sig(RiskDimension.STAGE_AGING, False, "in baseline"),
                deadline_proximity=_sig(RiskDimension.DEADLINE_PROXIMITY, True, "7 days to ROFR deadline"),
                communication_silence=_sig(RiskDimension.COMMUNICATION_SILENCE, False, "recent comms"),
                missing_prerequisites=_sig(RiskDimension.MISSING_PREREQUISITES, False, "no blockers"),
                unusual_characteristics=_sig(RiskDimension.UNUSUAL_CHARACTERISTICS, False, "no flags"),
            )
        return None

    import hiive_monitor.agents.investigator as _inv_mod
    monkeypatch.setattr(_inv_mod.llm_client, "call_structured", _mock_llm)

    graph = _get_fresh_graph(monkeypatch)
    snap = _build_snapshot(conn, "D-INV-003")
    tick_id = str(uuid.uuid4())
    seed_tick(conn, tick_id)
    state = _make_investigator_state("D-INV-003", snap, tick_id=tick_id)

    config = {"configurable": {"thread_id": f"{state['tick_id']}:D-INV-003"}}
    final = graph.invoke(state, config=config)

    assert final["severity"] == Severity.ACT

    # Observation must be in DB
    obs_rows = conn.execute(
        "SELECT * FROM agent_observations WHERE deal_id = 'D-INV-003'"
    ).fetchall()
    assert len(obs_rows) == 1
    assert obs_rows[0]["severity"] == "act"

    # Intervention must be drafted (outbound_nudge)
    iv_rows = conn.execute(
        "SELECT * FROM interventions WHERE deal_id = 'D-INV-003'"
    ).fetchall()
    assert len(iv_rows) == 1
    assert iv_rows[0]["intervention_type"] == "outbound_nudge"


# ── T064: healthy deal → early exit, no intervention ─────────────────────────


def test_early_exit_informational(db_path, monkeypatch):
    """
    A healthy deal with all signals non-triggered and informational severity
    must exit without drafting any intervention. (T064)
    """
    path, conn = db_path
    seed_issuer(conn)
    seed_deal(conn, "D-INV-004", stage="docs_pending", days_in_stage=2)

    from hiive_monitor.models.risk import Severity, SeverityDecision, SufficiencyDecision

    def _mock_llm(*, output_model, model, tick_id, deal_id, call_name,
                  prompt="", system="", timeout=30.0, template=None, template_vars=None):
        if output_model is SufficiencyDecision:
            return SufficiencyDecision(sufficient=True, rationale="all clear", tool_to_call=None)
        if output_model is SeverityDecision:
            return SeverityDecision(
                severity=Severity.INFORMATIONAL, reasoning="deal on track", primary_dimensions=[]
            )
        from hiive_monitor.models.risk import AllRiskSignals, RiskDimension, RiskSignal
        if output_model is AllRiskSignals:
            def _sig(dim):
                return RiskSignal(dimension=dim, triggered=False, evidence="all ok", confidence=0.05)
            return AllRiskSignals(
                stage_aging=_sig(RiskDimension.STAGE_AGING),
                deadline_proximity=_sig(RiskDimension.DEADLINE_PROXIMITY),
                communication_silence=_sig(RiskDimension.COMMUNICATION_SILENCE),
                missing_prerequisites=_sig(RiskDimension.MISSING_PREREQUISITES),
                unusual_characteristics=_sig(RiskDimension.UNUSUAL_CHARACTERISTICS),
            )
        return None

    draft_called: list[str] = []
    original_mock = _mock_llm

    def _mock_with_spy(*, output_model, model, tick_id, deal_id, call_name,
                       prompt="", system="", timeout=30.0, template=None, template_vars=None):
        if "draft" in call_name or "nudge" in call_name or "escalation" in call_name:
            draft_called.append(call_name)
        return original_mock(
            output_model=output_model, model=model,
            tick_id=tick_id, deal_id=deal_id, call_name=call_name,
        )

    import hiive_monitor.agents.investigator as _inv_mod
    monkeypatch.setattr(_inv_mod.llm_client, "call_structured", _mock_with_spy)

    graph = _get_fresh_graph(monkeypatch)
    snap = _build_snapshot(conn, "D-INV-004")
    tick_id = str(uuid.uuid4())
    seed_tick(conn, tick_id)
    state = _make_investigator_state("D-INV-004", snap, tick_id=tick_id)

    config = {"configurable": {"thread_id": f"{state['tick_id']}:D-INV-004"}}
    final = graph.invoke(state, config=config)

    assert final["severity"] == Severity.INFORMATIONAL
    assert final["intervention"] is None, "No intervention should be drafted for informational severity"
    assert draft_called == [], "draft_intervention node must not be reached"

    # No intervention rows in DB
    iv_rows = conn.execute(
        "SELECT * FROM interventions WHERE deal_id = 'D-INV-004'"
    ).fetchall()
    assert len(iv_rows) == 0
