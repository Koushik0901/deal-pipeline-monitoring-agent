"""
deepeval OpenRouter judge adapter (TEV05).

Wraps ChatOpenRouter as a DeepEvalBaseLLM so all deepeval metrics use
the same OpenRouter-routed judge model (EVAL_JUDGE_MODEL env var) without
requiring an OpenAI API key.
"""

from __future__ import annotations

import os
from typing import Any

from deepeval.models import DeepEvalBaseLLM
from langchain_openrouter import ChatOpenRouter


class OpenRouterJudge(DeepEvalBaseLLM):
    """deepeval-compatible judge backed by ChatOpenRouter."""

    def __init__(self) -> None:
        model_name = os.environ.get("EVAL_JUDGE_MODEL", "qwen/qwen3-plus")
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

    def generate(self, prompt: str, **kwargs: Any) -> str:
        from langchain_core.messages import HumanMessage
        response = self._llm.invoke([HumanMessage(content=prompt)])
        return response.content  # type: ignore[return-value]

    async def a_generate(self, prompt: str, **kwargs: Any) -> str:
        from langchain_core.messages import HumanMessage
        response = await self._llm.ainvoke([HumanMessage(content=prompt)])
        return response.content  # type: ignore[return-value]
