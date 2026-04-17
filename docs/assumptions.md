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

Used by the communication silence risk dimension.

| Stage | Silence threshold (days) | Rationale |
|---|---|---|
| docs_pending | 3 | Docs usually collected quickly |
| rofr_pending | 5 | Issuer deliberation takes longer |
| signing | 3 | Signatures should move fast |
| funding | 2 | Wire timing is critical |

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
