"""Pipeline page route — book-of-deals view with deterministic health tiers."""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Request

from hiive_monitor.db import dao
from hiive_monitor.db.connection import get_domain_conn
from hiive_monitor.models.risk import Severity
from hiive_monitor.web.pipeline_health import compute_health, compute_signals

router = APIRouter()

# Worst tier first so health-sorted rows surface escalate at the top.
_TIER_ORDER = {s.value: i for i, s in enumerate(reversed(list(Severity)))}


@router.get("/pipeline")
async def pipeline(
    request: Request,
    tier: str = "",
    stage: str = "",
    issuer: str = "",
    responsible: str = "",
    sort: str = "health",
):
    """
    Pipeline view — one row per live deal, ranked by deterministic health.

    Query params:
      tier         — filter by health tier (escalate|act|watch|informational)
      stage        — filter by deal stage
      issuer       — filter by issuer id
      responsible  — filter by responsible_party
      sort         — health | deadline | stage_age | last_comm
    """
    from hiive_monitor import clock as clk
    from hiive_monitor.app import templates

    now = clk.now()
    conn = get_domain_conn()

    try:
        deals = dao.get_live_deals(conn)

        issuer_rows = conn.execute("SELECT * FROM issuers").fetchall()
        issuers_by_id = {row["issuer_id"]: dict(row) for row in issuer_rows}

        latest_obs_by_deal = dao.get_latest_observation_per_deal(conn)
        last_inbound_by_deal = dao.get_latest_inbound_per_deal(conn)

        open_iv_deal_ids = {iv["deal_id"] for iv in dao.get_open_interventions(conn)}

        items = []
        for deal in deals:
            try:
                deal["risk_factors_parsed"] = json.loads(deal.get("risk_factors") or "{}")
            except (ValueError, TypeError):
                deal["risk_factors_parsed"] = {}
            try:
                deal["blockers_parsed"] = json.loads(deal.get("blockers") or "[]")
            except (ValueError, TypeError):
                deal["blockers_parsed"] = []

            issuer_row = issuers_by_id.get(deal["issuer_id"], {})

            last_inbound_str = last_inbound_by_deal.get(deal["deal_id"])
            last_inbound_at = datetime.fromisoformat(last_inbound_str) if last_inbound_str else None

            signals = compute_signals(deal, issuer_row, last_inbound_at, now)
            health = compute_health(signals)

            items.append(
                {
                    "deal": deal,
                    "issuer": issuer_row,
                    "signals": signals,
                    "health": health,
                    "agent_flag": (latest_obs_by_deal.get(deal["deal_id"]) or {}).get("severity"),
                    "has_open_intervention": deal["deal_id"] in open_iv_deal_ids,
                }
            )
    finally:
        conn.close()

    # Book-wide tier counts — header reflects the full pipeline, not the filtered slice.
    counts_by_tier = {s.value: 0 for s in Severity}
    for item in items:
        counts_by_tier[item["health"]["tier"]] += 1

    # ── Sort ─────────────────────────────────────────────────────────────────
    def sort_key(item):
        health_rank = _TIER_ORDER.get(item["health"]["tier"], 99)
        s = item["signals"]
        if sort == "deadline":
            d = s["days_to_rofr"]
            return (10_000 if d is None else d, health_rank)
        if sort == "stage_age":
            return (-s["stage_aging_ratio"], health_rank)
        if sort == "last_comm":
            c = s["days_since_last_inbound"]
            return (-(c if c is not None else -1), health_rank)
        return (health_rank, -s["stage_aging_ratio"])

    items.sort(key=sort_key)

    all_stages = sorted({d["stage"] for d in deals})
    all_issuers = sorted({d["issuer_id"] for d in deals})
    all_responsible = sorted({d.get("responsible_party") or "" for d in deals} - {""})

    return templates.TemplateResponse(
        request,
        "pipeline.html",
        {
            "items": items,
            "tier": tier,
            "stage": stage,
            "issuer": issuer,
            "responsible": responsible,
            "sort": sort,
            "all_stages": all_stages,
            "all_issuers": all_issuers,
            "all_responsible": all_responsible,
            "counts_by_tier": counts_by_tier,
            "total": len(items),
        },
    )


# Back-compat redirect: old /queue links should land on /pipeline
@router.get("/queue")
async def queue_redirect(request: Request):
    from fastapi.responses import RedirectResponse

    qs = request.url.query
    target = "/pipeline" + (f"?{qs}" if qs else "")
    return RedirectResponse(url=target, status_code=307)
