import pytest
from unittest.mock import mock_open, patch
from core.config_loader import load_config


@pytest.fixture
def fake_yaml_content():
    return """
    api_key: "test-key"
    secret_key: "secret"
    """


def test_load_config_success(fake_yaml_content):
    # os.path.exists가 True이고, 파일도 정상적으로 열리는 경우
    with patch("os.path.exists", return_value=True), \
            patch("builtins.open", mock_open(read_data=fake_yaml_content)), \
            patch("yaml.safe_load", return_value={"api_key": "test-key", "secret_key": "secret"}):
        config = load_config("dummy/path/config.yaml")
        assert config["api_key"] == "test-key"
        assert config["secret_key"] == "secret"


def test_load_config_file_not_found():
    # os.path.exists가 False일 때 FileNotFoundError 발생 여부 확인
    with patch("os.path.exists", return_value=False):
        with pytest.raises(FileNotFoundError, match="Config file not found"):
            load_config("nonexistent.yaml")
