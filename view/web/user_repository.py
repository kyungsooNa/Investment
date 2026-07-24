"""인증 설정을 읽는 사용자 저장소."""
from __future__ import annotations

import secrets
from dataclasses import dataclass

from view.web.authorization import ADMIN, ROLES
from view.web.security import verify_password


def _config_get(config, key: str, default=None):
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


@dataclass(frozen=True)
class AuthUser:
    username: str
    password_hash: str
    role: str
    enabled: bool = True


class ConfigUserRepository:
    """`auth.users`를 사용하며 기존 단일 계정은 admin으로 호환한다."""

    def __init__(self, auth_config) -> None:
        configured_users = _config_get(auth_config, "users", []) or []
        self._users = [
            AuthUser(
                username=str(_config_get(user, "username", "")),
                password_hash=str(_config_get(user, "password_hash", "")),
                role=str(_config_get(user, "role", "")),
                enabled=bool(_config_get(user, "enabled", True)),
            )
            for user in configured_users
        ]
        if not self._users:
            username = _config_get(auth_config, "username")
            password_hash = _config_get(auth_config, "password_hash")
            if isinstance(username, str) and isinstance(password_hash, str):
                self._users = [
                    AuthUser(
                        username=username,
                        password_hash=password_hash,
                        role=ADMIN,
                    )
                ]

    @property
    def has_configured_users(self) -> bool:
        return bool(self._users)

    def find_enabled(self, username: str) -> AuthUser | None:
        for user in self._users:
            if secrets.compare_digest(username, user.username):
                if user.enabled and user.role in ROLES:
                    return user
                return None
        return None

    def authenticate(self, username: str, password: str) -> AuthUser | None:
        matched = self.find_enabled(username)
        fallback_hash = self._users[0].password_hash if self._users else ""

        password_hash = matched.password_hash if matched else fallback_hash
        password_matches = verify_password(password, password_hash)
        if (
            matched is None
            or not password_matches
        ):
            return None
        return matched
