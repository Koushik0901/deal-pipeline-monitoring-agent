"""Debug view routes — raw structured log records for a tick or deal (US6, FR-028)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from hiive_monitor import logging as log_module


def _templates():
    """Lazy import of Jinja2Templates to break the app ↔ routes circular import."""
    from hiive_monitor.app import templates as _t
    return _t

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/tick/{tick_id}", response_class=HTMLResponse)
async def debug_tick(request: Request, tick_id: str):
    records = log_module.get_log_records(tick_id=tick_id)
    call_names = sorted({r.get("call_name", "") for r in records if r.get("call_name")})
    return _templates().TemplateResponse(
        request,
        "debug.html",
        {
            "title": f"Debug — tick {tick_id[:8]}",
            "filter_label": "tick_id",
            "filter_value": tick_id,
            "records": records,
            "call_names": call_names,
        },
    )


@router.get("/deal/{deal_id}", response_class=HTMLResponse)
async def debug_deal(request: Request, deal_id: str):
    records = log_module.get_log_records(deal_id=deal_id)
    call_names = sorted({r.get("call_name", "") for r in records if r.get("call_name")})
    return _templates().TemplateResponse(
        request,
        "debug.html",
        {
            "title": f"Debug — {deal_id}",
            "filter_label": "deal_id",
            "filter_value": deal_id,
            "records": records,
            "call_names": call_names,
        },
    )
