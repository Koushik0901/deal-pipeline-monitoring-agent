"""FastAPI application factory."""

from __future__ import annotations

import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from hiive_monitor import logging as log_module
from hiive_monitor.config import get_settings
from hiive_monitor.db.init import init_checkpoint_db, init_domain_db

_TEMPLATES_DIR = pathlib.Path(__file__).parent / "web" / "templates"
_STATIC_DIR = pathlib.Path(__file__).parent / "web" / "static"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
templates.env.filters["format_number"] = lambda v: f"{int(v):,}"


def create_app() -> FastAPI:
    log_module.configure_logging(get_settings().log_format)
    logger = log_module.get_logger()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_domain_db()
        init_checkpoint_db()
        logger.info("app.startup", db=get_settings().domain_db_path)

        from hiive_monitor.scheduler import start_scheduler

        def _tick():
            from hiive_monitor.agents.monitor import run_tick
            run_tick(mode="real_time")

        start_scheduler(_tick)
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

    return app


app = create_app()
