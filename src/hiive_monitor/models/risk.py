"""Risk signal and severity Pydantic models (FR-005, FR-006)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


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
    evidence: str
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

    reasoning: str
    primary_dimensions: list[RiskDimension] = Field(max_length=3)
    severity: Severity

    @model_validator(mode="after")
    def verdict_matches_rationale(self) -> SeverityDecision:
        rationale_lower = self.reasoning.lower()
        severity_name = self.severity.value.lower()

        severity_keywords = {
            "escalate": ["verdict: escalate", "verdict is escalate"],
            "act": ["verdict: act", "verdict is act"],
            "watch": ["verdict: watch", "verdict is watch"],
            "informational": ["verdict: informational", "verdict is informational"],
        }

        last_mentioned = None
        last_position = -1
        for sev, keywords in severity_keywords.items():
            for kw in keywords:
                pos = rationale_lower.rfind(kw)
                if pos > last_position:
                    last_position = pos
                    last_mentioned = sev

        if last_mentioned is None:
            return self

        if last_mentioned != severity_name:
            raise ValueError(
                f"Rationale concludes '{last_mentioned}' but severity "
                f"field is '{severity_name}' — drift detected"
            )
        return self


class AttentionScore(BaseModel):
    """Output schema for the screen_deal Haiku call."""

    deal_id: str
    score: float = Field(ge=0.0, le=1.0)
    reason: str


class SufficiencyDecision(BaseModel):
    """Output schema for N3 assess_sufficiency node (agentic loop control)."""

    sufficient: bool
    rationale: str
    tool_to_call: str | None = None  # one of the four enrichment tool names, or None
