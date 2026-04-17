"""
Haiku screening prompt — per-deal attention score for the Pipeline Monitor.

Cheap pass (~200 tokens in / ~80 tokens out) that decides which deals
warrant deep investigation this tick (FR-001, FR-001a).
"""

from __future__ import annotations

from hiive_monitor.models.risk import AttentionScore
from hiive_monitor.models.snapshot import DealSnapshot
from hiive_monitor.models.stages import DWELL_BASELINES, Stage

_SYSTEM = """\
You are a triage assistant for Hiive Transaction Services, a FINRA-member broker-dealer facilitating \
pre-IPO secondary stock transactions.

Your job: given a snapshot of one live deal, output an attention score between 0.0 and 1.0 indicating \
how urgently this deal needs deep human or agent review this monitoring cycle.

Scoring anchors:
- 0.0–0.2: Everything on track. No deadline pressure, no silence, no unusual factors.
- 0.3–0.5: Worth watching. Mild aging or minor communication gap, no immediate deadline.
- 0.6–0.8: Needs review this cycle. Significant aging, deadline within 10 days, or extended comm silence.
- 0.9–1.0: Urgent. ROFR expiring in <3 days, multi-layer ROFR unresolved, known prior breakage with stall.

Factor in: stage aging vs. baseline, days to ROFR deadline, days since last communication, \
responsible party, active blockers, first-time buyer, prior breakage count.

Reply only via the required tool.
"""


def build_screening_prompt(snapshot: DealSnapshot) -> str:
    baseline = DWELL_BASELINES.get(snapshot.stage, 0)
    aging_ratio = round(snapshot.days_in_stage / baseline, 2) if baseline > 0 else 0.0

    blockers_text = (
        "; ".join(f"{b.kind}: {b.description}" for b in snapshot.blockers)
        if snapshot.blockers
        else "none"
    )

    return f"""\
Deal snapshot for triage:

deal_id: {snapshot.deal_id}
issuer: {snapshot.issuer_name}
stage: {snapshot.stage.value}
days_in_stage: {snapshot.days_in_stage} (baseline {baseline}d, aging ratio {aging_ratio}×)
days_to_rofr_deadline: {snapshot.days_to_rofr if snapshot.days_to_rofr is not None else "N/A"}
days_since_last_comm: {snapshot.days_since_last_comm if snapshot.days_since_last_comm is not None else "N/A"}
responsible_party: {snapshot.responsible_party}
blockers: {blockers_text}
risk_factors: {snapshot.risk_factors}

Score this deal's urgency for monitoring this cycle."""


SCREENING_SYSTEM = _SYSTEM
SCREENING_MODEL = None  # caller passes model from settings
SCREENING_OUTPUT = AttentionScore
