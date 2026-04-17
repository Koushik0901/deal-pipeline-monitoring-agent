"""
deepeval + langfuse deep evaluation runner (TEV08, TEV09, TEV11).

Called by runner.main(deep=True) after the deterministic Layer 1 pass.
Builds LLMTestCase objects, evaluates all five metrics, logs traces to
langfuse, and appends a deep-eval section to the scorecard.
"""

from __future__ import annotations

import pathlib
import time
from collections.abc import Iterable


def run_deep_eval(
    layer1_results: list[dict],
    fixtures_dir: pathlib.Path,
    results_dir: pathlib.Path,
) -> None:
    """
    Run the Layer 2 LLM-as-judge evaluation over all scenarios.

    layer1_results: output of run_scenario() for every fixture, already executed.
    fixtures_dir:   path to eval/fixtures/ (for reloading scenario YAML content).
    results_dir:    where to write the deep scorecard appendix.
    """
    print()
    print("── Deep Eval (LLM-as-judge) ─────────────────────────────────────")

    # Lazy imports — keep base runner free of these deps
    from deepeval import evaluate

    from hiive_monitor.eval.deepeval_adapter import OpenRouterJudge
    from hiive_monitor.eval.deepeval_cases import build_expected_tools, build_test_case
    from hiive_monitor.eval.deepeval_metrics import build_metrics
    from hiive_monitor.eval.langfuse_tracer import EvalTracer
    from hiive_monitor.eval.runner import load_scenarios

    judge = OpenRouterJudge()
    print(f"Judge model: {judge.get_model_name()}")

    metrics = build_metrics(judge)
    tracer = EvalTracer()
    if tracer._enabled:
        print("Langfuse tracing: enabled")
    else:
        print("Langfuse tracing: disabled (set LANGFUSE_PUBLIC_KEY to enable)")
    print()

    # Reload scenarios to get full YAML content (ground_truth, setup, etc.)
    scenarios_by_id = {
        s.get("id", s.get("_file", "?")): s
        for s in load_scenarios(fixtures_dir)
    }

    scenario_scores: list[dict] = []

    for result in layer1_results:
        sid = result["id"]
        scenario = scenarios_by_id.get(sid)
        if scenario is None:
            print(f"  [{sid}] SKIP — scenario YAML not found")
            continue

        print(f"  [{sid}] evaluating...", end=" ", flush=True)
        t0 = time.monotonic()

        test_case = build_test_case(scenario, result)
        expected_tools = build_expected_tools(scenario)

        metric_scores: dict[str, float] = {}
        metric_passed: dict[str, bool] = {}

        # Run each metric individually to capture scores even if some fail
        for metric_name, metric in metrics.items():
            try:
                # Tool metrics need expected_tools; skip if scenario has none defined
                if metric_name in ("tool_correctness", "argument_correctness"):
                    if expected_tools is None and test_case.tools_called is None:
                        metric_scores[metric_name] = 1.0  # N/A → full score
                        metric_passed[metric_name] = True
                        continue
                    if expected_tools is not None:
                        test_case.expected_tools = expected_tools

                evaluate(test_cases=[test_case], metrics=[metric], run_async=False, show_indicator=False)
                score = float(metric.score) if metric.score is not None else 0.0
                metric_scores[metric_name] = score
                metric_passed[metric_name] = metric.success
            except Exception as e:
                metric_scores[metric_name] = 0.0
                metric_passed[metric_name] = False
                print(f"\n    WARN [{metric_name}]: {e}", end="")

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        all_passed = all(metric_passed.values())
        status = "PASS" if all_passed else "FAIL"
        print(f"{status} ({elapsed_ms}ms)")

        gt = result.get("ground_truth", {})
        tracer.trace_scenario(
            scenario_id=sid,
            category=result.get("category", "unknown"),
            expected_severity=gt.get("severity", "unknown"),
            actual_severity=result.get("severity"),
            metric_scores=metric_scores,
            layer1_passed=result.get("passed", False),
        )

        scenario_scores.append({
            "id": sid,
            "category": result.get("category"),
            "layer1_passed": result.get("passed"),
            "metric_scores": metric_scores,
            "metric_passed": metric_passed,
        })

    tracer.flush()

    # ── Scorecard appendix ──────────────────────────────────────────────────
    _write_deep_scorecard(scenario_scores, metrics.keys(), results_dir)

    total = len(scenario_scores)
    deep_passed = sum(1 for s in scenario_scores if all(s["metric_passed"].values()))
    print()
    print(f"Deep eval result: {deep_passed}/{total} scenarios passed all metrics")


def _write_deep_scorecard(
    scenario_scores: list[dict],
    metric_names: Iterable[str],
    results_dir: pathlib.Path,
) -> None:
    from hiive_monitor import clock as clk

    metric_names = list(metric_names)
    ts = clk.now().strftime("%Y%m%d_%H%M%S")
    path = results_dir / f"deep_scorecard_{ts}.md"

    header_row = " | ".join(f"{m:>22}" for m in metric_names)
    sep_row = "-+-".join("-" * 22 for _ in metric_names)

    lines = [
        "# Deep Eval Scorecard (LLM-as-Judge)",
        "",
        f"**Generated**: {clk.now().isoformat()}",
        "",
        f"| {'Scenario':<40} | {header_row} |",
        f"|{'-'*42}+-{sep_row}-|",
    ]

    metric_totals: dict[str, list[float]] = {m: [] for m in metric_names}

    for s in scenario_scores:
        scores = s["metric_scores"]
        row = " | ".join(
            f"{scores.get(m, 0.0):>22.2f}" for m in metric_names
        )
        layer1 = "✓" if s["layer1_passed"] else "✗"
        lines.append(f"| {layer1} {s['id']:<38} | {row} |")
        for m in metric_names:
            metric_totals[m].append(scores.get(m, 0.0))

    lines += [
        f"|{'-'*42}+-{sep_row}-|",
    ]
    avg_row = " | ".join(
        f"{sum(v)/len(v) if v else 0:>22.2f}" for v in metric_totals.values()
    )
    lines.append(f"| {'MEAN':42} | {avg_row} |")

    # Flag priority failures
    lines += ["", "## Priority Failures (score < 0.5)", ""]
    priority_failures = []
    for s in scenario_scores:
        for m, score in s["metric_scores"].items():
            if score < 0.5 and m in ("hallucination", "answer_correctness"):
                priority_failures.append(f"- :warning: **{s['id']}** — `{m}` score: {score:.2f}")
    if priority_failures:
        lines.extend(priority_failures)
    else:
        lines.append("None — all hallucination and answer_correctness scores ≥ 0.5.")

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Deep scorecard: {path}")
