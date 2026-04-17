"""Event model and EventType literals (FR-LOOP-03)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

EventType = Literal[
    "stage_transition",
    "doc_received",
    "doc_requested",
    "comm_outbound",
    "comm_inbound",
    "comm_sent_agent_recommended",
]


class Event(BaseModel):
    """Domain event appended to the events log. Never deleted or updated."""

    event_id: str
    deal_id: str
    event_type: EventType
    occurred_at: datetime  # simulated-clock time (FR-019)
    created_at: datetime   # real wall-clock time (FR-019)
    summary: str = ""
    payload: dict[str, Any] = {}
