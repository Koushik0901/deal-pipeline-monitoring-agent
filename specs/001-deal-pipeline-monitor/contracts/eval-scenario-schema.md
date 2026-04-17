# Golden Scenario YAML Contract (Tier 1)

One file per scenario under `src/hiive_monitor/eval/scenarios/`. Loaded by the Tier 1 runner, which resets `domain.db`, applies `setup`, sets `SimulatedClock`, invokes one Monitor tick, and evaluates `assertions`.

## Top-level shape

```yaml
id: <string, unique, filename-safe>
category: detection | prioritization | intervention_quality
description: <human-readable, 1–3 sentences>

setup:
  now: <ISO8601>                  # SimulatedClock starting value
  issuers: [<Issuer>]             # optional extras beyond seed
  parties: [<Party>]              # optional extras beyond seed
  deal: <Deal>                    # required for single-deal scenarios
  deals: [<Deal>]                 # used for prioritization scenarios
  events: [<Event>]               # seeded history

assertions:
  risk_signals: [<RiskSignalAssertion>]   # optional
  severity: <SeverityAssertion>           # optional
  intervention: <InterventionAssertion>   # optional
  brief_rank: <BriefRankAssertion>        # optional; for prioritization
  no_intervention: <bool>                 # if true, asserts no intervention was drafted
```

Exactly one of `setup.deal` or `setup.deals` MUST be present.

## Setup sub-schemas

### `Issuer`
```yaml
issuer_id: <string>
typical_response_days: <int>
rofr_window_days: <int>
multi_layer_rofr: <bool>
```

### `Party`
```yaml
party_id: <string>
party_type: buyer | seller
is_first_time: <bool, default false>
prior_breakage_count: <int, default 0>
typical_response_days: <int>
```

### `Deal`
```yaml
deal_id: <string>
issuer_id: <string>             # must match an existing issuer
buyer_id: <string>
seller_id: <string>
size_usd: <int>
stage: bid_accepted | docs_pending | issuer_notified | rofr_pending | rofr_cleared | signing | funding | settled | broken
stage_entered_at: <ISO8601>
rofr_deadline: <ISO8601 | null>
responsible_party: buyer | seller | issuer | hiive_ts
blockers: [<Blocker>]
risk_factors:
  is_first_time_buyer: <bool>
  prior_breakage_count: <int>
  # freeform additions allowed
```

### `Blocker`
```yaml
kind: missing_doc | pending_signature | awaiting_response | other
description: <string>
since: <ISO8601>
```

### `Event`
```yaml
deal_id: <string>                # defaults to setup.deal.deal_id
event_type: stage_transition | doc_received | doc_requested | comm_outbound | comm_inbound
occurred_at: <ISO8601>
summary: <string>
payload: <freeform mapping, optional>
```

## Assertion sub-schemas

### `RiskSignalAssertion`
```yaml
dimension: <RiskDimension>
triggered: <bool>
evidence_contains: [<string>]    # optional substring hints (all must match)
min_confidence: <float>          # optional
```
The runner locates the emitted `RiskSignal` for `dimension` and compares.

### `SeverityAssertion`
```yaml
equals: informational | watch | act | escalate     # exact match
# OR
one_of: [<Severity>, ...]                           # any of
```

### `InterventionAssertion`
```yaml
type: outbound_nudge | internal_escalation | brief_entry
recipient_type: buyer | seller | issuer            # if applicable
body_must_contain: [<string|regex>]                # all must match
body_must_not_contain: [<string|regex>]            # none may match
referenced_deadline_equals: <ISO8601>              # optional
```
Regex vs. substring: strings prefixed `re:` are treated as regex.

### `BriefRankAssertion`
```yaml
max: <int, 1–7>                   # deal must appear at rank ≤ max
# OR
equals: <int, 1–7>                # deal must appear at exact rank
deal_id: <string>                 # defaults to setup.deal.deal_id
```

### `SeverityAfterEnrichmentAssertion`

Ordering for comparison: `informational < watch < act < escalate`.

```yaml
severity_after_enrichment_lte: <informational | watch | act | escalate>
# Asserts that the final severity (after enrichment rounds complete)
# is less than or equal to the named level. Used to verify downgrade
# scenarios where enrichment reveals that a flagged signal has an
# innocent explanation.
# Example: verify that a comm_silence signal flagged as `act` downgrades
# to `watch` after fetch_communication_content reveals the silence was
# pre-explained in a prior message.

severity_after_enrichment_gte: <informational | watch | act | escalate>
# Asserts that the final severity (after enrichment rounds complete)
# is greater than or equal to the named level. Used to verify upgrade
# scenarios where enrichment reveals compounding risk.
# Example: verify that three watch-level signals upgrade to `act` after
# the agent reasons about their combination.
```

## Runner semantics

1. Open `domain.db`, truncate business tables (issuers/parties re-seeded from YAML if `setup.issuers`/`setup.parties` present; otherwise reused from base seed).
2. Insert `setup.deal(s)` and `setup.events`.
3. Set `SimulatedClock` to `setup.now`.
4. Invoke one Monitor tick end-to-end.
5. For each assertion block present, evaluate and collect pass/fail with a textual reason.
6. Write row to `out/scorecard.json`:
   ```json
   {"scenario_id": "...", "category": "...", "pass": false,
    "failures": [{"kind":"intervention.body_must_contain","expected":"April 23","got":"..."}]}
   ```
7. After all scenarios: compute aggregate precision/recall per `RiskDimension` and the 4×4 severity confusion matrix. Print summary.

## Category conventions

- **`detection` (×8)**: usually single-deal. Assertions focus on `risk_signals` + `severity`. Include both positive and negative scenarios (at least 2 where expected severity = `informational`).
- **`prioritization` (×4)**: multi-deal via `setup.deals`. Assertions focus on `brief_rank` across deals.
- **`intervention_quality` (×3)**: single-deal. Assertions focus on `intervention.body_must_contain` + `body_must_not_contain`. Checks deadline references, issuer-name presence, absence of placeholder language (e.g., `TODO`, `[NAME]`, "as soon as possible").

## Authoring rules

- Use **real Hiive-listed issuers** (SpaceX, Stripe, Anthropic, Perplexity, Cerebras, Groq, Databricks, Canva, Rippling, Ramp) — no fictitious companies, per Constitution IX.
- Date arithmetic is relative to `setup.now`; compute offsets by hand and write them ISO8601 in the YAML. The runner does not interpret relative expressions like `-14d`.
- Scenarios must be deterministic: no random elements; every assertion answer derivable from the setup.
- Keep each scenario under ~80 lines for reviewability.

## Stretch extension point

Tier 2 LLM-as-judge (stretch) reads the same YAML. When `category: intervention_quality` is scored at Tier 2, an additional `judge_rubric:` block is consulted:
```yaml
judge_rubric:
  tone_appropriate_for_recipient: <weight>
  factually_grounded: <weight>
  correct_deadline_referenced: <weight>
  urgency_level_appropriate: <weight>
```
MVP runner ignores `judge_rubric`; Tier 2 runner consumes it. Adding Tier 2 does not require changes to existing fixtures — only authoring new fixtures that opt in.
