# Deep Evaluation Framework — Spec

**Status**: Planned (Phase 12)
**Depends on**: Phase 9 eval harness (runner + fixture corpus), Phase 3+4 agent (end-to-end working)
**Makefile target**: `make eval-deep`
**Reference tasks**: TEV01–TEV13 in `tasks.md`

---

## Overview

The evaluation framework has two layers that share the same fixture corpus:

| Layer | What it checks | Speed | When to run |
|---|---|---|---|
| **Layer 1** — deterministic | Schema assertions, severity bounds, dimension presence, tool-call presence | ~2 min | Every CI push (`make eval`) |
| **Layer 2** — LLM-as-judge | Calibration quality, factual grounding, reasoning depth | ~10–20 min + judge LLM cost | Pre-release, after model swap (`make eval-deep`) |
| **Layer 3** — observability | Persistent traces, per-run cost + latency, regression detection across runs | Async | Always-on during `eval-deep` |

---

## Fixture Schema Extension — `ground_truth` block

Every fixture gains a `ground_truth` block alongside the existing `assertions` block. `assertions` controls Layer 1 pass/fail. `ground_truth` controls Layer 1 precision/recall computation and feeds Layer 2 test case construction.

```yaml
# Existing (unchanged)
assertions:
  severity: act
  dimensions_triggered:
    - deadline_proximity
    - communication_silence
  intervention_drafted: true

# New (TEV01 — backfilled on all fixtures)
ground_truth:
  severity: act                          # canonical label for confusion matrix
  dimensions_triggered:
    - deadline_proximity
    - communication_silence
  dimensions_not_triggered:
    - stage_aging
    - missing_prerequisites
    - unusual_characteristics
  expected_tools: []                     # empty = no enrichment expected
  # expected_tools: [fetch_communication_content]  # when enrichment should fire
```

`ground_truth.severity` and `ground_truth.dimensions_*` are the single source of truth for all metrics. `assertions` may be more lenient (e.g. `severity_lte: act`) while `ground_truth.severity` gives the exact canonical label the agent should produce.

---

## Layer 1 — Extended Scorecard Metrics

### Per-Dimension Precision/Recall Table

For each `RiskDimension` (`stage_aging`, `deadline_proximity`, `communication_silence`, `missing_prerequisites`, `unusual_characteristics`):

| | Actually Triggered | Not Triggered |
|---|---|---|
| **Ground Truth: Triggered** | TP | FN |
| **Ground Truth: Not Triggered** | FP | TN |

Emitted as Markdown table in scorecard:

```
## Per-Dimension Precision / Recall

| Dimension              | P     | R     | F1    | TP | FP | FN | TN |
|------------------------|-------|-------|-------|----|----|----|----|
| deadline_proximity     | 1.00  | 0.92  | 0.96  |  6 |  0 |  1 |  5 |
| communication_silence  | 0.88  | 1.00  | 0.94  |  7 |  1 |  0 |  4 |
| stage_aging            | ...   | ...   | ...   |  . |  . |  . |  . |
| missing_prerequisites  | ...   | ...   | ...   |  . |  . |  . |  . |
| unusual_characteristics| ...   | ...   | ...   |  . |  . |  . |  . |
```

### 4×4 Severity Confusion Matrix

Rows = expected severity (from `ground_truth.severity`), columns = actual severity (from observation).

```
## Severity Confusion Matrix

              | informational | watch | act | escalate |
expected →    |               |       |     |          |
informational |      3        |   1   |  0  |    0     |
watch         |      0        |   5   |  1  |    0     |
act           |      0        |   1   |  6  |    1     |
escalate      |      0        |   0   |  0  |    4     |
```

Patterns to flag:
- Off-diagonal toward lower severity = systematic under-escalation
- Off-diagonal toward higher severity = systematic over-escalation
- Any `escalate → informational` (or vice versa) = critical failure

---

## Layer 2 — LLM-as-Judge Metrics (deepeval)

### Judge Model Configuration

deepeval uses a `DeepEvalBaseLLM` adapter (`eval/deepeval_adapter.py`) that wraps `ChatOpenRouter`. The judge model is configured independently from the agent models to avoid circular evaluation:

```
EVAL_JUDGE_MODEL=qwen/qwen3-plus   # default — swap via .env
```

The judge must be a capable reasoning model. The same model-agnostic constraint applies: any model capable of structured instruction following can be the judge.

### LLMTestCase Construction

Each fixture + `run_scenario()` result is mapped to a deepeval `LLMTestCase` via `eval/deepeval_cases.py::build_test_case()`:

```python
LLMTestCase(
    input=format_deal_snapshot(scenario),          # deal facts: stage, dwell, events, parties
    actual_output=format_observation(run_result),  # severity + dimensions + intervention body
    expected_output=format_ground_truth(scenario), # canonical severity + dimensions
    context=extract_snapshot_facts(scenario),       # grounding facts for HallucinationMetric
    tools_called=[                                  # reconstructed from enrichment_chain
        ToolCall(name="fetch_communication_content",
                 input_parameters={"deal_id": "D-ADV-002"})
    ],
)
```

### Metric Definitions

#### 1. Task Completion (`TaskCompletionMetric`)

**Task description** (passed to judge):
> "Given a live deal from the Hiive secondary market pipeline, the monitoring agent must: (1) screen the deal and compute an attention score, (2) evaluate all five risk dimensions if the attention score exceeds the threshold, (3) assess whether the available context is sufficient or enrichment is needed, (4) score the severity as one of {informational, watch, act, escalate}, and (5) draft an outbound nudge or internal escalation when severity is act or escalate. The task is complete when a persisted observation exists with a severity score and, if severity ≥ act, a drafted intervention with deal-specific content."

