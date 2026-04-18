"""
deepeval metric wrappers (TEV07).

Five metrics, all using OpenRouterJudge so no OpenAI key is required.
Import is lazy to keep the base runner fast when --deep is not passed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_ANSWER_CORRECTNESS_RUBRIC = """
You are an expert evaluator assessing an AI monitoring agent that classifies risk in \
secondary-market private equity deal pipelines.

Your task is to score how accurately the agent's output matches the expected ground-truth answer.

## Inputs you will receive
- INPUT: Structured deal facts (stage, dwell time, communications, parties, blockers, deadlines)
- EXPECTED OUTPUT: The correct severity level and the correct set of triggered risk dimensions
- ACTUAL OUTPUT: What the agent produced (severity + triggered dimensions)

## Severity scale (ordered lowest → highest)
informational < watch < act < escalate

## Scoring rubric

Apply EXACTLY one score from the table below. Do not interpolate between bands.

| Score | Severity match | Dimension match | Notes |
|-------|---------------|-----------------|-------|
| 1.0   | Exact match   | All expected dimensions present AND no extra dimensions | Perfect answer |
| 0.8   | Exact match   | Exactly 1 dimension differs (one missed OR one extra) | Minor dimension error |
| 0.6   | Exact match   | 2 dimensions differ (missed or extra) | Severity correct, dimensions imprecise |
| 0.5   | Off by 1 level (e.g. act→watch, watch→act) | ≥ half of expected dimensions present | Acceptable near-miss |
| 0.2   | Off by 2 levels OR exact match with ≥ half dimensions missing | Any | Significant error |
| 0.0   | Off by 3 levels (escalate↔informational) OR no observation produced | Any | Critical failure |

## Tie-break rules
- If severity matches exactly but no expected dimensions exist (informational deals), score 1.0 if actual \
triggered dimensions is also empty; score 0.8 if ≤1 spurious dimension fired.
- If ACTUAL OUTPUT is absent or malformed, score 0.0.
- If both outputs list the same dimensions in different order, treat as exact match.

## Examples
- Expected: severity=act, dims=[deadline_proximity, communication_silence] \
  Actual: severity=act, dims=[deadline_proximity, communication_silence] → 1.0
- Expected: severity=act, dims=[deadline_proximity] \
  Actual: severity=act, dims=[deadline_proximity, stage_aging] → 0.8 (one extra dim)
- Expected: severity=escalate, dims=[deadline_proximity, communication_silence] \
  Actual: severity=act, dims=[deadline_proximity, communication_silence] → 0.5 (off by 1 level, dims OK)
- Expected: severity=escalate, dims=[deadline_proximity] \
  Actual: severity=watch, dims=[] → 0.2 (off by 2 levels)
- Expected: severity=escalate \
  Actual: severity=informational → 0.0 (maximally wrong)
""".strip()

_TASK_DESCRIPTION = (
    "You are evaluating a deal monitoring agent that processes secondary-market private equity deals. "
    "The agent's task is complete when ALL of the following steps have been performed correctly:\n"
    "  Step 1 — Evaluate all five risk dimensions independently: stage_aging, deadline_proximity, "
    "communication_silence, missing_prerequisites, and unusual_characteristics.\n"
    "  Step 2 — Assess whether the available context is sufficient to make a severity decision; "
    "if not, call exactly the right enrichment tool (fetch_communication_content, "
    "fetch_prior_observations, or fetch_issuer_history) with the correct deal or issuer ID.\n"
    "  Step 3 — Assign a severity level: informational, watch, act, or escalate — "
    "calibrated to the number and weight of triggered dimensions.\n"
    "  Step 4 — If severity is 'act' or 'escalate', draft a grounded intervention "
    "(outbound nudge or internal escalation) that references real deal facts "
    "(issuer name, deadline, share count) — no fabricated details.\n"
    "  Step 5 — Persist the observation (confirmed by 'Observation persisted: yes' in the output).\n"
    "The task is INCOMPLETE if: 'Observation persisted' is 'no', severity is missing, "
    "a required enrichment tool was skipped when context was insufficient, "
    "or an intervention was drafted for an informational/watch deal."
)


def build_metrics(judge: OpenRouterJudge | None = None):  # type: ignore[name-defined]  # noqa: F821
    """Return the five deepeval metric instances. Lazily imported."""
    from deepeval.metrics import (
        GEval,
        HallucinationMetric,
        TaskCompletionMetric,
        ToolCorrectnessMetric,
    )
    from deepeval.metrics.argument_correctness.argument_correctness import ArgumentCorrectnessMetric
    from deepeval.test_case import LLMTestCaseParams

    if judge is None:
        from hiive_monitor.eval.deepeval_adapter import OpenRouterJudge
        judge = OpenRouterJudge()

    task_completion = TaskCompletionMetric(
        task=_TASK_DESCRIPTION,
        model=judge,
        threshold=0.5,
        verbose_mode=False,
        include_reason=True,
    )

    tool_correctness = ToolCorrectnessMetric(
        threshold=0.5,
        model=judge,
        verbose_mode=False,
    )

    argument_correctness = ArgumentCorrectnessMetric(
        threshold=0.5,
        model=judge,
        verbose_mode=False,
    )

    hallucination = HallucinationMetric(
        model=judge,
        threshold=0.5,
        verbose_mode=False,
        include_reason=True,
    )

    answer_correctness = GEval(
        name="AnswerCorrectness",
        criteria=_ANSWER_CORRECTNESS_RUBRIC,
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
            LLMTestCaseParams.EXPECTED_OUTPUT,
        ],
        model=judge,
        threshold=0.5,
        verbose_mode=False,
    )

    return {
        "task_completion": task_completion,
        "tool_correctness": tool_correctness,
        "argument_correctness": argument_correctness,
        "hallucination": hallucination,
        "answer_correctness": answer_correctness,
    }
