"""웹 API의 역할 계층과 경로별 최소 권한 정책."""
from __future__ import annotations

VIEWER = "viewer"
OPERATOR = "operator"
ADMIN = "admin"
ROLES = frozenset({VIEWER, OPERATOR, ADMIN})

_ROLE_RANK = {
    VIEWER: 10,
    OPERATOR: 20,
    ADMIN: 30,
}

_VIEWER_GET_EXACT = frozenset(
    {
        "/api/auth/me",
        "/api/status",
        "/api/market-mode",
        "/api/stocks/list",
        "/api/overseas/stocks/list",
        "/api/top-market-cap",
    }
)
_VIEWER_GET_PREFIXES = (
    "/api/stock/",
    "/api/overseas/stock/",
    "/api/overseas/chart/",
    "/api/overseas/market-cap",
    "/api/chart/",
    "/api/indicator/",
    "/api/ranking/",
    "/api/strategies/diagnostic-reports",
    "/api/strategies/rejected-reasons",
    "/api/strategies/performance-by-regime",
)

_ADMIN_ALL_METHOD_PATHS = frozenset(
    {
        "/api/position-sizing/limits",
    }
)
_ADMIN_UNSAFE_EXACT = frozenset(
    {
        "/api/system/shutdown",
        "/api/system/restart",
        "/api/environment",
        "/api/market-mode",
        "/api/balance/sell_all",
    }
)
_ADMIN_UNSAFE_PREFIXES = (
    "/api/scheduler/",
    "/api/kill-switch/",
)


def role_allows(actual_role: str, required_role: str) -> bool:
    actual_rank = _ROLE_RANK.get(actual_role)
    required_rank = _ROLE_RANK.get(required_role)
    return (
        actual_rank is not None
        and required_rank is not None
        and actual_rank >= required_rank
    )


def required_role_for_request(path: str, method: str) -> str:
    method = method.upper()
    unsafe = method in {"POST", "PUT", "PATCH", "DELETE"}

    if path in _ADMIN_ALL_METHOD_PATHS:
        return ADMIN
    if unsafe and (
        path in _ADMIN_UNSAFE_EXACT
        or path.endswith("/force-update")
        or any(path.startswith(prefix) for prefix in _ADMIN_UNSAFE_PREFIXES)
    ):
        return ADMIN

    if method == "GET" and (
        path in _VIEWER_GET_EXACT
        or any(path.startswith(prefix) for prefix in _VIEWER_GET_PREFIXES)
    ):
        return VIEWER
    return OPERATOR
