"""Risk signal and severity Pydantic models (FR-005, FR-006)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Severity(StrEnum):
    INFORMATIONAL = "informational"
    WATCH = "watch"
    ACT = "act"
    ESCALATE = "escalate"


class RiskDimension(StrEnum):
    STAGE_AGING = "stage_aging"
    DEADLINE_PROXIMITY = "deadline_proximity"
    COMMUNICATION_SILENCE = "communication_silence"
    MISSING_PREREQUISITES = "missing_prerequisites"
    COUNTERPARTY_NONRESPONSIVENESS = "counterparty_nonresponsiveness"
    UNUSUAL_CHARACTERISTICS = "unusual_characteristics"


class RiskSignal(BaseModel):
    """Output schema for each dimension evaluator call."""

    dimension: RiskDimension
    triggered: bool
    evidence: str = Field(max_length=400)
    confidence: float = Field(ge=0.0, le=1.0)


class AllRiskSignals(BaseModel):
    """Combined output schema for the single all-dimensions risk evaluation call."""

    stage_aging: RiskSignal
    deadline_proximity: RiskSignal
    communication_silence: RiskSignal
    missing_prerequisites: RiskSignal
    unusual_characteristics: RiskSignal


class SeverityDecision(BaseModel):
    """Output schema for the decide_severity LLM call."""

    severity: Severity
    reasoning: str = Field(max_length=600)
    primary_dimensions: list[RiskDimension] = Field(max_length=3)


class AttentionScore(BaseModel):
    """Output schema for the screen_deal Haiku call."""

    deal_id: str
    score: float = Field(ge=0.0, le=1.0)
    reason: str = Field(max_length=240)


class SufficiencyDecision(BaseModel):
    """Output schema for N3 assess_sufficiency node (agentic loop control)."""

    sufficient: bool
    rationale: str = Field(max_length=300)
    tool_to_call: str | None = None  # one of the four enrichment tool names, or None
