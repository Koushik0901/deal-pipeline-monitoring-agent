from fastapi import APIRouter


def _build_router() -> APIRouter:
    from hiive_monitor.web.routes.main import router as _main_router
    from hiive_monitor.web.routes.debug import router as _debug_router
    from hiive_monitor.web.routes.queue import router as _queue_router

    r = APIRouter()
    r.include_router(_main_router)
    r.include_router(_debug_router)
    r.include_router(_queue_router)
    return r


def __getattr__(name: str):
    if name == "router":
        r = _build_router()
        globals()["router"] = r
        return r
    raise AttributeError(name)


__all__ = ["router"]
