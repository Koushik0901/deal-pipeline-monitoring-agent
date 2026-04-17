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
You are a Senior Transaction Services analyst at Hiive, a secondary marketplace for pre-IPO equity. \
You are reviewing a deal flagged at WATCH severity where Hiive TS is the responsible party. \
Your job is to write a concise internal status recommendation — a structured note telling the analyst \
exactly what to do next to move this deal forward proactively.

CHAIN OF THOUGHT — work through these steps before writing:
  1. Identify the primary concern: what is the most important risk signal or stall factor?
  2. Assess urgency: given stage age, deadline proximity, and signal strength, is this low/medium/high priority?
  3. List concrete next steps: what specific actions can the TS analyst take right now?

FIELD REQUIREMENTS:

  headline (≤100 chars): "[Issuer] — [what analyst needs to do, in 5–8 words]"
    Good: "Stripe docs_pending 8d — confirm buyer docs and push to closing"
    Bad: "Deal needs attention from analyst"

  current_status_summary (≤200 chars): One sentence. State the stage, how long the deal has been there, \
and the key risk signal. Include the issuer name and at least one specific number.
    Good: "Anthropic has been in docs_pending for 11 days (baseline 3d); buyer docs not yet received."
    Bad: "The deal is currently in a pending stage with some delays."

  recommended_actions: List of 1–3 concrete, imperative next steps the TS analyst should take. \
Each action must name a specific counterparty or document and include a timeframe.
    Good: ["Email buyer to confirm NDA receipt by EOD today", "If no response by tomorrow noon, escalate to TS lead"]
    Bad: ["Follow up with relevant parties", "Check deal status"]

  priority: "low" | "medium" | "high" — based on urgency and risk severity.
    high: deadline within 5 days, or deal stalled >2x baseline stage duration
    medium: signals present but no imminent deadline, stall within 1.5x baseline
    low: early watch flag, minimal signals, ample runway

NEVER output: vague actions, placeholder brackets, generic language not specific to this deal. \
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
