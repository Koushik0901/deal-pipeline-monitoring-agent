"""Stage aging risk dimension prompt (Dimension #1)."""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from hiive_monitor.models.risk import RiskSignal
from hiive_monitor.models.snapshot import DealSnapshot
from hiive_monitor.models.stages import DWELL_BASELINES

_SYSTEM = """\
You are a risk analyst for Hiive Transaction Services specializing in deal-stage throughput. \
Your job is to determine whether a deal has meaningfully stalled — not just run slightly long.

STAGE DWELL BASELINES (calendar days):
  bid_accepted: 1d | docs_pending: 3d | issuer_notified: 2d | rofr_pending: 20d
  rofr_cleared: 2d | signing: 4d | funding: 3d

TRIGGER THRESHOLDS:
  - rofr_pending or rofr_cleared: trigger if aging_ratio > 1.2× (deadline-sensitive)
  - All other stages: trigger if aging_ratio > 1.5×
  - aging_ratio < 1.0: deal is ahead of schedule — triggered=False, confidence=0.95

REASONING STEPS (follow in order):
  1. Identify the stage and its baseline from the table above.
  2. Compute aging_ratio = days_in_stage ÷ baseline (already provided in the prompt).
  3. Apply the appropriate threshold for this stage.
  4. Set triggered=True only if the ratio exceeds the threshold.
  5. Set confidence proportional to how far over the threshold the ratio is:
     - ratio just above threshold: 0.65–0.75
     - ratio 2×+ threshold: 0.90–0.98

EVIDENCE REQUIREMENTS (mandatory when triggered=True):
  Must cite ALL of: issuer name, stage name, actual days, baseline days, computed ratio.
  Format: "[Issuer] is [N] days into [stage] (baseline [B]d, ratio [R]×) — threshold is [T]×."

WHEN triggered=False:
  Evidence should briefly confirm why (e.g., "3 days in docs_pending, baseline 3d — on schedule").

Do not invent data not present in the snapshot. Reply only via the required tool.\
"""

_HUMAN = """\
Evaluate stage aging risk for:

deal_id: {deal_id}
issuer: {issuer_name}
stage: {stage}
days_in_stage: {days_in_stage} (baseline {baseline}d, aging ratio {aging_ratio}×)
responsible_party: {responsible_party}
blockers: {blockers}

Is this deal materially stalled in its current stage?\
"""

STAGE_AGING_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", _HUMAN),
])

STAGE_AGING_SYSTEM = _SYSTEM
STAGE_AGING_OUTPUT = RiskSignal


def build_stage_aging_prompt(snapshot: DealSnapshot) -> dict:
    """Return template_vars dict for use with STAGE_AGING_TEMPLATE."""
    baseline = DWELL_BASELINES.get(snapshot.stage, 0)
    ratio = round(snapshot.days_in_stage / baseline, 2) if baseline > 0 else 0.0
    return {
        "deal_id": snapshot.deal_id,
        "issuer_name": snapshot.issuer_name,
        "stage": snapshot.stage.value,
        "days_in_stage": snapshot.days_in_stage,
        "baseline": baseline,
        "aging_ratio": ratio,
        "responsible_party": snapshot.responsible_party,
        "blockers": [b.kind + ": " + b.description for b in snapshot.blockers] or "none",
    }
