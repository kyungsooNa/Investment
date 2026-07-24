from types import SimpleNamespace

import pytest

from view.web import security
from view.web.deployment_policy import is_public_operation_blocked


@pytest.fixture(autouse=True)
def reset_security_state():
    security.reset_security_state()
    yield
    security.reset_security_state()


def _auth_config(**overrides):
    config = {
        "secret_key": "signing-key",
        "session_max_age_seconds": 3600,
        "login_max_failures": 3,
        "login_lockout_seconds": 60,
    }
    config.update(overrides)
    return config


def test_password_hash_round_trip_and_plaintext_rejection():
    encoded = security.hash_password("correct horse")

    assert encoded.startswith("pbkdf2_sha256$")
    assert security.verify_password("correct horse", encoded) is True
    assert security.verify_password("wrong", encoded) is False
    assert security.verify_password("correct horse", "correct horse") is False


def test_signed_session_rejects_tampering_and_expiration():
    token, claims = security.issue_session(_auth_config(), "operator")

    assert token != "signing-key"
    assert security.verify_session(token, _auth_config()).username == "operator"
    assert security.verify_session(token + "tampered", _auth_config()) is None
    assert security.verify_session(
        token,
        _auth_config(session_max_age_seconds=-1),
    ) is None
    assert claims.csrf_token


def test_revoked_session_is_rejected():
    token, claims = security.issue_session(_auth_config(), "operator")

    security.revoke_session(claims.session_id)

    assert security.verify_session(token, _auth_config()) is None


def test_csrf_requires_session_cookie_header_match():
    token, claims = security.issue_session(_auth_config(), "operator")
    connection = SimpleNamespace(
        cookies={
            security.SESSION_COOKIE_NAME: token,
            security.CSRF_COOKIE_NAME: claims.csrf_token,
        },
        headers={security.CSRF_HEADER_NAME: claims.csrf_token},
    )

    assert security.verify_csrf(connection, claims) is True

    connection.headers = {security.CSRF_HEADER_NAME: "wrong"}
    assert security.verify_csrf(connection, claims) is False


def test_login_attempt_limiter_locks_and_resets(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(security.time, "monotonic", lambda: now[0])
    limiter = security.LoginAttemptLimiter()
    config = _auth_config(login_max_failures=2, login_lockout_seconds=30)
    key = ("127.0.0.1", "operator")

    assert limiter.is_blocked(key, config) is False
    limiter.record_failure(key, config)
    limiter.record_failure(key, config)
    assert limiter.is_blocked(key, config) is True

    now[0] += 31
    assert limiter.is_blocked(key, config) is False
    limiter.record_failure(key, config)
    limiter.record_success(key)
    assert limiter.is_blocked(key, config) is False


def test_public_mode_blocks_position_sizing_limits_read():
    ctx = SimpleNamespace(
        full_config={"deployment": {"public_mode": True}},
    )

    assert is_public_operation_blocked(
        ctx,
        "/api/position-sizing/limits",
        "GET",
    ) is True
