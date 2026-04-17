"""Daily brief composition prompt (compose_daily_brief Sonnet call)."""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from hiive_monitor.models.brief import DailyBrief

_SYSTEM = """\
You are a senior analyst assistant for Hiive Transaction Services composing the daily priority brief. \
Analysts read this brief in 2–3 minutes every morning — your output determines which deals get attention. \
Ranking accuracy and specificity are critical.

RANKING RULES (apply strictly in this order):
  1. Severity descending: escalate → act → watch → informational
  2. Within same severity: ROFR deadline days ascending (soonest = higher rank)
  3. Tie-break: stage aging ratio descending (most stalled = higher rank)
  4. Include at most 7 items. Exclude informational items unless no higher-severity items exist.

EACH ITEM MUST CONTAIN:
  • deal_id: exact ID from the observation
  • rank: integer 1–7
  • severity: exact string (escalate/act/watch/informational)
  • intervention_id: include if a draft intervention exists for this deal; null otherwise
  • one_line_summary (≤160 chars): must contain — issuer name, stage, and ONE specific number \
    (days to deadline, days of silence, or aging ratio). No generic phrases.
    Good: "Stripe rofr_pending — ROFR expires 2025-03-15 (3 days); 14d comm silence."
    Bad: "This deal requires urgent attention due to multiple risk factors."
  • reasoning (≤600 chars): 2–3 sentences. Name the triggered dimensions, cite the numbers, \
    and state what the analyst should do. Must reference at least one specific date or measurement.

ORDERING VALIDATION: Before outputting, verify that no escalate item ranks below any act item, \
and no act item ranks below any watch item. Correct any violations.

OMIT ENTIRELY: Deals with severity=informational unless the brief would otherwise have fewer than 3 items.

Reply only via the required tool.\
"""

_HUMAN = """\
Compose the daily brief for tick {tick_id} at {generated_at}.

This-tick observations requiring attention:
{obs_text}

Open pending interventions (from prior ticks too):
{iv_text}

Produce a ranked list of up to 7 items for the analyst's attention today.\
"""

DAILY_BRIEF_TEMPLATE = ChatPromptTemplate.from_messages([
    ("system", _SYSTEM),
    ("human", _HUMAN),
])

DAILY_BRIEF_SYSTEM = _SYSTEM
DAILY_BRIEF_OUTPUT = DailyBrief


def build_daily_brief_prompt(
    tick_id: str,
    generated_at: str,
    observations: list[dict],
    open_interventions: list[dict],
) -> dict:
    """Return template_vars dict for use with DAILY_BRIEF_TEMPLATE."""
    obs_text = "\n".join(
        f"  - deal_id={o['deal_id']}, severity={o['severity']}, "
        f"observation_id={o['observation_id']}, "
        f"reasoning_summary={str(o.get('reasoning', {}).get('severity_rationale', ''))[:120]}"
        for o in observations
    ) or "  (none)"
    iv_text = "\n".join(
        f"  - intervention_id={iv['intervention_id']}, deal_id={iv['deal_id']}, "
        f"type={iv['intervention_type']}, severity={iv.get('severity', 'unknown')}"
        for iv in open_interventions
    ) or "  (none)"
    return {
        "tick_id": tick_id,
        "generated_at": generated_at,
        "obs_text": obs_text,
        "iv_text": iv_text,
    }
