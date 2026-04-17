from fastapi import APIRouter

from hiive_monitor.web.routes.main import router as _main_router
from hiive_monitor.web.routes.debug import router as _debug_router
from hiive_monitor.web.routes.simulation import router as _simulation_router

router = APIRouter()
router.include_router(_main_router)
router.include_router(_debug_router)
router.include_router(_simulation_router)

__all__ = ["router"]
