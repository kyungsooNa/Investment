import os
import stat
import shutil
import pytest
from core.cache.cache_manager import CacheManager
from core.cache.cache_wrapper import ClientWithCache
import logging


@pytest.fixture(autouse=True)
def patch_cache_wrap_client_for_tests(mocker):
    def custom_cache_wrap_client(client, logger, time_manager, env_fn, config=None):
        fake_config = {
            "cache": {
                "base_dir": os.path.abspath("tests/.cache"),
                "enabled_methods": ["get_data"]
            }
        }
        return ClientWithCache(client, logger, time_manager, env_fn, config=fake_config)

    mocker.patch("brokers.broker_api_wrapper.cache_wrap_client", side_effect=custom_cache_wrap_client)


@pytest.fixture(scope="session")
def test_cache_config():
    test_base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".cache"))
    return {
        "cache": {
            "base_dir": test_base_dir,
            "enabled_methods": ["get_data"],
            "deserializable_classes": []
        }
    }


@pytest.fixture(scope="function")
def cache_manager(test_cache_config):
    return CacheManager(config=test_cache_config)


@pytest.fixture(autouse=True)
def clear_cache_files(test_cache_config):
    base_dir = test_cache_config["cache"]["base_dir"]

    def on_rm_error(func, path, _):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception as e:
            print(f"❌ 파일 삭제 실패: {path} - {e}")

    # ✅ 캐시 디렉토리 삭제 전 log 핸들 닫기
    for handler in logging.root.handlers[:]:
        handler.close()
        logging.root.removeHandler(handler)

    if os.path.exists(base_dir):
        shutil.rmtree(base_dir, onerror=on_rm_error)

    yield

    # ✅ 캐시 디렉토리 삭제 후에도 log 핸들 정리
    for handler in logging.root.handlers[:]:
        handler.close()
        logging.root.removeHandler(handler)

    if os.path.exists(base_dir):
        shutil.rmtree(base_dir, onerror=on_rm_error)