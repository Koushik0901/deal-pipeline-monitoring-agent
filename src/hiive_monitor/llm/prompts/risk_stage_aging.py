"""Stage aging risk dimension prompt (Dimension #1)."""

from __future__ import annotations

from hiive_monitor.models.risk import RiskSignal
from hiive_monitor.models.snapshot import DealSnapshot
from hiive_monitor.models.stages import DWELL_BASELINES

_SYSTEM = """\
You are a risk analyst for Hiive Transaction Services. Evaluate whether a deal's \
time in its current stage represents a meaningful stall risk.

Stage dwell baselines (calendar days):
  bid_accepted: 1d, docs_pending: 3d, issuer_notified: 2d, rofr_pending: 20d,
  rofr_cleared: 2d, signing: 4d, funding: 3d.

Trigger the signal if days_in_stage is materially over baseline (>1.5× for most stages, \
>1.2× for deadline-sensitive rofr stages). Evidence MUST cite the issuer name, stage name, \
actual days, and baseline. Reply only via the required tool."""


def build_stage_aging_prompt(snapshot: DealSnapshot) -> str:
    baseline = DWELL_BASELINES.get(snapshot.stage, 0)
    ratio = round(snapshot.days_in_stage / baseline, 2) if baseline > 0 else 0.0
    return f"""\
Evaluate stage aging risk for:

deal_id: {snapshot.deal_id}
issuer: {snapshot.issuer_name}
stage: {snapshot.stage.value}
days_in_stage: {snapshot.days_in_stage} (baseline {baseline}d, aging ratio {ratio}×)
responsible_party: {snapshot.responsible_party}
blockers: {[b.kind + ': ' + b.description for b in snapshot.blockers] or 'none'}

Is this deal materially stalled in its current stage?"""


STAGE_AGING_SYSTEM = _SYSTEM
STAGE_AGING_OUTPUT = RiskSignal
