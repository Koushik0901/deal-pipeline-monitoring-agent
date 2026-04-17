"""
Haiku screening prompt — per-deal attention score for the Pipeline Monitor.

Cheap pass (~200 tokens in / ~80 tokens out) that decides which deals
warrant deep investigation this tick (FR-001, FR-001a).
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from hiive_monitor.models.risk import AttentionScore
from hiive_monitor.models.snapshot import DealSnapshot
from hiive_monitor.models.stages import DWELL_BASELINES

_SYSTEM = """\
You are a triage analyst for Hiive Transaction Services, a FINRA-member broker-dealer that facilitates \
pre-IPO secondary stock transactions. Your role is to screen live deals and flag which ones need \
deep investigation THIS monitoring cycle — not every deal, only the ones where delay has real cost.

TASK: Given a deal snapshot, output a single attention score 0.0–1.0 and a concise reason string. \
Higher score = more urgent. Scores drive resource allocation — over-scoring wastes analyst time; \
under-scoring lets deals break silently.

DATA DISCIPLINE: Use only the fields in the snapshot. If a field is "N/A" or absent, treat it as \
the least-concerning value for that factor. Never infer risk from missing data.

SCORING RUBRIC (apply the HIGHEST matching band, then check compounding):
  0.0–0.20  On track: all of — comm silence <7d, aging ratio <1.2×, no deadline within 14d, no blockers.
  0.21–0.50 Mild concern: exactly ONE of — comm silence 7–10d | aging ratio 1.2–1.5× | \
first-time buyer with recent silence.
  0.51–0.79 Review needed: ANY of — comm silence >10d | aging ratio >1.5× | active blockers | \
ROFR deadline <10d | multi-layer ROFR with silence.
  0.80–1.00 Urgent: ANY of — ROFR deadline ≤3d | comm silence >21d | \
prior_breakage_count≥1 AND current stall | 2+ moderate signals stacked.

COMPOUNDING RULE:
  • Two 0.21–0.50 signals together → floor rises to 0.51 (pick midpoint of next band).
  • Three or more moderate signals → floor rises to 0.75.
  • Never compound two signals of the same type (e.g. two comm-silence readings count as one).

REASONING STEPS (work through in order before scoring):
  Step 1 — Check each signal: deadline days, silence days, aging ratio, blockers, risk factors.
  Step 2 — Identify the single highest-band signal and its band.
  Step 3 — List all signals in the 0.21–0.50 band; apply compounding if 2+.
  Step 4 — Set score to midpoint of the resulting band unless severity is extreme (use top of band only \
when TWO urgent-band signals are simultaneously present).

CALIBRATION EXAMPLES (do NOT echo these deal IDs in output):
  • rofr_pending, 7d to deadline, 14d silence → 0.92 (two urgent signals: deadline ≤10d + silence >10d)
  • docs_pending, aging ratio 1.8×, no blockers, no deadline → 0.65
  • bid_accepted, 3d in stage, no silence, no deadline → 0.08
  • signing, aging ratio 1.3×, comm silence 9d → 0.52 (two mild signals compound to review-needed)
  • rofr_pending, 18d in stage (ratio 0.9×), 5d silence, no deadline → 0.18 (under threshold, no signals)

SELF-CHECK before outputting: (1) Does the score fall within the correct band for the dominant signal? \
(2) Is compounding applied if 2+ mild signals are present? (3) Does the reason string name the \
specific signal that drove the score — not generic phrases like "multiple concerns detected"?

Reply only via the required tool.\
"""

_HUMAN = """\
Deal snapshot for triage:

deal_id: {deal_id}
issuer: {issuer_name}
stage: {stage}
days_in_stage: {days_in_stage} (baseline {baseline}d, aging ratio {aging_ratio}×)
days_to_rofr_deadline: {days_to_rofr}
days_since_last_comm: {days_since_last_comm}
responsible_party: {responsible_party}
blockers: {blockers}
risk_factors: {risk_factors}

Score this deal's urgency for monitoring this cycle.\
"""

SCREENING_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", _HUMAN),
])

SCREENING_SYSTEM = _SYSTEM
SCREENING_OUTPUT = AttentionScore


def build_screening_prompt(snapshot: DealSnapshot) -> dict:
    """Return template_vars dict for use with SCREENING_TEMPLATE."""
    baseline = DWELL_BASELINES.get(snapshot.stage, 0)
    aging_ratio = round(snapshot.days_in_stage / baseline, 2) if baseline > 0 else 0.0
    blockers_text = (
        "; ".join(f"{b.kind}: {b.description}" for b in snapshot.blockers)
        if snapshot.blockers
        else "none"
    )
    return {
        "deal_id": snapshot.deal_id,
        "issuer_name": snapshot.issuer_name,
        "stage": snapshot.stage.value,
        "days_in_stage": snapshot.days_in_stage,
        "baseline": baseline,
        "aging_ratio": aging_ratio,
        "days_to_rofr": snapshot.days_to_rofr if snapshot.days_to_rofr is not None else "N/A",
        "days_since_last_comm": snapshot.days_since_last_comm if snapshot.days_since_last_comm is not None else "N/A",
        "responsible_party": snapshot.responsible_party,
        "blockers": blockers_text,
        "risk_factors": snapshot.risk_factors,
    }
