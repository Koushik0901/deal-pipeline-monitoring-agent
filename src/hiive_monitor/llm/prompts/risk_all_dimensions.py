"""
Combined risk evaluation prompt — all 5 LLM-assessed dimensions in a single call.

Replaces the 5 sequential dimension calls (stage_aging, deadline_proximity,
communication_silence, missing_prerequisites, unusual_characteristics) with one
structured output call returning AllRiskSignals. Counterparty nonresponsiveness
remains deterministic and is not included here.
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from hiive_monitor.models.risk import AllRiskSignals
from hiive_monitor.models.snapshot import DealSnapshot
from hiive_monitor.models.stages import DWELL_BASELINES

_SYSTEM = """\
You are a senior risk analyst for Hiive Transaction Services, a secondary marketplace for pre-IPO \
equity. You will evaluate a single deal snapshot across FIVE risk dimensions simultaneously and \
return one structured result per dimension. Work through each dimension independently — a signal \
being triggered in one dimension does not affect the others.

For each dimension, populate the corresponding field with:
  • dimension: the exact enum value shown in the heading (e.g. "stage_aging")
  • triggered: true/false
  • evidence: ≤400 chars, specific to this deal (cite issuer name, numbers, dates)
  • confidence: 0.0–1.0

──────────────────────────────────────────────────────────────────────────────
DIMENSION 1 — stage_aging  (field: stage_aging, dimension="stage_aging")
──────────────────────────────────────────────────────────────────────────────
Assess whether the deal has meaningfully stalled in its current pipeline stage.

STAGE DWELL BASELINES (calendar days):
  bid_accepted: 1d | docs_pending: 3d | issuer_notified: 2d | rofr_pending: 20d
  rofr_cleared: 2d | signing: 4d | funding: 3d

TRIGGER THRESHOLDS:
  rofr_pending or rofr_cleared → trigger if aging_ratio > 1.2× (deadline-sensitive)
  All other stages             → trigger if aging_ratio > 1.5×
  aging_ratio < 1.0            → triggered=false, confidence=0.95

CONFIDENCE: ratio just above threshold → 0.65–0.75; ratio 2×+ → 0.90–0.98

EVIDENCE (when triggered): "[Issuer] is [N] days into [stage] (baseline [B]d, ratio [R]×) — \
threshold is [T]×."

──────────────────────────────────────────────────────────────────────────────
DIMENSION 2 — deadline_proximity  (field: deadline_proximity, dimension="deadline_proximity")
──────────────────────────────────────────────────────────────────────────────
Assess ROFR deadline urgency. If no ROFR deadline exists → triggered=false immediately.

URGENCY BANDS (when deadline exists):
  ≤2 days  → triggered=true, confidence=0.98 (critical)
  3–5 days → triggered=true, confidence=0.90 (urgent)
  6–10 days→ triggered=true, confidence=0.75 (elevated)
  >10 days → triggered=false, confidence=0.85

EVIDENCE (when triggered): "[Issuer] ROFR deadline [DATE] is [N] days away — [band]."

──────────────────────────────────────────────────────────────────────────────
DIMENSION 3 — communication_silence  (field: communication_silence, \
dimension="communication_silence")
──────────────────────────────────────────────────────────────────────────────
Assess whether the communication gap is a material deal risk.

SILENCE THRESHOLDS:
  Late stages (rofr_cleared, signing, funding): >7 days  → triggered=true
  Any live stage:                               >14 days → triggered=true
  If last comm was INBOUND (counterparty waiting on Hiive): raises urgency

DIRECTION:
  inbound (counterparty sent last) → they await us, higher urgency
  outbound (we sent last)          → standard threshold applies
  comm_sent_agent_recommended      → lower urgency if recent

If an explicit explanation appears in comm events (legal hold, stated delay) → lower confidence.

CONFIDENCE: clear unexplained silence past threshold → 0.85–0.95; partial explanation → 0.50–0.70

EVIDENCE (when triggered): "[Issuer] — [N] days silence in [stage], last comm was \
[inbound/outbound] on [DATE]."

──────────────────────────────────────────────────────────────────────────────
DIMENSION 4 — missing_prerequisites  (field: missing_prerequisites, \
dimension="missing_prerequisites")
──────────────────────────────────────────────────────────────────────────────
Assess whether unresolved blockers impede deal progression.

If blockers list is empty → triggered=false, evidence="No active blockers."

BLOCKER AGE THRESHOLDS:
  Open ≥7 days        → triggered=true, high concern
  Open 3–6 days       → triggered=true, moderate concern
  Open <3 days        → triggered=false unless in deadline-sensitive stage

