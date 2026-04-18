"""
deepeval + langfuse deep evaluation runner (TEV08, TEV09, TEV11).

Step 2 of the two-step eval workflow:
  make eval       → run agents on fixtures, save results_latest.json  (Tier 1)
  make eval-deep  → load results_latest.json, run LLM-as-judge        (Tier 2)

Builds LLMTestCase objects from saved Tier 1 results, evaluates all five
metrics, logs traces to langfuse, and writes a deep scorecard.
"""

from __future__ import annotations

import pathlib
import time
from collections.abc import Iterable


def run_deep_eval(
    layer1_results: list[dict],
    fixtures_dir: pathlib.Path,
    results_dir: pathlib.Path,
    console=None,
) -> None:
    """
    Run the Layer 2 LLM-as-judge evaluation over all scenarios.

    layer1_results: deserialized output of save_results_json() / run_scenario().
    fixtures_dir:   path to eval/fixtures/ (for reloading scenario YAML content).
    results_dir:    where to write the deep scorecard appendix.
    console:        rich Console (created here if None).
    """
    from rich.console import Console
    from rich.table import Table

    if console is None:
        console = Console()

    # Lazy imports — keep base runner free of these deps
    from deepeval import evaluate
    from deepeval.evaluate.configs import AsyncConfig, DisplayConfig, ErrorConfig

    _async_cfg = AsyncConfig(run_async=False)
    _display_cfg = DisplayConfig(show_indicator=False, print_results=False)
    _error_cfg = ErrorConfig(ignore_errors=True, skip_on_missing_params=False)

    from hiive_monitor.eval.deepeval_adapter import OpenRouterJudge
    from hiive_monitor.eval.deepeval_cases import build_expected_tools, build_test_case
    from hiive_monitor.eval.deepeval_metrics import build_metrics
    from hiive_monitor.eval.langfuse_tracer import EvalTracer
    from hiive_monitor.eval.runner import load_scenarios, make_eval_progress

    judge = OpenRouterJudge()
    console.print(f"[bold]Deep Eval[/]  judge: [cyan]{judge.get_model_name()}[/]  ·  {len(layer1_results)} scenarios")

    metrics = build_metrics(judge)
    tracer = EvalTracer()
    langfuse_tag = "[green]enabled[/]" if tracer._enabled else "[dim]disabled[/]"
    console.print(f"Langfuse tracing: {langfuse_tag}")
    console.print()

    # Reload scenarios to get full YAML content (ground_truth, setup, etc.)
    scenarios_by_id = {
        s.get("id", s.get("_file", "?")): s
        for s in load_scenarios(fixtures_dir)
    }

    scenario_scores: list[dict] = []
    n_pass = 0
    n_fail = 0

    with make_eval_progress(console) as progress:
        task = progress.add_task("Starting…", total=len(layer1_results), n_pass=0, n_fail=0)

        for result in layer1_results:
            sid = result["id"]
            scenario = scenarios_by_id.get(sid)
            progress.update(task, description=sid)

            if scenario is None:
                progress.console.print(f"  [yellow]SKIP[/] {sid} — YAML not found in fixtures")
                progress.update(task, advance=1, n_pass=n_pass, n_fail=n_fail)
                continue

            t0 = time.monotonic()
            test_case = build_test_case(scenario, result)
            expected_tools = build_expected_tools(scenario)

            metric_scores: dict[str, float] = {}
            metric_passed: dict[str, bool] = {}
            metric_reasons: dict[str, str] = {}
            metric_verbose_logs: dict[str, str] = {}

            for metric_name, metric in metrics.items():
                try:
                    if metric_name in ("tool_correctness", "argument_correctness"):
                        if expected_tools is None and test_case.tools_called is None:
                            metric_scores[metric_name] = 1.0
                            metric_passed[metric_name] = True
                            metric_reasons[metric_name] = "N/A — scenario has no expected tool calls"
                            continue
                        if expected_tools is not None:
                            test_case.expected_tools = expected_tools

                    evaluate(
                        test_cases=[test_case],
                        metrics=[metric],
                        async_config=_async_cfg,
                        display_config=_display_cfg,
                        error_config=_error_cfg,
                    )
                    score = float(metric.score) if metric.score is not None else 0.0
                    metric_scores[metric_name] = score
                    metric_passed[metric_name] = metric.success
                    metric_reasons[metric_name] = getattr(metric, "reason", None) or ""
                    metric_verbose_logs[metric_name] = getattr(metric, "verbose_logs", None) or ""
                except Exception as e:
                    metric_scores[metric_name] = 0.0
                    metric_passed[metric_name] = False
                    metric_reasons[metric_name] = f"ERROR: {e}"
                    progress.console.print(f"  [red]WARN[/] [{sid}] {metric_name}: {e}")

            elapsed_ms = int((time.monotonic() - t0) * 1000)
            all_passed = all(metric_passed.values())
            if all_passed:
                n_pass += 1
            else:
                n_fail += 1
                failed_metrics = [
                    f"[dim]{m}[/]={score:.2f}"
                    for m, score in metric_scores.items()
                    if not metric_passed.get(m, True)
                ]
                progress.console.print(
                    f"  [red]FAIL[/] [bold]{sid}[/]  ({elapsed_ms}ms)  {' '.join(failed_metrics)}"
                )

            gt = scenario.get("ground_truth", {}) or {}
            tracer.trace_scenario(
                scenario_id=sid,
                category=scenario.get("category", result.get("category", "unknown")),
                expected_severity=gt.get("severity", result.get("gt_severity", "unknown")),
                actual_severity=result.get("actual_severity") or result.get("severity"),
                metric_scores=metric_scores,
                layer1_passed=result.get("passed", False),
                metric_reasons=metric_reasons,
                metric_verbose_logs=metric_verbose_logs,
                scenario_trace_id=result.get("_trace_id"),
                test_case_summary={
                    "input": test_case.input,
                    "actual_output": test_case.actual_output,
                    "expected_output": test_case.expected_output,
                    "tools_called": [
                        t.name if hasattr(t, "name") else str(t)
                        for t in (test_case.tools_called or [])
                    ],
                    "expected_tools": [
                        t.name if hasattr(t, "name") else str(t)
                        for t in (test_case.expected_tools or [])
                    ],
                },
            )

            scenario_scores.append({
                "id": sid,
                "category": result.get("category"),
                "layer1_passed": result.get("passed"),
                "metric_scores": metric_scores,
                "metric_passed": metric_passed,
                "metric_reasons": metric_reasons,
                "metric_verbose_logs": metric_verbose_logs,
            })

            progress.update(task, advance=1, n_pass=n_pass, n_fail=n_fail)

    tracer.flush()

    # ── Summary table ───────────────────────────────────────────────────────
    metric_names = list(metrics.keys())
    summary = Table(title="Deep Eval — Mean Scores (LLM-as-Judge)", show_header=True)
    summary.add_column("Metric", style="bold")
    summary.add_column("Mean", justify="right")
    summary.add_column("Pass rate", justify="right")

    for m in metric_names:
        all_scores = [s["metric_scores"].get(m, 0.0) for s in scenario_scores]
        all_pass = [s["metric_passed"].get(m, False) for s in scenario_scores]
        mean = sum(all_scores) / len(all_scores) if all_scores else 0.0
        pass_n = sum(all_pass)
        # hallucination is inverted — 0.0 is perfect
        if m == "hallucination":
            color = "green" if mean < 0.1 else "yellow" if mean < 0.5 else "red"
        else:
            color = "green" if mean >= 0.8 else "yellow" if mean >= 0.5 else "red"
        summary.add_row(m, f"[{color}]{mean:.2f}[/]", f"{pass_n}/{len(all_pass)}")

    console.print()
    console.print(summary)

    # ── Scorecard appendix ──────────────────────────────────────────────────
    _write_deep_scorecard(scenario_scores, metric_names, results_dir, console=console)

    total = len(scenario_scores)
    deep_passed = sum(1 for s in scenario_scores if all(s["metric_passed"].values()))
    console.print()
    console.print(f"Deep eval: [bold]{deep_passed}/{total}[/] scenarios passed all metrics")


