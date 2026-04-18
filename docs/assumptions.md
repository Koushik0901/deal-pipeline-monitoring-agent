# Engineered Assumptions

This document records assumptions made in lieu of real Hiive
operational data. Every assumption here is a place where a
production deployment would substitute real measured values.

## Stage dwell-time baselines

Used by the stage aging risk dimension to determine when a deal
is aging abnormally. These are synthetic values calibrated to
produce realistic demo behavior — not derived from Hiive
transaction data.

| Stage | Baseline (days) | Notes |
|---|---|---|
| bid_accepted | 1 | Typically transitions same day |
| docs_pending | 3 | Buyer and seller doc collection |
| issuer_notified | 2 | Notification delivery |
| rofr_pending | 20 | Company deliberation period |
| rofr_cleared | 2 | Post-waiver admin |
| signing | 4 | Signature package coordination |
| funding | 3 | Wire transfer and confirmation |

## Issuer metadata

Response speed, ROFR window, and multi-layer ROFR flags are
synthetic. Production values would come from Hiive's internal
issuer relationship database.

| Issuer | Response speed | ROFR window (days) | Multi-layer |
|---|---|---|---|
| SpaceX | slow | 30 | true |
| Stripe | normal | 30 | false |
| Anthropic | fast | 14 | false |
| Perplexity | fast | 14 | false |
| Cerebras | normal | 30 | false |
| Groq | normal | 30 | false |
| Databricks | slow | 45 | true |
| Canva | normal | 30 | false |
| Rippling | fast | 14 | false |
| Ramp | fast | 14 | false |

## Communication silence thresholds

Used by the communication silence risk dimension. Thresholds are applied by the LLM evaluator
in `llm/prompts/risk_all_dimensions.py` (not as code-level checks), using a two-band rule:

| Stage category | Silence threshold | Trigger condition |
|---|---|---|
| Late stages (rofr_cleared, signing, funding) | 7 days | strictly **> 7** days |
| All other live stages | 14 days | strictly **> 14** days |

**Important:** The threshold is strictly `>` (not `≥`). A deal with exactly 7 or 14 days of
silence does NOT trigger. Set fixture `days_ago` values to threshold + 1 to ensure triggering.

**Direction modifier:** If the last communication was inbound (counterparty waiting on Hiive),
urgency increases — the late-stage threshold drops to 5 days.

In production, these thresholds would be calibrated per issuer and stage from historical
response-time distributions in Hiive's communication database.

## Attention suppression defaults

- `SUPPRESSION_MULTIPLIER`: `0.2` (reduces attention score to 20%
  of its raw value for suppressed deals)
- `SUPPRESSION_TICKS`: `3` (suppression lasts 3 ticks after
  analyst action)

These values produce a Daily Brief that stabilizes after
analyst intervention. Lower multiplier = more aggressive
suppression. Higher tick count = longer cooldown.

## Synthetic communication voice

Outbound communications in the synthetic deal pipeline are
written to approximate Transaction Services analyst outreach
at a regulated financial services firm. Key calibration
decisions:

- Professional but direct tone (not formal letter style)
- References specific deal facts (issuer, stage, deadline)
- Does not use legal disclaimers (handled at send time)
- Recipient type affects formality:
  - Issuer counsel: more formal, references legal agreements
  - Buyer/seller: direct and practical
  - Internal: casual, action-oriented

In production, voice calibration would be derived from
historical Transaction Services email samples.

## Eval harness assumptions

### Argument Correctness (metric)
The eval runner measures Tool Correctness (was the right enrichment tool
called?) but does not independently verify argument correctness (were the
right arguments passed?). The assumption is that LangGraph state propagates
`deal_id` and `issuer_id` deterministically from the fixture seed, so if the
correct tool was called and produced non-empty output, the arguments were
correct. A tool called with the wrong deal_id would return empty data, causing
the scenario's enrichment assertions to fail — so Tool Correctness implicitly
covers Argument Correctness for all cases that matter.

### Factual Grounding (Tier 1.5, string-match)
Share counts are checked in both plain (5000) and comma-formatted (5,000)
variants. Prices are checked as 185.50 and $185.50. This covers the formats
observed in LLM-drafted interventions but may miss unusual formatting (e.g.,
$185.5 without trailing zero). Any miss is surfaced in the scorecard detail.
The Tier 2 LLM-as-judge Factual Grounding metric provides a more semantic
check.

### deepeval judge model
Tier 2 G-Eval uses `OpenRouterJudge` (EVAL_JUDGE_MODEL, defaulting to
`qwen/qwen3-plus`) via `deepeval_adapter.py`. Scores are 0–1 and are not
calibrated against human raters — they represent the judge model's rubric
interpretation. A threshold of 0.7 is used as a passing bar; this is a
starting point and should be adjusted after reviewing a sample of scored
interventions.

### Langfuse local deployment
The `docker-compose.langfuse.yml` uses hardcoded local credentials
(`NEXTAUTH_SECRET`, `SALT`). These are placeholders suitable for local
development only. For any shared or persistent deployment, these must be
replaced with strong random values.
