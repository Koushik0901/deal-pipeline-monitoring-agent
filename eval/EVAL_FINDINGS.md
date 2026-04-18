# Eval Run Findings — 2026-04-17

First full eval run: 39 scenarios, Tier 1 + Tier 2 (LLM-as-judge via deepeval + Langfuse).

---

## Tier 1 Results (deterministic)

**32 / 39 passed (82%)**

| Metric | Score |
|--------|-------|
| Task Completion | 31/39 (79%) |
| Severity Accuracy | 26/31 (83%) |
| Tool Correctness | 0/2 (0%) |
| Factual Grounding | 0/29 (0%) — see issues below |

### Dimension F1

| Dimension | F1 | Notes |
|-----------|-----|-------|
| deadline_proximity | 1.00 | Perfect |
| missing_prerequisites | 0.86 | |
| stage_aging | 0.82 | |
| communication_silence | 0.82 | |
| unusual_characteristics | 0.74 | 4 false negatives — see #7, #8 below |
| counterparty_nonresponsiveness | n/a | No scenarios cover this |

### Severity Confusion Matrix (31 scenarios with ground truth)

| expected \ actual | informational | watch | act | escalate |
|-------------------|---------------|-------|-----|----------|
| **informational** | 1 | 0 | 1 | 0 |
| **watch** | 0 | 1 | 2 | 0 |
| **act** | 1 | 0 | 11 | 1 |
| **escalate** | 0 | 0 | 0 | 13 |

Key observations: `escalate` is perfect (13/13). `watch` is the weak band — 2/3 over-triggered to `act`.

---

## Tier 2 Results (LLM-as-judge)

| Metric | Mean | Notes |
|--------|------|-------|
| tool_correctness | 0.95 | |
| argument_correctness | 0.95 | |
| answer_correctness | 0.64 | Artificially low — see issue #2 |
| task_completion | 0.41 | Artificially low — see issues #3, #4 |
| hallucination | 0.00 | **PERFECT** (inverted metric — 0 = no hallucination) |

---

## Issues Found

### Bugs in the eval harness (fixed in this session)

**#1 — Hallucination metric inverted in deep scorecard** *(fixed)*
- File: `src/hiive_monitor/eval/deepeval_runner.py:213`
- `HallucinationMetric` scores 0.0 = no hallucination (best). The priority-failures section
  was treating it like `answer_correctness` (higher = better), flagging all 39 perfect scores
  as failures.
- Fix: inverted the check — only flag `hallucination >= 0.5` as a failure.

**#2 — Dimension attribution gap → `answer_correctness` artificially low** *(fixed)*
- File: `src/hiive_monitor/eval/deepeval_cases.py:83`
- `_format_actual_output()` built the triggered-dimensions list from `assertion_results` only
  for assertions with a `dimension:` prefix that passed. Scenarios without explicit dimension
  assertions (most IQ and regression fixtures) showed "Risk dimensions triggered: none" to
  the judge even when the agent triggered several. Judge scored 0.5 (severity right, no dims).
- Fix: replaced with `result.get("actual_triggered", [])` — always has the real data.

**#3 — `task_completion` rubric requires invisible step 1 (attention score)** *(fixed)*
- File: `src/hiive_monitor/eval/deepeval_metrics.py:61`
- Task description required evidence that "Step 1 — compute an attention score" was performed.
  The attention score is computed in the Monitor's `screen_with_SLM` node, not visible at the
  Investigator output level. Judge always docked for this.
- Fix: removed step 1 from the rubric; renumbered steps 2–5 → 1–4.

**#4 — `task_completion` rubric: observation persistence not surfaced** *(fixed)*
- File: `src/hiive_monitor/eval/deepeval_cases.py:77` and `deepeval_metrics.py:78`
- Judge penalized for "no observation was persisted" because the actual_output didn't say so.
- Fix: added `Observation persisted: yes/no` to `_format_actual_output()` using
  `result.get("task_completed")`; updated task description to reference this field.

