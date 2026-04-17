"""
LLM client — structured output calls via LangChain + OpenRouter.

All calls go through call_structured(), which:
  - Accepts a ChatPromptTemplate (or raw system+prompt strings for compat)
  - Forces structured output via Pydantic with_structured_output()
  - Retries transient errors with exponential backoff (max 3 attempts)
  - Retries parse failures once with a corrective reprompt (FR-025)
  - Caches by (tick_id, deal_id, call_name) for idempotency on restart
  - Logs every call with correlation IDs, latency, and token counts
"""

from __future__ import annotations

import os
import time
from typing import Any, Type, TypeVar

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate  # noqa: F401 — re-exported for callers
from langchain_openrouter import ChatOpenRouter
from pydantic import BaseModel, ValidationError

from hiive_monitor import logging as log_module
from hiive_monitor.config import get_settings


def _langfuse_handler(call_name: str, tick_id: str, deal_id: str):
    """
    Return a Langfuse CallbackHandler scoped to the current tick's root trace.

    All LLM calls within one tick share a parent trace (keyed by tick_id) so the
    Langfuse UI shows per-tick cost, latency, and token aggregates.  Each individual
    LLM call becomes a nested generation span with its own model, tokens, and latency.

    The handler automatically captures:
      - model name (from the ChatOpenRouter invocation)
      - input / output token counts (from OpenRouter's OpenAI-compatible usage response)
      - latency (start_time / end_time on the generation span)
      - cost (inferred by Langfuse when the model is in its registry; otherwise set
              cost_details manually — see _update_generation_cost())

    Gracefully returns None when LANGFUSE_PUBLIC_KEY is not set.
    """
    if not os.getenv("LANGFUSE_PUBLIC_KEY"):
        return None
    try:
        from langfuse.callback import CallbackHandler
        return CallbackHandler(
            # Nest this call under the tick's root trace rather than creating a new root.
            # All calls with the same trace_id share one parent trace in Langfuse.
            trace_id=tick_id,
            trace_name=f"tick/{tick_id}",
            # The span name identifies which pipeline step this generation belongs to.
            observation_name=call_name,
            session_id=f"deal/{deal_id}",
            metadata={
                "deal_id": deal_id,
                "tick_id": tick_id,
                "call_name": call_name,
            },
            tags=["llm-call", call_name],
        )
    except Exception:
        return None

T = TypeVar("T", bound=BaseModel)

_CALL_CACHE: dict[tuple[str, str, str], BaseModel] = {}
_CLIENT_CACHE: dict[str, ChatOpenRouter] = {}

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0


def _get_llm(model: str, timeout: float) -> ChatOpenRouter:
    """Return a cached ChatOpenRouter client for the given model."""
    cache_key = f"{model}:{timeout}"
    if cache_key not in _CLIENT_CACHE:
        settings = get_settings()
        kwargs: dict = dict(
            model=model,
            openrouter_api_key=settings.openrouter_api_key,
            temperature=0,  # deterministic outputs for classification/scoring
            timeout=timeout,
            max_retries=0,  # retry logic is handled by _call_with_retry
        )
        if settings.llm_max_tokens is not None:
            kwargs["max_tokens"] = settings.llm_max_tokens
        _CLIENT_CACHE[cache_key] = ChatOpenRouter(**kwargs)
    return _CLIENT_CACHE[cache_key]


def call_structured(
    *,
    prompt: str = "",
    output_model: Type[T],
    model: str,
    tick_id: str,
    deal_id: str,
    call_name: str,
    system: str = "",
    timeout: float = 30.0,
    template: ChatPromptTemplate | None = None,
    template_vars: dict[str, Any] | None = None,
) -> T | None:
    """
    Make a schema-validated LLM call and return a typed Pydantic instance.

    Accepts either:
      • template + template_vars  — LangChain ChatPromptTemplate (preferred)
      • system + prompt strings   — legacy plain-string interface (still supported)

    Returns None only if all retry attempts fail. Caller persists an error observation.
    """
    cache_key = (tick_id, deal_id, call_name)
    if cache_key in _CALL_CACHE:
        return _CALL_CACHE[cache_key]  # type: ignore[return-value]

    # Build message list
    if template is not None and template_vars is not None:
        messages: list[BaseMessage] = template.format_messages(**template_vars)
    else:
        messages = []
        if system:
            messages.append(SystemMessage(content=system))
        messages.append(HumanMessage(content=prompt))

    result = _call_with_retry(
        messages=messages,
        output_model=output_model,
        model=model,
        timeout=timeout,
        call_name=call_name,
        tick_id=tick_id,
        deal_id=deal_id,
    )

    if result is not None:
        _CALL_CACHE[cache_key] = result
    return result


def _call_with_retry(
    *,
    messages: list[BaseMessage],
    output_model: Type[T],
    model: str,
    timeout: float,
    call_name: str,
    tick_id: str,
    deal_id: str,
) -> T | None:
    logger = log_module.get_logger()
    llm = _get_llm(model, timeout)
    structured_llm = llm.with_structured_output(output_model, method="json_schema", strict=True)
    last_error: Exception | None = None

    lf_handler = _langfuse_handler(call_name, tick_id, deal_id)
    invoke_cfg = {"callbacks": [lf_handler]} if lf_handler else {}

    for attempt in range(1, _MAX_RETRIES + 1):
        t0 = time.monotonic()
        try:
            result = structured_llm.invoke(messages, config=invoke_cfg)
            latency_ms = int((time.monotonic() - t0) * 1000)

            logger.info(
                "llm.call.completed",
                call_name=call_name,
                model=model,
                latency_ms=latency_ms,
                tick_id=tick_id,
                deal_id=deal_id,
                attempt=attempt,
                parse_ok=True,
            )
            return result  # type: ignore[return-value]

        except (ValidationError, OutputParserException) as ve:
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
            if attempt == 1:
                # One corrective reprompt: append validation error as human message
                correction = HumanMessage(
                    content=(
                        f"Your previous response failed schema validation:\n{ve}\n\n"
                        "Correct the output and respond again using the same tool schema. "
                        "Every required field must be present and correctly typed."
                    )
                )
                messages = list(messages) + [correction]
                continue
            logger.error(
                "llm.call.parse_failure_final",
                call_name=call_name,
                model=model,
                tick_id=tick_id,
                deal_id=deal_id,
                error=str(ve),
            )
            return None

        except Exception as e:
            # Distinguish non-retryable errors (bad request / auth) from transient ones
            err_str = str(e).lower()
            if any(term in err_str for term in ("bad request", "invalid", "401", "403")):
                logger.error(
                    "llm.call.bad_request",
                    call_name=call_name,
                    model=model,
                    tick_id=tick_id,
                    deal_id=deal_id,
                    error=str(e),
                )
                return None

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
