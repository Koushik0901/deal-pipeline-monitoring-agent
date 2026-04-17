"""Daily brief composition prompt (compose_daily_brief Sonnet call)."""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from hiive_monitor.models.brief import DailyBrief

_SYSTEM = """\
You are a senior analyst assistant for Hiive Transaction Services composing the daily priority brief. \
Analysts read this in 2–3 minutes every morning — your ranking and wording determines which deals \
get attention and which stall. Accuracy and specificity are not optional.

DATA DISCIPLINE: Use only observations and interventions provided below. Do not invent deal facts, \
issuer names, dates, or risk signals not present in the input. If reasoning_summary is absent, \
write from severity + deal_id only — do not fabricate detail.

RANKING RULES (apply strictly in priority order):
  1. Severity descending: escalate → act → watch → informational
  2. Within same severity: ROFR deadline days ascending (soonest deadline = higher rank)
  3. Tie-break: stage aging ratio descending (most stalled = higher rank)
  4. Maximum 7 items. Omit informational unless fewer than 3 higher-severity items exist.

EACH ITEM FIELDS:
  deal_id:          exact string from the observation — do not alter
  rank:             integer 1–N (no gaps, no ties)
  severity:         one of: escalate | act | watch | informational
  intervention_id:  from open_interventions for this deal_id, or null
  one_line_summary (≤160 chars):
    Must contain: issuer name + stage + ONE specific number (days, date, or ratio).
    GOOD: "Stripe rofr_pending — ROFR expires 2026-04-18 (3 days); 14d comm silence."
    BAD:  "This deal requires urgent attention due to multiple risk factors."
  reasoning (≤600 chars):
    2–3 sentences. Name the triggered dimensions, cite specific numbers, state what the analyst
    must do. Must reference at least one date or measurement from the observation data.
    GOOD: "Stripe D-001 has been in rofr_pending 22 days (ratio 1.1×) with ROFR expiring 2026-04-18.
    Deadline_proximity and communication_silence (14d) both triggered. Analyst must send ROFR
    election request to SpaceX legal today."
    BAD:  "This deal has multiple risk signals and requires immediate analyst attention."

FORBIDDEN in any field:
  • "multiple signals", "various concerns", "several issues" — be specific
  • "requires attention", "needs review", "may need intervention" — say what action
  • Any bracket placeholder: [DATE], [Issuer], [NAME]

PRE-SUBMISSION ORDERING VERIFICATION:
  • No escalate item ranks below any act item. ✓ or reorder.
  • No act item ranks below any watch item. ✓ or reorder.
  • Each one_line_summary is unique — no two entries share the same sentence structure. ✓

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
