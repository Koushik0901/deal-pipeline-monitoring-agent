"""
Tier 2 LLM-as-judge using deepeval G-Eval.

Only activated when --deep is passed to runner. Evaluates 4 rubric criteria
on intervention_quality scenarios:
  1. Factual Grounding  — financial figures are accurate, no hallucination
  2. Tone Appropriateness — professional, calibrated to recipient type
  3. Urgency Calibration  — urgency in draft matches severity level
  4. Actionability       — clear ask with specific next step

Judge model:
  Uses OpenRouterJudge (deepeval_adapter.py) which wraps ChatOpenRouter as a
  DeepEvalBaseLLM. Judge model is set via EVAL_JUDGE_MODEL env var, defaulting
  to qwen/qwen3-plus. Requires OPENROUTER_API_KEY to be set.
"""

from __future__ import annotations

from datetime import datetime, timedelta


def _build_metrics() -> list:
    """Instantiate the 4 G-Eval metrics. Called lazily to avoid import cost on Tier 1 runs."""
    from deepeval.metrics import GEval
    from deepeval.test_case import LLMTestCaseParams

    from hiive_monitor.eval.deepeval_adapter import OpenRouterJudge

    judge = OpenRouterJudge()

    return [
        GEval(
            name="Factual Grounding",
            criteria=(
                "The intervention draft accurately reflects the financial details present in the deal "
                "context (INPUT). Specifically check: (1) share count matches exactly, (2) price per "
                "share matches exactly, (3) issuer name is correct, (4) any deadline dates are correct. "
                "Score 0 if any financial figure is fabricated or incorrect. Score 1 if all verifiable "
                "figures match the context. Partial credit for partially correct output."
            ),
            evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
            model=judge,
            threshold=0.7,
        ),
        GEval(
            name="Tone Appropriateness",
            criteria=(
                "The draft is professional and appropriate for a financial services context. "
                "For outbound_nudge: measured urgency, respectful tone, suitable for issuer/buyer/seller. "
                "For internal_escalation: factual, concise, suitable for a TS lead briefing. "
                "Penalise: alarmist or threatening language, informality, excessive hedging, "
                "placeholder text like [NAME] or TODO, or generic boilerplate."
            ),
            evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
            model=judge,
            threshold=0.7,
        ),
        GEval(
            name="Urgency Calibration",
            criteria=(
                "The urgency conveyed in the draft is calibrated to the severity level given in INPUT. "
                "escalate: strong urgency, explicit consequence framing, clear timeline. "
                "act: moderate urgency, specific ask, deadline reference where applicable. "
                "watch: informational in tone, no pressure language. "
                "Score low if urgency is mismatched to stated severity (e.g. casual tone for escalate, "
                "or alarmist framing for watch)."
            ),
            evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
            model=judge,
            threshold=0.7,
        ),
        GEval(
            name="Actionability",
            criteria=(
                "The draft contains a specific, unambiguous ask or next step for the recipient. "
                "Reward: named deadline, clear expected action, explicit response format requested. "
                "Penalise: vague closes ('please let us know', 'as soon as possible' without a date), "
                "missing any ask, placeholder text, or endings that leave the recipient without clear "
                "direction on what to do next."
            ),
            evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
            model=judge,
            threshold=0.7,
        ),
    ]


def _build_input_context(setup: dict, observations: list[dict], interventions: list[dict]) -> str:
    """Serialise deal context into a structured string for the judge INPUT field."""
    deal = setup.get("deal", {})
    issuers = setup.get("issuers", [{}])
    issuer_name = issuers[0].get("name", "unknown") if issuers else "unknown"
    obs = observations[0] if observations else {}
    iv = interventions[0] if interventions else {}

    now_str = setup.get("now", "2026-04-16T09:00:00Z")

    parts = [
        f"Deal ID: {deal.get('deal_id', 'unknown')}",
        f"Issuer: {issuer_name}",
        f"Stage: {deal.get('stage', 'unknown')}",
        f"Responsible party: {deal.get('responsible_party', 'unknown')}",
    ]

    shares = deal.get("shares")
    price = deal.get("price_per_share")
    if shares:
        parts.append(f"Shares: {shares:,}")
    if price:
        parts.append(f"Price per share: ${price:.2f}")
    if shares and price:
        parts.append(f"Notional value: ${shares * price:,.0f}")

    rofr_days = deal.get("rofr_deadline_days_from_now")
    if rofr_days is not None:
        now_dt = datetime.fromisoformat(now_str.replace("Z", "+00:00"))
        deadline = (now_dt + timedelta(days=rofr_days)).strftime("%Y-%m-%d")
        parts.append(f"ROFR deadline: {deadline} ({rofr_days} days from now)")

    severity = obs.get("severity", "unknown")
    parts.append(f"Severity: {severity}")
    parts.append(f"Intervention type: {iv.get('intervention_type', 'unknown')}")

    blockers = deal.get("blockers", [])
    if blockers:
        blocker_strs = [f"{b.get('kind', '')}: {b.get('description', '')}" for b in blockers]
        parts.append(f"Blockers: {'; '.join(blocker_strs)}")

    return "\n".join(parts)


def run_tier2(
    scenario_id: str,
    setup: dict,
    interventions: list[dict],
    observations: list[dict],
) -> dict[str, float | None] | None:
    """
    Run all 4 G-Eval metrics against the drafted intervention.

    Returns a dict mapping metric name to score (0.0–1.0), or None if there
    is no intervention to judge.
    """
    if not interventions:
        return None

    draft_body = interventions[0].get("draft_body", "")
    if not draft_body.strip():
        return None

    try:
        from deepeval.test_case import LLMTestCase
    except ImportError as exc:
        raise ImportError("deepeval is not installed. Run: uv add deepeval") from exc

    input_context = _build_input_context(setup, observations, interventions)
    test_case = LLMTestCase(input=input_context, actual_output=draft_body)

    metrics = _build_metrics()
    scores: dict[str, float | None] = {}

    for metric in metrics:
        try:
            metric.measure(test_case)
            scores[metric.name] = round(float(metric.score), 3)
        except Exception as e:
            print(f"      WARN: {metric.name} judge call failed: {e}")
            scores[metric.name] = None

    return scores
