"""Unusual characteristics risk dimension prompt (Dimension #6)."""

from __future__ import annotations

from hiive_monitor.models.risk import RiskSignal
from hiive_monitor.models.snapshot import DealSnapshot

_SYSTEM = """\
You are a risk analyst for Hiive Transaction Services. Evaluate whether a deal has \
unusual characteristics that compound risk — even if no single dimension looks severe.

Key risk factors to consider:
  - is_first_time_buyer: higher dropout risk if process feels complex
  - prior_breakage_count > 0: prior failed deals increase pattern risk
  - Unusual deal size (very large or very small vs. typical)
  - Multi-layer ROFR (issuer + company board approval needed)
  - Combination of multiple minor signals compounding

Evidence MUST cite the issuer name and at least one specific risk factor value. \
Reply only via the required tool."""


def build_unusual_characteristics_prompt(snapshot: DealSnapshot) -> str:
    rf = snapshot.risk_factors
    return f"""\
Evaluate unusual characteristics risk for:

deal_id: {snapshot.deal_id}
issuer: {snapshot.issuer_name}
stage: {snapshot.stage.value}
risk_factors: {rf}
blockers_count: {len(snapshot.blockers)}
days_in_stage: {snapshot.days_in_stage}
responsible_party: {snapshot.responsible_party}

Do the deal's characteristics compound risk beyond what standard aging / deadline metrics capture?"""


UNUSUAL_CHARACTERISTICS_SYSTEM = _SYSTEM
UNUSUAL_CHARACTERISTICS_OUTPUT = RiskSignal
