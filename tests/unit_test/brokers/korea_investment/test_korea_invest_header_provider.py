import pytest
from unittest.mock import MagicMock
from brokers.korea_investment.korea_invest_header_provider import KoreaInvestHeaderProvider, build_header_provider_from_env

@pytest.fixture
def provider():
    return KoreaInvestHeaderProvider(my_agent="test-agent", appkey="key", appsecret="secret")

def test_init(provider):
    headers = provider.build()
    assert headers["User-Agent"] == "test-agent"
    assert headers["appkey"] == "key"
    assert headers["appsecret"] == "secret"
    assert headers["custtype"] == "P"

def test_set_auth_bearer(provider):
    provider.set_auth_bearer("token123")
    assert provider.build()["Authorization"] == "Bearer token123"

def test_set_app_keys(provider):
    provider.set_app_keys("new_key", "new_secret")
    headers = provider.build()
    assert headers["appkey"] == "new_key"
    assert headers["appsecret"] == "new_secret"

def test_set_volatile_headers(provider):
    # tr_id
    provider.set_tr_id("TR123")
    assert provider.build()["tr_id"] == "TR123"
    provider.set_tr_id(None)
    assert "tr_id" not in provider.build()

    # custtype
    provider.set_custtype("B")
    assert provider.build()["custtype"] == "B"
    provider.set_custtype(None)
    assert "custtype" not in provider.build()

    # hashkey
    provider.set_hashkey("hash123")
    assert provider.build()["hashkey"] == "hash123"
    provider.set_hashkey(None)
    assert "hashkey" not in provider.build()

def test_set_gt_uid(provider):
    # Explicit
    provider.set_gt_uid("uid123")
    assert provider.build()["gt_uid"] == "uid123"
    
    # Auto-generate
    provider.set_gt_uid(None)
    val = provider.build()["gt_uid"]
    assert isinstance(val, str)
    assert len(val) > 0
    assert val != "uid123"

def test_clear_order_headers(provider):
    provider.set_hashkey("h")
    provider.set_gt_uid("u")
    provider.set_tr_id("t")
    
    provider.clear_order_headers()
    
    headers = provider.build()
    assert "hashkey" not in headers
    assert "gt_uid" not in headers
    assert headers["tr_id"] == "t" # Should remain

def test_sync_from_env(provider):
    mock_env = MagicMock()
    mock_env.active_config = {
        "api_key": "env_key",
        "api_secret_key": "env_secret",
        "custtype": "B"
    }
    
    provider.sync_from_env(mock_env)
    
    headers = provider.build()
    assert headers["appkey"] == "env_key"
    assert headers["appsecret"] == "env_secret"
    assert headers["custtype"] == "B"

def test_sync_from_env_empty_config(provider):
    mock_env = MagicMock()
    mock_env.active_config = None
    
    provider.sync_from_env(mock_env)
    
    headers = provider.build()
    # Empty strings are filtered out by build()
    assert "appkey" not in headers
    assert "appsecret" not in headers
    assert headers["custtype"] == "P" # Default "P"

def test_temp_context_manager(provider):
    provider.set_custtype("P")
    
    with provider.temp(tr_id="TEMP_TR", custtype="B", hashkey="HASH"):
        h = provider.build()
        assert h["tr_id"] == "TEMP_TR"
        assert h["custtype"] == "B"
        assert h["hashkey"] == "HASH"
    
    # Restore
    h = provider.build()
    assert "tr_id" not in h
    assert h["custtype"] == "P"
    assert "hashkey" not in h

def test_fork(provider):
    provider.set_tr_id("ORIG_TR")
    clone = provider.fork()
    
    assert clone.build()["tr_id"] == "ORIG_TR"
    assert clone is not provider
    assert clone._base is not provider._base
    assert clone._volatile is not provider._volatile
    
    # Modify clone
    clone.set_tr_id("NEW_TR")
    assert provider.build()["tr_id"] == "ORIG_TR"
    assert clone.build()["tr_id"] == "NEW_TR"

def test_build_header_provider_from_env():
    mock_env = MagicMock()
    mock_env.my_agent = "env-agent"
    
    p = build_header_provider_from_env(mock_env)
    assert p.build()["User-Agent"] == "env-agent"
    
    mock_env_no_agent = MagicMock()
    del mock_env_no_agent.my_agent
    # getattr(env, "my_agent", "python-client")
    p2 = build_header_provider_from_env(mock_env_no_agent)
    assert p2.build()["User-Agent"] == "python-client"

def test_build_filters_empty_values(provider):
    # Manually inject empty value
    provider._base["empty_key"] = ""
    provider._volatile["none_key"] = None
    
    h = provider.build()
    assert "empty_key" not in h
    assert "none_key" not in h