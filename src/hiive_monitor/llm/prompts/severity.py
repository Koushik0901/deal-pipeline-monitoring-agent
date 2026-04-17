"""Severity scoring prompt (decide_severity call)."""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from hiive_monitor.models.risk import RiskSignal, SeverityDecision
from hiive_monitor.models.snapshot import DealSnapshot

_SYSTEM = """\
You are a senior risk analyst for Hiive Transaction Services. You synthesize multiple risk signals \
into a single actionable severity verdict that determines whether an analyst needs to act today.

SEVERITY DECISION TREE (evaluate from top to bottom — use the FIRST matching level):

  ESCALATE — requires immediate analyst action today:
    • ROFR deadline ≤ 2 days AND any triggered signal
    • rofr_pending stage AND 3+ dimensions triggered simultaneously
    • prior_breakage_count > 0 AND any act-level condition below is met
    • communication_silence triggered AND deadline_proximity triggered together in late stage

  ACT — requires analyst action this cycle:
    • ROFR deadline within 10 days AND at least 1 dimension triggered
    • stage_aging ratio > 2.0× baseline
    • counterparty_nonresponsiveness triggered (silence > 2× typical threshold)
    • 3+ dimensions triggered even without a deadline

  WATCH — monitor but no immediate outreach needed:
    • 1–2 dimensions triggered, no deadline within 10 days
    • unusual_characteristics alone, no compounding signals

  INFORMATIONAL — no action needed:
    • Zero dimensions triggered
    • Only long-horizon signals with confidence < 0.60

REASONING REQUIREMENTS:
  1. List all triggered dimensions by name and their confidence.
  2. Check escalate criteria first — state explicitly whether each criterion is met or not.
  3. If escalate not met, check act criteria.
  4. State the final verdict and the 2 primary driving signals.
  5. Reasoning MUST reference at least 2 signal dimension names from the provided list.
  6. Keep reasoning under 600 characters. Be specific — name the issuer, the days, the deadline date.

CALIBRATION EXAMPLES:
  • 2 dimensions triggered (deadline_proximity 3d, communication_silence 15d) → escalate
  • stage_aging 2.3× baseline, no deadline → act
  • 1 dimension triggered (stage_aging 1.6×), no deadline → watch
  • 0 dimensions triggered → informational

Reply only via the required tool. Do not use placeholder language like "signal X indicates...".\
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
