"""Severity scoring prompt (decide_severity call)."""

from __future__ import annotations

from hiive_monitor.models.risk import RiskSignal, SeverityDecision
from hiive_monitor.models.snapshot import DealSnapshot

_SYSTEM = """\
You are a senior risk analyst for Hiive Transaction Services. Given a set of risk signals \
for one deal, determine the overall severity level.

Severity rubric:
  informational: No dimension triggered, or only long-horizon unusual characteristics.
  watch: 1–2 dimensions triggered, no deadline within 10 days.
  act: Deadline within 10 days AND a triggered dimension, OR stage aging >2× baseline, \
       OR counterparty silence >2× typical response threshold.
  escalate: Deadline within 2 days unresolved, OR multi-dimension hit on stalled rofr_pending, \
             OR prior_breakage_count > 0 with any act-level trigger.

Your reasoning must reference at least two signals by name. Reply only via the required tool."""


def build_severity_prompt(snapshot: DealSnapshot, signals: list[RiskSignal]) -> str:
    signals_text = "\n".join(
        f"  {s.dimension.value}: triggered={s.triggered}, confidence={s.confidence:.2f}, "
        f"evidence={s.evidence[:120]}"
        for s in signals
    )
    return f"""\
Determine severity for deal {snapshot.deal_id} ({snapshot.issuer_name}):

Stage: {snapshot.stage.value}, days_in_stage: {snapshot.days_in_stage}
ROFR deadline: {snapshot.rofr_deadline.strftime('%Y-%m-%d') if snapshot.rofr_deadline else 'none'} \
({snapshot.days_to_rofr if snapshot.days_to_rofr is not None else 'N/A'} days)
Risk factors: {snapshot.risk_factors}

Risk signals:
{signals_text}

What is the overall severity, your reasoning, and the primary dimensions driving it?"""


SEVERITY_SYSTEM = _SYSTEM
SEVERITY_OUTPUT = SeverityDecision
