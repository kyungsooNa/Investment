"""
웹 API 라우터 통합 모듈.
각 페이지별 라우터를 하나의 router로 결합하여 export.
"""
from fastapi import APIRouter, Depends
from view.web.api_common import (
    check_auth,
    check_csrf_for_unsafe_request,
    check_public_operation_allowed,
)

from view.web.routes.auth import router as auth_router
from view.web.routes.stock import router as stock_router
from view.web.routes.balance import router as balance_router
from view.web.routes.order import router as order_router
from view.web.routes.ranking import router as ranking_router
from view.web.routes.virtual import router as virtual_router
from view.web.routes.program import router as program_router
from view.web.routes.scheduler import router as scheduler_router
from view.web.routes.notification import router as notification_router
from view.web.routes.system import router as system_router
from view.web.routes.ohlcv import router as ohlcv_router
from view.web.routes.streaming import router as streaming_router
from view.web.routes.favorite import router as favorite_router
from view.web.routes.kill_switch import router as kill_switch_router
from view.web.routes.strategy_report import router as strategy_report_router
from view.web.routes.operator_dashboard import router as operator_dashboard_router
from view.web.routes.ai import router as ai_router

router = APIRouter(prefix="/api")
protected_router = APIRouter(
    dependencies=[
        Depends(check_auth),
        Depends(check_csrf_for_unsafe_request),
        Depends(check_public_operation_allowed),
    ]
)

router.include_router(auth_router)
protected_router.include_router(stock_router)
protected_router.include_router(balance_router)
protected_router.include_router(order_router)
protected_router.include_router(ranking_router)
protected_router.include_router(virtual_router)
protected_router.include_router(program_router)
protected_router.include_router(scheduler_router)
protected_router.include_router(notification_router)
protected_router.include_router(system_router)
protected_router.include_router(ohlcv_router)
protected_router.include_router(streaming_router)
protected_router.include_router(favorite_router)
protected_router.include_router(kill_switch_router)
protected_router.include_router(strategy_report_router)
protected_router.include_router(operator_dashboard_router)
protected_router.include_router(ai_router)
router.include_router(protected_router)
