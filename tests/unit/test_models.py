"""Round-trip serialization tests for every Pydantic contract model (T024)."""

from __future__ import annotations

from datetime import datetime, timezone

from hiive_monitor.models.brief import DailyBrief, DailyBriefItem
from hiive_monitor.models.event import Event
from hiive_monitor.models.interventions import BriefEntry, InternalEscalation, Intervention, OutboundNudge
from hiive_monitor.models.risk import (
    AttentionScore,
    RiskDimension,
    RiskSignal,
    Severity,
    SeverityDecision,
    SufficiencyDecision,
)
from hiive_monitor.models.snapshot import Blocker, DealSnapshot, EventRef
from hiive_monitor.models.stages import DWELL_BASELINES, LIVE_STAGES, Stage


def roundtrip(model_instance):
    """Serialize and re-parse a Pydantic model instance."""
    cls = type(model_instance)
    return cls.model_validate(model_instance.model_dump())


def test_stage_enum_values():
    assert Stage.ROFR_PENDING.value == "rofr_pending"
    assert Stage.SETTLED in Stage


def test_dwell_baselines_cover_live_stages():
    for stage in LIVE_STAGES:
        assert DWELL_BASELINES[stage] > 0, f"Missing baseline for {stage}"


def test_risk_signal_roundtrip():
    signal = RiskSignal(
        dimension=RiskDimension.STAGE_AGING,
        triggered=True,
        evidence="Deal has been in rofr_pending for 35 days vs 20-day baseline.",
        confidence=0.9,
    )
    assert roundtrip(signal) == signal


def test_attention_score_roundtrip():
    score = AttentionScore(deal_id="D-001", score=0.85, reason="ROFR deadline in 3 days.")
    assert roundtrip(score) == score


def test_severity_decision_roundtrip():
    sd = SeverityDecision(
        severity=Severity.ACT,
        reasoning="Deadline within 5 days with no recent comm.",
        primary_dimensions=[RiskDimension.DEADLINE_PROXIMITY, RiskDimension.COMMUNICATION_SILENCE],
    )
    assert roundtrip(sd) == sd


def test_sufficiency_decision_roundtrip():
    sd = SufficiencyDecision(sufficient=False, rationale="Need comm history.", tool_to_call="fetch_communication_content")
    assert roundtrip(sd) == sd


def test_outbound_nudge_roundtrip():
    nudge = OutboundNudge(
        recipient_type="buyer",
        recipient_name="Acme Ventures",
        subject="Transfer docs needed — SpaceX deal",
        body="Hi, we need the signed transfer docs for your SpaceX deal by Friday.",
        referenced_deadline="2026-04-20",
    )
    assert roundtrip(nudge) == nudge


def test_internal_escalation_roundtrip():
    esc = InternalEscalation(
        escalate_to="ts_lead",
        headline="SpaceX ROFR expiring in 2 days",
        body="Deal D-042 has not cleared ROFR with 2 days left.",
        suggested_next_step="Contact issuer counsel directly.",
    )
    assert roundtrip(esc) == esc


def test_brief_entry_roundtrip():
    entry = BriefEntry(
        headline="Stripe deal stalled at signing",
        one_line_summary="Signing docs pending for 8 days vs 4-day baseline.",
        recommended_action="Send follow-up to seller counsel.",
    )
    assert roundtrip(entry) == entry


def test_intervention_outbound_roundtrip():
    nudge = OutboundNudge(
        recipient_type="issuer",
        recipient_name="SpaceX Legal",
        subject="ROFR status check",
        body="Please confirm ROFR status for transfer D-001.",
        referenced_deadline=None,
    )
    iv = Intervention.outbound(nudge)
    assert roundtrip(iv) == iv
    assert iv.intervention_type == "outbound_nudge"


def test_daily_brief_item_roundtrip():
    item = DailyBriefItem(
        deal_id="D-007",
        rank=1,
        severity=Severity.ESCALATE,
        one_line_summary="ROFR expires in 1 day, no response from issuer.",
        reasoning="Escalate: multi-layer ROFR + 1-day deadline, prior breakage.",
        intervention_id="iv-abc",
    )
    assert roundtrip(item) == item


def test_daily_brief_roundtrip():
    brief = DailyBrief(
        tick_id="t-001",
        generated_at="2026-04-16T09:00:00Z",
        items=[
            DailyBriefItem(
                deal_id="D-001",
                rank=1,
                severity=Severity.ACT,
                one_line_summary="Action needed.",
                reasoning="Deadline in 5 days.",
                intervention_id=None,
            )
        ],
    )
    assert roundtrip(brief) == brief


def test_deal_snapshot_roundtrip():
    now = datetime(2026, 4, 16, 9, 0, tzinfo=timezone.utc)
    snap = DealSnapshot(
        deal_id="D-001",
        issuer_id="spacex",
        issuer_name="SpaceX",
        stage=Stage.ROFR_PENDING,
        stage_entered_at=now,
        days_in_stage=25,
        rofr_deadline=now,
        days_to_rofr=5,
        responsible_party="issuer",
        blockers=[
            Blocker(kind="awaiting_response", description="ROFR confirmation pending", since=now)
        ],
        risk_factors={"is_first_time_buyer": False, "prior_breakage_count": 0},
        recent_events=[
            EventRef(event_type="comm_outbound", occurred_at=now, summary="Sent ROFR notice.")
        ],
        days_since_last_comm=10,
    )
    assert roundtrip(snap) == snap


def test_event_roundtrip():
    now = datetime(2026, 4, 16, 9, 0, tzinfo=timezone.utc)
    ev = Event(
        event_id="ev-001",
        deal_id="D-001",
        event_type="comm_outbound",
        occurred_at=now,
        created_at=now,
        summary="Sent outreach email.",
        payload={"body": "Hi, please confirm."},
    )
    assert roundtrip(ev) == ev


def test_model_json_schema_generation():
    """Confirm all models produce valid JSON schemas (used by LLM client tool-use)."""
    for cls in [
        AttentionScore,
        RiskSignal,
        SeverityDecision,
        SufficiencyDecision,
        OutboundNudge,
        InternalEscalation,
        BriefEntry,
        Intervention,
        DailyBrief,
        DealSnapshot,
        Event,
    ]:
        schema = cls.model_json_schema()
        assert "properties" in schema or "anyOf" in schema, f"No properties in schema for {cls}"
