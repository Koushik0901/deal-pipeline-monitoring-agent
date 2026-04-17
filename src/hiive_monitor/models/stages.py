"""Deal stage definitions and dwell-time baselines (FR-004)."""

from __future__ import annotations

from enum import Enum


class Stage(str, Enum):
    BID_ACCEPTED = "bid_accepted"
    DOCS_PENDING = "docs_pending"
    ISSUER_NOTIFIED = "issuer_notified"
    ROFR_PENDING = "rofr_pending"
    ROFR_CLEARED = "rofr_cleared"
    SIGNING = "signing"
    FUNDING = "funding"
    SETTLED = "settled"
    BROKEN = "broken"


# Expected dwell-time baselines in calendar days (docs/assumptions.md §Stage Dwell-Time Baselines).
# Aging is computed as days_in_stage / baseline — values >1.0 indicate a stall.
DWELL_BASELINES: dict[Stage, int] = {
    Stage.BID_ACCEPTED: 1,
    Stage.DOCS_PENDING: 3,
    Stage.ISSUER_NOTIFIED: 2,
    Stage.ROFR_PENDING: 20,
    Stage.ROFR_CLEARED: 2,
    Stage.SIGNING: 4,
    Stage.FUNDING: 3,
    # Terminal stages have no meaningful baseline.
    Stage.SETTLED: 0,
    Stage.BROKEN: 0,
}

# Stages where the monitor should actively track progress.
LIVE_STAGES: frozenset[Stage] = frozenset(
    Stage(s)
    for s in (
        "bid_accepted",
        "docs_pending",
        "issuer_notified",
        "rofr_pending",
        "rofr_cleared",
        "signing",
        "funding",
    )
)
