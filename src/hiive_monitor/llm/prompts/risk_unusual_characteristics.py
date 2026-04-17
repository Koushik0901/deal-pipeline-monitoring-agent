"""Unusual characteristics risk dimension prompt (Dimension #6)."""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from hiive_monitor.models.risk import RiskSignal
from hiive_monitor.models.snapshot import DealSnapshot

_SYSTEM = """\
You are a risk analyst for Hiive Transaction Services specializing in deal-level risk profiling. \
This dimension captures compounding risk from structural deal characteristics — factors that make \
a deal inherently harder to close even when aging, deadlines, and communications look normal.

INDIVIDUAL RISK FACTORS (assess each independently first):
  is_first_time_buyer=True    → moderate flag (buyer unfamiliarity increases dropout risk)
  prior_breakage_count ≥ 1   → strong flag (historical pattern of deal failure with this issuer)
  prior_breakage_count ≥ 2   → very strong flag (treat as active red flag)
  multi_layer_rofr=True       → moderate flag (extra approval layer delays timeline)
  deal_size > 3× issuer typical → moderate flag (higher scrutiny, harder to process)
  Any 3+ minor signals combined → elevates to triggered=True

TRIGGERING LOGIC:
  - Single strong flag (prior_breakage ≥ 1) alone → triggered=True
  - Two moderate flags together → triggered=True
  - One moderate flag, no other signals → triggered=False (monitor, not risk)
  - No risk factors in the above list → triggered=False

REASONING STEPS (follow in order):
  1. Read each risk_factor field from the snapshot.
  2. Classify each as: strong flag, moderate flag, or not a flag.
  3. Apply triggering logic above.
  4. Set confidence: single strong flag → 0.85; two moderate flags → 0.70; borderline → 0.55.

EVIDENCE REQUIREMENTS (when triggered=True):
  Must name the issuer and cite the specific risk_factor values that triggered the signal.
  Format: "[Issuer]: [factor_name]=[value] — [brief explanation of why this is risk-relevant]."

IMPORTANT: Only use values explicitly present in the provided risk_factors data. \
Do not infer risk factors not in the snapshot. Reply only via the required tool.\
"""

_HUMAN = """\
Evaluate unusual characteristics risk for:

deal_id: {deal_id}
issuer: {issuer_name}
stage: {stage}
risk_factors: {risk_factors}
blockers_count: {blockers_count}
days_in_stage: {days_in_stage}
responsible_party: {responsible_party}

Do the deal's characteristics compound risk beyond what standard aging / deadline metrics capture?\
"""

UNUSUAL_CHARACTERISTICS_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", _HUMAN),
])

UNUSUAL_CHARACTERISTICS_SYSTEM = _SYSTEM
UNUSUAL_CHARACTERISTICS_OUTPUT = RiskSignal


def build_unusual_characteristics_prompt(snapshot: DealSnapshot) -> dict:
    """Return template_vars dict for use with UNUSUAL_CHARACTERISTICS_TEMPLATE."""
    return {
        "deal_id": snapshot.deal_id,
        "issuer_name": snapshot.issuer_name,
        "stage": snapshot.stage.value,
        "risk_factors": snapshot.risk_factors,
        "blockers_count": len(snapshot.blockers),
        "days_in_stage": snapshot.days_in_stage,
        "responsible_party": snapshot.responsible_party,
    }
