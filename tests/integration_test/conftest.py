#tests/integration_test/conftest.py

import os
import stat
import shutil
import pytest
import logging
from core.cache.cache_manager import CacheManager
from core.cache.cache_wrapper import ClientWithCache
from core.logger import Logger  # ⬅️ 추가
from unittest.mock import MagicMock


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

@pytest.fixture(scope="function")
def test_logger(request):
    # 📌 현재 conftest.py 기준 ./log 경로 생성
    log_dir = os.path.join(os.path.dirname(__file__), "log")
    logger = Logger(log_dir=log_dir)

    # 실행되는 테스트 케이스 이름 로깅
    tc_name = request.node.name
    logger.operational_logger.info(f"===== [TEST START] {tc_name} =====")
    logger.debug_logger.debug(f"===== [TEST START] {tc_name} =====")

    # MagicMock으로 감싸 호출 검증도 가능하게
    logger_proxy = MagicMock(wraps=logger)
    yield logger_proxy

    # 종료 로그 남기기
    logger_proxy.operational_logger.info(f"===== [TEST END] {tc_name} =====")
    logger_proxy.debug_logger.debug(f"===== [TEST END] {tc_name} =====")

    # 핸들러 정리 (윈도우 잠금 방지)
    for lg in (logger.operational_logger, logger.debug_logger):
        for h in lg.handlers[:]:
            try:
                h.close()
            finally:
                lg.removeHandler(h)