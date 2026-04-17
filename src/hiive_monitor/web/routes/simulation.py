"""Simulation autoplay SSE endpoint — advances simulated clock N days over M seconds."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from hiive_monitor.logging import get_logger

router = APIRouter(prefix="/api/simulation", tags=["simulation"])
log = get_logger(__name__)


def _sse_event(event: str, data: dict) -> str:
    """Format a single SSE message."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _autoplay_stream(days: int, speed: float) -> AsyncGenerator[str, None]:
    """Async generator that advances the simulated clock one day at a time."""
    from hiive_monitor import clock as clk

    # Guard: only run in simulated mode
    clock_mode = os.getenv("CLOCK_MODE", "real_time").lower()
    if clock_mode != "simulated":
        yield _sse_event(
            "error",
            {
                "message": "Autoplay is only available in simulated clock mode. "
                           "Restart with CLOCK_MODE=simulated.",
            },
        )
        return

    clock = clk.get_clock()

    try:
        for day_num in range(1, days + 1):
            # Advance clock by one day
            try:
                clock.advance(1)
            except RuntimeError as exc:
                yield _sse_event("error", {"message": str(exc)})
                return

            current_date = clock.now().strftime("%Y-%m-%d")
            log.info("autoplay.day_advanced", day=day_num, total=days, date=current_date)

            # Emit progress event
            yield _sse_event(
                "progress",
                {
                    "day": day_num,
                    "total_days": days,
                    "current_date": current_date,
                    "status": "advancing",
                },
            )

            # Optionally run a monitoring tick
            deals_processed = 0
            flags_raised = 0
            try:
                from hiive_monitor.agents.monitor import run_tick
                tick_id = await asyncio.to_thread(run_tick, mode="simulated")

                # Fetch tick results from DB if available
                try:
                    from hiive_monitor.db import dao
                    from hiive_monitor.db.connection import get_domain_conn
                    conn = get_domain_conn()
                    tick = dao.get_tick(conn, tick_id)
                    conn.close()
                    if tick:
                        deals_processed = tick.get("deals_screened") or 0
                        flags_raised = tick.get("deals_investigated") or 0
                except Exception:
                    pass  # DB read failure is non-fatal

            except Exception as exc:
                log.warning("autoplay.tick_skipped", day=day_num, error=str(exc))

            yield _sse_event(
                "tick_complete",
                {
                    "day": day_num,
                    "deals_processed": deals_processed,
                    "flags_raised": flags_raised,
                },
            )

            # Sleep between days (except after the last one)
            if day_num < days:
                await asyncio.sleep(speed)

        yield _sse_event(
            "done",
            {
                "message": "Autoplay complete",
                "total_days": days,
            },
        )

    except asyncio.CancelledError:
        log.info("autoplay.client_disconnected", day=day_num if 'day_num' in dir() else 0)
        # Do not re-raise — generator simply stops, connection closes cleanly


@router.get("/autoplay")
async def autoplay(
    days: int = Query(default=7, ge=1, le=30, description="Number of simulated days to advance"),
    speed: float = Query(default=1.0, gt=0.0, description="Real seconds per simulated day"),
):
    """Stream SSE progress events while advancing the simulated clock N days."""
    return StreamingResponse(
        _autoplay_stream(days=days, speed=speed),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering if proxied
        },
    )
