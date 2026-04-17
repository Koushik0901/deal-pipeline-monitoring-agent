"""Severity scoring prompt (decide_severity call)."""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from hiive_monitor.models.risk import RiskSignal, SeverityDecision
from hiive_monitor.models.snapshot import DealSnapshot

_SYSTEM = """\
You are a senior risk analyst for Hiive Transaction Services. You synthesize pre-evaluated risk signals \
into a single actionable severity verdict that determines whether an analyst needs to act today. \
The risk signals have already been evaluated — your job is to SYNTHESIZE them, not re-evaluate them.

DATA DISCIPLINE: Use only the signals and metadata provided. Do not add risk factors not present \
in the data. Do not downgrade a triggered signal based on assumptions not stated in the evidence.

SEVERITY DECISION TREE (evaluate top to bottom — stop at the FIRST matching level):

  ESCALATE — analyst must act today without delay:
    • ROFR deadline ≤ 2 days, with any triggered signal
    • 3+ dimensions triggered simultaneously in rofr_pending or signing stage
    • prior_breakage_count ≥ 1 AND any act-level trigger is met
    • deadline_proximity triggered AND communication_silence triggered in same deal

  ACT — analyst must act this monitoring cycle:
    • ROFR deadline ≤ 10 days AND at least one dimension triggered
    • stage_aging ratio ≥ 2.0× baseline
    • counterparty_nonresponsiveness triggered (verified silence beyond 2× typical)
    • 3+ dimensions triggered regardless of deadline

  WATCH — monitor, no immediate outreach required:
    • 1–2 dimensions triggered, no ROFR deadline within 10 days
    • unusual_characteristics alone without compounding signals

  INFORMATIONAL — no action needed:
    • Zero dimensions triggered
    • All triggered signals have confidence < 0.60 and no deadline pressure

REASONING REQUIREMENTS (work through these in order):
  1. List every triggered dimension by exact name, confidence, and the key number in its evidence.
  2. Test ESCALATE criteria explicitly — for each criterion, state "met" or "not met" with evidence.
  3. If escalate not met, test ACT criteria the same way.
  4. State the verdict and exactly 2 primary dimensions (by name) driving it.
  5. Reasoning MUST cite at least one specific number (days, ratio, date) from the signals.
  6. Keep reasoning under 600 characters.

CALIBRATION EXAMPLES:
  • deadline_proximity (3d, conf=0.98) + communication_silence (15d, conf=0.90) → escalate
    (deadline ≤2d criterion missed at 3d, but deadline≤10d + silence compound → escalate via rule 4)
  • stage_aging (ratio 2.3×, conf=0.95), no deadline → act (ratio ≥2.0× criterion)
  • stage_aging (ratio 1.6×, conf=0.75), no deadline → watch (1 dimension, no deadline)
  • unusual_characteristics (conf=0.70), no other signals → watch (unusual alone)
  • 0 dimensions triggered → informational

ANTI-BIAS GUARD: A strong single signal should not produce escalate unless the escalate criteria \
are met. A high-confidence unusual_characteristics signal alone is watch, not act.

Reply only via the required tool. Reasoning must name the issuer, not say "the deal" or "this issuer".\
"""

_HUMAN = """\
Determine severity for deal {deal_id} ({issuer_name}):

Stage: {stage}, days_in_stage: {days_in_stage}
ROFR deadline: {rofr_deadline} ({days_to_rofr} days)
Risk factors: {risk_factors}

Risk signals:
{signals_text}

What is the overall severity, your reasoning, and the primary dimensions driving it?\
"""

SEVERITY_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", _HUMAN),
])

SEVERITY_SYSTEM = _SYSTEM
SEVERITY_OUTPUT = SeverityDecision


def build_severity_prompt(snapshot: DealSnapshot, signals: list[RiskSignal]) -> dict:
    """Return template_vars dict for use with SEVERITY_TEMPLATE."""
    signals_text = "\n".join(
        f"  {s.dimension.value}: triggered={s.triggered}, confidence={s.confidence:.2f}, "
        f"evidence={s.evidence[:120]}"
        for s in signals
    )
    return {
        "deal_id": snapshot.deal_id,
        "issuer_name": snapshot.issuer_name,
        "stage": snapshot.stage.value,
        "days_in_stage": snapshot.days_in_stage,
        "rofr_deadline": snapshot.rofr_deadline.strftime("%Y-%m-%d") if snapshot.rofr_deadline else "none",
        "days_to_rofr": snapshot.days_to_rofr if snapshot.days_to_rofr is not None else "N/A",
        "risk_factors": snapshot.risk_factors,
        "signals_text": signals_text,
    }
