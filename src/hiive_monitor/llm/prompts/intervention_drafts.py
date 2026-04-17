"""
Intervention drafting prompts — outbound nudge, internal escalation, brief entry.
All three share the same design: system context + deal-specific user prompt.
"""

from __future__ import annotations

from hiive_monitor.models.interventions import BriefEntry, InternalEscalation, OutboundNudge
from hiive_monitor.models.risk import RiskSignal, SeverityDecision
from hiive_monitor.models.snapshot import DealSnapshot

# ── Outbound nudge ────────────────────────────────────────────────────────────

_OUTBOUND_SYSTEM = """\
You are drafting an outreach email for a Hiive Transaction Services analyst to send to an \
external counterparty (buyer, seller, or issuer). Voice: direct, professional, numerically grounded. \
No hedging. No signature block. No generic preamble.

Mandatory content: the email body MUST contain:
  1. The issuer display name
  2. The current deal stage context
  3. If deadline-driven: the exact calendar date of the relevant deadline
  4. A clear, specific request or next step

Tone: Transaction Services analyst — warm but decisive. Reply only via the required tool."""


def build_outbound_nudge_prompt(
    snapshot: DealSnapshot, severity: SeverityDecision, signals: list[RiskSignal]
) -> str:
    triggered = [s for s in signals if s.triggered]
    trigger_text = "; ".join(f"{s.dimension.value}: {s.evidence[:100]}" for s in triggered)
    if snapshot.rofr_deadline and snapshot.days_to_rofr is not None:
        deadline_date = snapshot.rofr_deadline.strftime("%Y-%m-%d")
        deadline_text = f"ROFR deadline: {deadline_date} ({snapshot.days_to_rofr} days remaining)"
        deadline_instruction = f"\nREQUIRED: Your email body MUST include the exact deadline date in ISO format: {deadline_date} — use this exact format, do not write it as a month name."
    else:
        deadline_text = "No ROFR deadline"
        deadline_instruction = ""
    return f"""\
Draft an outbound nudge for deal {snapshot.deal_id}:

Issuer: {snapshot.issuer_name}
Stage: {snapshot.stage.value} ({snapshot.days_in_stage} days)
{deadline_text}
Responsible party: {snapshot.responsible_party}
Severity: {severity.severity.value}
Key risk signals: {trigger_text or 'none'}{deadline_instruction}

Draft a concise, actionable outreach email to the {snapshot.responsible_party}."""


OUTBOUND_SYSTEM = _OUTBOUND_SYSTEM
OUTBOUND_OUTPUT = OutboundNudge

# ── Internal escalation ───────────────────────────────────────────────────────

_ESCALATION_SYSTEM = """\
You are drafting an internal escalation note for a Hiive Transaction Services analyst to \
route to the TS lead, legal, or ops. Voice: factual, urgent, specific. List the problem, \
what has been tried, and a concrete suggested next step. Reply only via the required tool."""


def build_escalation_prompt(
    snapshot: DealSnapshot, severity: SeverityDecision, signals: list[RiskSignal]
) -> str:
    triggered = [s for s in signals if s.triggered]
    trigger_text = "; ".join(f"{s.dimension.value}: {s.evidence[:120]}" for s in triggered)
    return f"""\
Draft an internal escalation note for deal {snapshot.deal_id}:

Issuer: {snapshot.issuer_name}
Stage: {snapshot.stage.value} ({snapshot.days_in_stage} days)
ROFR deadline: {snapshot.rofr_deadline.strftime('%Y-%m-%d') if snapshot.rofr_deadline else 'none'}
Risk factors: {snapshot.risk_factors}
Severity: {severity.severity.value} — {severity.reasoning[:200]}
Signals: {trigger_text or 'none'}

Who should this escalate to and what is the specific recommended next step?"""


ESCALATION_SYSTEM = _ESCALATION_SYSTEM
ESCALATION_OUTPUT = InternalEscalation

# ── Brief entry ───────────────────────────────────────────────────────────────

_BRIEF_SYSTEM = """\
You are drafting a brief-entry line for a Hiive Transaction Services analyst's daily summary. \
Voice: executive-style, one-line awareness item. Must convey what is happening and what the \
analyst should do, in plain language. No hedging. Reply only via the required tool."""


def build_brief_entry_prompt(
    snapshot: DealSnapshot, severity: SeverityDecision, signals: list[RiskSignal]
) -> str:
    triggered = [s for s in signals if s.triggered]
    trigger_names = ", ".join(s.dimension.value for s in triggered) or "no major signals"
    return f"""\
Draft a brief entry for deal {snapshot.deal_id} ({snapshot.issuer_name}):

Stage: {snapshot.stage.value}, severity: {severity.severity.value}
Primary signals: {trigger_names}
Reasoning: {severity.reasoning[:200]}

Write a headline, one-line summary, and recommended action for the analyst's daily brief."""


BRIEF_SYSTEM = _BRIEF_SYSTEM
BRIEF_OUTPUT = BriefEntry
