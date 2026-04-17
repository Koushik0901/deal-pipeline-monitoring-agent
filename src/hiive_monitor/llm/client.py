"""
LLM client — structured tool-use calls via OpenRouter (OpenAI-compatible API).

All calls go through call_structured(), which:
  - Forces tool-use output via Pydantic model_json_schema()
  - Retries transient errors with exponential backoff (max 3 attempts)
  - Retries parse failures once with a corrective reprompt (FR-025)
  - Caches by (tick_id, deal_id, call_name) for idempotency on restart
  - Logs every call with correlation IDs, latency, and token counts
"""

from __future__ import annotations

import json
import time
from typing import Any, Type, TypeVar

import openai
from pydantic import BaseModel, ValidationError

from hiive_monitor import logging as log_module
from hiive_monitor.config import get_settings

T = TypeVar("T", bound=BaseModel)

_CALL_CACHE: dict[tuple[str, str, str], BaseModel] = {}
_SCHEMA_CACHE: dict[type[BaseModel], dict[str, Any]] = {}

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0


def _get_client() -> openai.OpenAI:
    settings = get_settings()
    return openai.OpenAI(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
    )


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
    tool_schema = _SCHEMA_CACHE.get(output_model)
    if tool_schema is None:
        tool_schema = _inline_refs(output_model.model_json_schema())
        _SCHEMA_CACHE[output_model] = tool_schema

    tools = [
        {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": f"Output schema for {call_name}",
                "parameters": tool_schema,
            },
        }
    ]

    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    result = _call_with_retry(
        messages=messages,
        tools=tools,
        tool_name=tool_name,
        output_model=output_model,
        model=model,
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
            response = client.chat.completions.create(
                model=model,
                max_tokens=2048,
                tools=tools,
                tool_choice={"type": "function", "function": {"name": tool_name}},
                messages=messages,
                timeout=timeout,
            )
            latency_ms = int((time.monotonic() - t0) * 1000)

            choice = response.choices[0]
            tool_calls = choice.message.tool_calls
            if not tool_calls:
                raise ValueError(f"No tool_calls in response for {call_name}")

            raw_json = tool_calls[0].function.arguments
            raw_input = json.loads(raw_json)

            try:
                parsed = output_model.model_validate(raw_input)
                logger.info(
                    "llm.call.completed",
                    call_name=call_name,
                    model=model,
                    latency_ms=latency_ms,
                    input_tokens=response.usage.prompt_tokens if response.usage else 0,
                    output_tokens=response.usage.completion_tokens if response.usage else 0,
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
                    parse_failure_msg = str(ve)
                    assistant_msg: dict[str, Any] = {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": tool_calls[0].id,
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": raw_json,
                                },
                            }
                        ],
                    }
                    tool_result_msg: dict[str, Any] = {
                        "role": "tool",
                        "tool_call_id": tool_calls[0].id,
                        "content": f"Validation error: {ve}",
                    }
                    corrective_msg: dict[str, Any] = {
                        "role": "user",
                        "content": (
                            f"Your previous response failed validation:\n{ve}\n\n"
                            "Please correct the output and respond again using the same tool schema."
                        ),
                    }
                    messages = messages + [assistant_msg, tool_result_msg, corrective_msg]
                    continue
                else:
                    logger.error(
                        "llm.call.parse_failure_final",
                        call_name=call_name,
                        model=model,
                        tick_id=tick_id,
                        deal_id=deal_id,
                        error=str(ve),
                    )
                    return None

        except openai.BadRequestError as e:
            logger.error(
                "llm.call.bad_request",
                call_name=call_name,
                model=model,
                tick_id=tick_id,
                deal_id=deal_id,
                error=str(e),
            )
            return None

        except (openai.APIError, openai.APIConnectionError, openai.RateLimitError) as e:
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


def evict_tick(tick_id: str) -> None:
    """Drop cache entries for a completed tick so memory doesn't grow across runs."""
    for key in [k for k in _CALL_CACHE if k[0] == tick_id]:
        del _CALL_CACHE[key]


def _inline_refs(schema: dict) -> dict:
    """
    Flatten $defs/$ref references in a JSON schema so the tool API
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
