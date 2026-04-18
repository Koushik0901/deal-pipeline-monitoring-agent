"""Core FastAPI routes — brief, queue, deal detail, sim controls, clock API."""

from __future__ import annotations

import asyncio
import json
import time
import traceback
import uuid
from datetime import datetime

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
    portfolio_signals = []
    if tick and tick.get("signals"):
        try:
            portfolio_signals = json.loads(tick["signals"])
        except (ValueError, TypeError):
            pass

    handled_ivs = dao.get_handled_interventions(conn)
    conn.close()

    return _templates().TemplateResponse(
        request, "brief.html",
        {
            "items": items,
            "handled_items": handled_ivs,
            "tick": tick,
            "now": clk.now().isoformat(),
            "debug": debug == "1",
            "clock_mode": get_settings().clock_mode,
            "portfolio_signals": portfolio_signals,
        },
    )


def _confirmed_row(intervention_id: str, message: str, variant: str = "approve") -> str:
    """Inline confirmation div that replaces the row, then auto-refreshes the list after 1.5 s."""
    check = (
        '<svg class="w-3.5 h-3.5 shrink-0" viewBox="0 0 16 16" fill="none" stroke="currentColor"'
        ' stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<polyline points="2,8 6,12 14,4"/></svg>'
    )
    if variant == "approve":
        cls = "flex items-center gap-2 px-4 py-3 text-[0.8125rem] text-primary-container bg-primary-fixed/40 border-b-[0.5px] border-outline-variant/50"
        icon = check
    else:
        cls = "px-4 py-3 text-[0.8125rem] text-on-surface-variant border-b-[0.5px] border-outline-variant/50"
        icon = ""
    return (
        f'<div id="iv-{intervention_id}" class="{cls}"'
        f' hx-get="/brief" hx-trigger="load delay:1.5s"'
        f' hx-target="#brief-list" hx-swap="outerHTML" hx-select="#brief-list">'
        f'{icon}{message}'
        f'</div>'
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

    resp = HTMLResponse(_confirmed_row(intervention_id, "Approved \u2014 draft copied to clipboard", "approve"))
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

    resp = HTMLResponse(_confirmed_row(intervention_id, f"Dismissed \u2014 {iv['deal_id']}", "dismiss"))
    resp.headers["HX-Trigger"] = "refreshStats"
    return resp


@router.post("/interventions/batch-approve")
async def batch_approve_interventions(
    request: Request,
    severity_filter: str = Form("watch"),
):
    from hiive_monitor import clock as clk

    if severity_filter not in ("watch",):
        raise HTTPException(status_code=400, detail="Batch approve is limited to watch severity")

    conn = get_domain_conn()
    pending = dao.get_pending_interventions_by_severity(conn, severity_filter)

    approved_ids = []
    for iv in pending:
        try:
            dao.approve_intervention_atomic(conn, iv["intervention_id"], simulated_timestamp=clk.now())
            approved_ids.append(iv["intervention_id"])
        except Exception as exc:
            log.warning("batch_approve.skip", intervention_id=iv["intervention_id"], error=str(exc))

    conn.close()

    resp = HTMLResponse(
        f'<div class="px-4 py-2 text-[0.6875rem] text-on-surface-variant">'
        f'Approved {len(approved_ids)} watch intervention{"s" if len(approved_ids) != 1 else ""}.'
        f'</div>',
    )
    resp.headers["HX-Trigger"] = "refreshBriefList, refreshStats"
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

    resp = HTMLResponse(_confirmed_row(intervention_id, "Edited draft copied to clipboard", "approve"))
    resp.headers["HX-Trigger"] = "refreshStats"
    return resp


# ── Deal snooze ───────────────────────────────────────────────────────────────


@router.post("/deals/{deal_id}/snooze")
async def snooze_deal(
    request: Request,
    deal_id: str,
    hours: int = Form(48),
    reason: str = Form(...),
):
    from hiive_monitor.config import get_settings
    if not get_settings().enable_ts10_snooze:
        raise HTTPException(status_code=403, detail="Snooze feature not enabled")

    conn = get_domain_conn()
    deal = dao.get_deal(conn, deal_id)
    if not deal:
        conn.close()
        raise HTTPException(status_code=404, detail="Deal not found")

    snooze_until_iso = dao.snooze_deal(conn, deal_id, hours=hours, reason=reason)
    conn.close()

    until = datetime.fromisoformat(snooze_until_iso).strftime("%Y-%m-%d %H:%M")
    resp = HTMLResponse(
        f'<div class="px-4 py-2 text-[0.6875rem] text-on-surface-variant">'
        f'Snoozed {deal_id} until {until} \u2014 {reason}'
        f'</div>'
    )
    resp.headers["HX-Trigger"] = "refreshStats"
    return resp


# ── Deal detail ───────────────────────────────────────────────────────────────


@router.get("/deals/{deal_id}")
async def deal_detail(request: Request, deal_id: str, debug: str = ""):
    from hiive_monitor import clock as clk

    conn = get_domain_conn()
    deal = dao.get_deal(conn, deal_id)
    if deal is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Deal not found")

    events = dao.get_events(conn, deal_id)
    observations = dao.get_observations(conn, deal_id)
    interventions = dao.get_interventions(conn, deal_id)
    issuer = dao.get_issuer(conn, deal["issuer_id"])

    def _load_party(pid: str | None) -> dict | None:
        if not pid:
            return None
        row = conn.execute("SELECT * FROM parties WHERE party_id = ?", (pid,)).fetchone()
        return dict(row) if row else None

    buyer = _load_party(deal.get("buyer_id"))
    seller = _load_party(deal.get("seller_id"))
    conn.close()

    obs_enriched = []
    for obs in observations:
        reasoning = json.loads(obs["reasoning"]) if obs.get("reasoning") else {}
        obs_enriched.append({**obs, "reasoning_parsed": reasoning})

    # Derive display facts that match what the agent reasoned over.
    now = clk.now()

    def _parse(val):
        if not val:
            return None
        try:
            dt = datetime.fromisoformat(val)
            if dt.tzinfo is None:
                from datetime import UTC
                dt = dt.replace(tzinfo=UTC)
            return dt
        except Exception:
            return None

    stage_entered_at = _parse(deal.get("stage_entered_at"))
    days_in_stage = max(0, (now - stage_entered_at).days) if stage_entered_at else None

    rofr_deadline = _parse(deal.get("rofr_deadline"))
    days_to_rofr = (rofr_deadline - now).days if rofr_deadline else None
    rofr_expired = days_to_rofr is not None and days_to_rofr < 0

    comm_events = [e for e in events if e["event_type"] in ("comm_outbound", "comm_inbound", "comm_sent_agent_recommended")]
    if comm_events:
        last_comm_at = max(filter(None, (_parse(e["occurred_at"]) for e in comm_events)), default=None)
        days_since_last_comm = max(0, (now - last_comm_at).days) if last_comm_at else None
    else:
        days_since_last_comm = None

    try:
        risk_factors = json.loads(deal.get("risk_factors") or "{}")
    except (ValueError, TypeError):
        risk_factors = {}
    deal_size_usd = risk_factors.get("deal_size_usd")
    if deal_size_usd is None and deal.get("shares") and deal.get("price_per_share"):
        deal_size_usd = int(deal["shares"] * deal["price_per_share"])

    try:
        blockers = json.loads(deal.get("blockers") or "[]")
    except (ValueError, TypeError):
        blockers = []

    return _templates().TemplateResponse(
        request, "deal_detail.html",
        {
            "deal": deal,
            "events": events,
            "observations": obs_enriched,
            "interventions": interventions,
            "issuer": issuer,
            "buyer": buyer,
            "seller": seller,
            "days_in_stage": days_in_stage,
            "days_to_rofr": days_to_rofr,
            "rofr_expired": rofr_expired,
            "days_since_last_comm": days_since_last_comm,
            "deal_size_usd": deal_size_usd,
            "blockers": blockers,
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
    if dispatched_at and (time.time() - dispatched_at) > 180.0:
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
