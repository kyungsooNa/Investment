import os
import stat
import shutil
import time
import pytest
from unittest.mock import patch
from core.cache import cache_config

@pytest.fixture(autouse=True, scope="session")
def patch_cache_config():
    # ✅ 테스트 전용 config 경로 설정
    test_config_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "config", "cache_config.yaml")
    )

    # ✅ 실제 config 파일 로딩 (yaml 파싱까지 수행)
    config_data = cache_config.load_cache_config(test_config_path)

    # ✅ 해당 데이터로 load_cache_config를 patch
    with patch("core.cache.cache_config.load_cache_config", return_value=config_data):
        yield

@pytest.fixture(autouse=True)
def clear_cache_files():
    """
    테스트 실행 전후 .cache 디렉토리 및 메모리 캐시 제거
    """
    from core.cache.cache_manager import get_cache_manager  # ✅ patch 이후에 import

    base_dir = os.path.dirname(__file__)
    cache_dir = os.path.join(base_dir, ".cache")  # ./tests/.cache

    def on_rm_error(func, path, exc_info):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            time.sleep(0.1)
            try:
                func(path)
            except Exception:
                pass

    # ✅ 메모리 캐시 초기화
    get_cache_manager().clear()

    # ✅ 파일 캐시 삭제
    if os.path.exists(cache_dir):
        try:
            shutil.rmtree(cache_dir, onerror=on_rm_error)
        except Exception as e:
            print(f"⚠️ 캐시 디렉토리 삭제 실패: {e}")

    yield  # 테스트 실행

    # ✅ 테스트 종료 후도 정리
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir, onerror=on_rm_error)
