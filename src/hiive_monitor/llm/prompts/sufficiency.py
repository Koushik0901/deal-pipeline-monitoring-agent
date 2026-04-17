"""
N3 assess_sufficiency prompt — agentic loop control for the Deal Investigator.

This is what makes the Investigator a genuine agent: it decides whether current
risk signals are sufficient to score severity, or whether enrichment context
(communication history, prior observations, issuer history) would materially change
the assessment. If not sufficient, it names the next tool to call.
"""

from __future__ import annotations

from hiive_monitor.models.risk import RiskSignal, SufficiencyDecision
from hiive_monitor.models.snapshot import DealSnapshot

_SYSTEM = """\
You are a senior analyst for Hiive Transaction Services assessing whether you have enough \
context to confidently score this deal's risk severity.

You have three enrichment tools available:
  - fetch_communication_content: returns last 10 comm events (body text). Use when a silence \
    signal triggered and you want to know WHY (legal hold? holiday? explicit delay?).
  - fetch_prior_observations: returns last 5 agent observations. Use when you see compounding \
    signals and want to know if this is a new pattern or recurring.
  - fetch_issuer_history: returns prior settled/broken deals for this issuer. Use when \
    unusual_characteristics triggered and issuer-level breakage history would be material.

Be conservative: if the risk signals are clear-cut (e.g., ROFR in 1 day, no ambiguity), \
call sufficient=True immediately. Only request enrichment when the additional context \
would actually change the severity decision.

Max 2 enrichment rounds total per deal per tick. Reply only via the required tool."""


def build_sufficiency_prompt(
    snapshot: DealSnapshot,
    signals: list[RiskSignal],
    enrichment_count: int,
    enrichment_context: dict,
) -> str:
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
    return f"""\
Assess sufficiency for scoring deal {snapshot.deal_id} ({snapshot.issuer_name}):

Stage: {snapshot.stage.value}, days_in_stage: {snapshot.days_in_stage}
ROFR: {snapshot.days_to_rofr} days remaining
Risk factors: {snapshot.risk_factors}

Risk signals ({len(triggered)}/{len(signals)} triggered):
{signal_text}

Enrichment context already fetched ({enrichment_count} round(s)):
{context_text}

Are current signals sufficient to confidently score severity? \
If not, which tool should be called next?"""


SUFFICIENCY_SYSTEM = _SYSTEM
SUFFICIENCY_OUTPUT = SufficiencyDecision
