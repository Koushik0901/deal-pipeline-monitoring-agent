"""
Process-wide LLM token + cost tracker.

Every `call_structured` invocation attaches a callback that captures token
counts from the raw LangChain response and records them here, keyed by
(tick_id, model). Aggregates are read at tick close to emit a summary log
and persisted as JSON on the `ticks` row when the `llm_usage` column exists.

Totals are cumulative within a process — use `reset()` in tests if needed.

Cost priority: actual OpenRouter cost (requires usage.include=True in request)
is used when available. Falls back to pricing-table estimate otherwise.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any

from hiive_monitor.llm.pricing import estimate_cost_usd

_LOCK = threading.Lock()

# keyed by (tick_id, model)
_BY_TICK_MODEL: dict[tuple[str, str], dict[str, Any]] = defaultdict(
    lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "calls": 0,
        "cost_usd": 0.0,
        "cost_source": "estimated",  # "openrouter" when real cost is available
    }
)

# keyed by model only — lifetime process total
_BY_MODEL: dict[str, dict[str, Any]] = defaultdict(
    lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "calls": 0,
        "cost_usd": 0.0,
        "cost_source": "estimated",
    }
)


def record(
    *,
    tick_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    actual_cost_usd: float | None = None,
    call_name: str,
) -> dict[str, Any]:
    """Record a single LLM call's usage. Returns the per-call cost dict for logging."""
    if actual_cost_usd is not None:
        cost = actual_cost_usd
        cost_source = "openrouter"
    else:
        cost, _ = estimate_cost_usd(model, input_tokens, output_tokens)
        cost_source = "estimated"

    with _LOCK:
        for bucket in (_BY_TICK_MODEL[(tick_id, model)], _BY_MODEL[model]):
            bucket["input_tokens"] += input_tokens
            bucket["output_tokens"] += output_tokens
            bucket["reasoning_tokens"] += reasoning_tokens
            bucket["cache_read_tokens"] += cache_read_tokens
            bucket["cache_write_tokens"] += cache_write_tokens
            bucket["calls"] += 1
            bucket["cost_usd"] = round(bucket["cost_usd"] + cost, 8)
            if cost_source == "openrouter":
                bucket["cost_source"] = "openrouter"

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "cost_usd": cost,
        "cost_source": cost_source,
        "call_name": call_name,
    }


def snapshot_tick(tick_id: str) -> dict[str, dict[str, Any]]:
    """Return per-model totals for a tick. Keys are model names."""
    with _LOCK:
        return {
            model: dict(data)
            for (tid, model), data in _BY_TICK_MODEL.items()
            if tid == tick_id
        }


def tick_totals(tick_id: str) -> dict[str, Any]:
    """Return aggregate totals across all models for a tick."""
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "calls": 0,
        "cost_usd": 0.0,
        "cost_source": "estimated",
    }
    snap = snapshot_tick(tick_id)
    for data in snap.values():
        totals["input_tokens"] += data["input_tokens"]
        totals["output_tokens"] += data["output_tokens"]
        totals["reasoning_tokens"] += data["reasoning_tokens"]
        totals["cache_read_tokens"] += data["cache_read_tokens"]
        totals["cache_write_tokens"] += data["cache_write_tokens"]
        totals["calls"] += data["calls"]
        totals["cost_usd"] = round(totals["cost_usd"] + data["cost_usd"], 8)
        if data.get("cost_source") == "openrouter":
            totals["cost_source"] = "openrouter"
    return totals


def lifetime_by_model() -> dict[str, dict[str, Any]]:
    """Return per-model totals for the life of the process."""
    with _LOCK:
        return {model: dict(data) for model, data in _BY_MODEL.items()}


def evict_tick(tick_id: str) -> None:
    """Evict per-tick entries after tick close to prevent unbounded growth."""
    with _LOCK:
        for key in [k for k in _BY_TICK_MODEL if k[0] == tick_id]:
            del _BY_TICK_MODEL[key]


def reset() -> None:
    """Clear all counters — for tests."""
    with _LOCK:
        _BY_TICK_MODEL.clear()
        _BY_MODEL.clear()
