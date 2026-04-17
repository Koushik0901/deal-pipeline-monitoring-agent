"""
Langfuse Dataset management for the deal-pipeline eval harness.

Uploads all eval YAML fixtures as a versioned Langfuse Dataset so:
  - Experiment runs are properly linked to dataset items
  - The Langfuse UI shows side-by-side input/expected/actual comparisons
  - Historical experiment runs can be compared across model/prompt changes

Dataset name: "deal-pipeline-eval"
Item ID scheme: "eval/<scenario_id>" — stable across runs, enabling upsert semantics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langfuse import Langfuse

DATASET_NAME = "deal-pipeline-eval"


def dataset_item_id(scenario_id: str) -> str:
    """Return a stable, project-unique item ID for a scenario."""
    return f"eval/{scenario_id}"


def ensure_dataset(lf: Langfuse, scenarios: list[dict]) -> None:
    """
    Create the eval dataset (if absent) and upsert all scenario items.

    Safe to call on every eval run — existing items are updated in place via
    their stable ID so no duplicate items accumulate across runs.

    Input schema  → scenario setup block (deal facts, parties, events)
    Expected output → ground_truth block (severity, dimensions, expected tools)
    """
    lf.create_dataset(
        name=DATASET_NAME,
        description=(
            "Hiive deal-pipeline monitoring agent evaluation scenarios. "
            "Each item is one YAML fixture: input = deal snapshot, "
            "expected_output = ground-truth severity + risk dimensions."
        ),
        metadata={
            "scenario_count": len(scenarios),
            "fixture_schema_version": "2",
        },
    )

    for scenario in scenarios:
        sid = scenario.get("id", scenario.get("_file", "unknown"))
        gt = scenario.get("ground_truth", {})

        input_data = {
            "scenario_id": sid,
            "category": scenario.get("category", "unknown"),
            "setup": scenario.get("setup", {}),
            "assertions": scenario.get("assertions", {}),
        }

        expected_output = {
            "severity": gt.get("severity"),
            "dimensions_triggered": gt.get("dimensions_triggered", []),
            "dimensions_not_triggered": gt.get("dimensions_not_triggered", []),
            "expected_tools": gt.get("expected_tools"),
        }

        lf.create_dataset_item(
            dataset_name=DATASET_NAME,
            id=dataset_item_id(sid),
            input=input_data,
            expected_output=expected_output,
            metadata={"category": scenario.get("category", "unknown")},
        )


def get_dataset_items_by_scenario(lf: Langfuse) -> dict:
    """
    Return a mapping of scenario_id → DatasetItem for all items in the dataset.

    Returns an empty dict if the dataset doesn't exist or Langfuse is unavailable.
    """
    try:
        dataset = lf.get_dataset(DATASET_NAME)
        return {
            item.id.removeprefix("eval/"): item
            for item in dataset.items
            if item.id.startswith("eval/")
        }
    except Exception:
        return {}
