"""Deadline proximity risk dimension prompt (Dimension #2)."""

from __future__ import annotations

from hiive_monitor.models.risk import RiskSignal
from hiive_monitor.models.snapshot import DealSnapshot

_SYSTEM = """\
You are a risk analyst for Hiive Transaction Services. Evaluate whether a deal's \
upcoming ROFR deadline poses an escalating risk.

Escalating bands:
  <2 days: critical — intervene now
  <5 days: urgent — draft outreach today
  <10 days: elevated — flag for analyst awareness

Only trigger if a deadline exists. Evidence MUST cite the issuer name, the exact \
deadline date, and days remaining. Reply only via the required tool."""


def build_deadline_proximity_prompt(snapshot: DealSnapshot) -> str:
    if snapshot.rofr_deadline:
        deadline_str = snapshot.rofr_deadline.strftime("%Y-%m-%d")
        days_str = str(snapshot.days_to_rofr) if snapshot.days_to_rofr is not None else "unknown"
    else:
        deadline_str = "none"
        days_str = "N/A"

    return f"""\
Evaluate ROFR deadline proximity risk for:

deal_id: {snapshot.deal_id}
issuer: {snapshot.issuer_name}
stage: {snapshot.stage.value}
rofr_deadline: {deadline_str}
days_to_rofr: {days_str}
responsible_party: {snapshot.responsible_party}

Is the ROFR deadline creating an elevated or critical risk?"""


DEADLINE_PROXIMITY_SYSTEM = _SYSTEM
DEADLINE_PROXIMITY_OUTPUT = RiskSignal