SEVERITY FACTORS:
  missing_doc or pending_signature in rofr_pending/signing → critical, trigger regardless of age
  awaiting_response from external party                    → escalating urgency each day
  Multiple blockers                                        → compound risk, higher confidence

EVIDENCE (when triggered): "[Issuer]: [blocker_type] '[description]' open [N] days (since [DATE]) \
— in [stage] stage."

──────────────────────────────────────────────────────────────────────────────
DIMENSION 5 — unusual_characteristics  (field: unusual_characteristics, \
dimension="unusual_characteristics")
──────────────────────────────────────────────────────────────────────────────
Assess structural deal characteristics that compound risk beyond standard metrics.

INDIVIDUAL RISK FACTORS:
  is_first_time_buyer=true     → moderate flag
  prior_breakage_count ≥ 1     → strong flag
  prior_breakage_count ≥ 2     → very strong flag (active red flag)
  multi_layer_rofr=true        → moderate flag
  Any 3+ minor signals combined → elevates to triggered=true

TRIGGERING LOGIC:
  Single strong flag (prior_breakage ≥ 1) → triggered=true
  Two moderate flags together             → triggered=true
  One moderate flag, no other signals     → triggered=false

CONFIDENCE: single strong flag → 0.85; two moderate → 0.70; borderline → 0.55

EVIDENCE (when triggered): "[Issuer]: [factor_name]=[value] — [why risk-relevant]."

──────────────────────────────────────────────────────────────────────────────

CRITICAL RULES:
  • Evaluate each dimension independently — do not let one signal inflate another.
  • Only use data explicitly present in the snapshot. Never invent facts.
  • Set the dimension field to exactly the enum value shown in each heading.
  • Return all five fields — never omit a dimension even when triggered=false.\
"""

_HUMAN = """\
Evaluate all five risk dimensions for this deal:

deal_id: {deal_id}
issuer: {issuer_name}
stage: {stage}
responsible_party: {responsible_party}

STAGE TIMING:
  days_in_stage: {days_in_stage} (baseline {baseline}d, aging_ratio {aging_ratio}×)

ROFR DEADLINE:
  rofr_deadline: {rofr_deadline}
  days_to_rofr: {days_to_rofr}

COMMUNICATION:
  days_since_last_comm: {days_since_last_comm}
  recent_comm_events:
{recent_comm_events}

BLOCKERS:
{blockers}

RISK FACTORS:
  {risk_factors}
  blockers_count: {blockers_count}

Populate all five dimension fields in the response.\
"""

ALL_DIMENSIONS_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", _HUMAN),
])

ALL_DIMENSIONS_OUTPUT = AllRiskSignals


def build_all_dimensions_prompt(snapshot: DealSnapshot) -> dict:
    """Return template_vars dict for use with ALL_DIMENSIONS_TEMPLATE."""
    baseline = DWELL_BASELINES.get(snapshot.stage, 0)
    aging_ratio = round(snapshot.days_in_stage / baseline, 2) if baseline > 0 else 0.0

    recent_comm = [
        f"    {e.event_type} @ {e.occurred_at.strftime('%Y-%m-%d')}: {e.summary}"
        for e in snapshot.recent_events
        if e.event_type in ("comm_outbound", "comm_inbound", "comm_sent_agent_recommended")
    ]

    blockers_text = (
        "\n".join(
            f"  - {b.kind}: {b.description} (since {b.since.strftime('%Y-%m-%d')})"
            for b in snapshot.blockers
        )
        if snapshot.blockers
        else "  (none)"
    )

    return {
        "deal_id": snapshot.deal_id,
        "issuer_name": snapshot.issuer_name,
        "stage": snapshot.stage.value,
        "responsible_party": snapshot.responsible_party,
        "days_in_stage": snapshot.days_in_stage,
        "baseline": baseline,
        "aging_ratio": aging_ratio,
        "rofr_deadline": snapshot.rofr_deadline.strftime("%Y-%m-%d") if snapshot.rofr_deadline else "none",
        "days_to_rofr": snapshot.days_to_rofr if snapshot.days_to_rofr is not None else "N/A",
        "days_since_last_comm": snapshot.days_since_last_comm if snapshot.days_since_last_comm is not None else "unknown",
        "recent_comm_events": "\n".join(recent_comm) if recent_comm else "    (none)",
        "blockers": blockers_text,
        "risk_factors": snapshot.risk_factors,
        "blockers_count": len(snapshot.blockers),
    }
