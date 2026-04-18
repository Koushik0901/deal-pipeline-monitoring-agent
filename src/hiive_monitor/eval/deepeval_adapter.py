"""
deepeval OpenRouter judge adapter (TEV05).

Wraps ChatOpenRouter as a DeepEvalBaseLLM so all deepeval metrics use
the same OpenRouter-routed judge model (EVAL_JUDGE_MODEL env var) without
requiring an OpenAI API key.

Each generate/a_generate call emits a Langfuse `generation` observation
nested under the currently active trace context (the deepeval scenario
span) so judge prompts, outputs, tokens, and latency are visible.
"""

from __future__ import annotations

import os
import time
from typing import Any

from deepeval.models import DeepEvalBaseLLM
from langchain_openrouter import ChatOpenRouter


def _langfuse_enabled() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY"))


def _cost_details(model: str, usage: dict | None) -> dict | None:
    """Estimate USD cost from token counts via the project pricing table."""
    if not usage:
        return None
    try:
        from hiive_monitor.llm.pricing import estimate_cost_usd
        input_tokens = int(usage.get("input") or 0)
        output_tokens = int(usage.get("output") or 0)
        if not (input_tokens or output_tokens):
            return None
        cost_usd, _fallback = estimate_cost_usd(model, input_tokens, output_tokens)
        denom = max(input_tokens + output_tokens, 1)
        return {
            "input": round(cost_usd * input_tokens / denom, 6),
            "output": round(cost_usd * output_tokens / denom, 6),
            "total": round(cost_usd, 6),
        }
    except Exception:
        return None


def _response_metadata(response: Any, latency_ms: int | None) -> dict:
    """Pull finish_reason, system_fingerprint, cache info, latency from a LangChain response."""
    out: dict = {}
    try:
        meta = getattr(response, "response_metadata", {}) or {}
        for key in ("finish_reason", "model_name", "model", "system_fingerprint"):
            if meta.get(key) is not None:
                out[key] = meta[key]
        usage = meta.get("token_usage") or getattr(response, "usage_metadata", None) or {}
        cache_read = (usage.get("cache_read_input_tokens")
                      or usage.get("prompt_tokens_details", {}).get("cached_tokens")
                      if isinstance(usage, dict) else None)
        if cache_read:
            out["cache_read_input_tokens"] = cache_read
    except Exception:
        pass
    if latency_ms is not None:
        out["latency_ms"] = latency_ms
    return out


def _usage_from_response(response: Any) -> dict | None:
    """Extract token usage from a LangChain AIMessage if present."""
    try:
        meta = getattr(response, "response_metadata", {}) or {}
        usage = meta.get("token_usage") or getattr(response, "usage_metadata", None)
        if not usage:
            return None
        input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (input_tokens + output_tokens))
        if not (input_tokens or output_tokens):
            return None
        return {
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
        }
    except Exception:
        return None


class OpenRouterJudge(DeepEvalBaseLLM):
    """deepeval-compatible judge backed by ChatOpenRouter."""

    def __init__(self) -> None:
        model_name = os.environ.get("EVAL_JUDGE_MODEL", "google/gemini-3.1-pro-preview:exacto")
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        self._llm = ChatOpenRouter(
            model=model_name,
            openrouter_api_key=api_key,
            temperature=0,
            max_retries=2,
        )
        self._model_name = model_name

    def load_model(self) -> ChatOpenRouter:
        return self._llm

    def get_model_name(self) -> str:
        return self._model_name

    def _invoke_with_trace(self, prompt: str, invoke_fn) -> str:
        from langchain_core.messages import HumanMessage

        if not _langfuse_enabled():
            response = invoke_fn([HumanMessage(content=prompt)])
            return response.content  # type: ignore[return-value]

        try:
            from langfuse import get_client
            lf = get_client()
        except Exception:
            response = invoke_fn([HumanMessage(content=prompt)])
            return response.content  # type: ignore[return-value]

        model_parameters = {
            "temperature": getattr(self._llm, "temperature", None),
            "max_retries": getattr(self._llm, "max_retries", None),
        }
        model_parameters = {k: v for k, v in model_parameters.items() if v is not None}
        with lf.start_as_current_observation(
            name="judge-llm",
            as_type="generation",
            model=self._model_name,
            model_parameters=model_parameters or None,
            input=[{"role": "user", "content": prompt}],
            metadata={"component": "deepeval-judge"},
        ) as gen:
            start_ts = time.time()
            try:
                response = invoke_fn([HumanMessage(content=prompt)])
                content = response.content
                latency_ms = int((time.time() - start_ts) * 1000)
                usage = _usage_from_response(response)
                cost_details = _cost_details(self._model_name, usage)
                resp_meta = _response_metadata(response, latency_ms)
                try:
                    gen.update(
                        output=content,
                        usage_details=usage or {},
                        cost_details=cost_details,
                        metadata=resp_meta or None,
                    )
                except Exception:
                    pass
                return content  # type: ignore[return-value]
            except Exception as exc:
                try:
                    gen.update(
                        output={"error": type(exc).__name__, "message": str(exc)},
                        level="ERROR",
                        status_message=f"{type(exc).__name__}: {exc}",
                    )
                except Exception:
                    pass
                raise

    def generate(self, prompt: str, **kwargs: Any) -> str:
        return self._invoke_with_trace(prompt, self._llm.invoke)

    async def a_generate(self, prompt: str, **kwargs: Any) -> str:
        from langchain_core.messages import HumanMessage

        if not _langfuse_enabled():
            response = await self._llm.ainvoke([HumanMessage(content=prompt)])
            return response.content  # type: ignore[return-value]

        try:
            from langfuse import get_client
            lf = get_client()
        except Exception:
            response = await self._llm.ainvoke([HumanMessage(content=prompt)])
            return response.content  # type: ignore[return-value]

        model_parameters = {
            "temperature": getattr(self._llm, "temperature", None),
            "max_retries": getattr(self._llm, "max_retries", None),
        }
        model_parameters = {k: v for k, v in model_parameters.items() if v is not None}
        with lf.start_as_current_observation(
            name="judge-llm",
            as_type="generation",
            model=self._model_name,
            model_parameters=model_parameters or None,
            input=[{"role": "user", "content": prompt}],
            metadata={"component": "deepeval-judge"},
        ) as gen:
            start_ts = time.time()
            try:
                response = await self._llm.ainvoke([HumanMessage(content=prompt)])
                content = response.content
                latency_ms = int((time.time() - start_ts) * 1000)
                usage = _usage_from_response(response)
                cost_details = _cost_details(self._model_name, usage)
                resp_meta = _response_metadata(response, latency_ms)
                try:
                    gen.update(
                        output=content,
                        usage_details=usage or {},
                        cost_details=cost_details,
                        metadata=resp_meta or None,
                    )
                except Exception:
                    pass
                return content  # type: ignore[return-value]
            except Exception as exc:
                try:
                    gen.update(
                        output={"error": type(exc).__name__, "message": str(exc)},
                        level="ERROR",
                        status_message=f"{type(exc).__name__}: {exc}",
                    )
                except Exception:
                    pass
                raise
