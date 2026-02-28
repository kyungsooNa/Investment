import pytest
from unittest.mock import mock_open, patch
from config.config_loader import load_config, load_configs
import yaml
import json


@pytest.fixture
def fake_yaml_content():
    return """
    api_key: "test-key"
    secret_key: "secret"
    """


def test_load_config_success(fake_yaml_content):
    # yaml.safe_load가 정상적으로 동작하는 경우
    with patch("builtins.open", mock_open(read_data=fake_yaml_content)), \
            patch("yaml.safe_load", return_value={"api_key": "test-key", "secret_key": "secret"}):
        config = load_config("dummy/path/config.yaml")
        assert config["api_key"] == "test-key"
        assert config["secret_key"] == "secret"


def test_load_config_file_not_found():
    # 파일이 없을 때 FileNotFoundError 발생 여부 확인
    with patch("builtins.open", side_effect=FileNotFoundError):
        with pytest.raises(FileNotFoundError, match="파일을 찾을 수 없습니다"):
            load_config("nonexistent.yaml")

def test_load_configs_success():
    """load_configs 함수가 3개의 설정 파일을 로드하고 병합하는지 테스트"""
    with patch("config.config_loader.load_config") as mock_load:
        mock_load.side_effect = [
            {"main": 1},
            {"tr_id": 2},
            {"kis": 3}
        ]
        
        config = load_configs()
        
        assert config == {"main": 1, "tr_id": 2, "kis": 3}
        assert mock_load.call_count == 3

def test_load_config_json_fallback():
    """yaml.safe_load 실패(ImportError) 시 json.load 시도 테스트"""
    json_content = '{"key": "value"}'
    
    with patch("builtins.open", mock_open(read_data=json_content)) as mock_file:
        # yaml.safe_load가 ImportError를 발생시키도록 설정 (yaml 모듈이 없는 상황 시뮬레이션 등)
        with patch("yaml.safe_load", side_effect=ImportError):
            with patch("json.load", return_value={"key": "value"}) as mock_json_load:
                result = load_config("dummy.json")
                
                assert result == {"key": "value"}
                # 파일 포인터를 처음으로 되돌렸는지 확인
                mock_file.return_value.seek.assert_called_with(0)
                mock_json_load.assert_called()

def test_load_config_invalid_yaml_format():
    """잘못된 YAML 형식일 때 ValueError 발생 테스트"""
    with patch("builtins.open", mock_open(read_data="invalid: yaml: content")):
        with patch("yaml.safe_load", side_effect=yaml.YAMLError):
            with pytest.raises(ValueError, match="설정 파일 형식이 올바르지 않습니다"):
                load_config("invalid.yaml")

def test_load_config_invalid_json_format():
    """잘못된 JSON 형식일 때 ValueError 발생 테스트 (ImportError 발생 후 JSON 시도 시)"""
    with patch("builtins.open", mock_open(read_data="{invalid json")):
        with patch("yaml.safe_load", side_effect=ImportError):
            with patch("json.load", side_effect=json.JSONDecodeError("msg", "doc", 0)):
                with pytest.raises(ValueError, match="설정 파일 형식이 올바르지 않습니다"):
                    load_config("invalid.json")
