import os
import stat
import shutil
import pytest
from core.cache.cache_manager import CacheManager


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
        os.chmod(path, stat.S_IWRITE)
        func(path)

    if os.path.exists(base_dir):
        shutil.rmtree(base_dir, onerror=on_rm_error)

    yield

    if os.path.exists(base_dir):
        shutil.rmtree(base_dir, onerror=on_rm_error)
