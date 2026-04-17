"""
LLM client — structured tool-use calls to Anthropic Claude.

All calls go through call_structured(), which:
  - Forces tool-use output via Pydantic model_json_schema()
  - Retries transient errors with exponential backoff (max 3 attempts)
  - Retries parse failures once with a corrective reprompt (FR-025)
  - Caches by (tick_id, deal_id, call_name) for idempotency on restart
  - Logs every call with correlation IDs, latency, and token counts
"""

from __future__ import annotations

import time
from typing import Any, Type, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from hiive_monitor import logging as log_module
from hiive_monitor.config import get_settings

T = TypeVar("T", bound=BaseModel)

# In-memory idempotency cache keyed on (tick_id, deal_id, call_name).
# Cleared on process restart — that's fine; restarts will re-execute the same
# LLM call and the result should be semantically identical.
_CALL_CACHE: dict[tuple[str, str, str], BaseModel] = {}

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # seconds, doubled each attempt


def _get_client() -> anthropic.Anthropic:
    settings = get_settings()
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def call_structured(
    *,
    prompt: str,
    output_model: Type[T],
    model: str,
    tick_id: str,
    deal_id: str,
    call_name: str,
    system: str = "",
    timeout: float = 30.0,
) -> T | None:
    """
    Make a schema-validated LLM call and return a typed Pydantic instance.

    Returns None only if both the primary call and the corrective reprompt
    both fail validation. Caller is responsible for persisting an error observation.
    """
    cache_key = (tick_id, deal_id, call_name)
    if cache_key in _CALL_CACHE:
        return _CALL_CACHE[cache_key]  # type: ignore[return-value]

    logger = log_module.get_logger()
    tool_name = call_name.replace(".", "_")
    tool_schema = output_model.model_json_schema()

    # Remove $defs — Anthropic tool schema doesn't support references.
    tool_schema = _inline_refs(tool_schema)

    tools = [
        {
            "name": tool_name,
            "description": f"Output schema for {call_name}",
            "input_schema": tool_schema,
        }
    ]

    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

    result = _call_with_retry(
        messages=messages,
        tools=tools,
        tool_name=tool_name,
        output_model=output_model,
        model=model,
        system=system,
        timeout=timeout,
        call_name=call_name,
        tick_id=tick_id,
        deal_id=deal_id,
        logger=logger,
    )

    if result is not None:
        _CALL_CACHE[cache_key] = result
    return result


def _call_with_retry(
    *,
    messages: list[dict],
    tools: list[dict],
    tool_name: str,
    output_model: Type[T],
    model: str,
    system: str,
    timeout: float,
    call_name: str,
    tick_id: str,
    deal_id: str,
    logger: Any,
) -> T | None:
    client = _get_client()
    last_error: Exception | None = None
    parse_failure_msg: str | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        t0 = time.monotonic()
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": 2048,
                "tools": tools,
                "tool_choice": {"type": "tool", "name": tool_name},
                "messages": messages,
            }
            if system:
                kwargs["system"] = system

            response = client.messages.create(**kwargs)
            latency_ms = int((time.monotonic() - t0) * 1000)

            # Extract tool-use block
            tool_block = next(
                (b for b in response.content if b.type == "tool_use"),
                None,
            )
            if tool_block is None:
                raise ValueError(f"No tool_use block in response for {call_name}")

            raw_input = tool_block.input  # dict

            try:
                parsed = output_model.model_validate(raw_input)
                logger.info(
                    "llm.call.completed",
                    call_name=call_name,
                    model=model,
                    latency_ms=latency_ms,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    tick_id=tick_id,
                    deal_id=deal_id,
                    attempt=attempt,
                    parse_ok=True,
                )
                return parsed

            except ValidationError as ve:
                latency_ms = int((time.monotonic() - t0) * 1000)
                logger.warning(
                    "llm.call.parse_failure",
                    call_name=call_name,
                    model=model,
                    latency_ms=latency_ms,
                    tick_id=tick_id,
                    deal_id=deal_id,
                    attempt=attempt,
                    error=str(ve),
                )
                if parse_failure_msg is None:
                    # First parse failure — do one corrective reprompt (FR-025).
                    parse_failure_msg = str(ve)
                    corrective = (
                        f"Your previous response failed validation:\n{ve}\n\n"
                        "Please correct the output and respond again using the same tool schema."
                    )
                    messages = messages + [
                        {"role": "assistant", "content": response.content},
                        {"role": "user", "content": corrective},
                    ]
                    continue  # retry immediately without backoff
                else:
                    # Second parse failure — give up.
                    logger.error(
                        "llm.call.parse_failure_final",
                        call_name=call_name,
                        model=model,
                        tick_id=tick_id,
                        deal_id=deal_id,
                        error=str(ve),
                    )
                    return None

        except anthropic.BadRequestError as e:
            # Non-retryable — malformed request.
            logger.error(
                "llm.call.bad_request",
                call_name=call_name,
                model=model,
                tick_id=tick_id,
                deal_id=deal_id,
                error=str(e),
            )
            return None

        except (anthropic.APIError, anthropic.APIConnectionError, anthropic.RateLimitError) as e:
            latency_ms = int((time.monotonic() - t0) * 1000)
            last_error = e
            delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                "llm.call.transient_error",
                call_name=call_name,
                model=model,
                attempt=attempt,
                retry_in_s=delay,
                tick_id=tick_id,
                deal_id=deal_id,
                error=str(e),
            )
            if attempt < _MAX_RETRIES:
                time.sleep(delay)

    logger.error(
        "llm.call.exhausted_retries",
        call_name=call_name,
        model=model,
        tick_id=tick_id,
        deal_id=deal_id,
        last_error=str(last_error),
    )
    return None


def clear_cache() -> None:
    """Clear the idempotency cache. Used in tests."""
    _CALL_CACHE.clear()


def _inline_refs(schema: dict) -> dict:
    """
    Flatten $defs/$ref references in a JSON schema so Anthropic's tool API
    can parse it. Handles one level of nesting — sufficient for our models.
    """
    import copy

    schema = copy.deepcopy(schema)
    defs = schema.pop("$defs", {})
    if not defs:
        return schema

    def resolve(obj: Any) -> Any:
        if isinstance(obj, dict):
            if "$ref" in obj:
                ref_name = obj["$ref"].split("/")[-1]
                if ref_name in defs:
                    return resolve(copy.deepcopy(defs[ref_name]))
            return {k: resolve(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [resolve(item) for item in obj]
        return obj

    return resolve(schema)
