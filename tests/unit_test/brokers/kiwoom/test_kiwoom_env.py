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
