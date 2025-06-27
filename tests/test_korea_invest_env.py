from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv

def test_get_full_config_returns_correct_values():
    config_data = {
        "paper_api_key": "paper-key",
        "paper_api_secret_key": "paper-secret",
        "paper_stock_account_number": "123-45-67890",
        "paper_url": "https://paper-api.test",
        "paper_websocket_url": "wss://paper-ws.test",
        "htsid": "mock-htsid",
        "custtype": "P",
        "is_paper_trading": True,
        "tr_ids": {"some": "value"},
        "my_agent": "test-agent"
    }

    env = KoreaInvestApiEnv(config_data)

    result = env.get_full_config()

    assert result["api_key"] == "paper-key"
    assert result["api_secret_key"] == "paper-secret"
    assert result["stock_account_number"] == "123-45-67890"
    assert result["base_url"] == "https://paper-api.test"
    assert result["websocket_url"] == "wss://paper-ws.test"
    assert result["htsid"] == "mock-htsid"
    assert result["custtype"] == "P"
    assert result["is_paper_trading"] is True
    assert result["tr_ids"] == {"some": "value"}
    assert result["_env_instance"] == env
