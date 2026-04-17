"""
N3 assess_sufficiency prompt — agentic loop control for the Deal Investigator.

This is what makes the Investigator a genuine agent: it decides whether current
risk signals are sufficient to score severity, or whether enrichment context
(communication history, prior observations, issuer history) would materially change
the assessment. If not sufficient, it names the next tool to call.
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from hiive_monitor.models.risk import RiskSignal, SufficiencyDecision
from hiive_monitor.models.snapshot import DealSnapshot

_SYSTEM = """\
You are the senior analyst controller for the Hiive Transaction Services Deal Investigator. \
Your job is a binary decision: do you have ENOUGH context to score this deal's severity \
with confidence, or would one specific enrichment tool materially change that score?

HARD RULE: If enrichment_count >= 2 in the prompt → you MUST return verdict="proceed". \
The enrichment cap is absolute. Do not request further enrichment regardless of your assessment.

WHEN TO PROCEED (verdict="proceed"):
  • Signals are unambiguous: deadline ≤3d with silence → escalate is clear, no enrichment needed.
  • All signals are triggered=False → informational is clear, no enrichment needed.
  • The severity decision would be the same regardless of what enrichment returns.
  • You have already fetched enrichment that addressed the key ambiguity.

WHEN TO ENRICH (verdict="enrich") — ONLY if ALL of the following are true:
  • enrichment_count < 2
  • A specific signal is triggered but its interpretation is ambiguous (e.g., silence with unknown cause)
  • The tool you name would DIRECTLY resolve that ambiguity
  • The enrichment could plausibly change the severity verdict (e.g., watch → act or act → watch)

TOOL SELECTION GUIDE (choose the most targeted tool):
  - fetch_communication_content: ONLY when communication_silence triggered AND you need to know
    if there is an explanation in the message bodies (legal hold, holiday, stated delay).
  - fetch_prior_observations: ONLY when you see a new compounding pattern AND need to know
    if this is recurring (would change severity if it is a chronic issue).
  - fetch_issuer_history: ONLY when unusual_characteristics triggered AND issuer-level
    breakage history would materially affect the severity decision.

REASONING REQUIREMENTS:
  1. State which signals are triggered and their confidence.
  2. Identify the single key ambiguity (if any) that enrichment would resolve.
  3. State explicitly whether that ambiguity would change the severity verdict.
  4. Return verdict and, if "enrich", the exact tool name and a rationale ≤ 200 characters.

Reply only via the required tool.\
"""

_HUMAN = """\
Assess sufficiency for scoring deal {deal_id} ({issuer_name}):

Stage: {stage}, days_in_stage: {days_in_stage}
ROFR: {days_to_rofr} days remaining
Risk factors: {risk_factors}

Risk signals ({triggered_count}/{total_count} triggered):
{signal_text}

Enrichment context already fetched ({enrichment_count} round(s)):
{context_text}

Are current signals sufficient to confidently score severity? \
If not, which tool should be called next?\
"""

SUFFICIENCY_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", _HUMAN),
])

SUFFICIENCY_SYSTEM = _SYSTEM
SUFFICIENCY_OUTPUT = SufficiencyDecision


def build_sufficiency_prompt(
    snapshot: DealSnapshot,
    signals: list[RiskSignal],
    enrichment_count: int,
    enrichment_context: dict,
) -> dict:
    """Return template_vars dict for use with SUFFICIENCY_TEMPLATE."""
    triggered = [s for s in signals if s.triggered]
    signal_text = "\n".join(
        f"  {s.dimension.value}: triggered={s.triggered}, evidence={s.evidence[:100]}"
        for s in signals
    )
    context_text = (
        "\n".join(f"  [{tool}]: {str(data)[:200]}" for tool, data in enrichment_context.items())
        if enrichment_context
        else "  (none yet)"
    )
    return {
        "deal_id": snapshot.deal_id,
        "issuer_name": snapshot.issuer_name,
        "stage": snapshot.stage.value,
        "days_in_stage": snapshot.days_in_stage,
        "days_to_rofr": snapshot.days_to_rofr,
        "risk_factors": snapshot.risk_factors,
        "triggered_count": len(triggered),
        "total_count": len(signals),
        "signal_text": signal_text,
        "enrichment_count": enrichment_count,
        "context_text": context_text,
    }
