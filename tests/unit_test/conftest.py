import os
import stat
import tempfile
import shutil
import pytest
import time
import asyncio
from core.cache.cache_manager import CacheManager


@pytest.fixture(scope="session")
def test_cache_config():
    # test_base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".cache"))
    test_base_dir = tempfile.mktemp()
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
            time.sleep(0.2)  # 여유시간
            func(path)
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[삭제 실패] {path}: {e}")

    if os.path.exists(base_dir):
        shutil.rmtree(base_dir, onerror=on_rm_error)

    yield

    if os.path.exists(base_dir):
        shutil.rmtree(base_dir, onerror=on_rm_error)

pytest.fixture(autouse=True)
def fast_sleep(monkeypatch, request):
    # 특정 TC만 원래 sleep을 쓰고 싶으면: @pytest.mark.real_sleep
    if request.node.get_closest_marker("real_sleep"):
        return

    # sync sleep 제거
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)

    # async sleep 제거
    async def _fast_async_sleep(*a, **k): return None
    monkeypatch.setattr(asyncio, "sleep", _fast_async_sleep)

