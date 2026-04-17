"""Post-parse validator ensuring LLM risk-signal evidence references the specific deal."""

from __future__ import annotations

import re

from hiive_monitor.models.risk import RiskSignal
from hiive_monitor.models.snapshot import DealSnapshot

_BLOCKER_FRAGMENT_LEN = 30
_COUNTERPARTY_ROLES = frozenset({"buyer", "seller", "issuer", "assignee", "counterparty"})
_RE_DAY_COUNT = re.compile(r"\b\d+\s*day")
_RE_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def assert_evidence_references_deal(signal: RiskSignal, snapshot: DealSnapshot) -> bool:
    """Return True if evidence contains at least one deal-specific token."""
    ev = signal.evidence.lower()

    if snapshot.issuer_name.lower() in ev:
        return True

    if snapshot.stage.value.lower() in ev:
        return True

    if _RE_DAY_COUNT.search(ev):
        return True

    if _RE_DATE.search(ev):
        return True

    if any(role in ev for role in _COUNTERPARTY_ROLES):
        return True

    for blocker in snapshot.blockers:
        fragment = blocker.description[:_BLOCKER_FRAGMENT_LEN].lower()
        if fragment and fragment in ev:
            return True

    return False
