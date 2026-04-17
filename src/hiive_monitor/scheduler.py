"""APScheduler wrapper — drives the monitoring tick in real-time mode."""

from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from hiive_monitor import logging as log_module
from hiive_monitor.config import get_settings

_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler()
    return _scheduler


def start_scheduler(tick_fn) -> None:
    """
    Start the background scheduler.
    In real_time mode: registers tick_fn on a 60-second interval.
    In simulated mode: no auto trigger — tick is driven by POST /sim/advance.
    """
    settings = get_settings()
    scheduler = get_scheduler()

    if settings.clock_mode == "real_time":
        scheduler.add_job(
            tick_fn,
            trigger=IntervalTrigger(seconds=settings.tick_interval_seconds),
            id="monitor_tick",
            replace_existing=True,
        )
        log_module.get_logger().info(
            "scheduler.started",
            mode="real_time",
            interval_seconds=settings.tick_interval_seconds,
        )
    else:
        log_module.get_logger().info(
            "scheduler.started",
            mode="simulated",
            note="tick driven by /sim/advance",
        )

    if not scheduler.running:
        scheduler.start()


def stop_scheduler() -> None:
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)
