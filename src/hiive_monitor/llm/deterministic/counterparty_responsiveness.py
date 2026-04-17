"""
Deterministic counterparty non-responsiveness evaluator (FR-004, Dimension #5).

Pure Python — no LLM call. Compares days_since_last_comm against the issuer's
typical_response_days baseline to produce a RiskSignal.
"""

from __future__ import annotations

from hiive_monitor.models.risk import RiskDimension, RiskSignal
from hiive_monitor.models.snapshot import DealSnapshot


def evaluate_counterparty_responsiveness(
    snapshot: DealSnapshot,
    issuer_typical_response_days: int,
) -> RiskSignal:
    """
    Returns a triggered RiskSignal if days_since_last_comm exceeds the issuer
    typical_response_days threshold (×1.5 buffer applied).
    """
    if snapshot.days_since_last_comm is None:
        return RiskSignal(
            dimension=RiskDimension.COUNTERPARTY_NONRESPONSIVENESS,
            triggered=False,
            evidence="No communication history available to assess responsiveness.",
            confidence=0.3,
        )

    threshold = max(issuer_typical_response_days, 3)  # floor at 3 days
    silence = snapshot.days_since_last_comm
    ratio = silence / threshold

    if ratio >= 1.5:
        return RiskSignal(
            dimension=RiskDimension.COUNTERPARTY_NONRESPONSIVENESS,
            triggered=True,
            evidence=(
                f"{snapshot.issuer_name} typically responds within {threshold} days; "
                f"{snapshot.deal_id} has had no communication for {silence} days "
                f"({ratio:.1f}× threshold). Responsible party: {snapshot.responsible_party}."
            ),
            confidence=min(0.95, 0.5 + (ratio - 1.5) * 0.3),
        )
    else:
        return RiskSignal(
            dimension=RiskDimension.COUNTERPARTY_NONRESPONSIVENESS,
            triggered=False,
            evidence=(
                f"Communication gap of {silence} days is within {threshold}-day "
                f"response window for {snapshot.issuer_name} ({ratio:.1f}× threshold)."
            ),
            confidence=0.8,
        )
