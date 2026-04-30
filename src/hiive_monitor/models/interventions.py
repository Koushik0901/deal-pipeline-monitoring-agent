"""Intervention output models (FR-007, FR-008, FR-009)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class OutboundNudge(BaseModel):
    """Outbound message draft to buyer, seller, or issuer."""

    recipient_type: Literal["buyer", "seller", "issuer"]
    recipient_name: str
    subject: str = Field(max_length=120)
    # Cap raised from 1200 → 1600 chars: the new prompt requires 2–3 paragraph breaks for
    # visual rhythm in inboxes (a wall-of-text email gets skimmed and lost). Three paragraphs
    # of 2–3 sentences each comfortably exceed the old 1200 cap; 1600 is a sane email length.
    body: str = Field(max_length=1600)
    referenced_deadline: str | None = None  # ISO8601 date when deadline-driven


class InternalEscalation(BaseModel):
    """Internal escalation note to TS lead, legal, or ops."""

    escalate_to: Literal["ts_lead", "legal", "ops"]
    headline: str = Field(max_length=120)
    # No max_length on body: the five-section labelled format (What's blocked / How long /
    # What we tried / Why this matters / The ask) routinely exceeds static limits, especially
    # when the LLM glosses jargon parenthetically. Per CLAUDE.md guidance: "Fields like
    # evidence, reasoning, rationale, reason must have no max_length — CoT prompting
    # routinely exceeds static limits and raises Pydantic ValidationError at runtime."
    body: str
    # 400-char cap accommodates multi-step asks ("X to do Y by date; if unreachable, escalate
    # to Z by deadline"). 240 was too tight in practice — caused parse failures on legitimate
    # well-formed asks. The UI wraps this text so length isn't a visual concern.
    suggested_next_step: str = Field(max_length=400)


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
