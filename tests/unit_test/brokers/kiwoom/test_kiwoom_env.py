from unittest.mock import AsyncMock, MagicMock

import pytest

from brokers.kiwoom.kiwoom_env import KiwoomApiEnv


def test_kiwoom_env_selects_paper_config_by_default():
    env = KiwoomApiEnv({
        "kiwoom": {
            "is_paper_trading": True,
            "paper": {
                "app_key": "paper-key",
                "app_secret": "paper-secret",
                "account_no": "paper-account",
                "base_url": "https://mockapi.kiwoom.com",
                "websocket_url": "wss://mockapi.kiwoom.com:10000",
            },
            "real": {
                "app_key": "real-key",
                "app_secret": "real-secret",
                "account_no": "real-account",
                "base_url": "https://api.kiwoom.com",
                "websocket_url": "wss://api.kiwoom.com:10000",
            },
            "token": {
                "paper_path": "config/token_kiwoom_paper.json",
                "real_path": "config/token_kiwoom_real.json",
            },
        }
    })

    config = env.get_full_config()

    assert env.is_paper_trading is True
    assert config["api_key"] == "paper-key"
    assert config["api_secret_key"] == "paper-secret"
    assert config["stock_account_number"] == "paper-account"
    assert config["base_url"] == "https://mockapi.kiwoom.com"
    assert config["websocket_url"] == "wss://mockapi.kiwoom.com:10000"


def test_kiwoom_env_can_switch_to_real_config():
    env = KiwoomApiEnv({
        "kiwoom": {
            "is_paper_trading": True,
            "paper": {
                "app_key": "paper-key",
                "app_secret": "paper-secret",
                "account_no": "paper-account",
            },
            "real": {
                "app_key": "real-key",
                "app_secret": "real-secret",
                "account_no": "real-account",
            },
        }
    })

    env.set_trading_mode(False)

    assert env.is_paper_trading is False
    assert env.get_base_url() == "https://api.kiwoom.com"
    assert env.get_websocket_url() == "wss://api.kiwoom.com:10000"
    assert env.active_config["api_key"] == "real-key"
    assert env.active_config["stock_account_number"] == "real-account"


def test_kiwoom_env_raises_when_active_app_key_missing():
    with pytest.raises(ValueError, match="kiwoom.paper.app_key"):
        KiwoomApiEnv({
            "kiwoom": {
                "is_paper_trading": True,
                "paper": {
                    "app_secret": "paper-secret",
                    "account_no": "paper-account",
                },
                "real": {
                    "app_key": "real-key",
                    "app_secret": "real-secret",
                    "account_no": "real-account",
                },
            }
        })


def _config(**overrides):
    base = {
        "kiwoom": {
            "is_paper_trading": True,
            "paper": {
                "app_key": "paper-key",
                "app_secret": "paper-secret",
                "account_no": "paper-account",
            },
            "real": {
                "app_key": "real-key",
                "app_secret": "real-secret",
                "account_no": "real-account",
            },
        }
    }
    base["kiwoom"].update(overrides)
    return base


def test_kiwoom_env_accepts_config_without_kiwoom_wrapper():
    env = KiwoomApiEnv(_config()["kiwoom"])

    assert env.is_paper_trading is True
    assert env.active_config["api_key"] == "paper-key"


def test_kiwoom_env_falls_back_to_default_urls():
    env = KiwoomApiEnv(_config())

    assert env.get_base_url() == "https://mockapi.kiwoom.com"
    assert env.get_websocket_url() == "wss://mockapi.kiwoom.com:10000"


def test_set_trading_mode_is_noop_when_mode_unchanged():
    env = KiwoomApiEnv(_config())
    before = env.active_config

    env.set_trading_mode(True)

    assert env.active_config is before


def test_base_headers_declare_json_content_type():
    env = KiwoomApiEnv(_config())

    assert env.get_base_headers() == {"Content-Type": "application/json;charset=UTF-8"}


@pytest.mark.parametrize(
    "token_config, paper_expected, real_expected",
    [
        ({}, "config/token_kiwoom_paper.json", "config/token_kiwoom_real.json"),
        ({"path": "shared.json"}, "shared.json", "config/token_kiwoom_real.json"),
        (
            {"paper_path": "p.json", "real_path": "r.json", "path": "shared.json"},
            "p.json",
            "r.json",
        ),
    ],
)
def test_token_paths_resolve_per_mode(token_config, paper_expected, real_expected):
    env = KiwoomApiEnv(_config(token=token_config))

    assert env._token_provider_paper.token_file_path == paper_expected
    assert env._token_provider_real.token_file_path == real_expected


@pytest.mark.asyncio
async def test_get_access_token_delegates_to_active_provider():
    env = KiwoomApiEnv(_config())
    provider = MagicMock()
    provider.get_access_token = AsyncMock(return_value="paper-token")
    env._token_provider = provider

    token = await env.get_access_token()

    assert token == "paper-token"
    provider.invalidate_token.assert_not_called()
    assert provider.get_access_token.await_args.kwargs == {
        "base_url": "https://mockapi.kiwoom.com",
        "app_key": "paper-key",
        "app_secret": "paper-secret",
    }


@pytest.mark.asyncio
async def test_get_access_token_invalidates_first_when_forced():
    env = KiwoomApiEnv(_config())
    provider = MagicMock()
    provider.get_access_token = AsyncMock(return_value="fresh-token")
    env._token_provider = provider

    token = await env.get_access_token(force_new=True)

    assert token == "fresh-token"
    provider.invalidate_token.assert_called_once()


@pytest.mark.asyncio
async def test_refresh_token_delegates_to_active_provider():
    env = KiwoomApiEnv(_config())
    provider = MagicMock()
    provider.refresh_token = AsyncMock()
    env._token_provider = provider

    await env.refresh_token()

    assert provider.refresh_token.await_args.kwargs["base_url"] == "https://mockapi.kiwoom.com"


@pytest.mark.asyncio
async def test_token_operations_raise_without_provider():
    env = KiwoomApiEnv(_config())
    env._token_provider = None

    with pytest.raises(RuntimeError):
        await env.get_access_token()
    with pytest.raises(RuntimeError):
        await env.refresh_token()


def test_invalidate_token_is_safe_without_provider():
    env = KiwoomApiEnv(_config())
    provider = MagicMock()
    env._token_provider = provider

    env.invalidate_token()
    provider.invalidate_token.assert_called_once()

    env._token_provider = None
    env.invalidate_token()
