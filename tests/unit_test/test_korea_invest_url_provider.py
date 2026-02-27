# tests/unit_test/test_korea_invest_url_provider.py
import pytest
from unittest.mock import MagicMock, patch
from enum import Enum

from brokers.korea_investment.korea_invest_url_provider import KoreaInvestUrlProvider
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv

# --- Fixtures ---

@pytest.fixture
def mock_env():
    """KoreaInvestApiEnv의 Mock 객체를 생성합니다."""
    env = MagicMock(spec=KoreaInvestApiEnv)
    env.get_base_url.return_value = "https://mock.api.com"
    return env

@pytest.fixture
def mock_paths():
    """테스트용 경로 딕셔너리를 생성합니다."""
    return {
        "get_price": "/api/v1/price",
        "get_balance": "/api/v1/balance"
    }

@pytest.fixture
def url_provider(mock_env, mock_paths):
    """KoreaInvestUrlProvider의 테스트용 인스턴스를 생성합니다."""
    return KoreaInvestUrlProvider(get_base_url=mock_env.get_base_url, paths=mock_paths)

class DummyEnum(Enum):
    GET_PRICE = "get_price"

# --- Test Cases ---

def test_init_with_none_paths():
    """__init__: paths가 None일 때도 정상적으로 초기화되는지 테스트합니다."""
    provider = KoreaInvestUrlProvider(get_base_url=lambda: "base", paths=None)
    assert provider._paths == {}

def test_from_env_and_kis_config_success(mock_env):
    """from_env_and_kis_config: 정상적인 config로 인스턴스 생성 성공 테스트."""
    kis_config = {"paths": {"key1": "/path1"}}
    provider = KoreaInvestUrlProvider.from_env_and_kis_config(mock_env, kis_config_override=kis_config)
    assert provider.has("key1")
    assert provider.path("key1") == "/path1"
    assert provider._get_base_url == mock_env.get_base_url

@patch('brokers.korea_investment.korea_invest_url_provider.load_config')
def test_from_env_and_kis_config_uses_load_config(mock_load_config, mock_env):
    """from_env_and_kis_config: kis_config_override가 없을 때 load_config를 사용하는지 테스트."""
    mock_load_config.return_value = {"paths": {"key_from_file": "/path_from_file"}}
    
    provider = KoreaInvestUrlProvider.from_env_and_kis_config(mock_env)
    
    mock_load_config.assert_called_once()
    assert provider.has("key_from_file")
    assert provider.path("key_from_file") == "/path_from_file"

def test_from_env_and_kis_config_no_paths_raises_error(mock_env):
    """from_env_and_kis_config: config에 'paths'가 없거나 비어있을 때 ValueError 발생 테스트."""
    with pytest.raises(ValueError, match="paths가 없거나 비었습니다"):
        KoreaInvestUrlProvider.from_env_and_kis_config(mock_env, kis_config_override={})

    with pytest.raises(ValueError, match="paths가 없거나 비었습니다"):
        KoreaInvestUrlProvider.from_env_and_kis_config(mock_env, kis_config_override={"paths": {}})
        
    with pytest.raises(ValueError, match="paths가 없거나 비었습니다"):
        KoreaInvestUrlProvider.from_env_and_kis_config(mock_env, kis_config_override={"paths": "not_a_dict"})

def test_has(url_provider):
    """has: 키 존재 여부 확인 테스트."""
    assert url_provider.has("get_price") is True
    assert url_provider.has("non_existent_key") is False

def test_keys(url_provider, mock_paths):
    """keys: 등록된 모든 키를 반환하는지 테스트."""
    assert set(url_provider.keys()) == set(mock_paths.keys())

def test_path_success(url_provider):
    """path: 존재하는 키에 대해 올바른 경로를 반환하는지 테스트."""
    assert url_provider.path("get_balance") == "/api/v1/balance"

def test_path_key_error(url_provider):
    """path: 존재하지 않는 키에 대해 KeyError 발생 테스트."""
    with pytest.raises(KeyError, match="'non_existent_key'가 없습니다"):
        url_provider.path("non_existent_key")

def test_url_with_key(url_provider):
    """url: 키를 사용하여 전체 URL을 생성하는지 테스트."""
    assert url_provider.url("get_price") == "https://mock.api.com/api/v1/price"

def test_url_with_path(url_provider):
    """url: 키가 아닌 직접 경로를 사용하여 전체 URL을 생성하는지 테스트."""
    assert url_provider.url("/api/v2/new_endpoint") == "https://mock.api.com/api/v2/new_endpoint"

def test_url_with_enum(url_provider):
    """url: Enum 멤버를 사용하여 전체 URL을 생성하는지 테스트."""
    assert url_provider.url(DummyEnum.GET_PRICE) == "https://mock.api.com/api/v1/price"

def test_url_with_slashes(mock_paths):
    """url: base_url과 path의 슬래시 처리가 올바른지 테스트."""
    # Case 1: base_url에 trailing slash
    provider1 = KoreaInvestUrlProvider(get_base_url=lambda: "https://base.com/", paths=mock_paths)
    assert provider1.url("get_price") == "https://base.com/api/v1/price"
    
    # Case 2: path에 leading slash 없음
    paths_no_slash = {"get_price": "api/v1/price"}
    provider2 = KoreaInvestUrlProvider(get_base_url=lambda: "https://base.com", paths=paths_no_slash)
    assert provider2.url("get_price") == "https://base.com/api/v1/price"

def test_url_empty_base_url_raises_error(url_provider, mock_env):
    """url: base_url이 비어있을 때 ValueError 발생 테스트."""
    mock_env.get_base_url.return_value = ""
    with pytest.raises(ValueError, match="env.get_base_url\\(\\)이 빈 값입니다"):
        url_provider.url("get_price")
        
    mock_env.get_base_url.return_value = None
    with pytest.raises(ValueError, match="env.get_base_url\\(\\)이 빈 값입니다"):
        url_provider.url("get_price")