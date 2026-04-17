"""Smoke tests: app boots, key routes respond, debug view, brief actions (T033, T078, T082, T086a, T088, T093)."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile

import pytest
from fastapi.testclient import TestClient

os.environ["CLOCK_MODE"] = "simulated"
os.environ["ANTHROPIC_API_KEY"] = "test-key"

# Use temp files so sqlite3 connections share state within the test session
_tmp_domain = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_ckpt = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DOMAIN_DB_PATH"] = _tmp_domain.name
os.environ["CHECKPOINT_DB_PATH"] = _tmp_ckpt.name
_tmp_domain.close()
_tmp_ckpt.close()


@pytest.fixture(scope="module")
def client():
    from hiive_monitor.app import create_app
    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    # Cleanup temp files
    try:
        os.unlink(_tmp_domain.name)
        os.unlink(_tmp_ckpt.name)
    except OSError:
        pass


def test_root_redirects_to_brief(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/brief"


def test_brief_returns_200(client):
    resp = client.get("/brief")
    assert resp.status_code == 200
    assert b"Daily Brief" in resp.content


def test_queue_returns_200(client):
    resp = client.get("/queue")
    assert resp.status_code == 200
    assert b"Queue" in resp.content


def test_sim_page_returns_200(client):
    resp = client.get("/sim")
    assert resp.status_code == 200
    assert b"Simulation" in resp.content


# ── Debug view (T093, US6) ────────────────────────────────────────────────────


def test_debug_tick_returns_200(client):
    """Debug tick route returns 200 even for an unknown tick_id."""
    resp = client.get("/debug/tick/nonexistent-tick-id-000")
    assert resp.status_code == 200


def test_debug_deal_returns_200(client):
    """Debug deal route returns 200 even for an unknown deal_id."""
    resp = client.get("/debug/deal/D-UNKNOWN")
    assert resp.status_code == 200


def test_brief_debug_param_accepted(client):
    """GET /brief?debug=1 returns 200 (debug flag wired into template)."""
    resp = client.get("/brief?debug=1")
    assert resp.status_code == 200


# ── Brief route actions (T078) ────────────────────────────────────────────────


def test_approve_nonexistent_intervention_returns_404(client):
    """POST approve for unknown intervention returns 404 (T078)."""
    resp = client.post("/interventions/nonexistent-id/approve")
    assert resp.status_code == 404


def test_dismiss_nonexistent_intervention_returns_404(client):
    """POST dismiss for unknown intervention returns 404 (T078)."""
    resp = client.post("/interventions/nonexistent-id/dismiss")
    assert resp.status_code == 404


# ── Sim advance (T082) ────────────────────────────────────────────────────────


def test_sim_advance_returns_200(client):
    """POST /sim/advance with days=1 succeeds in simulated mode (T082)."""
    resp = client.post("/sim/advance", data={"days": 1})
    assert resp.status_code == 200


# ── Per-deal view (T088) ──────────────────────────────────────────────────────


def test_deal_detail_404_for_unknown(client):
    """GET /deals/{id} returns 404 for a deal that doesn't exist (T088)."""
    resp = client.get("/deals/D-DOES-NOT-EXIST")
    assert resp.status_code == 404


# ── comm_sent_agent_recommended timeline indicator (T086a) ───────────────────


def test_deal_timeline_shows_agent_recommendation_badge(client):
    """
    Deal timeline renders comm_sent_agent_recommended events with a distinct
    indicator distinct from ordinary comm_outbound rows (T086a, FR-LOOP-03).
    """
    db_path = os.environ["DOMAIN_DB_PATH"]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Minimal seed: issuer + deal + two events
    now = "2026-04-16T12:00:00+00:00"
    conn.execute(
        "INSERT OR IGNORE INTO issuers (issuer_id, name, typical_response_days, rofr_window_days, multi_layer_rofr, sector, created_at) VALUES (?,?,?,?,?,?,?)",
        ("ISSUER-TEST", "TestCo", 7, 30, 0, "Tech", now),
    )
    conn.execute(
        """INSERT OR IGNORE INTO deals
           (deal_id, issuer_id, buyer_id, seller_id, shares, price_per_share,
            stage, stage_entered_at, responsible_party, blockers, risk_factors, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("D-T086A", "ISSUER-TEST", "B-01", "S-01", 1000, 25.0, "rofr_pending", now, "hiive_ts", "[]", "{}", now, now),
    )
    conn.execute(
        "INSERT OR IGNORE INTO events (event_id, deal_id, event_type, occurred_at, created_at, summary, payload) VALUES (?,?,?,?,?,?,?)",
        ("EVT-OUT-1", "D-T086A", "comm_outbound", now, now, "Sent follow-up", "{}"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO events (event_id, deal_id, event_type, occurred_at, created_at, summary, payload) VALUES (?,?,?,?,?,?,?)",
        ("EVT-AGENT-1", "D-T086A", "comm_sent_agent_recommended", now, now, "Outreach via agent", json.dumps({"intervention_id": "IV-001"})),
    )
    conn.commit()
    conn.close()

    resp = client.get("/deals/D-T086A")
    assert resp.status_code == 200
    html = resp.text
    # The template uses replace('_',' ') on event_type and applies a distinct class
    # to comm_sent_agent_recommended — verify both indicators are present
    assert "comm sent agent recommended" in html
    assert "bg-primary-container" in html  # distinct visual indicator (FR-LOOP-03)
