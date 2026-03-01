"""
FastAPI 라우터: 웹 뷰용 API 엔드포인트.
각 페이지별 라우터는 view.web.routes 패키지에 분리되어 있으며,
이 모듈은 하위 호환성을 위한 파사드 역할을 한다.
"""

# --- 통합 라우터 ---
from view.web.routes import router  # noqa: F401

# --- 공통 유틸리티 re-export (기존 코드 호환용) ---
from view.web.api_common import (  # noqa: F401
    set_ctx,
    _get_ctx,
    check_auth,
    _serialize_response,
    _serialize_list_items,
    _PRICE_CACHE,
    OrderRequest,
    EnvironmentRequest,
    ProgramTradingRequest,
    ProgramTradingUnsubscribeRequest,
    ProgramTradingDataModel,
)

# --- 개별 엔드포인트 함수 re-export (테스트 호환용) ---
from view.web.routes.program import stream_program_trading  # noqa: F401


# _ctx를 프로퍼티처럼 동작시키기 위해 모듈 레벨에서 api_common 참조
import view.web.api_common as _api_common


def __getattr__(name):
    """모듈 레벨 속성 접근 시 api_common의 _ctx를 반환 (테스트 호환용)."""
    if name == "_ctx":
        return _api_common._ctx
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
