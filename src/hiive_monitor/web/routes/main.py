"""Core FastAPI routes — brief, queue, deal detail, sim controls, clock API."""

from __future__ import annotations

import asyncio
import json
import time
import traceback
import uuid

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from hiive_monitor.db import dao
from hiive_monitor.db.connection import get_domain_conn
from hiive_monitor.logging import get_logger


def _templates():
    """Lazy import of Jinja2Templates to break the app ↔ routes circular import."""
    from hiive_monitor.app import templates as _t
    return _t

router = APIRouter()
log = get_logger(__name__)

# Tracks real wall-clock dispatch time for in-flight ticks (used for timeout detection).
_tick_dispatch_times: dict[str, float] = {}


def _tick_polling_div(tick_id: str) -> str:
    """Return the HTMX polling fragment for an in-flight tick (self-replaces via outerHTML swap)."""
    short = tick_id[:8]
    return (
        f'<div id="tick-{short}" class="rounded-[4px] border-[0.5px] border-outline-variant bg-surface-container-low px-4 py-2 text-[0.75rem] text-on-surface-variant"'
        f' hx-get="/api/tick/{tick_id}/status" hx-trigger="every 2s" hx-swap="outerHTML">'
        f'Tick {short}&hellip; running <span class="animate-pulse">●</span>'
        f"</div>"
    )


@router.get("/")
async def root():
    return RedirectResponse(url="/brief", status_code=307)


# ── Daily brief ───────────────────────────────────────────────────────────────


@router.get("/brief")
async def daily_brief(request: Request, debug: str = ""):
    from hiive_monitor import clock as clk
    from hiive_monitor.config import get_settings

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

    return _templates().TemplateResponse(
        request, "brief.html",
        {
            "items": items,
            "tick": tick,
            "now": clk.now().isoformat(),
            "debug": debug == "1",
            "clock_mode": get_settings().clock_mode,
        },
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

    resp = HTMLResponse(
        f'<div class="rounded-[4px] border-[0.5px] border-outline-variant bg-primary-fixed px-4 py-3 text-[0.8125rem] text-primary-container">'
        f'Approved and sent \u2014 {iv["deal_id"]}'
        f'</div>'
    )
    resp.headers["HX-Trigger"] = "refreshStats"
    return resp


@router.post("/interventions/{intervention_id}/dismiss")
async def dismiss_intervention(request: Request, intervention_id: str):
    conn = get_domain_conn()
    iv = dao.get_intervention(conn, intervention_id)
    if not iv:
        conn.close()
        raise HTTPException(status_code=404, detail="Intervention not found")

    dao.update_intervention_status(conn, intervention_id, status="dismissed")
    conn.close()

    resp = HTMLResponse(
        f'<div class="px-4 py-2 text-[0.6875rem] text-on-surface-variant">Dismissed \u2014 {iv["deal_id"]}</div>'
    )
    resp.headers["HX-Trigger"] = "refreshStats"
    return resp


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
        f'<div class="rounded-[4px] border-[0.5px] border-outline-variant bg-primary-fixed px-4 py-3 text-[0.8125rem] text-primary-container">'
        f'Edited draft sent \u2014 {iv["deal_id"]}'
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
    return _templates().TemplateResponse(
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

    return _templates().TemplateResponse(
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
    return _templates().TemplateResponse(
        request, "sim.html", {"now": clk.now().isoformat()}
    )


@router.post("/sim/advance")
async def advance_sim(
    request: Request,
    background_tasks: BackgroundTasks,
    days: int = Form(...),
):
    from hiive_monitor import clock as clk
    from hiive_monitor.agents.monitor import run_tick

    try:
        clk.get_clock().advance(days)
    except RuntimeError:
        return HTMLResponse(
            '<div class="rounded-[4px] border-[0.5px] border-outline-variant bg-error-container px-4 py-2 text-[0.75rem] text-error">'
            "Cannot advance clock in real-time mode \u2014 restart with CLOCK_MODE=simulated."
            "</div>",
            status_code=400,
        )

    tick_id = str(uuid.uuid4())
    _tick_dispatch_times[tick_id] = time.time()

    async def _run():
        try:
            await asyncio.to_thread(run_tick, mode="simulated", tick_id=tick_id)
        except Exception as e:
            log.error("sim.tick_failed", tick_id=tick_id, error=str(e), traceback=traceback.format_exc())
        finally:
            _tick_dispatch_times.pop(tick_id, None)

    background_tasks.add_task(_run)

    return HTMLResponse(_tick_polling_div(tick_id))


@router.get("/api/tick/{tick_id}/status")
async def tick_status(tick_id: str):
    conn = get_domain_conn()
    tick = dao.get_tick(conn, tick_id)
    conn.close()
    short = tick_id[:8]
    if tick and tick.get("tick_completed_at"):
        _tick_dispatch_times.pop(tick_id, None)
        resp = HTMLResponse(
            f'<div class="rounded-[4px] border-[0.5px] border-outline-variant bg-primary-fixed px-4 py-2 text-[0.75rem] text-primary-container">'
            f'Tick {short} complete. <a href="/brief" class="ml-3 font-medium underline">View Brief \u2192</a>'
            f"</div>"
        )
        resp.headers["HX-Trigger"] = "tickComplete"
        return resp
    dispatched_at = _tick_dispatch_times.get(tick_id)
    if dispatched_at and (time.time() - dispatched_at) > 30.0:
        _tick_dispatch_times.pop(tick_id, None)
        return HTMLResponse(
            f'<div class="rounded-[4px] border-[0.5px] border-outline-variant bg-error-container px-4 py-2 text-[0.75rem] text-error">'
            f"Tick {short} timed out \u2014 check logs for errors.</div>"
        )
    return HTMLResponse(_tick_polling_div(tick_id))


# ── API ───────────────────────────────────────────────────────────────────────


@router.get("/api/brief-stats")
async def brief_stats(request: Request):
    conn = get_domain_conn()
    tick = dao.get_last_completed_tick(conn)
    open_ivs = dao.get_open_interventions(conn)
    conn.close()
    items = [{"severity": iv["severity"]} for iv in open_ivs]
    return _templates().TemplateResponse(
        request, "_brief_stats.html", {"items": items, "tick": tick}
    )


@router.get("/api/status-bar")
async def status_bar(request: Request):
    conn = get_domain_conn()
    tick = dao.get_last_completed_tick(conn)
    conn.close()
    return _templates().TemplateResponse(request, "_status_bar.html", {"tick": tick})


@router.get("/api/clock")
async def clock_api():
    from hiive_monitor import clock as clk
    conn = get_domain_conn()
    tick = dao.get_last_completed_tick(conn)
    conn.close()
    now_str = clk.now().strftime("%Y-%m-%d %H:%M")
    tick_str = tick["tick_completed_at"][:16] if tick else "—"
    return HTMLResponse(
        f'<span>{now_str}</span>'
        f'<span class="mx-1 text-neutral-300">·</span>'
        f'<span>last tick {tick_str}</span>'
    )


@router.post("/api/tick")
async def run_tick_api():
    from hiive_monitor.agents.monitor import run_tick
    tick_id = run_tick(mode="simulated")
    return {"tick_id": tick_id}
