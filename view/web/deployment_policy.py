"""외부 공개 모드와 실전 주문 master gate 정책."""
from __future__ import annotations


def _config_get(config, key: str, default=None):
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


config_value = _config_get


def config_section(ctx, name: str):
    return _config_get(getattr(ctx, "full_config", None), name, None)


def is_public_mode(ctx) -> bool:
    deployment = config_section(ctx, "deployment")
    return _config_get(deployment, "public_mode", False) is True


def is_host_allowed(ctx, host_header: str) -> bool:
    if not is_public_mode(ctx):
        return True

    host = host_header.strip().lower().rstrip(".")
    if host.startswith("["):
        closing_bracket = host.find("]")
        host = host[1:closing_bracket] if closing_bracket >= 0 else ""
    elif ":" in host:
        candidate, port = host.rsplit(":", 1)
        if port.isdigit():
            host = candidate

    deployment = config_section(ctx, "deployment")
    allowed_hosts = _config_get(deployment, "allowed_hosts", [])
    normalized = {
        str(allowed).strip().lower().rstrip(".")
        for allowed in allowed_hosts
        if str(allowed).strip()
    }
    return host in normalized


def live_trading_block(ctx, *, require_legacy_overseas_gate: bool = False):
    """실전 주문 차단 시 ``(rule, message)``를, 허용 시 ``None``을 반환한다."""
    deployment = config_section(ctx, "deployment")
    if bool(_config_get(deployment, "public_mode", False)):
        return (
            "public_mode_live_trading_blocked",
            "공개 모드에서는 실전 주문이 차단됩니다.",
        )
    if not bool(_config_get(deployment, "allow_live_trading", False)):
        return (
            "global_live_trading_disabled",
            "실전 주문은 deployment.allow_live_trading=true 설정 전까지 차단됩니다.",
        )
    if require_legacy_overseas_gate:
        overseas = config_section(ctx, "overseas_stock")
        if not bool(_config_get(overseas, "allow_live_trading", False)):
            return (
                "overseas_live_trading_disabled",
                "해외 설정 마이그레이션 중에는 overseas_stock.allow_live_trading=true도 필요합니다.",
            )
    return None


def is_public_operation_blocked(ctx, path: str, method: str) -> bool:
    if not is_public_mode(ctx):
        return False
    method = method.upper()
    if path == "/api/position-sizing/limits":
        return True
    if method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    if path in {"/api/system/shutdown", "/api/system/restart"}:
        return True
    if path.endswith("/force-update"):
        return True
    return any(
        path.startswith(prefix)
        for prefix in (
            "/api/scheduler/",
            "/api/position-sizing/limits",
            "/api/kill-switch/",
        )
    )