**#5 — Factual grounding 0/29: runs on wrong intervention types** *(fixed)*
- File: `src/hiive_monitor/eval/runner.py:352`
- `_check_factual_grounding()` checked ALL intervention types. The internal escalation and
  brief_entry prompts do not include shares/price (correct — they don't need them). So all
  escalate-severity scenarios failed grounding spuriously.
- Fix: filter to `intervention_type == "outbound_nudge"` before checking. Escalate/watch
  scenarios now return None (not applicable) instead of FAIL.

**#6 — `edge_suppression_active` fails: tick-based suppression blind in isolated eval DB** *(fixed)*
- File: `eval/fixtures/edge_suppression_active.yaml`
- `dao.get_suppressed_deal_ids()` requires completed ticks to compute the cutoff timestamp.
  In each scenario's isolated DB there are no prior completed ticks, so the function returns
  an empty set immediately and suppression never fires — even with a `comm_sent_agent_recommended`
  event present.
- Fix: added 3 `prior_ticks` entries (days_ago=3,2,1) to the fixture setup block, giving the
  suppression query the tick history it needs.

---

### Bugs in agent behavior (not yet fixed — next sprint)

**#7 — `unusual_characteristics` misses deal size + first-time buyer combination**
- Scenario: `edge_first_time_large_deal`
- The `unusual_characteristics` prompt scores `is_first_time_buyer=true` at 1.0 pt, but the
  trigger threshold is 1.5 pts (one moderate flag alone is not enough). The fixture has 50,000
  shares at $185/share ($9.25M notional — ~5× typical). Deal size is NOT a scored factor in
  the current dimension definition. Agent correctly follows the prompt and returns triggered=false,
  but the fixture expects triggered=true.
- Proposed fix: add a deal-size factor to dimension 5 (e.g., notional > $5M = 1pt), so that
  large-deal + first-time buyer = 2pt → triggered.

**#8 — `unusual_characteristics` misses multi-layer ROFR alone**
- Scenario: `edge_multi_layer_rofr_stall`
- `multi_layer_rofr=true` scores 1.0 pt (one moderate flag). Same threshold issue: 1pt < 1.5pt
  → not triggered. Multi-layer ROFR is a genuinely unusual structural characteristic that
  justifies attention on its own.
- Proposed fix: raise `multi_layer_rofr` from 1pt to 1.5pt (strong flag), making it self-triggering.

**#9 — `adversarial_conflicting_comm`: enrichment not called, severity over-triggered**
- Scenario: `adversarial_conflicting_comm`
- The fixture has a `comm_inbound` event with summary "Palantir legal citing legal hold — cannot
  respond for ~2 weeks". This summary IS passed to the risk evaluation prompt in `recent_comm_events`.
  The communication_silence dimension rule says to reduce confidence by 0.20 for known explanations,
  but the agent is not doing so sufficiently — still calling ACT instead of WATCH.
- Two sub-problems:
  a) Sufficiency: agent sees the legal hold in the summary and reasons "no enrichment needed" —
     skips `fetch_communication_content`, failing the `enrichment_tool_called` assertion.
  b) Severity: communication_silence fires at ≥0.85 confidence → ACT. Should be WATCH at reduced
     confidence after the explanation is acknowledged.
- Root tension: the fixture description assumes the legal hold is only visible via enrichment,
  but the event summary leaks it into the snapshot. Either the fixture summary needs to be opaque,
  or the risk prompt needs to require enrichment even when a summary mentions an explanation.

**#10 — `edge_enrichment_cap`: enrichment never fires despite three triggers**
- Scenario: `edge_enrichment_cap`
- 18-day comm silence + prior_breakage_count=2 + 18-day stage aging all present. Sufficiency
  assessment decides "verdict would be the same no matter what any tool returns" (all signals
  clearly escalate) and skips enrichment entirely. Fixture asserts `agent_triggers_enrichment: true`.
- Root cause: the sufficiency "proceed if verdict is unchangeable" shortcut fires too eagerly
  when multiple high-confidence signals are present, even when the fixture specifically wants to
  test the enrichment path.
- Proposed fix: narrow the shortcut — only treat verdict as unchangeable when an emergency
  trigger is certain (ROFR ≤ 2 days), not merely when 3+ dimensions are triggered.

**#11 — Factual grounding: outbound nudge LLM omits shares/price (secondary)**
- Even with the outbound nudge prompt's mandatory-content rule and verification gate, the LLM
  sometimes writes the body without quoting the exact share count and price. This is a prompt
  compliance issue — the verification gate is in the system message, which may be deprioritized.
- Proposed fix: add an inline reminder at the end of `_OUTBOUND_HUMAN` forcing the LLM to
  check verbatim inclusion before returning.

---

## Metrics after harness fixes (expected)

After fixes #1–#6 above, the next eval run should see:

| Metric | Before | Expected after |
|--------|--------|----------------|
| Hallucination priority failures | 39 false failures | 0 (all ≥ 0.00 = perfect) |
| answer_correctness mean | 0.64 | ~0.80+ (dims now visible to judge) |
| task_completion mean | 0.41 | ~0.65+ (observation persisted + step 1 removed) |
| Factual grounding | 0/29 (0%) | ~N/A for escalate; real signal for act scenarios |
| edge_suppression_active | FAIL | Expected PASS |
| Tier 1 pass rate | 32/39 (82%) | 33/39 (85%) with suppression fix |
