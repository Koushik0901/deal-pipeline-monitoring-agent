"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import pathlib
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from hiive_monitor import logging as log_module
from hiive_monitor.config import get_settings
from hiive_monitor.db.init import init_checkpoint_db, init_domain_db
from hiive_monitor.db.migrations import stretch_migrations

_TEMPLATES_DIR = pathlib.Path(__file__).parent / "web" / "templates"
_STATIC_DIR = pathlib.Path(__file__).parent / "web" / "static"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
templates.env.filters["format_number"] = lambda v: f"{int(v):,}"


def _human_stage(value: object) -> str:
    """Map a Stage enum or its `.value` (e.g. 'rofr_pending') to the plain-English label
    in STAGE_DISPLAY_NAMES. Falls back to the raw string with underscores replaced if the
    value is unrecognised. Single source of truth for stage labels in user-facing copy.
    """
    from hiive_monitor.models.stages import STAGE_DISPLAY_NAMES, Stage

    raw = value.value if hasattr(value, "value") else value
    if not isinstance(raw, str):
        return str(raw)
    try:
        return STAGE_DISPLAY_NAMES.get(Stage(raw), raw.replace("_", " "))
    except ValueError:
        return raw.replace("_", " ")


def _humanize_stage_codes(text: object) -> str:
    """Substring-replace any Stage enum value (`rofr_pending`, `docs_pending`, …) inside a
    free-text string with its STAGE_DISPLAY_NAMES label. Used to clean LLM-produced text
    (reasoning summaries, legacy draft bodies) at render time so the schema language never
    leaks to a user. Sorted longest-first defensively to avoid partial-prefix collisions.
    """
    from hiive_monitor.models.stages import STAGE_DISPLAY_NAMES, Stage

    if not isinstance(text, str):
        return text  # type: ignore[return-value]
    for stage in sorted(Stage, key=lambda s: -len(s.value)):
        text = text.replace(stage.value, STAGE_DISPLAY_NAMES.get(stage, stage.value))
    return text


templates.env.filters["human_stage"] = _human_stage
templates.env.filters["humanize_stage_codes"] = _humanize_stage_codes

# Expose clock_mode as a global so base.html can render sim-only controls
# without requiring every route to pass it explicitly.
def _clock_mode_global() -> str:
    return get_settings().clock_mode

templates.env.globals["clock_mode"] = _clock_mode_global()


async def _bootstrap_if_empty(logger) -> None:
    """Seed deals and run one tick on first startup so the brief is never blank."""
    from hiive_monitor.db.connection import get_domain_conn

    conn = get_domain_conn()
    deal_count = conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    completed_ticks = conn.execute(
        "SELECT COUNT(*) FROM ticks WHERE tick_completed_at IS NOT NULL"
    ).fetchone()[0]
    conn.close()

    loop = asyncio.get_running_loop()

    if deal_count == 0:
        from hiive_monitor.seed.seed_data import seed
        logger.info("app.bootstrap.seed")
        await loop.run_in_executor(None, seed)
        logger.info("app.bootstrap.seed_done")

    if completed_ticks == 0:
        from hiive_monitor.agents.monitor import run_tick
        tick_id = f"startup-{uuid.uuid4().hex[:8]}"
        logger.info("app.bootstrap.tick", tick_id=tick_id)
        await loop.run_in_executor(None, lambda: run_tick(mode="simulated", tick_id=tick_id))
        logger.info("app.bootstrap.tick_done", tick_id=tick_id)


def create_app() -> FastAPI:
    _cfg = get_settings()
    log_module.configure_logging(log_format=_cfg.log_format, logs_path=_cfg.logs_path)
    logger = log_module.get_logger()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_domain_db()
        init_checkpoint_db()
        stretch_migrations()
        logger.info("app.startup", db=get_settings().domain_db_path)

        from hiive_monitor.scheduler import start_scheduler

        def _tick():
            from hiive_monitor.agents.monitor import run_tick
            run_tick(mode="real_time")

        start_scheduler(_tick)

        if get_settings().clock_mode == "simulated":
            asyncio.create_task(_bootstrap_if_empty(logger))

        yield

        from hiive_monitor.scheduler import stop_scheduler
        stop_scheduler()
        logger.info("app.shutdown")

    app = FastAPI(
        title="Hiive Deal Pipeline Monitor",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    from hiive_monitor.web import routes
    app.include_router(routes.router)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        if exc.status_code == 404:
            return HTMLResponse(
                '<html><body style="font-family:monospace;padding:2rem">'
                f'<h2 style="color:#555">404 — Not found</h2>'
                f'<p style="color:#888">{exc.detail}</p>'
                '<p><a href="/brief" style="color:#333">← Back to brief</a></p>'
                '</body></html>',
                status_code=404,
            )
        raise exc

    return app


app = create_app()
