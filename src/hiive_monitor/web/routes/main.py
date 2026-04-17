"""Core FastAPI routes — brief, queue, deal detail, sim controls, clock API."""

from __future__ import annotations

import json

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from hiive_monitor.app import templates
from hiive_monitor.db import dao
from hiive_monitor.db.connection import get_domain_conn

router = APIRouter()


@router.get("/")
async def root():
    return RedirectResponse(url="/brief", status_code=307)


# ── Daily brief ───────────────────────────────────────────────────────────────


@router.get("/brief")
async def daily_brief(request: Request, debug: str = ""):
    from hiive_monitor import clock as clk

    conn = get_domain_conn()
    tick = dao.get_last_completed_tick(conn)
    open_ivs = dao.get_open_interventions(conn)

    # Enrich each intervention with deal info
    items = []
    for iv in open_ivs:
        deal = dao.get_deal(conn, iv["deal_id"])
        obs = dao.get_observations(conn, iv["deal_id"])
        latest_obs = obs[-1] if obs else None
        reasoning = json.loads(latest_obs["reasoning"]) if latest_obs and latest_obs.get("reasoning") else {}
        items.append({
            **iv,
            "deal": deal,
            "reasoning_summary": reasoning.get("severity_rationale", ""),
            "dimensions_triggered": reasoning.get("dimensions_triggered", []),
        })
    conn.close()

    return templates.TemplateResponse(
        request, "brief.html",
        {"items": items, "tick": tick, "now": clk.now().isoformat(), "debug": debug == "1"},
    )


@router.post("/interventions/{intervention_id}/approve")
async def approve_intervention(request: Request, intervention_id: str):
    from hiive_monitor import clock as clk

    conn = get_domain_conn()
    iv = dao.get_intervention(conn, intervention_id)
    if not iv:
        conn.close()
        raise HTTPException(status_code=404, detail="Intervention not found")

    dao.approve_intervention_atomic(conn, intervention_id, simulated_timestamp=clk.now())
    conn.close()

    # Return HTMX partial — replace the card with "sent" state
    return HTMLResponse(
        f'<div class="rounded border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-700">'
        f'✓ Approved and marked sent for deal {iv["deal_id"]}'
        f'</div>'
    )


@router.post("/interventions/{intervention_id}/dismiss")
async def dismiss_intervention(request: Request, intervention_id: str):
    conn = get_domain_conn()
    iv = dao.get_intervention(conn, intervention_id)
    if not iv:
        conn.close()
        raise HTTPException(status_code=404, detail="Intervention not found")

    dao.update_intervention_status(conn, intervention_id, status="dismissed")
    conn.close()

    return HTMLResponse(
        f'<div class="text-xs text-neutral-400 px-4 py-2">Dismissed — {iv["deal_id"]}</div>'
    )


@router.post("/interventions/{intervention_id}/confirm-edit")
async def confirm_edit(request: Request, intervention_id: str, final_text: str = Form(...)):
    from hiive_monitor import clock as clk

    conn = get_domain_conn()
    iv = dao.get_intervention(conn, intervention_id)
    if not iv:
        conn.close()
        raise HTTPException(status_code=404, detail="Intervention not found")

    dao.edit_intervention_atomic(
        conn, intervention_id, simulated_timestamp=clk.now(), final_text=final_text
    )
    conn.close()

    return HTMLResponse(
        f'<div class="rounded border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-700">'
        f'✓ Edited draft sent for deal {iv["deal_id"]}'
        f'</div>'
    )


# ── Queue ─────────────────────────────────────────────────────────────────────


@router.get("/queue")
async def queue(request: Request, severity: str = "", stage: str = "", issuer: str = ""):
    conn = get_domain_conn()
    open_ivs = dao.get_open_interventions(conn)
    items = []
    for iv in open_ivs:
        deal = dao.get_deal(conn, iv["deal_id"])
        if severity and iv.get("severity") != severity:
            continue
        if stage and deal and deal.get("stage") != stage:
            continue
        if issuer and deal and deal.get("issuer_id") != issuer:
            continue
        items.append({**iv, "deal": deal})
    conn.close()
    return templates.TemplateResponse(
        request, "queue.html",
        {"items": items, "severity": severity, "stage": stage, "issuer": issuer},
    )


# ── Deal detail ───────────────────────────────────────────────────────────────


@router.get("/deals/{deal_id}")
async def deal_detail(request: Request, deal_id: str, debug: str = ""):
    conn = get_domain_conn()
    deal = dao.get_deal(conn, deal_id)
    if deal is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Deal not found")

    events = dao.get_events(conn, deal_id)
    observations = dao.get_observations(conn, deal_id)
    interventions = dao.get_interventions(conn, deal_id)
    issuer = dao.get_issuer(conn, deal["issuer_id"])
    conn.close()

    obs_enriched = []
    for obs in observations:
        reasoning = json.loads(obs["reasoning"]) if obs.get("reasoning") else {}
        obs_enriched.append({**obs, "reasoning_parsed": reasoning})

    return templates.TemplateResponse(
        request, "deal_detail.html",
        {
            "deal": deal,
            "events": events,
            "observations": obs_enriched,
            "interventions": interventions,
            "issuer": issuer,
            "debug": debug == "1",
        },
    )


# ── Simulation controls ───────────────────────────────────────────────────────


@router.get("/sim")
async def sim_page(request: Request):
    from hiive_monitor import clock as clk
    return templates.TemplateResponse(
        request, "sim.html", {"now": clk.now().isoformat()}
    )


@router.post("/sim/advance")
async def advance_sim(request: Request, days: int = Form(...)):
    from hiive_monitor import clock as clk
    from hiive_monitor.agents.monitor import run_tick

    clk.get_clock().advance(days)
    tick_id = run_tick(mode="simulated")

    return HTMLResponse(
        f'<div class="rounded border border-accent-200 bg-accent-50 px-4 py-3 text-sm">'
        f'Advanced {days} day(s). New tick: {tick_id[:8]}…'
        f'<a href="/brief" class="ml-3 font-medium text-accent-700 underline">View Brief →</a>'
        f'</div>'
    )


# ── API ───────────────────────────────────────────────────────────────────────


@router.get("/api/clock")
async def clock_api():
    from hiive_monitor import clock as clk
    conn = get_domain_conn()
    tick = dao.get_last_completed_tick(conn)
    conn.close()
    return {
        "now": clk.now().isoformat(),
        "last_tick": tick["tick_id"] if tick else None,
        "last_tick_at": tick["tick_completed_at"] if tick else None,
    }


@router.post("/api/tick")
async def run_tick_api():
    from hiive_monitor.agents.monitor import run_tick
    tick_id = run_tick(mode="simulated")
    return {"tick_id": tick_id}
