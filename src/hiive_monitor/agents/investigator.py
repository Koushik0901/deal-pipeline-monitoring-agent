"""
Deal Investigator — per-deal LangGraph agentic sub-graph.

7-node variable-path graph (FR-AGENT-01–06):
  N1 observe          → load snapshot (already in state from Monitor fan-out)
  N2 evaluate_risks   → 5 LLM dimension calls + 1 deterministic call (parallel via Send)
  N3 assess_sufficiency → agentic loop: LLM decides if more context needed (max 2 rounds)
  N4 enrich_context   → calls one enrichment tool per round
  N5 score_severity   → Sonnet severity rubric call
  N6 draft_intervention → (only if severity ∈ {act, escalate}) draft outbound/escalation/brief
  N7 emit_observation → persist observation + intervention to DB

What makes it agentic: N3→N4→N3 loop with branching based on LLM reasoning.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from langgraph.graph import END, START, StateGraph

from hiive_monitor import clock as clk
from hiive_monitor import logging as log_module
from hiive_monitor.agents.graph_state import EnrichmentStep, InvestigatorState
from hiive_monitor.config import get_settings
from hiive_monitor.db import dao
from hiive_monitor.db.connection import get_domain_conn
from hiive_monitor.llm import client as llm_client
from hiive_monitor.llm.deterministic.counterparty_responsiveness import (
    evaluate_counterparty_responsiveness,
)
from hiive_monitor.llm.prompts.intervention_drafts import (
    BRIEF_OUTPUT,
    BRIEF_TEMPLATE,
    ESCALATION_OUTPUT,
    ESCALATION_TEMPLATE,
    OUTBOUND_OUTPUT,
    OUTBOUND_TEMPLATE,
    build_brief_entry_prompt,
    build_escalation_prompt,
    build_outbound_nudge_prompt,
)
from hiive_monitor.llm.prompts.intervention_status_recommendation import (
    STATUS_REC_OUTPUT,
    STATUS_REC_TEMPLATE,
    build_status_rec_prompt,
)
from hiive_monitor.llm.prompts.risk_communication_silence import (
    COMMUNICATION_SILENCE_OUTPUT,
    COMMUNICATION_SILENCE_TEMPLATE,
    build_communication_silence_prompt,
)
from hiive_monitor.llm.prompts.risk_deadline_proximity import (
    DEADLINE_PROXIMITY_OUTPUT,
    DEADLINE_PROXIMITY_TEMPLATE,
    build_deadline_proximity_prompt,
)
from hiive_monitor.llm.prompts.risk_missing_prerequisites import (
    MISSING_PREREQUISITES_OUTPUT,
    MISSING_PREREQUISITES_TEMPLATE,
    build_missing_prerequisites_prompt,
)
from hiive_monitor.llm.prompts.risk_stage_aging import (
    STAGE_AGING_OUTPUT,
    STAGE_AGING_TEMPLATE,
    build_stage_aging_prompt,
)
from hiive_monitor.llm.prompts.risk_unusual_characteristics import (
    UNUSUAL_CHARACTERISTICS_OUTPUT,
    UNUSUAL_CHARACTERISTICS_TEMPLATE,
    build_unusual_characteristics_prompt,
)
from hiive_monitor.llm.prompts.severity import SEVERITY_OUTPUT, SEVERITY_TEMPLATE, build_severity_prompt
from hiive_monitor.llm.prompts.sufficiency import (
    SUFFICIENCY_OUTPUT,
    SUFFICIENCY_TEMPLATE,
    build_sufficiency_prompt,
)
from hiive_monitor.models.interventions import BriefEntry, InternalEscalation, Intervention, OutboundNudge
from hiive_monitor.models.risk import RiskDimension, RiskSignal, Severity, SeverityDecision

INVESTIGATOR_GRAPH_NAME = "deal_investigator"
_MAX_ENRICHMENT_ROUNDS = 2


# ── N1: observe ───────────────────────────────────────────────────────────────


def observe(state: InvestigatorState) -> dict:
    """Snapshot already in state (passed by Monitor fan-out). Just bind logging."""
    log_module.bind_deal(state["deal_id"])
    log_module.get_logger().info(
        "investigator.observe",
        deal_id=state["deal_id"],
        stage=state["deal_snapshot"].stage.value,
    )
    return {}


# ── N2: evaluate_risks ────────────────────────────────────────────────────────


def evaluate_risks(state: InvestigatorState) -> dict:
    """
    Run all 6 risk dimension evaluators. 5 LLM calls + 1 deterministic.
    Results accumulate via the Annotated[list, operator.add] reducer.
    """
    settings = get_settings()
    snap = state["deal_snapshot"]
    tick_id = state["tick_id"]
    deal_id = state["deal_id"]
    signals: list[RiskSignal] = []

    # Dimension 1: Stage aging
    r1 = llm_client.call_structured(
        template=STAGE_AGING_TEMPLATE,
        template_vars=build_stage_aging_prompt(snap),
        output_model=STAGE_AGING_OUTPUT,
        model=settings.llm_model,
        tick_id=tick_id,
        deal_id=deal_id,
        call_name="evaluate_risk_stage_aging",
    )
    if r1:
        signals.append(r1)

    # Dimension 2: Deadline proximity
    r2 = llm_client.call_structured(
        template=DEADLINE_PROXIMITY_TEMPLATE,
        template_vars=build_deadline_proximity_prompt(snap),
        output_model=DEADLINE_PROXIMITY_OUTPUT,
        model=settings.llm_model,
        tick_id=tick_id,
        deal_id=deal_id,
        call_name="evaluate_risk_deadline_proximity",
    )
    if r2:
        signals.append(r2)

    # Dimension 3: Communication silence
    r3 = llm_client.call_structured(
        template=COMMUNICATION_SILENCE_TEMPLATE,
        template_vars=build_communication_silence_prompt(snap),
        output_model=COMMUNICATION_SILENCE_OUTPUT,
        model=settings.llm_model,
        tick_id=tick_id,
        deal_id=deal_id,
        call_name="evaluate_risk_communication_silence",
    )
    if r3:
        signals.append(r3)

    # Dimension 4: Missing prerequisites
    r4 = llm_client.call_structured(
        template=MISSING_PREREQUISITES_TEMPLATE,
        template_vars=build_missing_prerequisites_prompt(snap),
        output_model=MISSING_PREREQUISITES_OUTPUT,
        model=settings.llm_model,
        tick_id=tick_id,
        deal_id=deal_id,
        call_name="evaluate_risk_missing_prerequisites",
    )
    if r4:
        signals.append(r4)

    # Dimension 5: Counterparty non-responsiveness (deterministic)
    conn = get_domain_conn()
    issuer = dao.get_issuer(conn, snap.issuer_id)
    conn.close()
    typical_days = issuer.get("typical_response_days", 7) if issuer else 7
    r5 = evaluate_counterparty_responsiveness(snap, typical_days)
    signals.append(r5)

    # Dimension 6: Unusual characteristics
    r6 = llm_client.call_structured(
        template=UNUSUAL_CHARACTERISTICS_TEMPLATE,
        template_vars=build_unusual_characteristics_prompt(snap),
        output_model=UNUSUAL_CHARACTERISTICS_OUTPUT,
        model=settings.llm_model,
        tick_id=tick_id,
        deal_id=deal_id,
        call_name="evaluate_risk_unusual_characteristics",
    )
    if r6:
        signals.append(r6)

    log_module.get_logger().info(
        "investigator.risks_evaluated",
        deal_id=deal_id,
        triggered=[s.dimension.value for s in signals if s.triggered],
    )
    return {"risk_signals": signals}


# ── N3: assess_sufficiency ────────────────────────────────────────────────────


def assess_sufficiency(state: InvestigatorState) -> dict:
    """
    Agentic loop control node. LLM decides if current signals are sufficient or
    whether one more enrichment tool call would change the severity assessment.
    Bounded at MAX_ENRICHMENT_ROUNDS to prevent runaway loops.
    """
    if state.get("enrichment_count", 0) >= _MAX_ENRICHMENT_ROUNDS:
        return {"_sufficient": True}  # Force exit after max rounds

    settings = get_settings()
    decision = llm_client.call_structured(
        template=SUFFICIENCY_TEMPLATE,
        template_vars=build_sufficiency_prompt(
            state["deal_snapshot"],
            state.get("risk_signals", []),
            state.get("enrichment_count", 0),
            state.get("enrichment_context", {}),
        ),
        output_model=SUFFICIENCY_OUTPUT,
        model=settings.llm_model,
        tick_id=state["tick_id"],
        deal_id=state["deal_id"],
        call_name=f"assess_sufficiency_r{state.get('enrichment_count', 0)}",
    )

    if decision is None or decision.sufficient:
        return {"_sufficient": True}

    log_module.get_logger().info(
        "investigator.enrichment_requested",
        deal_id=state["deal_id"],
        tool=decision.tool_to_call,
        rationale=decision.rationale[:100],
    )
    return {"_sufficient": False, "_next_tool": decision.tool_to_call, "_sufficiency_rationale": decision.rationale}


def _sufficiency_router(state: InvestigatorState) -> str:
    """Route from N3: either to N4 (enrich) or N5 (score severity)."""
    if state.get("_sufficient", True):
        return "score_severity"
    return "enrich_context"


# ── N4: enrich_context ────────────────────────────────────────────────────────


def enrich_context(state: InvestigatorState) -> dict:
    """Call one enrichment tool per round and record the step in enrichment_chain."""
    tool_name = state.get("_next_tool", "fetch_communication_content")
    conn = get_domain_conn()
    deal_id = state["deal_id"]
    snap = state["deal_snapshot"]

    try:
        if tool_name == "fetch_communication_content":
            data = dao.fetch_communication_content(conn, deal_id)
            summary = f"Last {len(data)} comms: " + (
                "; ".join(f"{c['direction']} on {c['occurred_at'][:10]}: {c['body'][:60]}" for c in data[:3])
                if data else "none"
            )
        elif tool_name == "fetch_prior_observations":
            data = dao.fetch_prior_observations(conn, deal_id)
            summary = f"{len(data)} prior observations: " + (
                "; ".join(f"{o['severity']}: {o['reasoning_summary'][:60]}" for o in data[:3])
                if data else "none"
            )
        elif tool_name == "fetch_issuer_history":
            data = dao.fetch_issuer_history(conn, snap.issuer_id)
            summary = f"{len(data)} prior deals for {snap.issuer_id}: " + (
                "; ".join(f"{d['final_stage']}: {d.get('key_signals', [])}" for d in data[:3])
                if data else "none"
            )
        else:
            data = []
            summary = "unknown tool"
    finally:
        conn.close()

    round_num = state.get("enrichment_count", 0) + 1
    step = EnrichmentStep(
        round=round_num,
        tool_called=tool_name,
        tool_rationale=state.get("_sufficiency_rationale", ""),
        context_summary=summary,
    )

    ctx = dict(state.get("enrichment_context", {}))
    ctx[tool_name] = data

    log_module.get_logger().info(
        "investigator.enriched",
        deal_id=deal_id,
        tool=tool_name,
        round=round_num,
    )
    return {
        "enrichment_count": round_num,
        "enrichment_chain": [step],
        "enrichment_context": ctx,
    }


# ── N5: score_severity ────────────────────────────────────────────────────────


def score_severity(state: InvestigatorState) -> dict:
    settings = get_settings()
    decision = llm_client.call_structured(
        template=SEVERITY_TEMPLATE,
        template_vars=build_severity_prompt(state["deal_snapshot"], state.get("risk_signals", [])),
        output_model=SEVERITY_OUTPUT,
        model=settings.llm_model,
        tick_id=state["tick_id"],
        deal_id=state["deal_id"],
        call_name="decide_severity",
    )

    severity = decision.severity if decision else Severity.INFORMATIONAL
    reasoning = decision.reasoning if decision else "LLM call failed; defaulting to informational."

    log_module.get_logger().info(
        "investigator.severity_scored",
        deal_id=state["deal_id"],
        severity=severity.value,
    )
    return {"severity": severity, "severity_reasoning": reasoning}


def _severity_router(state: InvestigatorState) -> str:
    """Route after severity scoring: draft for act/escalate (outbound/escalation) or watch (brief_entry)."""
    sev = state.get("severity")
    if sev in (Severity.ACT, Severity.ESCALATE, Severity.WATCH):
        return "draft_intervention"
    return "emit_observation"


# ── N6: draft_intervention ────────────────────────────────────────────────────


def draft_intervention(state: InvestigatorState) -> dict:
    settings = get_settings()
    snap = state["deal_snapshot"]
    severity = state.get("severity", Severity.ACT)
    signals = state.get("risk_signals", [])

    # Build a SeverityDecision object for the drafting prompts
    sev_decision = SeverityDecision(
        severity=severity,
        reasoning=state.get("severity_reasoning", ""),
        primary_dimensions=[s.dimension for s in signals if s.triggered][:3],
    )

    intervention: Intervention | None = None

    responsible = snap.responsible_party or "hiive_ts"

    if severity == Severity.WATCH and responsible == "hiive_ts":
        # watch + hiive_ts responsible → status recommendation (proactive deal health check)
        result = llm_client.call_structured(
            template=STATUS_REC_TEMPLATE,
            template_vars=build_status_rec_prompt(snap, sev_decision, signals),
            output_model=STATUS_REC_OUTPUT,
            model=settings.llm_model,
            tick_id=state["tick_id"],
            deal_id=state["deal_id"],
            call_name="draft_status_recommendation",
        )
        if result:
            intervention = Intervention.status_recommendation(result)
    elif severity == Severity.WATCH:
        # watch + external responsible → brief_entry (awareness item, no outreach)
        result = llm_client.call_structured(
            template=BRIEF_TEMPLATE,
            template_vars=build_brief_entry_prompt(snap, sev_decision, signals),
            output_model=BRIEF_OUTPUT,
            model=settings.llm_model,
            tick_id=state["tick_id"],
            deal_id=state["deal_id"],
            call_name="draft_brief_entry",
        )
        if result:
            intervention = Intervention.brief(result)
    elif (responsible == "hiive_ts") and (severity == Severity.ESCALATE):
        # hiive_ts responsible + escalate → internal escalation note
        result = llm_client.call_structured(
            template=ESCALATION_TEMPLATE,
            template_vars=build_escalation_prompt(snap, sev_decision, signals),
            output_model=ESCALATION_OUTPUT,
            model=settings.llm_model,
            tick_id=state["tick_id"],
            deal_id=state["deal_id"],
            call_name="draft_internal_escalation",
        )
        if result:
            intervention = Intervention.escalation(result)
    else:
        # external responsible party → outbound nudge
        result = llm_client.call_structured(
            template=OUTBOUND_TEMPLATE,
            template_vars=build_outbound_nudge_prompt(snap, sev_decision, signals),
            output_model=OUTBOUND_OUTPUT,
            model=settings.llm_model,
            tick_id=state["tick_id"],
            deal_id=state["deal_id"],
            call_name="draft_outbound_nudge",
        )
        if result:
            intervention = Intervention.outbound(result)

    if intervention:
        log_module.get_logger().info(
            "investigator.intervention_drafted",
            deal_id=state["deal_id"],
            type=intervention.intervention_type,
        )

    return {"intervention": intervention}


# ── N7: emit_observation ──────────────────────────────────────────────────────


def emit_observation(state: InvestigatorState) -> dict:
    """Persist observation + optional intervention to domain DB. INSERT OR IGNORE for idempotency."""
    conn = get_domain_conn()
    snap = state["deal_snapshot"]
    signals = state.get("risk_signals", [])
    severity = state.get("severity", Severity.INFORMATIONAL)
    enrichment_chain = state.get("enrichment_chain", [])

    reasoning = {
        "severity_rationale": state.get("severity_reasoning", ""),
        "dimensions_triggered": [s.dimension.value for s in signals if s.triggered],
        "all_signals": [s.model_dump() for s in signals],
        "enrichment_chain": enrichment_chain,
        "enrichment_rounds": state.get("enrichment_count", 0),
    }

    oid = dao.insert_observation(
        conn,
        tick_id=state["tick_id"],
        deal_id=state["deal_id"],
        severity=severity.value,
        dimensions_evaluated=[s.dimension.value for s in signals],
        reasoning=reasoning,
    )

    intervention_id: str | None = None
    iv = state.get("intervention")
    if iv and oid:
        if not dao.has_open_intervention(conn, state["deal_id"]):
            payload = iv.payload.model_dump()
            intervention_id = dao.insert_intervention(
                conn,
                deal_id=state["deal_id"],
                observation_id=oid,
                intervention_type=iv.intervention_type,
                draft_body=payload.get("body", payload.get("headline", "")),
                recipient_type=payload.get("recipient_type") or payload.get("escalate_to"),
                draft_subject=payload.get("subject") or payload.get("headline"),
                reasoning_ref=oid,
            )

    conn.commit()
    conn.close()

    log_module.get_logger().info(
        "investigator.observation_emitted",
        deal_id=state["deal_id"],
        severity=severity.value,
        observation_id=oid,
        intervention_id=intervention_id,
    )
    return {"observation_id": oid}


# ── Graph construction ────────────────────────────────────────────────────────


def build_investigator_graph(checkpointer=None):
    g = StateGraph(InvestigatorState)

    g.add_node("observe", observe)
    g.add_node("evaluate_risks", evaluate_risks)
    g.add_node("assess_sufficiency", assess_sufficiency)
    g.add_node("enrich_context", enrich_context)
    g.add_node("score_severity", score_severity)
    g.add_node("draft_intervention", draft_intervention)
    g.add_node("emit_observation", emit_observation)

    g.add_edge(START, "observe")
    g.add_edge("observe", "evaluate_risks")
    g.add_edge("evaluate_risks", "assess_sufficiency")

    # Agentic loop: N3 → N4 → N3 (bounded by enrichment_count check in N3)
    g.add_conditional_edges("assess_sufficiency", _sufficiency_router, {
        "enrich_context": "enrich_context",
        "score_severity": "score_severity",
    })
    g.add_edge("enrich_context", "assess_sufficiency")

    # After severity scoring: branch to draft or emit
    g.add_conditional_edges("score_severity", _severity_router, {
        "draft_intervention": "draft_intervention",
        "emit_observation": "emit_observation",
    })
    g.add_edge("draft_intervention", "emit_observation")
    g.add_edge("emit_observation", END)

    return g.compile(checkpointer=checkpointer)


_investigator_graph = None
_checkpointer = None


def get_investigator_graph():
    """Return compiled Investigator graph with SqliteSaver checkpointer (singleton)."""
    global _investigator_graph, _checkpointer
    if _investigator_graph is None:
        import sqlite3 as _sqlite3
        from langgraph.checkpoint.sqlite import SqliteSaver
        from hiive_monitor.config import get_settings

        db_path = get_settings().checkpoint_db_path
        # Open a persistent connection (check_same_thread=False for APScheduler threads)
        _conn = _sqlite3.connect(db_path, check_same_thread=False)
        _checkpointer = SqliteSaver(_conn)
        _investigator_graph = build_investigator_graph(checkpointer=_checkpointer)
    return _investigator_graph
