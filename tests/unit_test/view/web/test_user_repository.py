from view.web.security import hash_password
from view.web.user_repository import ConfigUserRepository


def test_config_user_repository_authenticates_role_user():
    repository = ConfigUserRepository(
        {
            "users": [
                {
                    "username": "ops",
                    "password_hash": hash_password("password", iterations=1_000),
                    "role": "operator",
                }
            ]
        }
    )

    user = repository.authenticate("ops", "password")

    assert user is not None
    assert user.username == "ops"
    assert user.role == "operator"


def test_config_user_repository_rejects_disabled_or_wrong_password():
    repository = ConfigUserRepository(
        {
            "users": [
                {
                    "username": "root",
                    "password_hash": hash_password("password", iterations=1_000),
                    "role": "admin",
                    "enabled": False,
                }
            ]
        }
    )

    assert repository.authenticate("root", "password") is None
    assert repository.authenticate("root", "wrong") is None


def test_config_user_repository_maps_legacy_single_user_to_admin():
    repository = ConfigUserRepository(
        {
            "username": "legacy",
            "password_hash": hash_password("password", iterations=1_000),
        }
    )

    user = repository.authenticate("legacy", "password")

    assert user is not None
    assert user.role == "admin"
