"""Deal snapshot and supporting value objects passed into the Investigator graph."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from hiive_monitor.models.stages import Stage


class Blocker(BaseModel):
    kind: Literal["missing_doc", "pending_signature", "awaiting_response", "other"]
    description: str
    since: datetime


class EventRef(BaseModel):
    event_type: str
    occurred_at: datetime
    summary: str


class DealSnapshot(BaseModel):
    """Frozen read of a deal's state at observation time. Passed into the Investigator graph."""

    deal_id: str
    issuer_id: str
    issuer_name: str
    stage: Stage
    stage_entered_at: datetime
    days_in_stage: int
    rofr_deadline: datetime | None
    days_to_rofr: int | None
    responsible_party: Literal["buyer", "seller", "issuer", "hiive_ts"]
    blockers: list[Blocker]
    risk_factors: dict[str, Any]
    recent_events: list[EventRef]
    days_since_last_comm: int | None
    missing_documents: list[str] = []  # TS06: required docs not yet received (empty if feature off)
    shares: int | None = None
    price_per_share: float | None = None


class AttentionScoreWithSuppression(BaseModel):
    """Raw and suppressed attention score for a deal in a single tick."""

    deal_id: str
    raw_score: float
    suppressed_score: float
    suppression_applied: bool
