"""Deal stage definitions and dwell-time baselines (FR-004)."""

from __future__ import annotations

from enum import StrEnum


class Stage(StrEnum):
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

# Documents required from the buyer/seller before each stage can progress.
# Values are canonical filenames; `documents_received` on the deal row is a JSON list of these.
REQUIRED_DOCUMENTS_BY_STAGE: dict[Stage, list[str]] = {
    Stage.BID_ACCEPTED: ["bid_confirmation.pdf"],
    Stage.DOCS_PENDING: [
        "nda_signed.pdf",
        "buyer_accreditation_letter.pdf",
        "transfer_agreement_draft.pdf",
    ],
    Stage.ISSUER_NOTIFIED: ["issuer_notification_receipt.pdf"],
    Stage.ROFR_PENDING: ["rofr_waiver_request.pdf"],
    Stage.ROFR_CLEARED: ["rofr_clearance_confirmation.pdf"],
    Stage.SIGNING: ["transfer_agreement_signed.pdf", "seller_release_form.pdf"],
    Stage.FUNDING: ["wire_confirmation.pdf"],
    Stage.SETTLED: [],
    Stage.BROKEN: [],
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
