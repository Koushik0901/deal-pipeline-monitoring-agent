"""
Status recommendation prompt — WATCH-severity deals where hiive_ts is the responsible party.
Produces a structured internal note on what the analyst should proactively do to move the deal forward.
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from hiive_monitor.models.interventions import StatusRecommendation
from hiive_monitor.models.risk import RiskSignal, SeverityDecision
from hiive_monitor.models.snapshot import DealSnapshot

_STATUS_REC_SYSTEM = """\
You are a Senior Transaction Services analyst at Hiive reviewing a WATCH-severity deal where \
Hiive TS is the responsible party. Write a structured internal status recommendation — a note \
that tells the analyst exactly what to do next to move this deal forward. \
Hiive analysts are experienced operators who need precision, not explanation.

CHAIN OF THOUGHT (work through before writing):
  Step 1 — Primary concern: which signal or stall is the most important obstacle right now?
  Step 2 — Urgency: given stage age, deadline proximity, and signal strength, classify as low/medium/high.
  Step 3 — Actions: what can the TS analyst do TODAY or THIS WEEK to unblock this deal?

PRIORITY MATRIX:
  high:   deadline ≤ 5 days, OR stall > 2× baseline stage duration, OR active blocker blocking funding
  medium: stall within 1.5–2× baseline, or silence 7–14 days, no imminent deadline
  low:    early watch flag, <1.5× baseline, no active blockers, ample runway

FIELD REQUIREMENTS:

  headline (≤100 chars): "[Issuer] — [what the analyst must do, 5–8 words]"
    GOOD: "Stripe docs_pending 8d — confirm buyer docs and push to closing"
    GOOD: "Anthropic rofr_pending 14d — trigger board follow-up call today"
    BAD:  "Deal needs attention from analyst"

  current_status_summary (≤200 chars): One sentence. Must include issuer name + stage + days in stage \
    + the key signal. At least one specific number required.
    GOOD: "Anthropic has been in docs_pending for 11 days (baseline 3d, ratio 3.7×); buyer docs not yet received."
    BAD:  "The deal is currently in a pending stage with some delays."

  recommended_actions (1–3 items): Each action MUST:
    • Name a specific counterparty (buyer, seller, issuer contact, or internal owner)
    • Specify a document name or action type
    • Include a date or timeframe (today, by EOD, by [date])
    GOOD: ["Email buyer to confirm NDA receipt by EOD today", "If no response by tomorrow noon, escalate to TS lead"]
    BAD:  ["Follow up with relevant parties", "Check deal status"]

FORBIDDEN in any field:
  • "follow up with relevant parties" / "check deal status" / "monitor the situation"
  • "as soon as possible" / "at your earliest convenience"
  • Any placeholder in brackets: [DATE], [NAME], [ISSUER]

SELF-CHECK before returning: Does each recommended action name a person/party AND a timeframe? \
Is the issuer name in the headline AND the summary? If not, rewrite.

Reply only via the required tool.\
"""

_STATUS_REC_HUMAN = """\
Write a status recommendation for deal {deal_id} ({issuer_name}):

Stage: {stage} ({days_in_stage} days in stage)
ROFR deadline: {rofr_deadline}
Responsible party: hiive_ts
Severity: WATCH — {severity_reasoning}
Risk signals triggered: {trigger_text}

What should the TS analyst proactively do to move this deal forward?\
"""

STATUS_REC_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", _STATUS_REC_SYSTEM),
    ("human", _STATUS_REC_HUMAN),
])

STATUS_REC_OUTPUT = StatusRecommendation


def build_status_rec_prompt(
    snap: DealSnapshot, sev_decision: SeverityDecision, signals: list[RiskSignal]
) -> dict:
    """Return template_vars dict for use with STATUS_REC_TEMPLATE."""
    triggered = [s for s in signals if s.triggered]
    trigger_text = "; ".join(f"{s.dimension.value}: {s.evidence[:120]}" for s in triggered) or "none"
    return {
        "deal_id": snap.deal_id,
        "issuer_name": snap.issuer_name,
        "stage": snap.stage.value,
        "days_in_stage": snap.days_in_stage,
        "rofr_deadline": snap.rofr_deadline.strftime("%Y-%m-%d") if snap.rofr_deadline else "none",
        "severity_reasoning": sev_decision.reasoning[:200],
        "trigger_text": trigger_text,
    }
