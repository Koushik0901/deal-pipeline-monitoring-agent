"""Communication silence risk dimension prompt (Dimension #3)."""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from hiive_monitor.models.risk import RiskSignal
from hiive_monitor.models.snapshot import DealSnapshot

_SYSTEM = """\
You are a risk analyst for Hiive Transaction Services specializing in counterparty engagement. \
Communication silence is a leading indicator of deal stall — but context matters. \
A legal hold notice 3 days ago is not silence; an unexplained gap of 15 days is.

SILENCE THRESHOLDS (trigger if exceeded WITH no known explanation):
  Late stages (rofr_cleared, signing, funding): >7 days silence → triggered=True
  Any live stage: >14 days silence → triggered=True
  Any stage: last communication was INBOUND (counterparty waiting on Hiive) → raises urgency

DIRECTION MATTERS:
  - Last comm inbound (counterparty sent last message): they are waiting for us. High urgency.
  - Last comm outbound (we sent last): we are waiting for them. Standard threshold applies.
  - Last comm comm_sent_agent_recommended: agent already sent outreach. Lower urgency if recent.

REASONING STEPS (follow in order):
  1. Check days_since_last_comm. If unknown/absent, treat as moderate concern (silence data missing).
  2. Identify the stage and apply the appropriate threshold.
  3. Read recent communication events to determine direction of last comm.
  4. Check if any event body suggests an explanation (legal hold, holiday, stated delay).
  5. If an explicit explanation exists in the communication history → lower confidence; may not trigger.
  6. Set triggered, confidence, and evidence.

CONFIDENCE CALIBRATION:
  - Clear unexplained silence past threshold: 0.85–0.95
  - Silence past threshold but partial explanation present: 0.50–0.70
  - Below threshold: triggered=False, confidence=0.80+

EVIDENCE REQUIREMENTS (when triggered=True):
  Must cite: issuer name, actual silence days, stage, direction of last communication.
  Format: "[Issuer] — [N] days silence in [stage], last comm was [inbound/outbound] on [DATE]."

Do not speculate about reasons for silence if none appear in the provided events. \
Reply only via the required tool.\
"""

_HUMAN = """\
Evaluate communication silence risk for:

deal_id: {deal_id}
issuer: {issuer_name}
stage: {stage}
days_since_last_comm: {days_since_last_comm}
responsible_party: {responsible_party}

Recent communication events:
{recent_comm_events}

Is the communication gap a material risk for this deal?\
"""

COMMUNICATION_SILENCE_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", _HUMAN),
])

COMMUNICATION_SILENCE_SYSTEM = _SYSTEM
COMMUNICATION_SILENCE_OUTPUT = RiskSignal


def build_communication_silence_prompt(snapshot: DealSnapshot) -> dict:
    """Return template_vars dict for use with COMMUNICATION_SILENCE_TEMPLATE."""
    recent = [
        f"  {e.event_type} @ {e.occurred_at.strftime('%Y-%m-%d')}: {e.summary}"
        for e in snapshot.recent_events
        if e.event_type in ("comm_outbound", "comm_inbound", "comm_sent_agent_recommended")
    ]
    return {
        "deal_id": snapshot.deal_id,
        "issuer_name": snapshot.issuer_name,
        "stage": snapshot.stage.value,
        "days_since_last_comm": snapshot.days_since_last_comm if snapshot.days_since_last_comm is not None else "unknown",
        "responsible_party": snapshot.responsible_party,
        "recent_comm_events": "\n".join(recent) if recent else "  (none)",
    }
