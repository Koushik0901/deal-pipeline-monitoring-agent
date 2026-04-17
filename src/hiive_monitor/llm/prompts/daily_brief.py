"""Daily brief composition prompt (compose_daily_brief Sonnet call)."""

from __future__ import annotations

from hiive_monitor.models.brief import DailyBrief

_SYSTEM = """\
You are a senior analyst assistant for Hiive Transaction Services. Compose the daily brief \
for the analyst: a ranked list of up to 7 deals requiring attention today.

Ranking rules (strict order):
  1. escalate before act before watch
  2. Within same severity: ROFR deadline proximity ascending (soonest first)
  3. Ties: stage aging ratio descending

Each item needs: deal_id, rank, severity, one_line_summary (≤160 chars), reasoning (≤600 chars). \
Include intervention_id if a draft exists. Be specific — no generic statements. \
Reference issuer name, stage, and key signal in each summary. Reply only via the required tool."""


def build_daily_brief_prompt(
    tick_id: str,
    generated_at: str,
    observations: list[dict],
    open_interventions: list[dict],
) -> str:
    obs_text = "\n".join(
        f"  - deal_id={o['deal_id']}, severity={o['severity']}, "
        f"observation_id={o['observation_id']}, "
        f"reasoning_summary={str(o.get('reasoning', {}).get('severity_rationale', ''))[:120]}"
        for o in observations
    )
    iv_text = "\n".join(
        f"  - intervention_id={iv['intervention_id']}, deal_id={iv['deal_id']}, "
        f"type={iv['intervention_type']}, severity={iv.get('severity', 'unknown')}"
        for iv in open_interventions
    )
    return f"""\
Compose the daily brief for tick {tick_id} at {generated_at}.

This-tick observations requiring attention:
{obs_text or '  (none)'}

Open pending interventions (from prior ticks too):
{iv_text or '  (none)'}

Produce a ranked list of up to 7 items for the analyst's attention today."""


DAILY_BRIEF_SYSTEM = _SYSTEM
DAILY_BRIEF_OUTPUT = DailyBrief
