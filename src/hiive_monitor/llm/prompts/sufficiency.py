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
Your job is a single binary decision: is the current signal set SUFFICIENT to score severity \
with confidence, or would one specific enrichment tool materially change that score?

ABSOLUTE RULE: enrichment_count ≥ 2 → return sufficient=true immediately. No exceptions. \
This cap is enforced in code — requesting further enrichment at this point has no effect.

MANDATORY ENRICH (sufficient=false) — override PROCEED rules when enrichment_count = 0:
  • communication_silence triggered AND fetch_communication_content NOT yet in enrichment_context:
    Always fetch — a legal hold, known deferral, or acknowledgment may reduce severity regardless
    of how confident the silence signal appears. Severity cannot be finalized without validating
    whether an explanation exists.
  • stage_aging AND unusual_characteristics (prior_breakage_count ≥ 1) both triggered AND
    fetch_prior_observations NOT yet in enrichment_context:
    Always fetch — distinguishing a new pattern from a recurring one is essential for accurate
    severity and intervention quality.

PROCEED (sufficient=true) when ANY of the following apply (after mandatory enrich is satisfied):
  • All signals triggered=false → verdict is clearly informational.
  • Deadline ≤ 2 days with any triggered signal → verdict is clearly escalate; enrichment won't change it.
  • You already fetched the enrichment tool that would have addressed the ambiguity.

ENRICH (sufficient=false) when ALL four conditions hold (beyond mandatory enrich above):
  ① enrichment_count < 2
  ② At least one signal is triggered with ambiguous cause
  ③ The tool you name would directly resolve that specific ambiguity
  ④ The result could plausibly change the verdict by at least one severity level

TOOL SELECTION — pick the single most targeted tool:
  fetch_communication_content:
    When to use: communication_silence triggered AND you need the message bodies to check
    for an explicit explanation (legal hold, holiday, stated delay, known deferral).
    When NOT to use: if silence is already unambiguous (>21d, no deadline, no explanation possible).

  fetch_prior_observations:
    When to use: a compounding pattern is visible (recurring stall, escalating silence) AND
    knowing if it's chronic would change whether you escalate vs. watch.
    When NOT to use: first occurrence with no prior signal history visible.

  fetch_issuer_history:
    When to use: unusual_characteristics triggered AND prior issuer breakage count would
    shift severity from watch to act.
    When NOT to use: unusual_characteristics is the only signal and breakage history is absent.

  fetch_intervention_outcomes:
    When to use: communication_silence OR counterparty_nonresponsiveness triggered AND knowing
    historical outbound-nudge response rate for this issuer would change urgency. High past rate
    (≥70%) → watch may suffice; low rate (<30%) → supports escalating to act.
    When NOT to use: deadline ≤3d (escalate directly) or silence is clearly explained.

ANTI-LOOP RULE: Never request the same tool twice. If fetch_communication_content is already
in enrichment_context, do not request it again — proceed.

CHAIN OF THOUGHT — work through these steps:
  1. List triggered signals and their confidence scores.
  2. Is the dominant ambiguity resolvable by one of the four tools? Name the ambiguity precisely.
  3. Would resolving it change the severity verdict? State explicitly yes or no.
  4. If yes and conditions ①–④ hold → enrich. Otherwise → proceed.

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
