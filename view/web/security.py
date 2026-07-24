"""웹 인증의 비밀번호, 세션, CSRF, 로그인 제한 primitives."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from itsdangerous import BadData, URLSafeTimedSerializer

SESSION_COOKIE_NAME = "access_token"
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"

_SESSION_SALT = "investment.web.session.v1"
_PASSWORD_SCHEME = "pbkdf2_sha256"
_PASSWORD_ITERATIONS = 310_000


def _config_get(config, key: str, default=None):
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str, *, iterations: int = _PASSWORD_ITERATIONS) -> str:
    """PBKDF2-SHA256 형식으로 비밀번호를 해시한다."""
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return f"{_PASSWORD_SCHEME}${iterations}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    """지원하는 해시 형식만 검증하며 평문 설정은 허용하지 않는다."""
    if not isinstance(password, str) or not isinstance(encoded, str):
        return False
    try:
        scheme, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
        if scheme != _PASSWORD_SCHEME:
            return False
        iterations = int(raw_iterations)
        salt = _b64decode(raw_salt)
        expected = _b64decode(raw_digest)
    except (TypeError, ValueError):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual, expected)


@dataclass(frozen=True)
class SessionClaims:
    username: str
    session_id: str
    csrf_token: str


_active_sessions: dict[str, float] = {}


def _serializer(auth_config) -> URLSafeTimedSerializer | None:
    secret_key = _config_get(auth_config, "secret_key")
    if not isinstance(secret_key, str) or not secret_key:
        return None
    return URLSafeTimedSerializer(secret_key=secret_key, salt=_SESSION_SALT)


def issue_session(auth_config, username: str) -> tuple[str, SessionClaims]:
    """서명 세션을 발급하고 현재 프로세스의 활성 세션으로 등록한다."""
    serializer = _serializer(auth_config)
    if serializer is None:
        raise ValueError("auth.secret_key is required")
    max_age = int(_config_get(auth_config, "session_max_age_seconds", 3600))
    if max_age <= 0:
        raise ValueError("session_max_age_seconds must be positive")

    claims = SessionClaims(
        username=username,
        session_id=secrets.token_urlsafe(24),
        csrf_token=secrets.token_urlsafe(32),
    )
    token = serializer.dumps(
        {
            "sub": claims.username,
            "sid": claims.session_id,
            "csrf": claims.csrf_token,
        }
    )
    _active_sessions[claims.session_id] = time.time() + max_age
    return token, claims


def verify_session(token: str | None, auth_config) -> SessionClaims | None:
    """세션 서명·만료·활성 상태를 모두 검증한다."""
    serializer = _serializer(auth_config)
    if serializer is None or not isinstance(token, str) or not token:
        return None
    try:
        max_age = int(_config_get(auth_config, "session_max_age_seconds", 3600))
        payload = serializer.loads(token, max_age=max_age)
        claims = SessionClaims(
            username=payload["sub"],
            session_id=payload["sid"],
            csrf_token=payload["csrf"],
        )
    except (BadData, KeyError, TypeError, ValueError):
        return None

    expires_at = _active_sessions.get(claims.session_id)
    if expires_at is None or expires_at <= time.time():
        _active_sessions.pop(claims.session_id, None)
        return None
    return claims


def revoke_session(session_id: str) -> None:
    _active_sessions.pop(session_id, None)


def verify_csrf(connection, claims: SessionClaims) -> bool:
    cookie_token = connection.cookies.get(CSRF_COOKIE_NAME)
    header_token = connection.headers.get(CSRF_HEADER_NAME)
    if not isinstance(cookie_token, str) or not isinstance(header_token, str):
        return False
    return (
        secrets.compare_digest(cookie_token, claims.csrf_token)
        and secrets.compare_digest(header_token, claims.csrf_token)
    )


class LoginAttemptLimiter:
    """프로세스 내 IP·사용자 조합별 로그인 실패 제한."""

    def __init__(self) -> None:
        self._attempts: dict[tuple[str, str], tuple[int, float]] = {}

    def is_blocked(self, key: tuple[str, str], auth_config) -> bool:
        count, locked_until = self._attempts.get(key, (0, 0.0))
        now = time.monotonic()
        if locked_until > now:
            return True
        if locked_until:
            self._attempts.pop(key, None)
        return False

    def record_failure(self, key: tuple[str, str], auth_config) -> None:
        max_failures = max(1, int(_config_get(auth_config, "login_max_failures", 5)))
        lockout_seconds = max(
            1,
            int(_config_get(auth_config, "login_lockout_seconds", 60)),
        )
        count, locked_until = self._attempts.get(key, (0, 0.0))
        if locked_until and locked_until <= time.monotonic():
            count = 0
        count += 1
        if count >= max_failures:
            locked_until = time.monotonic() + lockout_seconds
        self._attempts[key] = (count, locked_until)

    def record_success(self, key: tuple[str, str]) -> None:
        self._attempts.pop(key, None)

    def clear(self) -> None:
        self._attempts.clear()


login_attempt_limiter = LoginAttemptLimiter()


def reset_security_state() -> None:
    """테스트와 프로세스 재초기화를 위한 메모리 상태 초기화."""
    _active_sessions.clear()
    login_attempt_limiter.clear()