**Passes when**: The agent completed all five steps in order. Fails if observation is missing, severity is null, or intervention is absent when it should be present.

#### 2. Tool Correctness (`ToolCorrectnessMetric`)

**What it checks**: Did the agent call the enrichment tools that `ground_truth.expected_tools` says it should? And only those tools?

| Ground truth | Agent called | Result |
|---|---|---|
| `[]` | `[]` | Pass — correct restraint |
| `[fetch_communication_content]` | `[fetch_communication_content]` | Pass |
| `[fetch_communication_content]` | `[]` | Fail — missed required enrichment |
| `[]` | `[fetch_prior_observations]` | Fail — spurious tool call |
| `[fetch_communication_content]` | `[fetch_prior_observations]` | Fail — wrong tool |

#### 3. Argument Correctness (`ArgumentCorrectnessMetric`)

**What it checks**: When a tool was called, did it receive the correct identifiers?

- `fetch_communication_content(deal_id)` → must match `scenario.setup.deal.deal_id`
- `fetch_prior_observations(deal_id)` → must match `scenario.setup.deal.deal_id`
- `fetch_issuer_history(issuer_id)` → must match `scenario.setup.deal.issuer_id`

Fails if a tool was called with a hallucinated or wrong identifier.

#### 4. Misinformation / Factual Grounding (`HallucinationMetric`)

**Context** (grounding facts extracted from fixture):
```
- Issuer: {issuer_name}
- Deal ID: {deal_id}
- Stage: {stage}
- Shares: {shares}
- Price per share: {price_per_share}
- ROFR deadline: {rofr_deadline_date or "none"}
- Buyer: {buyer_display_name}
- Seller: {seller_display_name}
```

**Actual output**: The full `draft_body` of the intervention (outbound nudge or escalation).

**Fails when**: The intervention body contains names, dates, share counts, or prices that do not appear in the context. Common hallucination patterns:
- Wrong issuer name or ticker
- Fabricated deadline date that differs from the actual ROFR deadline
- Wrong share count or price
- References to events not in the event history

This metric is the primary guard against factual errors in analyst-facing output. A score below 0.5 on this metric is a `:warning: priority failure` regardless of other metrics passing.

#### 5. Answer Correctness (`GEval` with custom rubric)

**Purpose**: Did the agent produce the right severity AND the right set of triggered dimensions?

This is a `GEval` (not `AnswerCorrectnessMetric` which is QA-oriented) because the "answer" is structured — a severity label + a set of triggered dimensions — not free-text.

**Rubric** (passed to judge):
```
You are evaluating an AI monitoring agent that assesses risk in financial deal pipelines.

The deal facts are:
{input}

The expected output is:
- Severity: {expected_output.severity}
- Triggered dimensions: {expected_output.dimensions}

The agent produced:
- Severity: {actual_output.severity}
- Triggered dimensions: {actual_output.dimensions}

Score the agent's answer from 0.0 to 1.0 using this rubric:
- 1.0: Severity matches exactly AND all expected dimensions are triggered AND no unexpected dimensions fired.
- 0.8: Severity matches AND ≤1 dimension differs (one missed or one extra).
- 0.5: Severity is off by one level (e.g. act→watch or watch→act) AND dimensions are mostly correct (≥75% match).
- 0.2: Severity is off by two levels OR majority of expected dimensions were missed.
- 0.0: Severity is maximally wrong (escalate→informational or informational→escalate) OR the observation is entirely absent.

Return only the numeric score.
```

**Aggregate threshold**: mean `AnswerCorrectness` score across all 30 scenarios must be ≥ 0.75 for the deep eval run to pass.

---

## Layer 3 — Langfuse Observability

### Setup

```env
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com   # or self-hosted
```

If keys are absent, `EvalTracer` no-ops silently — `make eval` continues to work offline.

### Trace Structure

One `langfuse.trace()` per scenario:

```python
trace = langfuse.trace(
    name=f"eval-scenario/{scenario_id}",
    tags=[category, f"expected:{expected_severity}", f"actual:{actual_severity}"],
    metadata={
        "scenario_id": scenario_id,
        "category": category,
        "expected_severity": expected_severity,
        "actual_severity": actual_severity,
        "enrichment_rounds": len(enrichment_chain),
        "llm_call_count": llm_call_count,
        "layer1_passed": layer1_passed,
    }
)

# One score per deepeval metric
for metric_name, score in metric_scores.items():
    trace.score(name=metric_name, value=score)
```

### Dashboard queries

After a `make eval-deep` run, useful Langfuse filters:
- `tags: adversarial_calibration` — see all calibration scenario traces
- `score: misinformation < 0.5` — find hallucination failures
- `score: answer_correctness < 0.5` — find severity/dimension failures
- Compare `expected:escalate` vs `expected:watch` tag groups to detect severity-level bias

---

## Adding a New Metric

1. Add the metric wrapper to `eval/deepeval_metrics.py`
2. Add it to the `run_deep_eval()` call list in `deepeval_runner.py`
3. Add the corresponding `langfuse.score()` call in `langfuse_tracer.py`
4. Document the metric definition in this file under §Layer 2

No changes to fixtures or the Layer 1 runner are needed unless the metric requires new `ground_truth` fields.

---

## Adding a New Fixture

1. Author the YAML in `eval/fixtures/` following the schema in `contracts/eval-scenario-schema.md`
2. Add the `ground_truth` block (TEV01 format)
3. If the fixture is an enrichment scenario, set `ground_truth.expected_tools`
4. Run `make eval` to verify Layer 1 assertions pass
5. Run `make eval-deep` to generate Layer 2 scores and langfuse traces
6. If `HallucinationMetric < 0.5`, review the intervention prompt for factual anchoring
