"""
웹 API 공통 유틸리티: 컨텍스트 관리, 응답 직렬화, 인증, Pydantic 모델.
"""
from fastapi import HTTPException, WebSocketException, status
from pydantic import BaseModel
from starlette.requests import HTTPConnection
from view.web import security

_ctx = None  # 전역 변수로 선언

# API 호출 실패 시 대처를 위한 전역 가격 캐시 (메모리)
_PRICE_CACHE = {}  # {code: (price, rate, timestamp)}

# 서버 측 in-flight 요청 추적 (hang 진단용): request_id → {path, method, start, query}
_active_requests: dict = {}

# 최근 완료 요청 이력 (hang 직전 분석용): deque-like list, 최대 20건
_recent_completed: list = []
_RECENT_MAX = 20


def set_ctx(ctx):
    global _ctx
    _ctx = ctx


def _get_ctx():
    if _ctx is None:
        raise HTTPException(status_code=503, detail="서비스가 초기화되지 않았습니다.")
    return _ctx


def _config_get(config, key: str, default=None):
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def get_auth_value(key: str, default=None, *, ctx=None):
    """웹 인증 설정은 broker active_config가 아닌 전체 앱 설정에서만 읽는다."""
    if ctx is None:
        ctx = _get_ctx()
    auth_config = _config_get(ctx.full_config, "auth", {})
    return _config_get(auth_config, key, default)


def get_auth_config(*, ctx=None):
    if ctx is None:
        ctx = _get_ctx()
    return _config_get(ctx.full_config, "auth", {})


def get_session_claims(connection: HTTPConnection, *, ctx=None):
    auth_config = get_auth_config(ctx=ctx)
    token = connection.cookies.get(security.SESSION_COOKIE_NAME)
    claims = security.verify_session(token, auth_config)
    if claims is None:
        return None

    from view.web.user_repository import ConfigUserRepository

    repository = ConfigUserRepository(auth_config)
    if not repository.has_configured_users:
        return claims
    user = repository.find_enabled(claims.username)
    if user is None or user.role != claims.role:
        security.revoke_session(claims.session_id)
        return None
    return claims


def is_authenticated(connection: HTTPConnection, *, ctx=None) -> bool:
    return get_session_claims(connection, ctx=ctx) is not None


def check_auth(connection: HTTPConnection):
    """로그인 여부를 확인하는 공통 함수."""
    if not is_authenticated(connection):
        scope = getattr(connection, "scope", {})
        if isinstance(scope, dict) and scope.get("type") == "websocket":
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True


def get_authenticated_operator(connection: HTTPConnection) -> str:
    """감사 로그에 토큰 원문 대신 비민감 운영자 식별자를 반환한다."""
    claims = get_session_claims(connection)
    if claims is None:
        check_auth(connection)
    return claims.username


def get_authenticated_principal(connection: HTTPConnection):
    claims = get_session_claims(connection)
    if claims is None:
        check_auth(connection)
    return claims


def _audit_authorization(
    connection: HTTPConnection,
    claims,
    required_role: str,
    *,
    allowed: bool,
) -> None:
    ctx = _get_ctx()
    logger = getattr(ctx, "logger", None)
    log_method = getattr(logger, "info" if allowed else "warning", None)
    if not callable(log_method):
        return
    scope = getattr(connection, "scope", {})
    log_method(
        {
            "event": "rbac_authorization",
            "username": claims.username,
            "role": claims.role,
            "required_role": required_role,
            "method": scope.get("method", "WEBSOCKET"),
            "path": scope.get("path", ""),
            "allowed": allowed,
        }
    )


def require_role(connection: HTTPConnection, required_role: str):
    from view.web.authorization import ADMIN, role_allows

    claims = get_authenticated_principal(connection)
    scope = getattr(connection, "scope", {})
    state = scope.setdefault("state", {}) if isinstance(scope, dict) else {}
    audit_key = (
        claims.session_id,
        required_role,
        scope.get("method"),
        scope.get("path"),
    )
    already_checked = state.get("_rbac_authorization") == audit_key
    allowed = role_allows(claims.role, required_role)
    if not already_checked and (not allowed or required_role == ADMIN):
        _audit_authorization(
            connection,
            claims,
            required_role,
            allowed=allowed,
        )
    state["_rbac_authorization"] = audit_key
    if allowed:
        return claims

    if scope.get("type") == "websocket":
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
    raise HTTPException(status_code=403, detail="Insufficient role")


def check_role_for_request(connection: HTTPConnection) -> bool:
    from view.web.authorization import required_role_for_request

    scope = getattr(connection, "scope", {})
    if not isinstance(scope, dict):
        raise HTTPException(status_code=403, detail="Authorization scope unavailable")
    required_role = required_role_for_request(
        str(scope.get("path", "")),
        str(scope.get("method", "GET")),
    )
    require_role(connection, required_role)
    return True


def check_csrf(connection: HTTPConnection) -> bool:
    """상태 변경 요청의 세션 결합 CSRF 토큰을 검증한다."""
    claims = get_session_claims(connection)
    if claims is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not security.verify_csrf(connection, claims):
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    return True


def check_csrf_for_unsafe_request(connection: HTTPConnection) -> bool:
    """HTTP 상태 변경 메서드에만 CSRF 검증을 적용한다."""
    scope = getattr(connection, "scope", {})
    if not isinstance(scope, dict) or scope.get("type") != "http":
        return True
    if scope.get("method", "GET").upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        return check_csrf(connection)
    return True


def check_public_operation_allowed(connection: HTTPConnection) -> bool:
    from view.web.deployment_policy import is_public_operation_blocked

    scope = getattr(connection, "scope", {})
    if not isinstance(scope, dict):
        return True
    if is_public_operation_blocked(
        _get_ctx(),
        str(scope.get("path", "")),
        str(scope.get("method", "GET")),
    ):
        raise HTTPException(
            status_code=403,
            detail="공개 모드에서는 이 운영 작업을 실행할 수 없습니다.",
        )
    return True


def _serialize_response(resp):
    """ResCommonResponse를 JSON 직렬화 가능한 dict로 변환."""
    if resp is None:
        result = {"rt_cd": "999", "msg1": "응답 없음", "data": None}
    elif hasattr(resp, 'to_dict'):
        result = resp.to_dict()
    else:
        result = {"rt_cd": str(resp.rt_cd), "msg1": str(resp.msg1), "data": resp.data}

    if _ctx is not None:
        from view.web.data_masking import mask_sensitive_data
        from view.web.deployment_policy import is_public_mode

        if is_public_mode(_ctx):
            return mask_sensitive_data(result)
    return result


def _serialize_list_items(items):
    """dataclass 리스트를 dict 리스트로 변환."""
    result = []
    for item in (items or []):
        if hasattr(item, 'to_dict'):
            result.append(item.to_dict())
        elif isinstance(item, dict):
            result.append(item)
        else:
            result.append(str(item))
    return result


# --- Pydantic 모델 ---

class OrderRequest(BaseModel):
    code: str
    price: str
    qty: str
    side: str  # "buy" or "sell"
    real_order_confirmation: str | None = None


class EnvironmentRequest(BaseModel):
    is_paper: bool
    real_mode_confirmation: str | None = None


class ProgramTradingRequest(BaseModel):
    code: str


class ProgramTradingUnsubscribeRequest(BaseModel):
    code: str | None = None


class ProgramTradingDataModel(BaseModel):
    chartData: dict
    subscribedCodes: list
    codeNameMap: dict
    savedAt: str | None = None
