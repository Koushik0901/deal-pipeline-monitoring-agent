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

    # Pull the per-tick cost summary emitted at tick completion (if present),
    # and compute a live fallback from per-call log records so the panel always
    # shows something useful even mid-tick or when running without persistence.
    cost_summary = next(
        (r for r in reversed(records) if r.get("event") == "monitor.tick_cost_summary"),
        None,
    )
    if not cost_summary:
        per_model: dict[str, dict] = {}
        for r in records:
            if r.get("event") != "llm.call.completed":
                continue
            m = r.get("model", "unknown")
            bucket = per_model.setdefault(
                m, {"input_tokens": 0, "output_tokens": 0, "calls": 0, "cost_usd": 0.0}
            )
            bucket["input_tokens"] += int(r.get("input_tokens") or 0)
            bucket["output_tokens"] += int(r.get("output_tokens") or 0)
            bucket["cost_usd"] = round(bucket["cost_usd"] + float(r.get("cost_usd") or 0.0), 6)
            bucket["calls"] += 1
        if per_model:
            totals = {
                "calls": sum(b["calls"] for b in per_model.values()),
                "input_tokens": sum(b["input_tokens"] for b in per_model.values()),
                "output_tokens": sum(b["output_tokens"] for b in per_model.values()),
                "cost_usd": round(sum(b["cost_usd"] for b in per_model.values()), 6),
            }
            cost_summary = {
                "by_model": per_model,
                "total_calls": totals["calls"],
                "total_input_tokens": totals["input_tokens"],
                "total_output_tokens": totals["output_tokens"],
                "total_cost_usd": totals["cost_usd"],
            }

    return _templates().TemplateResponse(
        request,
        "debug.html",
        {
            "title": f"Debug — tick {tick_id[:8]}",
            "filter_label": "tick_id",
            "filter_value": tick_id,
            "records": records,
            "call_names": call_names,
            "cost_summary": cost_summary,
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
