"""
Approximate LLM pricing table (USD per 1M tokens) for cost estimation.

Prices reflect OpenRouter list rates as of 2026-04 and are intentionally kept
conservative — they round up to the nearest sensible cent. Treat estimates
as ballpark only; authoritative cost comes from OpenRouter billing or Langfuse.

When a model is not in the table, the `_default` row is used and the estimate
is flagged so dashboards know the number is a fallback.
"""

from __future__ import annotations

# (input_per_1m_usd, output_per_1m_usd)
_PRICING: dict[str, tuple[float, float]] = {
    "anthropic/claude-sonnet-4.6": (3.00, 15.00),
    "anthropic/claude-sonnet-4-6": (3.00, 15.00),
    "anthropic/claude-opus-4.7": (15.00, 75.00),
    "anthropic/claude-haiku-4.5": (0.80, 4.00),
    "google/gemma-4-31b-it": (0.10, 0.30),
    "google/gemini-2.5-pro": (1.25, 10.00),
    "openai/gpt-4.1": (2.00, 8.00),
    "openai/gpt-4o": (2.50, 10.00),
    "openai/gpt-4o-mini": (0.15, 0.60),
    "qwen/qwen3.6-plus": (0.40, 1.20),
    "_default": (1.00, 3.00),
}


def lookup(model: str) -> tuple[float, float, bool]:
    """Return (input_rate, output_rate, is_fallback) for a model."""
    if model in _PRICING:
        rates = _PRICING[model]
        return rates[0], rates[1], False
    rates = _PRICING["_default"]
    return rates[0], rates[1], True


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> tuple[float, bool]:
    """Return (cost_usd, is_fallback). Cost is computed from the pricing table."""
    in_rate, out_rate, fallback = lookup(model)
    cost = (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000.0
    return round(cost, 6), fallback
