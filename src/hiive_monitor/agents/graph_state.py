"""LangGraph TypedDict state shapes for the Monitor and Investigator graphs."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from hiive_monitor.models.interventions import Intervention
from hiive_monitor.models.risk import RiskSignal, Severity
from hiive_monitor.models.snapshot import DealSnapshot


class EnrichmentStep(TypedDict):
    """Record of one enrichment tool call made by N4 enrich_context."""

    round: int
    tool_called: Literal[
        "fetch_communication_content",
        "fetch_prior_observations",
        "fetch_issuer_history",
        "fetch_intervention_outcomes",
    ]
    tool_rationale: str
    context_summary: str


class ErrorRecord(TypedDict):
    deal_id: str
    error: str
    node: str


class InvestigatorResult(TypedDict):
    deal_id: str
    severity: str | None
    observation_id: str | None
    intervention_id: str | None
    error: str | None


# ── Monitor (supervisor) graph state ──────────────────────────────────────────


class MonitorState(TypedDict):
    tick_id: str
    mode: Literal["real_time", "simulated"]
    tick_started_at: str  # ISO8601
    live_deals: list[dict]  # full deal rows, fetched once in load_live_deals
    candidate_deals: list[str]  # all live deal_ids (derived from live_deals)
    attention_scores: dict[str, float]  # deal_id -> raw Haiku score [0,1]
    suppressed_scores: dict[str, float]  # deal_id -> post-suppression score
    investigation_queue: list[str]  # top-K selected deal_ids
    investigator_results: Annotated[list[InvestigatorResult], operator.add]
    errors: Annotated[list[ErrorRecord], operator.add]


# ── Investigator (per-deal) graph state ───────────────────────────────────────


class InvestigatorState(TypedDict):
    tick_id: str
    deal_id: str
    deal_snapshot: DealSnapshot  # Pydantic, frozen at observation time
    risk_signals: Annotated[list[RiskSignal], operator.add]
    severity: Severity | None
    severity_reasoning: str | None
    enrichment_count: int  # number of enrichment rounds completed so far
    enrichment_chain: Annotated[list[EnrichmentStep], operator.add]  # audit trail
    enrichment_context: dict[str, Any]  # accumulated tool outputs, keyed by tool name
    intervention: Intervention | None
    observation_id: str | None
    error: str | None
