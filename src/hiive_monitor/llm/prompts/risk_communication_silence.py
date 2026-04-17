"""Communication silence risk dimension prompt (Dimension #3)."""

from __future__ import annotations

from hiive_monitor.models.risk import RiskSignal
from hiive_monitor.models.snapshot import DealSnapshot

_SYSTEM = """\
You are a risk analyst for Hiive Transaction Services. Evaluate whether a deal's \
communication gap is a meaningful risk signal.

Context: each stage has an expected outreach cadence. Silence becomes a risk when:
  - No communication >7 days in a late stage (rofr_cleared, signing, funding)
  - No communication >14 days in any live stage
  - Last communication was inbound (counterparty waiting on Hiive)

Evidence MUST cite the issuer name, actual silence duration, and stage. \
Reply only via the required tool."""


def build_communication_silence_prompt(snapshot: DealSnapshot) -> str:
    recent = [
        f"  {e.event_type} @ {e.occurred_at.strftime('%Y-%m-%d')}: {e.summary}"
        for e in snapshot.recent_events
        if e.event_type in ("comm_outbound", "comm_inbound", "comm_sent_agent_recommended")
    ]
    recent_text = "\n".join(recent) if recent else "  (none)"

    return f"""\
Evaluate communication silence risk for:

deal_id: {snapshot.deal_id}
issuer: {snapshot.issuer_name}
stage: {snapshot.stage.value}
days_since_last_comm: {snapshot.days_since_last_comm if snapshot.days_since_last_comm is not None else 'unknown'}
responsible_party: {snapshot.responsible_party}

Recent communication events:
{recent_text}

Is the communication gap a material risk for this deal?"""


COMMUNICATION_SILENCE_SYSTEM = _SYSTEM
COMMUNICATION_SILENCE_OUTPUT = RiskSignal
