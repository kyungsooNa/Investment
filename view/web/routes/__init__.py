"""
웹 API 라우터 통합 모듈.
각 페이지별 라우터를 하나의 router로 결합하여 export.
"""
from fastapi import APIRouter

from view.web.routes.auth import router as auth_router
from view.web.routes.stock import router as stock_router
from view.web.routes.balance import router as balance_router
from view.web.routes.order import router as order_router
from view.web.routes.ranking import router as ranking_router
from view.web.routes.virtual import router as virtual_router
from view.web.routes.program import router as program_router
from view.web.routes.scheduler import router as scheduler_router

router = APIRouter(prefix="/api")

router.include_router(auth_router)
router.include_router(stock_router)
router.include_router(balance_router)
router.include_router(order_router)
router.include_router(ranking_router)
router.include_router(virtual_router)
router.include_router(program_router)
router.include_router(scheduler_router)
