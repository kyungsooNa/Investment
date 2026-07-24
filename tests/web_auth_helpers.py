"""웹 통합 테스트용 서명 세션 클라이언트 옵션."""
from view.web.security import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    issue_session,
)


def authenticated_client_options(ctx):
    full_config = ctx.full_config
    auth_config = (
        full_config.get("auth", {})
        if isinstance(full_config, dict)
        else getattr(full_config, "auth", None)
    )
    username = (
        auth_config.get("username", "test-operator")
        if isinstance(auth_config, dict)
        else getattr(auth_config, "username", None) or "test-operator"
    )
    token, claims = issue_session(auth_config, username)
    return {
        "cookies": {
            SESSION_COOKIE_NAME: token,
            CSRF_COOKIE_NAME: claims.csrf_token,
        },
        "headers": {CSRF_HEADER_NAME: claims.csrf_token},
    }
