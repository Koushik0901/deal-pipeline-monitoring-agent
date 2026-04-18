"""
Deterministic health scoring for the Pipeline (book-of-deals) view.

The agent's LLM-driven severity gate only surfaces deals above a confidence
threshold. The Pipeline page needs to show EVERY active deal, including items
that are merely drifting. We therefore replicate the numeric rules from
`llm/prompts/severity.py` as pure Python — no LLM call — so every row has a
health tier computed from the same thresholds the agent uses.

Tiers returned (aligned with severity.Severity for continuity):
  - "escalate"     — red    — matches agent ESCALATE rules 1-3
  - "act"          — amber  — matches agent ACT rules 4-5
  - "watch"        — blue   — matches agent WATCH rules 6
  - "informational"— green  — everything else (healthy / baseline)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _days_between(later: datetime, earlier: datetime) -> int:
    return (later - earlier).days


def compute_signals(
    deal: dict[str, Any],
    issuer: dict[str, Any],
    last_inbound_at: datetime | None,
    now: datetime,
) -> dict[str, Any]:
    """Compute the numeric signals feeding the health tier."""
    stage_entered = datetime.fromisoformat(deal["stage_entered_at"])
    days_in_stage = max(0, _days_between(now, stage_entered))

    typical = issuer.get("typical_response_days") or 5
    stage_aging_ratio = days_in_stage / typical if typical else 0.0

    days_to_rofr: int | None = None
    if deal.get("rofr_deadline"):
        rofr_dl = datetime.fromisoformat(deal["rofr_deadline"])
        days_to_rofr = _days_between(rofr_dl, now)

    days_since_last_inbound: int | None = None
    if last_inbound_at is not None:
        days_since_last_inbound = max(0, _days_between(now, last_inbound_at))

    risk_factors = deal.get("risk_factors_parsed") or {}
    prior_breakage = int(risk_factors.get("prior_breakage_count") or 0)
    is_first_time_buyer = bool(risk_factors.get("is_first_time_buyer"))
    multi_layer_rofr = bool(risk_factors.get("multi_layer_rofr"))

    blockers = deal.get("blockers_parsed") or []
    blocker_count = len(blockers) if isinstance(blockers, list) else 0

    return {
        "days_in_stage": days_in_stage,
        "typical_response_days": typical,
        "stage_aging_ratio": round(stage_aging_ratio, 2),
        "days_to_rofr": days_to_rofr,
        "days_since_last_inbound": days_since_last_inbound,
        "prior_breakage_count": prior_breakage,
        "is_first_time_buyer": is_first_time_buyer,
        "multi_layer_rofr": multi_layer_rofr,
        "blocker_count": blocker_count,
    }


def compute_health(signals: dict[str, Any]) -> dict[str, Any]:
    """
    Apply the same decision tree as `llm/prompts/severity.py` to numeric signals.
    Returns {"tier", "score" (0-1), "reasons": [str, ...]}.
    """
    reasons: list[str] = []
    triggered_dims = 0

    ratio = signals["stage_aging_ratio"]
    typical = signals["typical_response_days"]
    silence = signals["days_since_last_inbound"]
    deadline = signals["days_to_rofr"]
    blockers = signals["blocker_count"]
    prior_breakage = signals["prior_breakage_count"]
    first_time = signals["is_first_time_buyer"]
    multi_layer = signals["multi_layer_rofr"]

    # Dimension triggers (match severity.py thresholds)
    stage_aging_triggered = ratio >= 1.5
    stage_aging_act_level = ratio >= 2.0
    if stage_aging_triggered:
        triggered_dims += 1
        reasons.append(f"stage aging {ratio}\u00d7 baseline ({typical}d norm)")

    silence_triggered = silence is not None and silence >= 2 * typical
    if silence_triggered:
        triggered_dims += 1
        reasons.append(f"{silence}d since last inbound (\u22652\u00d7 {typical}d norm)")

    deadline_short = deadline is not None and deadline <= 2
    deadline_soon = deadline is not None and deadline <= 10
    deadline_expired = deadline is not None and deadline < 0
    if deadline_expired:
        triggered_dims += 1
        reasons.append(f"ROFR expired {abs(deadline)}d ago")
    elif deadline_soon:
        triggered_dims += 1
        reasons.append(f"ROFR in {deadline}d")

    blockers_triggered = blockers > 0
    if blockers_triggered:
        triggered_dims += 1
        reasons.append(f"{blockers} blocker{'s' if blockers != 1 else ''}")

    unusual_triggered = first_time or prior_breakage >= 1 or multi_layer
    if unusual_triggered:
        triggered_dims += 1
        parts = []
        if first_time:
            parts.append("first-time buyer")
        if prior_breakage >= 1:
            parts.append(f"{prior_breakage} prior breakage{'s' if prior_breakage != 1 else ''}")
        if multi_layer:
            parts.append("multi-layer ROFR")
        reasons.append(", ".join(parts))

    # ── ESCALATE rules (severity.py rules 1-3) ───────────────────────────────
    if deadline_short or deadline_expired:
        return {"tier": "escalate", "score": 1.0, "reasons": reasons}
    if prior_breakage >= 1 and stage_aging_act_level:
        return {"tier": "escalate", "score": 0.95, "reasons": reasons}
    if deadline_soon and silence_triggered:
        return {"tier": "escalate", "score": 0.9, "reasons": reasons}

    # ── ACT rules (severity.py rules 4-5) ────────────────────────────────────
    if deadline_soon and triggered_dims >= 1:
        return {"tier": "act", "score": 0.75, "reasons": reasons}
    if stage_aging_act_level:
        return {"tier": "act", "score": 0.7, "reasons": reasons}
    if silence_triggered and triggered_dims >= 2:
        return {"tier": "act", "score": 0.65, "reasons": reasons}

    # ── WATCH (severity.py rule 6) ───────────────────────────────────────────
    if triggered_dims >= 1:
        return {"tier": "watch", "score": 0.4, "reasons": reasons}

    # ── INFORMATIONAL (baseline healthy) ─────────────────────────────────────
    return {"tier": "informational", "score": 0.05, "reasons": reasons or ["no triggers"]}
