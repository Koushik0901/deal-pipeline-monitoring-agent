"""
Langfuse observability integration for eval runs.

Responsibilities:
  1. Upload/sync YAML fixtures as a versioned Langfuse Dataset (one-time per run).
  2. Open an Experiment Run (DatasetRun) scoped to the current eval session.
  3. For each scenario, expose an item.run() context so the scenario trace is
     automatically linked to its dataset item — enabling the Langfuse Experiments UI.
  4. Score individual traces (tier1_pass, severity_correct, per-dimension hit/miss).
  5. Emit aggregate P/R/F1 and confusion-matrix scores on the run-level trace.
  6. For deep eval runs, record deepeval LLM-as-judge metric scores per scenario.

Gracefully no-ops when LANGFUSE_PUBLIC_KEY is not set so `make eval` works offline.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Generator
from typing import Any


class EvalTracer:
    """Manages a single Langfuse eval session: dataset sync + experiment run."""

    def __init__(self) -> None:
        self._enabled = bool(os.environ.get("LANGFUSE_PUBLIC_KEY"))
        self._client = None
        self._run_name: str | None = None
        self._dataset_items: dict = {}  # scenario_id → DatasetItem

        if not self._enabled:
            return
        try:
            from langfuse import Langfuse
            self._client = Langfuse(
                public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
                secret_key=os.environ.get("LANGFUSE_SECRET_KEY", ""),
                host=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
            )
        except Exception as e:
            print(f"WARN: langfuse init failed — tracing disabled ({e})")
            self._enabled = False

    # ── Dataset + experiment run setup ────────────────────────────────────────

    def setup_experiment(
        self,
        scenarios: list[dict],
        run_name: str,
        run_metadata: dict | None = None,
    ) -> None:
        """
        Upload scenarios as dataset items and cache dataset item handles.

        Must be called before the scenario loop so item.run() contexts are available.
        """
        if not self._enabled or self._client is None:
            return
        try:
            from hiive_monitor.eval.langfuse_dataset import (
                DATASET_NAME,
                ensure_dataset,
                get_dataset_items_by_scenario,
            )
            ensure_dataset(self._client, scenarios)
            self._dataset_items = get_dataset_items_by_scenario(self._client)
            self._run_name = run_name
            print(
                f"  Langfuse: dataset '{DATASET_NAME}' synced "
                f"({len(self._dataset_items)} items), run='{run_name}'"
            )
        except Exception as e:
            print(f"  WARN: langfuse dataset setup failed — experiment linking disabled ({e})")

    # ── Per-scenario context manager ──────────────────────────────────────────

    @contextlib.contextmanager
    def scenario_run(self, scenario_id: str) -> Generator[Any, None, None]:
        """
        Context manager that wraps a single scenario execution.

        Creates a root trace for the scenario (using a seed-stable trace_id so
        repeated runs of the same scenario are always distinct) and links it to
        the dataset item + experiment run via the v4 dataset_run_items API.

        If Langfuse is unavailable, yields None (no-op sentinel).

        Usage:
            with tracer.scenario_run(sid) as span:
                result = run_scenario(scenario)
                tracer.score_scenario(span, result)
        """
        if not self._enabled or self._run_name is None:
            yield None
            return

        try:
            from langfuse import Langfuse
            # Unique trace per scenario+run combination
            trace_id = Langfuse.create_trace_id()
            item = self._dataset_items.get(scenario_id)

            with self._client.start_as_current_observation(
                trace_context={"trace_id": trace_id},
                name=f"eval/{scenario_id}",
                as_type="span",
                metadata={
                    "scenario_id": scenario_id,
                    "run_name": self._run_name,
                    "eval_type": "tier1",
                },
            ) as span:
                # Link the trace to the dataset item + experiment run
                if item is not None:
                    try:
                        self._client.api.dataset_run_items.create(
                            run_name=self._run_name,
                            dataset_item_id=item.id,
                            trace_id=trace_id,
                        )
                    except Exception as link_err:
                        print(f"  WARN: dataset_run_item link failed for {scenario_id}: {link_err}")
                yield span
        except Exception as e:
            print(f"  WARN: langfuse scenario_run failed for {scenario_id}: {e}")
            yield None

    # ── Per-scenario scoring ───────────────────────────────────────────────────

    def score_scenario(self, span: Any, result: dict) -> None:
        """
        Attach tier-1 scores to the scenario's trace span.

        Called inside a scenario_run() context with the span yielded by it.
        """
        if span is None or not self._enabled:
            return
        try:
            _ALL_DIMENSIONS = [
                "stage_aging", "deadline_proximity", "communication_silence",
                "missing_prerequisites", "counterparty_nonresponsiveness",
                "unusual_characteristics",
            ]
            gt_triggered = set(result.get("gt_triggered", []))
            gt_not_triggered = set(result.get("gt_not_triggered", []))
            actual_triggered = set(result.get("actual_triggered", []))

            span.score_trace(
                name="tier1_pass",
                value=1.0 if result["passed"] else 0.0,
            )
            span.score_trace(
                name="task_completed",
                value=1.0 if result.get("task_completed") else 0.0,
            )

            if result.get("gt_severity") and result.get("actual_severity"):
                correct = result["gt_severity"] == result["actual_severity"]
                span.score_trace(
                    name="severity_correct",
                    value=1.0 if correct else 0.0,
                    comment=f"expected={result['gt_severity']}, actual={result['actual_severity']}",
                )

            if result.get("factual_grounding") is not None:
                span.score_trace(
                    name="factual_grounding",
                    value=1.0 if result["factual_grounding"] else 0.0,
                    comment=result.get("factual_grounding_detail", ""),
                )

            for dim in _ALL_DIMENSIONS:
                if dim in gt_triggered:
                    span.score_trace(
                        name=f"dim/{dim}",
                        value=1.0 if dim in actual_triggered else 0.0,
                        comment="expected triggered",
                    )
                elif dim in gt_not_triggered:
                    span.score_trace(
                        name=f"dim/{dim}",
                        value=1.0 if dim not in actual_triggered else 0.0,
                        comment="expected NOT triggered",
                    )
        except Exception as e:
            print(f"  WARN: langfuse score_scenario failed for {result.get('id', '?')}: {e}")

    # ── Deep-eval metric scores (called from deepeval_runner) ─────────────────

    def trace_scenario(
        self,
        *,
        scenario_id: str,
        category: str,
        expected_severity: str,
        actual_severity: str | None,
        metric_scores: dict[str, float],
        layer1_passed: bool,
        enrichment_rounds: int = 0,
        llm_call_count: int = 0,
    ) -> None:
        """
        Emit a standalone Langfuse trace for a deepeval LLM-as-judge run.

        Creates one trace per scenario tagged with category + severity labels,
        then attaches a score for every deepeval metric.
        """
        if not self._enabled or self._client is None:
            return
        try:
            trace = self._client.trace(
                name=f"deepeval/{scenario_id}",
                tags=[
                    category,
                    f"expected:{expected_severity}",
                    f"actual:{actual_severity or 'none'}",
                    "layer1-pass" if layer1_passed else "layer1-fail",
                ],
                metadata={
                    "scenario_id": scenario_id,
                    "category": category,
                    "expected_severity": expected_severity,
                    "actual_severity": actual_severity,
                    "enrichment_rounds": enrichment_rounds,
                    "llm_call_count": llm_call_count,
                    "layer1_passed": layer1_passed,
                },
            )
            for metric_name, score in metric_scores.items():
                trace.score(name=metric_name, value=score)
        except Exception as e:
            print(f"  WARN: langfuse trace_scenario failed for {scenario_id}: {e}")

    # ── Aggregate run-level scores ─────────────────────────────────────────────

    def emit_aggregate_scores(self, metrics: dict) -> None:
        """
        Emit a single aggregate trace for the entire eval run.

        Scores: precision/<dim>, recall/<dim>, f1/<dim> per dimension,
                severity_accuracy/<level> per severity level (confusion matrix diagonal),
                overall tier1_pass_rate.
        Metadata: full confusion matrix, full dimension TP/FP/FN/TN stats.
        """
        if not self._enabled or self._client is None:
            return
        try:
            _SEVERITY_ORDER = ["informational", "watch", "act", "escalate"]
            matrix = metrics.get("matrix", {})

            confusion_flat = {
                f"matrix/{exp}_predicted_{act}": matrix.get(exp, {}).get(act, 0)
                for exp in _SEVERITY_ORDER
                for act in _SEVERITY_ORDER
            }
            dim_meta = {
                dim: {k: round(v, 4) if v is not None else None for k, v in stats.items()}
                for dim, stats in metrics.get("dim_stats", {}).items()
            }
            tc_n, tc_d = metrics.get("task_completion", (0, 0))
            pass_rate = round(tc_n / tc_d, 4) if tc_d else 0.0

            run_trace = self._client.trace(
                name=f"eval/aggregate/{self._run_name or 'unknown'}",
                tags=["aggregate", "tier1"],
                metadata={
                    "run_name": self._run_name,
                    "tier1_pass_rate": pass_rate,
                    "total_scenarios": tc_d,
                    "dimension_stats": dim_meta,
                    **confusion_flat,
                },
            )

            # P/R/F1 per dimension
            for dim, stats in metrics.get("dim_stats", {}).items():
                for metric_name in ("precision", "recall", "f1"):
                    val = stats.get(metric_name)
                    if val is not None:
                        run_trace.score(name=f"{metric_name}/{dim}", value=round(val, 4))

            # Confusion matrix diagonal (per-severity recall)
            for sev in _SEVERITY_ORDER:
                row_total = sum(matrix.get(sev, {}).get(a, 0) for a in _SEVERITY_ORDER)
                if row_total > 0:
                    run_trace.score(
                        name=f"severity_recall/{sev}",
                        value=round(matrix[sev][sev] / row_total, 4),
                        comment=f"{matrix[sev][sev]}/{row_total} correct",
                    )

            run_trace.score(name="tier1_pass_rate", value=pass_rate)

            print(f"  Langfuse: aggregate scores emitted (run='{self._run_name}')")
        except Exception as e:
            print(f"  WARN: langfuse emit_aggregate_scores failed: {e}")

    # ── Flush ─────────────────────────────────────────────────────────────────

    def flush(self) -> None:
        if not self._enabled or self._client is None:
            return
        try:
            self._client.flush()
        except Exception:
            pass
