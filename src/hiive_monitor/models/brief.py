"""Daily brief output models (FR-011, US1)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from hiive_monitor.models.risk import Severity


class DailyBriefItem(BaseModel):
    """Single ranked item in the analyst's daily brief."""

    deal_id: str
    rank: int = Field(ge=1, le=7)
    severity: Severity
    one_line_summary: str = Field(max_length=160)
    reasoning: str = Field(max_length=600)
    intervention_id: str | None = None


class DailyBrief(BaseModel):
    """Full daily brief produced by compose_daily_brief LLM call."""

    tick_id: str
    generated_at: str  # ISO8601
    items: list[DailyBriefItem] = Field(max_length=7)
