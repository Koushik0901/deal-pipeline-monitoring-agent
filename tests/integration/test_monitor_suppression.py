"""Integration tests for Monitor suppression logic (FR-LOOP-02, T036c)."""

from __future__ import annotations

import uuid

import pytest

from tests.integration.conftest import seed_deal, seed_event, seed_issuer


def _make_monitor_state(tick_id: str, deal_id: str, raw_score: float) -> dict:
    from hiive_monitor.agents.graph_state import MonitorState
    return {
        "tick_id": tick_id,
        "mode": "simulated",
        "tick_started_at": "2026-01-01T00:00:00+00:00",
        "live_deals": [],
        "candidate_deals": [deal_id],
        "attention_scores": {deal_id: raw_score},
        "suppressed_scores": {},
        "investigation_queue": [],
        "investigator_results": [],
        "errors": [],
    }


def _insert_completed_tick(conn, minutes_ago: int = 2) -> str:
    tid = str(uuid.uuid4())
    conn.execute(
        f"""INSERT INTO ticks (tick_id, mode, tick_started_at, tick_completed_at)
            VALUES (?, 'simulated',
                    datetime('now', '-{minutes_ago} minutes'),
                    datetime('now', '-{minutes_ago + 1} minutes'))""",
        (tid,),
    )
    conn.commit()
    return tid


def test_suppression_discounts_score_to_0_16(db_path, monkeypatch):
    """
    Deal with comm_sent_agent_recommended within suppression window gets score
    discounted by SUPPRESSION_MULTIPLIER (0.2). 0.8 * 0.2 = 0.16. (FR-LOOP-02)
    """
    path, conn = db_path
    deal_id = "D-SUP-001"
    seed_issuer(conn)
    seed_deal(conn, deal_id=deal_id)

    # Insert a completed tick so the suppression window query finds it
    _insert_completed_tick(conn, minutes_ago=2)

    # Insert comm_sent_agent_recommended event within suppression window
    seed_event(conn, deal_id, "comm_sent_agent_recommended", minutes_ago=1)

    from hiive_monitor.agents.monitor import apply_suppression
    state = _make_monitor_state("new-tick-id", deal_id, raw_score=0.8)
    result = apply_suppression(state)

    assert result["suppressed_scores"][deal_id] == pytest.approx(0.16, abs=1e-9)


def test_suppression_does_not_affect_unrelated_deals(db_path):
    """
    A deal without recent agent-recommended comms keeps its full raw score.
    """
    path, conn = db_path
    deal_id = "D-SUP-002"
    seed_issuer(conn)
    seed_deal(conn, deal_id=deal_id)
    _insert_completed_tick(conn)

    from hiive_monitor.agents.monitor import apply_suppression
    state = _make_monitor_state("tick-999", deal_id, raw_score=0.8)
    result = apply_suppression(state)

    assert result["suppressed_scores"][deal_id] == pytest.approx(0.8)


def test_suppression_does_not_appear_in_investigation_queue(db_path):
    """
    Suppressed deal score (0.16) is below threshold (0.6) and must not
    appear in investigation_queue. (FR-LOOP-02)
    """
    path, conn = db_path
    deal_id = "D-SUP-003"
    seed_issuer(conn)
    seed_deal(conn, deal_id=deal_id)
    _insert_completed_tick(conn)
    seed_event(conn, deal_id, "comm_sent_agent_recommended", minutes_ago=1)

    from hiive_monitor.agents.monitor import apply_suppression, select_investigation_queue
    state = _make_monitor_state("tick-abc", deal_id, raw_score=0.8)
    after_suppression = {**state, **apply_suppression(state)}
    queue_result = select_investigation_queue(after_suppression)

    assert deal_id not in queue_result["investigation_queue"]


def test_suppression_expires_after_window(db_path):
    """
    After suppression_ticks completed ticks, the deal is no longer suppressed.
    Default suppression_ticks=3 → if we have 4 ticks all older than the event,
    the event falls outside the window and no suppression applies.
    """
    path, conn = db_path
    deal_id = "D-SUP-004"
    seed_issuer(conn)
    seed_deal(conn, deal_id=deal_id)

    # Insert 4 completed ticks all after the event (event is too old)
    seed_event(conn, deal_id, "comm_sent_agent_recommended", minutes_ago=60)

    for i in range(4):
        tick_id = str(uuid.uuid4())
        conn.execute(
            f"""INSERT INTO ticks (tick_id, mode, tick_started_at, tick_completed_at)
                VALUES (?, 'simulated',
                        datetime('now', '-{3 - i} minutes'),
                        datetime('now', '-{2 - i} minutes'))""",
            (tick_id,),
        )
    conn.commit()

    from hiive_monitor.agents.monitor import apply_suppression
    state = _make_monitor_state("tick-new", deal_id, raw_score=0.8)
    result = apply_suppression(state)

    # The event is outside the 3-tick window; score should be unchanged
    assert result["suppressed_scores"][deal_id] == pytest.approx(0.8)