def _write_deep_scorecard(
    scenario_scores: list[dict],
    metric_names: Iterable[str],
    results_dir: pathlib.Path,
    console=None,
) -> None:
    from hiive_monitor import clock as clk

    metric_names = list(metric_names)
    now = clk.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    path = results_dir / f"deep_scorecard_{ts}.md"

    header_row = " | ".join(f"{m:>22}" for m in metric_names)
    sep_row = "-+-".join("-" * 22 for _ in metric_names)

    lines = [
        "# Deep Eval Scorecard (LLM-as-Judge)",
        "",
        f"**Generated**: {now.isoformat()}",
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

    # Flag priority failures — hallucination is inverted (0.0 = perfect, 1.0 = fully hallucinated)
    lines += ["", "## Priority Failures (score < 0.5)", ""]
    priority_failures = []
    for s in scenario_scores:
        for m, score in s["metric_scores"].items():
            if m == "hallucination":
                if score >= 0.5:
                    priority_failures.append(f"- :warning: **{s['id']}** — `{m}` score: {score:.2f} (hallucination detected)")
            elif score < 0.5:
                priority_failures.append(f"- :warning: **{s['id']}** — `{m}` score: {score:.2f}")
    if priority_failures:
        lines.extend(priority_failures)
    else:
        lines.append("None — no priority failures detected.")

    # Per-scenario judge reasoning appendix
    lines += ["", "## Judge Reasoning", ""]
    for s in scenario_scores:
        reasons = s.get("metric_reasons", {})
        if not reasons:
            continue
        lines.append(f"### {s['id']}")
        lines.append("")
        for m in metric_names:
            score = s["metric_scores"].get(m, 0.0)
            reason = reasons.get(m, "").strip()
            if not reason:
                continue
            lines.append(f"**{m}** — score: {score:.2f}")
            lines.append("")
            lines.append(f"> {reason}")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    if console is None:
        from rich.console import Console
        console = Console()
    console.print(f"[dim]Deep scorecard:[/] {path}")


# -- Entrypoint ----------------------------------------------------------------


def main() -> None:
    import argparse
    import sys

    from rich.console import Console

    parser = argparse.ArgumentParser(
        description="Tier 2 eval — LLM-as-judge on saved Tier 1 results. Run 'make eval' first."
    )
    parser.add_argument(
        "--results",
        metavar="PATH",
        help="Path to results JSON from Tier 1 (default: eval_results/results_latest.json)",
    )
    args = parser.parse_args()

    console = Console()

    from hiive_monitor.eval.runner import _find_fixtures_dir, _find_results_dir, load_results_json

    results_dir = _find_results_dir()

    if args.results:
        results_path = pathlib.Path(args.results)
    else:
        results_path = results_dir / "results_latest.json"

    if not results_path.exists():
        console.print(f"[red]ERROR:[/] No results file found at {results_path}")
        console.print("Run [bold]make eval[/] first to generate Tier 1 results.")
        sys.exit(1)

    console.print(f"[dim]Loading:[/] {results_path}")
    layer1_results = load_results_json(results_path)
    console.print(f"[dim]Loaded {len(layer1_results)} Tier 1 results.[/]")
    console.print()

    try:
        fixtures_dir = _find_fixtures_dir()
    except FileNotFoundError as e:
        console.print(f"[red]ERROR:[/] {e}")
        sys.exit(1)

    run_deep_eval(layer1_results, fixtures_dir, results_dir, console=console)
    sys.exit(0)


if __name__ == "__main__":
    main()
