"""
Seed data generator — 40 deals across 10 real Hiive-listed issuers.

Engineered for variety (not random): covers the 5 scenarios the agent must detect:
  1. ROFR expiring soon with no issuer response
  2. Extended communication silence with first-time buyer
  3. Stage stall (docs_pending) with unresolved blockers
  4. Multi-layer ROFR with counterparty silence
  5. Prior breakage history + current stall

Issuers: SpaceX, Stripe, Anthropic, Perplexity, Cerebras, Groq, Databricks, Canva,
         Rippling, Ramp (per BUILD_PLAN.md §6.2)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from hiive_monitor.db import dao
from hiive_monitor.db.connection import get_domain_conn
from hiive_monitor.db.init import init_domain_db

# ── Reference data ────────────────────────────────────────────────────────────

ISSUERS = [
    {"issuer_id": "spacex", "name": "SpaceX", "typical_response_days": 7, "rofr_window_days": 30, "multi_layer_rofr": 0, "sector": "aerospace", "created_at": "2026-01-01T00:00:00Z"},
    {"issuer_id": "stripe", "name": "Stripe", "typical_response_days": 5, "rofr_window_days": 30, "multi_layer_rofr": 0, "sector": "fintech", "created_at": "2026-01-01T00:00:00Z"},
    {"issuer_id": "anthropic", "name": "Anthropic", "typical_response_days": 5, "rofr_window_days": 30, "multi_layer_rofr": 1, "sector": "ai", "created_at": "2026-01-01T00:00:00Z"},
    {"issuer_id": "perplexity", "name": "Perplexity AI", "typical_response_days": 3, "rofr_window_days": 21, "multi_layer_rofr": 0, "sector": "ai", "created_at": "2026-01-01T00:00:00Z"},
    {"issuer_id": "cerebras", "name": "Cerebras Systems", "typical_response_days": 10, "rofr_window_days": 30, "multi_layer_rofr": 1, "sector": "ai_hardware", "created_at": "2026-01-01T00:00:00Z"},
    {"issuer_id": "groq", "name": "Groq", "typical_response_days": 7, "rofr_window_days": 30, "multi_layer_rofr": 0, "sector": "ai_hardware", "created_at": "2026-01-01T00:00:00Z"},
    {"issuer_id": "databricks", "name": "Databricks", "typical_response_days": 5, "rofr_window_days": 30, "multi_layer_rofr": 0, "sector": "data", "created_at": "2026-01-01T00:00:00Z"},
    {"issuer_id": "canva", "name": "Canva", "typical_response_days": 5, "rofr_window_days": 21, "multi_layer_rofr": 0, "sector": "design", "created_at": "2026-01-01T00:00:00Z"},
    {"issuer_id": "rippling", "name": "Rippling", "typical_response_days": 4, "rofr_window_days": 30, "multi_layer_rofr": 0, "sector": "hr_tech", "created_at": "2026-01-01T00:00:00Z"},
    {"issuer_id": "ramp", "name": "Ramp", "typical_response_days": 3, "rofr_window_days": 21, "multi_layer_rofr": 0, "sector": "fintech", "created_at": "2026-01-01T00:00:00Z"},
]

PARTIES = [
    {"party_id": "buyer_01", "party_type": "buyer", "display_name": "Andromeda Capital Partners", "is_first_time": 0, "prior_breakage_count": 0, "created_at": "2026-01-01T00:00:00Z"},
    {"party_id": "buyer_02", "party_type": "buyer", "display_name": "Nexus Ventures Fund III", "is_first_time": 0, "prior_breakage_count": 1, "created_at": "2026-01-01T00:00:00Z"},
    {"party_id": "buyer_03", "party_type": "buyer", "display_name": "Kairos Secondary LP", "is_first_time": 0, "prior_breakage_count": 0, "created_at": "2026-01-01T00:00:00Z"},
    {"party_id": "buyer_04", "party_type": "buyer", "display_name": "James Thornton (Individual)", "is_first_time": 1, "prior_breakage_count": 0, "created_at": "2026-01-01T00:00:00Z"},
    {"party_id": "buyer_05", "party_type": "buyer", "display_name": "Meridian Family Office", "is_first_time": 0, "prior_breakage_count": 0, "created_at": "2026-01-01T00:00:00Z"},
    {"party_id": "buyer_06", "party_type": "buyer", "display_name": "Pinnacle Asset Management", "is_first_time": 0, "prior_breakage_count": 2, "created_at": "2026-01-01T00:00:00Z"},
    {"party_id": "seller_01", "party_type": "seller", "display_name": "Sarah Chen", "is_first_time": 0, "prior_breakage_count": 0, "created_at": "2026-01-01T00:00:00Z"},
    {"party_id": "seller_02", "party_type": "seller", "display_name": "Marcus Webb", "is_first_time": 0, "prior_breakage_count": 0, "created_at": "2026-01-01T00:00:00Z"},
    {"party_id": "seller_03", "party_type": "seller", "display_name": "Priya Sharma", "is_first_time": 1, "prior_breakage_count": 0, "created_at": "2026-01-01T00:00:00Z"},
    {"party_id": "seller_04", "party_type": "seller", "display_name": "DataFlow Holdings LLC", "is_first_time": 0, "prior_breakage_count": 0, "created_at": "2026-01-01T00:00:00Z"},
    {"party_id": "seller_05", "party_type": "seller", "display_name": "Alex Kowalski", "is_first_time": 0, "prior_breakage_count": 1, "created_at": "2026-01-01T00:00:00Z"},
    {"party_id": "seller_06", "party_type": "seller", "display_name": "Vertex Employee Trust", "is_first_time": 0, "prior_breakage_count": 0, "created_at": "2026-01-01T00:00:00Z"},
]


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _now_base() -> datetime:
    """Fixed simulation base: 2026-04-16 09:00 UTC."""
    return datetime(2026, 4, 16, 9, 0, tzinfo=timezone.utc)


def _ago(days: float, base: datetime | None = None) -> datetime:
    b = base or _now_base()
    return b - timedelta(days=days)


def _fwd(days: float, base: datetime | None = None) -> datetime:
    b = base or _now_base()
    return b + timedelta(days=days)


def build_deals() -> list[dict]:
    now = _now_base()
    deals = []

    def deal(
        deal_id, issuer_id, buyer_id, seller_id, shares, price,
        stage, stage_entered_days_ago,
        rofr_deadline_days=None,  # positive = future
        responsible_party="hiive_ts",
        blockers=None,
        risk_factors=None,
        created_days_ago=None,
    ) -> dict:
        entered_at = _ago(stage_entered_days_ago)
        rofr_dl = _fwd(rofr_deadline_days) if rofr_deadline_days is not None else None
        created = _ago(created_days_ago or stage_entered_days_ago + 2)
        return {
            "deal_id": deal_id,
            "issuer_id": issuer_id,
            "buyer_id": buyer_id,
            "seller_id": seller_id,
            "shares": shares,
            "price_per_share": price,
            "stage": stage,
            "stage_entered_at": _iso(entered_at),
            "rofr_deadline": _iso(rofr_dl) if rofr_dl else None,
            "responsible_party": responsible_party,
            "blockers": json.dumps(blockers or []),
            "risk_factors": json.dumps(risk_factors or {}),
            "created_at": _iso(created),
            "updated_at": _iso(now),
        }

    # ── SCENARIO 1: ROFR expiring soon, no issuer response (SpaceX) ──────────
    deals.append(deal(
        "D-001", "spacex", "buyer_01", "seller_01", 5000, 185.50,
        stage="rofr_pending", stage_entered_days_ago=22,
        rofr_deadline_days=2,  # CRITICAL: 2 days left
        responsible_party="issuer",
        risk_factors={"is_first_time_buyer": False, "prior_breakage_count": 0, "deal_size_usd": 927500},
    ))

    # SpaceX deal #2 — healthy, for contrast
    deals.append(deal(
        "D-002", "spacex", "buyer_03", "seller_02", 2000, 185.50,
        stage="rofr_cleared", stage_entered_days_ago=1,
        responsible_party="buyer",
        risk_factors={"is_first_time_buyer": False, "prior_breakage_count": 0, "deal_size_usd": 371000},
    ))

    # ── SCENARIO 2: Comm silence + first-time buyer (Stripe) ─────────────────
    deals.append(deal(
        "D-003", "stripe", "buyer_04", "seller_03", 1500, 42.00,
        stage="docs_pending", stage_entered_days_ago=9,  # 3× baseline
        responsible_party="buyer",
        blockers=[{"kind": "missing_doc", "description": "Signed transfer agreement not received", "since": _iso(_ago(7))}],
        risk_factors={"is_first_time_buyer": True, "prior_breakage_count": 0, "deal_size_usd": 63000},
    ))

    # Stripe deal #2 — normal progress
    deals.append(deal(
        "D-004", "stripe", "buyer_05", "seller_04", 8000, 42.00,
        stage="signing", stage_entered_days_ago=2,
        responsible_party="buyer",
        risk_factors={"is_first_time_buyer": False, "prior_breakage_count": 0, "deal_size_usd": 336000},
    ))

    # ── SCENARIO 3: Stage stall with blockers (Anthropic) ────────────────────
    deals.append(deal(
        "D-005", "anthropic", "buyer_02", "seller_01", 3000, 510.00,
        stage="issuer_notified", stage_entered_days_ago=12,  # 6× baseline
        responsible_party="issuer",
        blockers=[
            {"kind": "awaiting_response", "description": "Anthropic legal review pending — multi-layer ROFR process", "since": _iso(_ago(10))},
            {"kind": "missing_doc", "description": "Board approval documentation not yet provided", "since": _iso(_ago(8))},
        ],
        risk_factors={"is_first_time_buyer": False, "prior_breakage_count": 1, "multi_layer_rofr": True, "deal_size_usd": 1530000},
    ))

    # Anthropic deal #2 — near-complete
    deals.append(deal(
        "D-006", "anthropic", "buyer_03", "seller_06", 1000, 510.00,
        stage="funding", stage_entered_days_ago=1,
        responsible_party="buyer",
        risk_factors={"is_first_time_buyer": False, "prior_breakage_count": 0, "multi_layer_rofr": True, "deal_size_usd": 510000},
    ))

    # ── SCENARIO 4: Multi-layer ROFR + silence (Cerebras) ────────────────────
    deals.append(deal(
        "D-007", "cerebras", "buyer_06", "seller_05", 10000, 28.75,
        stage="rofr_pending", stage_entered_days_ago=25,  # 1.25× baseline
        rofr_deadline_days=5,
        responsible_party="issuer",
        risk_factors={"is_first_time_buyer": False, "prior_breakage_count": 2, "multi_layer_rofr": True, "deal_size_usd": 287500},
    ))

    # Cerebras deal #2
    deals.append(deal(
        "D-008", "cerebras", "buyer_01", "seller_02", 4000, 28.75,
        stage="bid_accepted", stage_entered_days_ago=0,
        responsible_party="seller",
        risk_factors={"is_first_time_buyer": False, "prior_breakage_count": 0, "multi_layer_rofr": True, "deal_size_usd": 115000},
    ))

    # ── SCENARIO 5: Prior breakage + current stall (Groq) ────────────────────
    deals.append(deal(
        "D-009", "groq", "buyer_06", "seller_04", 7500, 14.20,
        stage="docs_pending", stage_entered_days_ago=11,  # 3.7× baseline
        responsible_party="buyer",
        blockers=[{"kind": "pending_signature", "description": "Buyer hasn't returned executed docs — prior deal also broke at this stage", "since": _iso(_ago(9))}],
        risk_factors={"is_first_time_buyer": False, "prior_breakage_count": 2, "deal_size_usd": 106500},
    ))

    # Groq deal #2 — healthy
    deals.append(deal(
        "D-010", "groq", "buyer_03", "seller_01", 6000, 14.20,
        stage="rofr_cleared", stage_entered_days_ago=1,
        responsible_party="buyer",
        risk_factors={"is_first_time_buyer": False, "prior_breakage_count": 0, "deal_size_usd": 85200},
    ))

    # ── Remaining 20 deals — variety across stages ────────────────────────────
    remaining = [
        ("D-011", "perplexity", "buyer_01", "seller_06", 2500, 65.00, "bid_accepted", 0, None, "seller", {}, {}),
        ("D-012", "perplexity", "buyer_05", "seller_03", 5000, 65.00, "docs_pending", 2, None, "buyer", {}, {"is_first_time_buyer": False}),
        ("D-013", "perplexity", "buyer_02", "seller_02", 3000, 65.00, "issuer_notified", 1, None, "issuer", {}, {}),
        ("D-014", "databricks", "buyer_04", "seller_05", 4000, 95.00, "rofr_pending", 15, 14, "issuer", {}, {"is_first_time_buyer": True}),
        ("D-015", "databricks", "buyer_01", "seller_04", 8000, 95.00, "rofr_pending", 5, 24, "issuer", {}, {}),
        ("D-016", "databricks", "buyer_03", "seller_01", 2000, 95.00, "rofr_cleared", 2, None, "buyer", {}, {}),
        ("D-017", "canva", "buyer_05", "seller_06", 6000, 34.50, "signing", 3, None, "seller", {}, {}),
        ("D-018", "canva", "buyer_02", "seller_03", 4000, 34.50, "signing", 5, None, "buyer",
            [{"kind": "missing_doc", "description": "Seller hasn't returned signature pages", "since": _iso(_ago(4))}], {}),
        ("D-019", "canva", "buyer_06", "seller_02", 10000, 34.50, "funding", 2, None, "buyer", {}, {}),
        ("D-020", "rippling", "buyer_01", "seller_05", 3000, 48.00, "bid_accepted", 1, None, "seller", {}, {}),
        ("D-021", "rippling", "buyer_04", "seller_04", 2000, 48.00, "docs_pending", 3, None, "buyer",
            [{"kind": "awaiting_response", "description": "Waiting on buyer accreditation confirmation", "since": _iso(_ago(2))}],
            {"is_first_time_buyer": True}),
        ("D-022", "rippling", "buyer_03", "seller_01", 5000, 48.00, "issuer_notified", 2, None, "issuer", {}, {}),
        ("D-023", "ramp", "buyer_05", "seller_06", 8000, 22.50, "rofr_pending", 8, 20, "issuer", {}, {}),
        ("D-024", "ramp", "buyer_02", "seller_03", 4000, 22.50, "rofr_cleared", 1, None, "buyer", {}, {}),
        ("D-025", "ramp", "buyer_01", "seller_02", 6000, 22.50, "signing", 1, None, "seller", {}, {}),
        ("D-026", "stripe", "buyer_06", "seller_05", 3000, 42.00, "docs_pending", 4, None, "buyer", {}, {"prior_breakage_count": 2}),
        ("D-027", "spacex", "buyer_04", "seller_04", 1200, 185.50, "rofr_pending", 18, 10, "issuer",
            [{"kind": "awaiting_response", "description": "SpaceX ROFR team has not yet confirmed receipt", "since": _iso(_ago(10))}], {}),
        ("D-028", "anthropic", "buyer_05", "seller_02", 500, 510.00, "signing", 6, None, "buyer",
            [{"kind": "pending_signature", "description": "TA docs sent but not countersigned", "since": _iso(_ago(5))}],
            {"multi_layer_rofr": True}),
        ("D-029", "groq", "buyer_01", "seller_06", 9000, 14.20, "issuer_notified", 3, None, "issuer", {}, {}),
        ("D-030", "cerebras", "buyer_03", "seller_03", 5000, 28.75, "funding", 2, None, "buyer", {}, {}),
    ]

    for row in remaining:
        (did, iid, bid, sid, shares, price, stage, entered, rofr_fwd, resp, blk, rf) = row
        deals.append(deal(
            did, iid, bid, sid, shares, price, stage, entered,
            rofr_deadline_days=rofr_fwd,
            responsible_party=resp,
            blockers=blk if isinstance(blk, list) else [],
            risk_factors=rf,
        ))

    # ── Deals 31-40: extended coverage ───────────────────────────────────────

    # SCENARIO: ROFR 1-2 days away + no issuer comms 5+ days (combined deadline_proximity + communication_silence)
    deals.append(deal(
        "D-031", "ramp", "buyer_02", "seller_05", 6000, 22.50,
        stage="rofr_pending", stage_entered_days_ago=20,
        rofr_deadline_days=1,  # CRITICAL: 1 day left
        responsible_party="issuer",
        risk_factors={"is_first_time_buyer": False, "prior_breakage_count": 1, "deal_size_usd": 135000},
    ))

    deals.append(deal(
        "D-032", "databricks", "buyer_05", "seller_06", 3500, 95.00,
        stage="rofr_pending", stage_entered_days_ago=28,
        rofr_deadline_days=2,  # CRITICAL: 2 days left
        responsible_party="issuer",
        risk_factors={"is_first_time_buyer": False, "prior_breakage_count": 0, "deal_size_usd": 332500},
    ))

    # SCENARIO: Very long stage stall (45+ days in docs_pending) + first-time buyer (stage_aging + unusual_characteristics)
    deals.append(deal(
        "D-033", "groq", "buyer_04", "seller_03", 2000, 14.20,
        stage="docs_pending", stage_entered_days_ago=47,  # 15.7× baseline
        responsible_party="buyer",
        blockers=[{"kind": "missing_doc", "description": "Accreditation docs never returned by first-time buyer", "since": _iso(_ago(45))}],
        risk_factors={"is_first_time_buyer": True, "prior_breakage_count": 0, "deal_size_usd": 28400},
    ))

    deals.append(deal(
        "D-034", "canva", "buyer_04", "seller_02", 4500, 34.50,
        stage="docs_pending", stage_entered_days_ago=52,  # 17.3× baseline
        responsible_party="buyer",
        blockers=[{"kind": "pending_signature", "description": "Transfer agreement unsigned since stage entry — first-time buyer unresponsive", "since": _iso(_ago(50))}],
        risk_factors={"is_first_time_buyer": True, "prior_breakage_count": 0, "deal_size_usd": 155250},
    ))

    # SCENARIO: Buyer with 2+ prior breakages in critical stage (unusual_characteristics high risk)
    deals.append(deal(
        "D-035", "stripe", "buyer_06", "seller_01", 8000, 42.00,
        stage="rofr_pending", stage_entered_days_ago=18,
        rofr_deadline_days=12,
        responsible_party="issuer",
        risk_factors={"is_first_time_buyer": False, "prior_breakage_count": 2, "deal_size_usd": 336000},
    ))

    deals.append(deal(
        "D-036", "perplexity", "buyer_06", "seller_04", 5000, 65.00,
        stage="signing", stage_entered_days_ago=10,  # 2.5× baseline
        responsible_party="buyer",
        blockers=[{"kind": "pending_signature", "description": "Buyer has not returned executed TA — third deal breakage pattern emerging", "since": _iso(_ago(8))}],
        risk_factors={"is_first_time_buyer": False, "prior_breakage_count": 2, "deal_size_usd": 325000},
    ))

    # SCENARIO: Missing prerequisites (blocker) + deadline approaching (missing_prerequisites + deadline_proximity combined)
    deals.append(deal(
        "D-037", "spacex", "buyer_03", "seller_05", 3000, 185.50,
        stage="rofr_pending", stage_entered_days_ago=24,
        rofr_deadline_days=4,  # 4 days to deadline with active blocker
        responsible_party="issuer",
        blockers=[{"kind": "awaiting_response", "description": "SpaceX board has not provided ROFR election — meeting postponed twice", "since": _iso(_ago(12))}],
        risk_factors={"is_first_time_buyer": False, "prior_breakage_count": 0, "deal_size_usd": 556500},
    ))

    deals.append(deal(
        "D-038", "rippling", "buyer_02", "seller_06", 4000, 48.00,
        stage="rofr_pending", stage_entered_days_ago=26,
        rofr_deadline_days=3,  # 3 days to deadline with active blocker
        responsible_party="issuer",
        blockers=[{"kind": "missing_doc", "description": "Board consent form not received — Rippling legal not responding", "since": _iso(_ago(10))}],
        risk_factors={"is_first_time_buyer": False, "prior_breakage_count": 0, "deal_size_usd": 192000},
    ))

    # SCENARIO: Completely clean deals — no risk signals (informational baseline)
    deals.append(deal(
        "D-039", "anthropic", "buyer_01", "seller_04", 2000, 510.00,
        stage="rofr_cleared", stage_entered_days_ago=1,
        responsible_party="buyer",
        risk_factors={"is_first_time_buyer": False, "prior_breakage_count": 0, "multi_layer_rofr": True, "deal_size_usd": 1020000},
    ))

    deals.append(deal(
        "D-040", "ramp", "buyer_03", "seller_02", 5000, 22.50,
        stage="signing", stage_entered_days_ago=1,
        responsible_party="buyer",
        risk_factors={"is_first_time_buyer": False, "prior_breakage_count": 0, "deal_size_usd": 112500},
    ))

    return deals


# ── Communication events ──────────────────────────────────────────────────────

def build_events(deals: list[dict]) -> list[dict]:
    """Seed realistic communication history for each deal."""
    events = []

    def evt(deal_id, event_type, days_ago, summary, payload=None):
        occ = _ago(days_ago)
        return {
            "event_id": str(uuid.uuid4()),
            "deal_id": deal_id,
            "event_type": event_type,
            "occurred_at": _iso(occ),
            "created_at": _iso(occ),
            "summary": summary,
            "payload": json.dumps(payload or {"summary": summary}),
        }

    # D-001: SpaceX ROFR critical — last comm was 14 days ago
    events += [
        evt("D-001", "stage_transition", 22, "Entered rofr_pending"),
        evt("D-001", "comm_outbound", 22, "ROFR notice sent to SpaceX legal",
            {"body": "Dear SpaceX Legal Team, Please find the attached ROFR notice for the transfer of 5,000 shares at $185.50. The ROFR clock runs for 30 days from this notice. Please confirm receipt and advise on your election. Best regards, Hiive TS"}),
        evt("D-001", "comm_inbound", 18, "SpaceX acknowledged receipt",
            {"body": "Thank you for the notice. We are reviewing internally and will respond within the ROFR window."}),
        evt("D-001", "comm_outbound", 14, "Follow-up on ROFR election status",
            {"body": "Hi, following up on the ROFR notice sent April 3rd. The 30-day window expires April 18th. Please advise on SpaceX's election at your earliest convenience."}),
    ]

    # D-003: Stripe docs stall — first-time buyer, last comm 12 days ago
    events += [
        evt("D-003", "stage_transition", 9, "Entered docs_pending"),
        evt("D-003", "comm_outbound", 9, "Transfer docs sent to buyer",
            {"body": "Hi James, The executed transfer agreement and accreditation verification forms are attached. Please review, sign, and return within 5 business days. Let me know if you have any questions — happy to walk through the docs."}),
        evt("D-003", "comm_inbound", 7, "Buyer acknowledged, asked for extension",
            {"body": "Hi, thank you for sending these over. I'm reviewing with my attorney. Can I have a few extra days to return them?"}),
        evt("D-003", "comm_outbound", 7, "Confirmed 3-day extension",
            {"body": "Absolutely, James. Please return the signed docs by EOD April 12th. Let me know if your attorney has any questions."}),
    ]

    # D-005: Anthropic stall — multi-layer ROFR, last comm 10 days ago
    events += [
        evt("D-005", "stage_transition", 12, "Entered issuer_notified"),
        evt("D-005", "comm_outbound", 12, "ROFR/transfer notice sent to Anthropic legal",
            {"body": "Dear Anthropic Legal, Please find the attached notice for the transfer of 3,000 shares at $510.00. Per the transfer agreement, Anthropic has 30 days to exercise its ROFR or provide board consent. Please confirm receipt."}),
        evt("D-005", "comm_inbound", 10, "Anthropic acknowledged — multi-layer review noted",
            {"body": "Received. Please note this requires board approval in addition to our standard legal review given the deal size. We'll be in touch."}),
    ]

    # D-007: Cerebras multi-layer ROFR — last comm 18 days ago
    events += [
        evt("D-007", "stage_transition", 25, "Entered rofr_pending"),
        evt("D-007", "comm_outbound", 25, "ROFR notice sent to Cerebras",
            {"body": "Dear Cerebras Legal Team, Attached is the ROFR notice for 10,000 shares at $28.75. The ROFR window runs 30 days. This transfer requires board-level approval per the shareholders' agreement. Please confirm receipt and initiate your internal process."}),
        evt("D-007", "comm_inbound", 18, "Cerebras: board meeting scheduled",
            {"body": "Thank you. We have scheduled a board meeting for April 5th to address this and other pending transfers. We will respond by April 10th."}),
    ]

    # D-009: Groq breakage risk — last comm 9 days ago
    events += [
        evt("D-009", "stage_transition", 11, "Entered docs_pending"),
        evt("D-009", "comm_outbound", 11, "Transfer docs sent",
            {"body": "Hi, transfer docs attached for the Groq deal. Please sign and return. Note: we need these back within 5 business days to keep the timeline on track."}),
        evt("D-009", "comm_inbound", 9, "Buyer: reviewing internally",
            {"body": "Got it, reviewing now."}),
    ]

    # D-014: Databricks ROFR with first-time buyer — some comms
    events += [
        evt("D-014", "stage_transition", 15, "Entered rofr_pending"),
        evt("D-014", "comm_outbound", 15, "ROFR notice to Databricks",
            {"body": "Dear Databricks, ROFR notice attached for 4,000 shares. 30-day window started today."}),
        evt("D-014", "comm_outbound", 8, "Buyer check-in",
            {"body": "Hi — wanted to update you that the ROFR window has 14 days remaining. No action needed from you at this stage."}),
        evt("D-014", "comm_inbound", 7, "Buyer acknowledges",
            {"body": "Thanks for the update. Standing by."}),
    ]

    # D-018: Canva signing stall
    events += [
        evt("D-018", "stage_transition", 5, "Entered signing"),
        evt("D-018", "comm_outbound", 5, "Signature pages sent",
            {"body": "Hi, attached are the signature pages for the Canva transfer. Please countersign and return at your earliest convenience."}),
        evt("D-018", "comm_outbound", 3, "Reminder on signature pages",
            {"body": "Following up on the Canva docs sent April 13th. Please return at your earliest — we're waiting on your countersignature to move to funding."}),
    ]

    # D-021: Rippling first-time buyer docs
    events += [
        evt("D-021", "stage_transition", 3, "Entered docs_pending"),
        evt("D-021", "comm_outbound", 3, "Docs package sent, explained process",
            {"body": "Hi, attached are the transfer documents for your Rippling shares. This is a straightforward process — please complete the accreditation form first, then sign the transfer agreement. Happy to schedule a call if helpful."}),
        evt("D-021", "comm_inbound", 2, "Buyer has questions about accreditation",
            {"body": "Thanks for sending. I have a few questions about the accreditation form — what counts as qualifying?"}),
        evt("D-021", "comm_outbound", 2, "Accreditation guidance provided",
            {"body": "Happy to help. For individual accreditation, you qualify if you have income exceeding $200K in each of the past two years (or $300K jointly with a spouse) or a net worth exceeding $1M excluding primary residence. Please let me know if you need any other guidance."}),
    ]

    # D-027: SpaceX ROFR with no response to recent follow-up
    events += [
        evt("D-027", "stage_transition", 18, "Entered rofr_pending"),
        evt("D-027", "comm_outbound", 18, "ROFR notice sent",
            {"body": "Dear SpaceX, ROFR notice for 1,200 shares at $185.50. Window: 30 days."}),
        evt("D-027", "comm_outbound", 10, "Follow-up — no response yet",
            {"body": "Following up on the ROFR notice from April 1st. 10 days remaining in the window. Please advise on SpaceX's election."}),
        evt("D-027", "comm_outbound", 3, "Second follow-up — urgent",
            {"body": "Second follow-up — the ROFR window expires in 10 days. We have not received a response from SpaceX. Please contact us at your earliest to avoid a waiver by non-response."}),
    ]

    # D-028: Anthropic signing stall
    events += [
        evt("D-028", "stage_transition", 6, "Entered signing"),
        evt("D-028", "comm_outbound", 6, "Signature pages sent",
            {"body": "Hi, attached are the Anthropic transfer signature pages. Please countersign and return."}),
        evt("D-028", "comm_outbound", 4, "Follow-up",
            {"body": "Following up on the docs sent April 12th. Please return at your earliest."}),
        evt("D-028", "comm_outbound", 2, "Second follow-up",
            {"body": "Second follow-up on Anthropic transfer docs. We need the countersigned pages to proceed."}),
    ]

    # D-031: Ramp ROFR critical — 1 day left, last issuer comm 6 days ago
    events += [
        evt("D-031", "stage_transition", 20, "Entered rofr_pending"),
        evt("D-031", "comm_outbound", 20, "ROFR notice sent to Ramp legal",
            {"body": "Dear Ramp Legal, ROFR notice attached for 6,000 shares at $22.50. Window runs 21 days from this notice. Please confirm receipt and advise on election."}),
        evt("D-031", "comm_inbound", 14, "Ramp acknowledged receipt",
            {"body": "Received. We will review and respond before the window closes."}),
        evt("D-031", "comm_outbound", 6, "Urgent follow-up — ROFR deadline imminent",
            {"body": "Urgent: The Ramp ROFR window closes in 7 days. We have not received an election notice. Please respond immediately to avoid automatic waiver."}),
    ]

    # D-032: Databricks ROFR critical — 2 days left, last comm 7 days ago
    events += [
        evt("D-032", "stage_transition", 28, "Entered rofr_pending"),
        evt("D-032", "comm_outbound", 28, "ROFR notice sent to Databricks",
            {"body": "Dear Databricks Legal, Please find the attached ROFR notice for 3,500 shares at $95.00. The 30-day ROFR window begins today."}),
        evt("D-032", "comm_outbound", 14, "Mid-window check-in",
            {"body": "Following up on the ROFR notice from April 3rd. Please advise on Databricks' election status."}),
        evt("D-032", "comm_inbound", 10, "Databricks: still reviewing",
            {"body": "Thank you for the follow-up. We are still in internal review. Will respond before the deadline."}),
        evt("D-032", "comm_outbound", 7, "Final follow-up — 2 days remaining after today",
            {"body": "Second follow-up: the ROFR window closes April 18th (2 days remaining). We have not received an election. Please respond urgently."}),
    ]

    # D-033: Groq docs stall — first-time buyer, 47 days
    events += [
        evt("D-033", "stage_transition", 47, "Entered docs_pending"),
        evt("D-033", "comm_outbound", 47, "Transfer docs sent to first-time buyer",
            {"body": "Hi, the Groq transfer documents are attached. As a first-time buyer, please review carefully and return the completed accreditation form and signed transfer agreement."}),
        evt("D-033", "comm_inbound", 40, "Buyer: reviewing with attorney",
            {"body": "Thanks. My attorney is reviewing. Will get back to you."}),
        evt("D-033", "comm_outbound", 35, "Follow-up",
            {"body": "Following up on the Groq docs. Please advise on timeline for return."}),
        evt("D-033", "comm_outbound", 20, "Second follow-up — no response",
            {"body": "Second follow-up on the Groq transfer docs sent March 31st. The deal cannot progress without your signed documents. Please respond."}),
    ]

    # D-034: Canva docs stall — first-time buyer, 52 days
    events += [
        evt("D-034", "stage_transition", 52, "Entered docs_pending"),
        evt("D-034", "comm_outbound", 52, "Transfer docs package sent",
            {"body": "Hi, the Canva transfer docs are attached. Please sign and return the transfer agreement. Let us know if you have any questions as a first-time buyer."}),
        evt("D-034", "comm_inbound", 45, "Buyer acknowledged, asked for 2-week extension",
            {"body": "Got it. Can I have two weeks? I need to consult with my financial advisor."}),
        evt("D-034", "comm_outbound", 45, "Agreed to extension",
            {"body": "Understood. Please return the docs by April 2nd."}),
        evt("D-034", "comm_outbound", 30, "Follow-up — past extension deadline",
            {"body": "The agreed deadline of April 2nd has passed. Please return the Canva docs urgently."}),
    ]

    # D-035: Stripe ROFR with high-risk buyer (2 prior breakages)
    events += [
        evt("D-035", "stage_transition", 18, "Entered rofr_pending"),
        evt("D-035", "comm_outbound", 18, "ROFR notice sent to Stripe",
            {"body": "Dear Stripe Legal, ROFR notice attached for 8,000 shares at $42.00. Note: 30-day window."}),
        evt("D-035", "comm_inbound", 12, "Stripe acknowledged",
            {"body": "Received. We will review and respond within the ROFR window."}),
        evt("D-035", "comm_outbound", 5, "Buyer risk briefing — internal note",
            {"body": "Note: Buyer Pinnacle Asset Management has 2 prior deal breakages on Hiive. Monitoring closely."}),
    ]

    # D-036: Perplexity signing stall — high-risk buyer
    events += [
        evt("D-036", "stage_transition", 10, "Entered signing"),
        evt("D-036", "comm_outbound", 10, "Signature pages sent",
            {"body": "Hi, the Perplexity transfer signature pages are attached. Please countersign and return. Note: this is time-sensitive."}),
        evt("D-036", "comm_inbound", 7, "Buyer: reviewing",
            {"body": "Reviewing now."}),
        evt("D-036", "comm_outbound", 4, "Follow-up",
            {"body": "Following up on the Perplexity docs sent April 6th. Please return the countersigned pages."}),
        evt("D-036", "comm_outbound", 1, "Urgent — second follow-up",
            {"body": "Second follow-up: Perplexity signature pages still outstanding. This buyer has previously broken two deals at this stage. Please return docs today."}),
    ]

    # D-037: SpaceX ROFR blocker + 4-day deadline
    events += [
        evt("D-037", "stage_transition", 24, "Entered rofr_pending"),
        evt("D-037", "comm_outbound", 24, "ROFR notice sent",
            {"body": "Dear SpaceX Legal, ROFR notice for 3,000 shares at $185.50. 30-day window starts today."}),
        evt("D-037", "comm_inbound", 20, "SpaceX: board meeting scheduled Apr 5",
            {"body": "Received. The board will address this at our April 5th meeting."}),
        evt("D-037", "comm_outbound", 14, "Follow-up post April 5th",
            {"body": "Following up — the April 5th board meeting has passed. Please advise on SpaceX's ROFR election."}),
        evt("D-037", "comm_inbound", 12, "SpaceX: meeting postponed to Apr 10",
            {"body": "Apologies — the meeting was postponed to April 10th. Will follow up after."}),
        evt("D-037", "comm_outbound", 6, "Second follow-up — still no election",
            {"body": "The ROFR window closes in approximately 4 days. We have not received SpaceX's election notice. Urgent response required."}),
    ]

    # D-038: Rippling ROFR blocker + 3-day deadline
    events += [
        evt("D-038", "stage_transition", 26, "Entered rofr_pending"),
        evt("D-038", "comm_outbound", 26, "ROFR notice sent to Rippling",
            {"body": "Dear Rippling Legal, ROFR notice for 4,000 shares at $48.00. 30-day window from today."}),
        evt("D-038", "comm_inbound", 18, "Rippling: in review",
            {"body": "Received. Our legal team is reviewing."}),
        evt("D-038", "comm_outbound", 10, "Follow-up — missing board consent form",
            {"body": "We note the board consent form has not been received. This is required before ROFR election can be processed. Please provide ASAP."}),
        evt("D-038", "comm_outbound", 3, "Critical — window closing",
            {"body": "The Rippling ROFR window closes in 3 days and we still have not received the board consent form or election notice. Please respond immediately."}),
    ]

    # D-039: Anthropic rofr_cleared — clean, healthy
    events += [
        evt("D-039", "stage_transition", 1, "Entered rofr_cleared"),
        evt("D-039", "comm_outbound", 1, "ROFR cleared — next steps to buyer",
            {"body": "Hi, Anthropic has cleared the ROFR. The deal can now proceed to funding. Please arrange wire transfer per the instructions attached."}),
    ]

    # D-040: Ramp signing — clean, healthy
    events += [
        evt("D-040", "stage_transition", 1, "Entered signing"),
        evt("D-040", "comm_outbound", 1, "Signature pages sent",
            {"body": "Hi, the Ramp transfer signature pages are attached. Please review and countersign at your earliest convenience."}),
    ]

    # Add stage_transition events for remaining deals that don't have them
    stage_transitions = [
        ("D-002", "rofr_cleared", 1),
        ("D-004", "signing", 2),
        ("D-006", "funding", 1),
        ("D-008", "bid_accepted", 0),
        ("D-010", "rofr_cleared", 1),
        ("D-011", "bid_accepted", 0),
        ("D-012", "docs_pending", 2),
        ("D-013", "issuer_notified", 1),
        ("D-015", "rofr_pending", 5),
        ("D-016", "rofr_cleared", 2),
        ("D-017", "signing", 3),
        ("D-019", "funding", 2),
        ("D-020", "bid_accepted", 1),
        ("D-022", "issuer_notified", 2),
        ("D-023", "rofr_pending", 8),
        ("D-024", "rofr_cleared", 1),
        ("D-025", "signing", 1),
        ("D-026", "docs_pending", 4),
        ("D-029", "issuer_notified", 3),
        ("D-030", "funding", 2),
    ]
    for (did, stage, days) in stage_transitions:
        events.append(evt(did, "stage_transition", days, f"Entered {stage}"))

    return events


# ── Main seeder ───────────────────────────────────────────────────────────────

def seed(reset: bool = False) -> None:
    """Seed the domain database. If reset=True, drop and recreate first."""
    from hiive_monitor import logging as log_module
    log_module.configure_logging()
    logger = log_module.get_logger()

    if reset:
        import os
        from hiive_monitor.config import get_settings
        db_path = get_settings().domain_db_path
        if db_path != ":memory:" and os.path.exists(db_path):
            os.remove(db_path)
            logger.info("seed.reset", path=db_path)

    init_domain_db()
    conn = get_domain_conn()

    for issuer in ISSUERS:
        dao.insert_issuer(conn, issuer)

    for party in PARTIES:
        dao.insert_party(conn, party)

    deals = build_deals()
    for deal in deals:
        dao.insert_deal(conn, deal)

    events = build_events(deals)
    for ev in events:
        dao.insert_event(
            conn,
            deal_id=ev["deal_id"],
            event_type=ev["event_type"],
            occurred_at=datetime.fromisoformat(ev["occurred_at"]),
            summary=ev["summary"],
            payload=json.loads(ev["payload"]) if isinstance(ev["payload"], str) else ev["payload"],
            event_id=ev["event_id"],
        )

    conn.commit()
    conn.close()
    logger.info("seed.complete", deals=len(deals), events=len(events), issuers=len(ISSUERS))
    print(f"Seeded {len(deals)} deals, {len(events)} events, {len(ISSUERS)} issuers.")
    assert len(deals) == 40, f"Expected 40 deals, got {len(deals)}"


if __name__ == "__main__":
    import sys
    reset = "--reset" in sys.argv
    seed(reset=reset)
