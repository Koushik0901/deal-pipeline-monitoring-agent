"""Intervention output models (FR-007, FR-008, FR-009)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class OutboundNudge(BaseModel):
    """Outbound message draft to buyer, seller, or issuer."""

    recipient_type: Literal["buyer", "seller", "issuer"]
    recipient_name: str
    subject: str = Field(max_length=120)
    body: str = Field(max_length=1200)
    referenced_deadline: str | None = None  # ISO8601 date when deadline-driven


class InternalEscalation(BaseModel):
    """Internal escalation note to TS lead, legal, or ops."""

    escalate_to: Literal["ts_lead", "legal", "ops"]
    headline: str = Field(max_length=120)
    body: str = Field(max_length=800)
    suggested_next_step: str = Field(max_length=200)


class BriefEntry(BaseModel):
    """Brief-only entry for watch-level items needing awareness, not outreach."""

    headline: str = Field(max_length=100)
    one_line_summary: str = Field(max_length=160)
    recommended_action: str = Field(max_length=200)


class StatusRecommendation(BaseModel):
    """Internal recommendation for what the TS analyst should proactively do to advance the deal."""

    headline: str = Field(max_length=100)
    current_status_summary: str = Field(max_length=200)
    recommended_actions: list[str] = Field(min_length=1, max_length=5, description="1–3 concrete next steps")
    priority: Literal["low", "medium", "high"]


InterventionPayload = Annotated[
    OutboundNudge | InternalEscalation | BriefEntry | StatusRecommendation,
    Field(discriminator=None),
]


class Intervention(BaseModel):
    """Discriminated union wrapping any intervention type."""

    intervention_type: Literal["outbound_nudge", "internal_escalation", "brief_entry", "status_recommendation"]
    payload: OutboundNudge | InternalEscalation | BriefEntry | StatusRecommendation

    @classmethod
    def outbound(cls, payload: OutboundNudge) -> Intervention:
        return cls(intervention_type="outbound_nudge", payload=payload)

    @classmethod
    def escalation(cls, payload: InternalEscalation) -> Intervention:
        return cls(intervention_type="internal_escalation", payload=payload)

    @classmethod
    def brief(cls, payload: BriefEntry) -> Intervention:
        return cls(intervention_type="brief_entry", payload=payload)

    @classmethod
    def status_recommendation(cls, payload: StatusRecommendation) -> Intervention:
        return cls(intervention_type="status_recommendation", payload=payload)
