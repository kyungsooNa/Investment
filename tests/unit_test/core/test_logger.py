import os
import time
import glob
import logging
import json
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

# 실제 core.logger 경로에 맞게 수정
from core.logger import (
    Logger, get_strategy_logger, get_performance_logger,
    get_streaming_logger, StreamingEventLogger,
    get_cache_event_logger, CacheEventLogger,
    JsonFormatter, reset_log_timestamp_for_test, SizeTimeRotatingFileHandler,
)
from repositories.cache import _LRUCache, _LFUCache
from repositories.stock_price_repository import StockPriceRepository
from repositories.stock_ohlcv_repository import StockOhlcvRepository


@pytest.fixture
def clean_logger_instance(tmp_path):
    """
    각 테스트마다 독립적인 로거 인스턴스와 로그 디렉토리를 제공하는 픽스처.
    기존 root 로거 핸들러를 정리하여 테스트 간 간섭을 방지합니다.
    """
    # 테스트 로거 디렉토리 설정
    log_dir = tmp_path / "logs"
    # Logger 클래스가 common 디렉토리를 생성하므로 미리 만들 필요 없음

    # 전역 타임스탬프 리셋
    reset_log_timestamp_for_test()
    
    # 기존 root 로거 핸들러 백업 및 제거
    original_handlers = logging.root.handlers[:]
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    # 로거 인스턴스 생성
    logger_instance = Logger(log_dir=str(log_dir))

    yield logger_instance, log_dir.joinpath("common") # 검증을 위해 common 디렉토리 경로 반환

    # 테스트 후 정리
    # 로거 핸들러 닫기 (파일 잠금 해제)
    for handler in logging.getLogger('operational_logger').handlers:
        handler.close()
        logging.getLogger('operational_logger').removeHandler(handler)
    for handler in logging.getLogger('debug_logger').handlers:
        handler.close()
        logging.getLogger('debug_logger').removeHandler(handler)

    # 기존 root 로거 핸들러 복원
    logging.root.handlers = original_handlers


def test_logger_creates_log_files(clean_logger_instance):
    logger, common_log_dir = clean_logger_instance

    # When
    logger.info("info message")
    logger.debug("debug message")
    logger.warning("warning message")
    logger.error("error message")
    logger.critical("critical message")

    # Then
    # `logs/common` 디렉토리에서 로그 파일 검색
    log_files = list(common_log_dir.glob("*.log"))
    assert len(log_files) == 2  # operational, debug

    # 파일 이름 형식 확인
    assert any("debug" in f.name for f in log_files)
    assert any("operational" in f.name for f in log_files)

    for f in log_files:
        content = f.read_text(encoding='utf-8')
        assert "info message" in content
        assert "warning message" in content
        assert "error message" in content
        assert "critical message" in content
        if "debug" in f.name:
            assert "debug message" in content
            # %(filename)s:%(lineno)d 포맷터로 호출자 정보가 포맷에 포함됨
            assert f"{os.path.basename(__file__)}:" in content and "error message" in content
            assert f"{os.path.basename(__file__)}:" in content and "critical message" in content
        else:
            assert "debug message" not in content


def test_logger_error_with_exc_info(clean_logger_instance):
    """
    error() 메서드가 exc_info=True로 호출될 때의 로깅을 테스트합니다.
    """
    logger, common_log_dir = clean_logger_instance
    message = "Test error with exception"
    try:
        raise ValueError("Something went wrong")
    except ValueError as e:
        logger.error(message, exc_info=True)

    # Then
    log_files = list(common_log_dir.glob("*.log"))
    assert len(log_files) == 2

    for f in log_files:
        content = f.read_text(encoding='utf-8')
        assert message in content
        assert "ValueError: Something went wrong" in content # 예외 정보 포함 확인
        assert "Traceback" in content # 스택 트레이스 포함 확인


def test_logger_critical_with_exc_info(clean_logger_instance):
    """
    critical() 메서드가 exc_info=True로 호출될 때의 로깅을 테스트합니다.
    """
    logger, common_log_dir = clean_logger_instance
    message = "Test critical with exception"
    try:
        raise RuntimeError("Critical error occurred")
    except RuntimeError as e:
        logger.critical(message, exc_info=True)

    # Then
    log_files = list(common_log_dir.glob("*.log"))
    assert len(log_files) == 2

    for f in log_files:
        content = f.read_text(encoding='utf-8')
        assert message in content
        assert "RuntimeError: Critical error occurred" in content
        assert "Traceback" in content


def test_logger_creates_log_dir_if_not_exists(tmp_path):
    """
    TC: 로그 디렉토리가 존재하지 않을 때 Logger 초기화 시 디렉토리가 생성되는지 테스트합니다.
    이는 core/logger.py의 23-24 라인을 커버합니다.
    """
    # given: tmp_path는 각 테스트마다 비어 있는 임시 디렉토리를 제공합니다.
    # 따라서, tmp_path/non_existent_logs_dir은 처음에는 존재하지 않습니다.
    non_existent_log_dir = tmp_path / "non_existent_logs_dir"

    # when: Logger 인스턴스를 생성할 때, 해당 디렉토리가 생성되어야 합니다.
    # 기존 root 로거 핸들러를 정리하여 테스트 간 간섭을 방지
    original_handlers = logging.root.handlers[:]
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    
    reset_log_timestamp_for_test()
    logger = Logger(log_dir=str(non_existent_log_dir))

    # then: 로그 디렉토리가 성공적으로 생성되었는지 확인
    assert non_existent_log_dir.is_dir() # 디렉토리가 존재하는지 확인
    assert (non_existent_log_dir / "common").is_dir()
    assert (non_existent_log_dir / "strategies").is_dir()

    # cleanup: 테스트 후 로거 핸들러 및 디렉토리 정리
    for handler in logging.getLogger('operational_logger').handlers:
        handler.close()
        logging.getLogger('operational_logger').removeHandler(handler)
    for handler in logging.getLogger('debug_logger').handlers:
        handler.close()
        logging.getLogger('debug_logger').removeHandler(handler)

    if os.path.exists(non_existent_log_dir):
        import shutil
        shutil.rmtree(non_existent_log_dir)

    logging.root.handlers = original_handlers


def test_get_strategy_logger(tmp_path):
    """
    get_strategy_logger가 타임스탬프가 포함된 JSON 파일 핸들러를 올바르게 생성하는지 테스트합니다.
    """
    strategy_name = "TestStrategy"
    log_dir = tmp_path / "logs"
    
    # 테스트 격리를 위해 타임스탬프 리셋
    reset_log_timestamp_for_test()

    # 로거 생성
    logger = get_strategy_logger(strategy_name, log_dir=str(log_dir))

    # 1. 로거 속성 검증
    assert isinstance(logger, logging.Logger)
    assert logger.name == f"strategy.{strategy_name}"
    assert logger.propagate
    assert len(logger.handlers) == 1  # JSON 파일 핸들러만 존재 (콘솔 핸들러 제거됨)

    # 2. 파일 핸들러 검증
    file_handler = next((h for h in logger.handlers if isinstance(h, logging.FileHandler)), None)
    assert file_handler is not None
    assert isinstance(file_handler.formatter, JsonFormatter)

    # 파일명에 타임스탬프와 인덱스(_1)가 포함되므로 glob으로 검색
    strategy_log_dir = log_dir / "strategies"
    log_files = list(strategy_log_dir.glob(f"*_{strategy_name}_*.log.json"))
    assert len(log_files) == 1
    log_file_path = log_files[0]
    assert file_handler.baseFilename == str(log_file_path)
    assert file_handler.mode == 'a'

    # 3. 로깅 동작 검증
    dict_message = {"event": "test_event", "data": {"code": "005930", "price": 80000}}
    str_message = "This is a simple string message."
    logger.info(dict_message)
    logger.warning(str_message)

    # 핸들러를 닫아 파일에 내용이 기록되도록 함
    for handler in logger.handlers[:]:
        handler.close()

    # 5. 로그 파일 내용 검증
    assert log_file_path.exists()
    with open(log_file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    assert len(lines) == 2

    log1 = json.loads(lines[0])
    assert log1['level'] == 'INFO'
    assert log1['data'] == dict_message

    log2 = json.loads(lines[1])
    assert log2['level'] == 'WARNING'
    assert log2['message'] == str_message
    
    # 6. 핸들러 정리 (테스트 격리)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # 전역 타임스탬프 다시 리셋
    reset_log_timestamp_for_test()


def test_get_performance_logger(tmp_path):
    """
    get_performance_logger가 성능 측정 전용 로거를 올바르게 생성하는지 테스트합니다.
    """
    # 기존 핸들러 정리 (다른 테스트의 영향 방지)
    perf_logger = logging.getLogger("performance")
    for handler in perf_logger.handlers[:]:
        handler.close()
        perf_logger.removeHandler(handler)

    log_dir = tmp_path / "logs"
    
    # 테스트 격리를 위해 타임스탬프 리셋
    reset_log_timestamp_for_test()

    # 로거 생성
    logger = get_performance_logger(log_dir=str(log_dir))

    # 1. 로거 속성 검증
    assert isinstance(logger, logging.Logger)
    assert logger.name == "performance"
    assert not logger.propagate
    assert len(logger.handlers) == 1

    # 2. 파일 핸들러 검증
    file_handler = next((h for h in logger.handlers if isinstance(h, SizeTimeRotatingFileHandler)), None)
    assert file_handler is not None
    # 포맷터는 기본 logging.Formatter여야 함 (JsonFormatter 아님)
    assert not isinstance(file_handler.formatter, JsonFormatter)

    # 파일명 확인 (인덱스 포함: *_perf_1.log 형식)
    perf_log_dir = log_dir / "performance"
    log_files = list(perf_log_dir.glob("*_perf_*.log"))
    assert len(log_files) == 1
    log_file_path = log_files[0]
    assert file_handler.baseFilename == str(log_file_path)

    # 3. 로깅 동작 검증
    msg = "Performance test message"
    logger.info(msg)

    # 핸들러 닫기
    for handler in logger.handlers[:]:
        handler.close()

    # 4. 로그 파일 내용 검증
    assert log_file_path.exists()
    content = log_file_path.read_text(encoding='utf-8')
    assert msg in content
    
    # 5. 핸들러 정리
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    reset_log_timestamp_for_test()


def test_log_rotation(clean_logger_instance):
    """
    RotatingFileHandler가 설정된 크기를 초과하면 인덱싱 방식(_1, _2...)으로 로그 파일을 회전시키는지 테스트합니다.
    """
    logger, common_log_dir = clean_logger_instance
    
    # operational_logger의 RotatingFileHandler 찾기
    handler = next(h for h in logger.operational_logger.handlers if isinstance(h, SizeTimeRotatingFileHandler))
    
    # 포맷터: %(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s
    # 헤더(~56) + 파일명:라인(~20) + 메시지(50) ≈ 130바이트 → maxBytes=200이면 1건은 미초과
    handler.maxBytes = 200

    # 1. 첫 번째 로그 기록 (단일 라인 ~130바이트 → 200 미초과)
    msg = "A" * 50
    logger.info(msg)
    
    # 파일이 하나만 있어야 함 (인덱스 포함: *_operational_1.log 형식)
    log_files = list(common_log_dir.glob("*_operational_*.log"))
    assert len(log_files) == 1
    
    # 2. 두 번째 로그 기록 (누적 100바이트 초과 -> 회전 발생 예상)
    logger.info(msg)
    
    # 백업 파일이 생성되었는지 확인 (인덱스가 붙은 파일)
    # 원본 파일 외에 백업 파일이 존재해야 함
    # 백업 파일명 예시: ..._operational_1.log
    log_files = list(common_log_dir.glob("*_operational_*.log"))
    assert len(log_files) >= 1
    
    # 백업 파일 중 _1.log가 존재하는지 확인
    assert any(f.name.endswith("_1.log") for f in log_files)


def test_log_cleanup(tmp_path):
    """
    Logger 초기화 시 오래된 로그 파일(30일 이상)이 삭제되는지 테스트합니다.
    """
    # 1. 테스트용 로그 디렉토리 준비
    log_dir = tmp_path / "logs_cleanup_test"
    common_dir = log_dir / "common"
    strategies_dir = log_dir / "strategies"
    os.makedirs(common_dir)
    os.makedirs(strategies_dir)
    
    # 2. 파일 생성
    # A. 삭제되어야 할 오래된 파일 (31일 전)
    old_log = common_dir / "old_app.log"
    old_log.touch()
    old_json = strategies_dir / "old_strat.log.json"
    old_json.touch()
    
    days_ago_31 = time.time() - (31 * 86400)
    os.utime(old_log, (days_ago_31, days_ago_31))
    os.utime(old_json, (days_ago_31, days_ago_31))
    
    # B. 유지되어야 할 최신 파일 (1일 전)
    recent_log = common_dir / "recent_app.log"
    recent_log.touch()
    
    days_ago_1 = time.time() - (1 * 86400)
    os.utime(recent_log, (days_ago_1, days_ago_1))
    
    # C. 로그 파일이 아닌 파일 (삭제되지 않아야 함)
    other_file = common_dir / "readme.txt"
    other_file.touch()
    os.utime(other_file, (days_ago_31, days_ago_31))

    # 3. Logger 초기화 (이때 _cleanup_old_logs가 실행됨)
    # 기존 핸들러 정리 (안전장치)
    reset_log_timestamp_for_test()
    original_handlers = logging.root.handlers[:]
    logging.root.handlers = []
    
    try:
        Logger(log_dir=str(log_dir))
        
        # 4. 검증
        assert not old_log.exists(), "30일 지난 .log 파일은 삭제되어야 합니다."
        assert not old_json.exists(), "30일 지난 .json 파일은 삭제되어야 합니다."
        assert recent_log.exists(), "최신 로그 파일은 유지되어야 합니다."
        assert other_file.exists(), "로그 확장자가 아닌 파일은 유지되어야 합니다."
        
    finally:
        # 정리: 생성된 로거의 핸들러를 닫아야 파일 잠금이 해제됨
        for name in ['operational_logger', 'debug_logger']:
            logger = logging.getLogger(name)
            for h in logger.handlers[:]:
                h.close()
                logger.removeHandler(h)
        
        logging.root.handlers = original_handlers


def test_logger_exception_method(clean_logger_instance):
    """
    exception() 메서드가 예외 정보를 포함하여 ERROR 레벨로 로그를 남기는지 테스트합니다.
    """
    logger, common_log_dir = clean_logger_instance
    message = "Test exception method"
    try:
        raise ValueError("Exception method test")
    except ValueError:
        logger.exception(message)

    # Then
    log_files = list(common_log_dir.glob("*.log"))
    for f in log_files:
        content = f.read_text(encoding='utf-8')
        assert message in content
        assert "ValueError: Exception method test" in content
        assert "Traceback" in content


def test_size_time_rotating_handler_backup_limit(tmp_path):
    """
    SizeTimeRotatingFileHandler가 backupCount 제한을 넘어가면 오래된 파일(낮은 인덱스)을 삭제하는지 검증합니다.
    """
    log_file = tmp_path / "test_backup.log"
    backup_count = 2
    
    # maxBytes를 작게 설정하여 매 로그마다 로테이션 유도
    handler = SizeTimeRotatingFileHandler(str(log_file), maxBytes=10, backupCount=backup_count)
    logger = logging.getLogger("test_backup_limit_logger")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    
    # 4번 기록 -> 4번 로테이션 (_1 -> _2 -> _3 -> _4 -> _5)
    # 예상: test_backup_3.log, test_backup_4.log (백업), test_backup_5.log (활성) → 총 3개 (_1, _2는 삭제됨)
    for i in range(4):
        logger.info(f"Log message {i} " * 5)

    handler.close()
    logger.removeHandler(handler)

    # 전체 인덱스 파일 개수 확인: 백업(backupCount개) + 활성(1개)
    files = list(tmp_path.glob("test_backup_*.log"))
    assert len(files) == backup_count + 1

    filenames = [f.name for f in files]
    assert "test_backup_3.log" in filenames
    assert "test_backup_4.log" in filenames
    assert "test_backup_5.log" in filenames  # 활성 파일 (마지막 기록)
    assert "test_backup_1.log" not in filenames
    assert "test_backup_2.log" not in filenames


def test_loggers_use_custom_handler(tmp_path):
    """Logger 및 get_strategy_logger가 SizeTimeRotatingFileHandler를 사용하는지 검증합니다."""
    reset_log_timestamp_for_test()
    log_dir = tmp_path / "logs"

    # 1. Strategy Logger
    strat_logger = get_strategy_logger("test_strat", log_dir=str(log_dir))
    assert any(isinstance(h, SizeTimeRotatingFileHandler) for h in strat_logger.handlers)
    for h in strat_logger.handlers:
        h.close()

    # 2. Main Logger
    app_logger = Logger(log_dir=str(log_dir))
    assert any(isinstance(h, SizeTimeRotatingFileHandler) for h in app_logger.operational_logger.handlers)
    assert any(isinstance(h, SizeTimeRotatingFileHandler) for h in app_logger.debug_logger.handlers)

    # 정리
    for logger in [app_logger.operational_logger, app_logger.debug_logger]:
        for h in logger.handlers:
            h.close()


# ── StreamingEventLogger / get_streaming_logger 테스트 ─────────────────────────


@pytest.fixture
def streaming_logger_setup(tmp_path):
    """get_streaming_logger()가 생성하는 로거와 로그 파일 경로를 준비하는 픽스처."""
    reset_log_timestamp_for_test()

    # 테스트 전: streaming_event 로거 핸들러 초기화 (다른 테스트 잔재 방지)
    existing = logging.getLogger("streaming_event")
    for h in existing.handlers[:]:
        h.close()
        existing.removeHandler(h)

    log_dir = tmp_path / "logs"
    streaming_logger = get_streaming_logger(log_dir=str(log_dir))

    yield streaming_logger, log_dir / "streaming"

    # 정리
    inner = logging.getLogger("streaming_event")
    for h in inner.handlers[:]:
        h.close()
        inner.removeHandler(h)


def _read_json_lines(log_dir):
    """logs/streaming/ 아래 .log.json 파일의 모든 행을 파싱하여 반환합니다."""
    files = list(log_dir.glob("*.log.json"))
    assert len(files) == 1, f"예상과 다른 로그 파일 수: {[f.name for f in files]}"
    with open(files[0], encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_get_streaming_logger_creates_file(streaming_logger_setup):
    """get_streaming_logger()가 logs/streaming/ 아래에 JSON 파일을 생성하는지 검증합니다."""
    streaming_logger, streaming_log_dir = streaming_logger_setup

    assert isinstance(streaming_logger, StreamingEventLogger)
    assert streaming_log_dir.is_dir()

    inner = logging.getLogger("streaming_event")
    assert not inner.propagate
    assert len(inner.handlers) == 1
    handler = inner.handlers[0]
    assert isinstance(handler, SizeTimeRotatingFileHandler)
    assert isinstance(handler.formatter, JsonFormatter)

    # 파일은 첫 로그를 쓸 때 생성됩니다.
    streaming_logger.log_connect()
    handler.flush()

    log_files = list(streaming_log_dir.glob("*_streaming_*.log.json"))
    assert len(log_files) == 1


def test_log_connect_writes_json(streaming_logger_setup):
    """log_connect()가 action='connect' JSON 항목을 파일에 기록하는지 검증합니다."""
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_connect()

    for h in logging.getLogger("streaming_event").handlers:
        h.flush()

    lines = _read_json_lines(streaming_log_dir)
    assert len(lines) == 1
    assert lines[0]["data"]["action"] == "connect"
    assert lines[0]["level"] == "INFO"


def test_log_disconnect_writes_reason(streaming_logger_setup):
    """log_disconnect(reason=...)가 reason 필드를 포함하는지 검증합니다."""
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_disconnect(reason="market_closed")

    for h in logging.getLogger("streaming_event").handlers:
        h.flush()

    lines = _read_json_lines(streaming_log_dir)
    assert lines[0]["data"]["action"] == "disconnect"
    assert lines[0]["data"]["reason"] == "market_closed"


def test_log_subscribe_writes_categories_and_count(streaming_logger_setup):
    """log_subscribe()가 code, categories, active_count를 올바르게 기록하는지 검증합니다."""
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_subscribe(
        code="005930",
        categories={"portfolio": 1, "strategy_momentum": 2},
        active_count=3,
    )

    for h in logging.getLogger("streaming_event").handlers:
        h.flush()

    lines = _read_json_lines(streaming_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "subscribe"
    assert d["code"] == "005930"
    assert d["categories"] == {"portfolio": 1, "strategy_momentum": 2}
    assert d["active_count"] == 3


def test_log_unsubscribe_writes_code_and_count(streaming_logger_setup):
    """log_unsubscribe()가 code, active_count를 올바르게 기록하는지 검증합니다."""
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_unsubscribe(code="005930", active_count=2)

    for h in logging.getLogger("streaming_event").handlers:
        h.flush()

    lines = _read_json_lines(streaming_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "unsubscribe"
    assert d["code"] == "005930"
    assert d["active_count"] == 2


def test_log_summary_writes_full_state(streaming_logger_setup):
    """log_summary()가 active_count, active_codes, pending_by_priority를 기록하는지 검증합니다."""
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_summary(
        active_count=2,
        active_codes=["005930", "000660"],
        pending_by_priority={"HIGH": ["005930"], "MEDIUM": ["000660"], "LOW": []},
    )

    for h in logging.getLogger("streaming_event").handlers:
        h.flush()

    lines = _read_json_lines(streaming_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "summary"
    assert d["active_count"] == 2
    assert d["active_codes"] == ["000660", "005930"]  # sorted
    assert d["pending_by_priority"]["HIGH"] == ["005930"]


def test_log_reconnect_writes_trigger_and_stats(streaming_logger_setup):
    """log_reconnect()가 trigger, codes, success, total을 기록하는지 검증합니다."""
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_reconnect(
        trigger="receive_task_dead",
        codes=["005930", "000660"],
        success=2,
        total=2,
    )

    for h in logging.getLogger("streaming_event").handlers:
        h.flush()

    lines = _read_json_lines(streaming_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "reconnect"
    assert d["trigger"] == "receive_task_dead"
    assert d["codes"] == ["000660", "005930"]  # sorted
    assert d["success"] == 2
    assert d["total"] == 2


def test_log_restore_writes_stats(streaming_logger_setup):
    """log_restore()가 codes, success, total을 기록하는지 검증합니다."""
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_restore(codes=["005930"], success=1, total=1)

    for h in logging.getLogger("streaming_event").handlers:
        h.flush()

    lines = _read_json_lines(streaming_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "restore"
    assert d["codes"] == ["005930"]
    assert d["success"] == 1
    assert d["total"] == 1


def test_log_pt_subscribe_and_unsubscribe(streaming_logger_setup):
    """log_pt_subscribe / log_pt_unsubscribe가 H0STPGM0 이벤트를 기록하는지 검증합니다."""
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_pt_subscribe(code="005930", reason="reconnect")
    streaming_logger.log_pt_unsubscribe(code="005930", reason="reconnect_failed")

    for h in logging.getLogger("streaming_event").handlers:
        h.flush()

    lines = _read_json_lines(streaming_log_dir)
    assert len(lines) == 2

    assert lines[0]["data"]["action"] == "pt_subscribe"
    assert lines[0]["data"]["code"] == "005930"
    assert lines[0]["data"]["reason"] == "reconnect"

    assert lines[1]["data"]["action"] == "pt_unsubscribe"
    assert lines[1]["data"]["reason"] == "reconnect_failed"


def test_log_price_subscribe_and_unsubscribe(streaming_logger_setup):
    """log_price_subscribe / log_price_unsubscribe가 H0STCNT0 이벤트를 기록하는지 검증합니다."""
    streaming_logger, streaming_log_dir = streaming_logger_setup

    streaming_logger.log_price_subscribe(code="000660", reason="restore")
    streaming_logger.log_price_unsubscribe(code="000660", reason="restore_failed")

    for h in logging.getLogger("streaming_event").handlers:
        h.flush()

    lines = _read_json_lines(streaming_log_dir)
    assert len(lines) == 2

    assert lines[0]["data"]["action"] == "price_subscribe"
    assert lines[0]["data"]["code"] == "000660"
    assert lines[0]["data"]["reason"] == "restore"

    assert lines[1]["data"]["action"] == "price_unsubscribe"
    assert lines[1]["data"]["reason"] == "restore_failed"


def test_get_streaming_logger_returns_same_file_on_second_call(tmp_path):
    """같은 프로세스에서 get_streaming_logger()를 두 번 호출해도 파일이 하나만 생성되는지 검증합니다."""
    reset_log_timestamp_for_test()

    existing = logging.getLogger("streaming_event")
    for h in existing.handlers[:]:
        h.close()
        existing.removeHandler(h)

    log_dir = tmp_path / "logs"

    logger1 = get_streaming_logger(log_dir=str(log_dir))
    logger2 = get_streaming_logger(log_dir=str(log_dir))

    # 파일은 첫 로그를 쓸 때 생성됩니다.
    logger1.log_connect()
    for h in logging.getLogger("streaming_event").handlers:
        h.flush()

    streaming_log_dir = log_dir / "streaming"
    log_files = list(streaming_log_dir.glob("*_streaming_*.log.json"))
    assert len(log_files) == 1  # 두 번 호출해도 파일은 한 개

    for h in logging.getLogger("streaming_event").handlers[:]:
        h.close()
        logging.getLogger("streaming_event").removeHandler(h)


# ── CacheEventLogger / get_cache_event_logger 테스트 ──────────────────────────


@pytest.fixture
def cache_logger_setup(tmp_path):
    """get_cache_event_logger()가 생성하는 로거와 로그 파일 경로를 준비하는 픽스처."""
    reset_log_timestamp_for_test()

    existing = logging.getLogger("cache_event")
    for h in existing.handlers[:]:
        h.close()
        existing.removeHandler(h)

    log_dir = tmp_path / "logs"
    cache_logger = get_cache_event_logger(log_dir=str(log_dir))

    yield cache_logger, log_dir / "cache"

    inner = logging.getLogger("cache_event")
    for h in inner.handlers[:]:
        h.close()
        inner.removeHandler(h)


def _flush_cache_logger():
    for h in logging.getLogger("cache_event").handlers:
        h.flush()


# _read_json_lines는 이미 위에 정의되어 있으므로 그대로 재사용 (log_dir만 다르게 전달)


def test_get_cache_event_logger_creates_file(cache_logger_setup):
    """get_cache_event_logger()가 logs/cache/ 아래에 JSON 파일을 생성하는지 검증합니다."""
    cache_logger, cache_log_dir = cache_logger_setup

    assert isinstance(cache_logger, CacheEventLogger)
    assert cache_log_dir.is_dir()

    inner = logging.getLogger("cache_event")
    assert not inner.propagate
    assert inner.level == logging.DEBUG  # 모든 레벨 기록
    assert len(inner.handlers) == 1
    handler = inner.handlers[0]
    assert isinstance(handler, SizeTimeRotatingFileHandler)
    assert isinstance(handler.formatter, JsonFormatter)

    cache_logger.log_ohlcv_miss("005930", "test")
    _flush_cache_logger()

    log_files = list(cache_log_dir.glob("*_cache_*.log.json"))
    assert len(log_files) == 1


def test_get_cache_event_logger_returns_same_logger_on_second_call(tmp_path):
    """같은 프로세스에서 두 번 호출해도 파일이 하나만 생성되는지 검증합니다."""
    reset_log_timestamp_for_test()
    existing = logging.getLogger("cache_event")
    for h in existing.handlers[:]:
        h.close()
        existing.removeHandler(h)

    log_dir = tmp_path / "logs"
    logger1 = get_cache_event_logger(log_dir=str(log_dir))
    logger2 = get_cache_event_logger(log_dir=str(log_dir))

    logger1.log_ohlcv_miss("005930", "test")
    _flush_cache_logger()

    log_files = list((log_dir / "cache").glob("*_cache_*.log.json"))
    assert len(log_files) == 1

    for h in logging.getLogger("cache_event").handlers[:]:
        h.close()
        logging.getLogger("cache_event").removeHandler(h)


# ── 현재가 캐시 이벤트 ─────────────────────────────────────────────────────────


def test_log_price_set_new_entry(cache_logger_setup):
    """log_price_set()이 신규 등록 시 is_new=True와 before/after price를 기록합니다."""
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_price_set("005930", "api", None, "75000", is_new=True)
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "price_set"
    assert d["code"] == "005930"
    assert d["caller"] == "api"
    assert d["before_price"] is None
    assert d["after_price"] == "75000"
    assert d["is_new"] is True
    assert lines[0]["level"] == "INFO"


def test_log_price_set_update(cache_logger_setup):
    """log_price_set()이 기존 항목 갱신 시 is_new=False와 before/after를 기록합니다."""
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_price_set("005930", "api", "74000", "75000", is_new=False)
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["before_price"] == "74000"
    assert d["after_price"] == "75000"
    assert d["is_new"] is False


def test_log_price_hit_fields(cache_logger_setup):
    """log_price_hit()이 caller, age_sec, is_streaming을 DEBUG로 기록합니다."""
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_price_hit("005930", "strategy_service", 1.23, is_streaming=True)
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "price_hit"
    assert d["code"] == "005930"
    assert d["caller"] == "strategy_service"
    assert d["age_sec"] == 1.23
    assert d["is_streaming"] is True
    assert lines[0]["level"] == "DEBUG"


def test_log_price_miss_not_found(cache_logger_setup):
    """log_price_miss()가 reason='not_found'를 DEBUG로 기록합니다."""
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_price_miss("000660", "market_data", "not_found")
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "price_miss"
    assert d["code"] == "000660"
    assert d["reason"] == "not_found"
    assert lines[0]["level"] == "DEBUG"


def test_log_price_miss_ttl_expired(cache_logger_setup):
    """log_price_miss()가 reason='ttl_expired'를 기록합니다."""
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_price_miss("000660", "market_data", "ttl_expired")
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    assert lines[0]["data"]["reason"] == "ttl_expired"


def test_log_price_update_tick_fields(cache_logger_setup):
    """log_price_update_tick()이 before/after price와 volume을 DEBUG로 기록합니다."""
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_price_update_tick("005930", "74000", "75000", volume=123456)
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "price_update_tick"
    assert d["before_price"] == "74000"
    assert d["after_price"] == "75000"
    assert d["volume"] == 123456
    assert lines[0]["level"] == "DEBUG"


def test_log_price_evicted_is_warning(cache_logger_setup):
    """log_price_evicted()가 WARNING 레벨로 code와 capacity를 기록합니다."""
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_price_evicted("005930", capacity=3000)
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "price_evicted"
    assert d["code"] == "005930"
    assert d["capacity"] == 3000
    assert lines[0]["level"] == "WARNING"


# ── 스트리밍 상태 이벤트 ──────────────────────────────────────────────────────


def test_log_streaming_mark_and_unmark(cache_logger_setup):
    """log_streaming_mark/unmark()가 streaming_count를 INFO로 기록합니다."""
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_streaming_mark("005930", streaming_count=5)
    cache_logger.log_streaming_unmark("005930", streaming_count=4)
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    assert len(lines) == 2

    mark = lines[0]["data"]
    assert mark["action"] == "streaming_mark"
    assert mark["code"] == "005930"
    assert mark["streaming_count"] == 5
    assert lines[0]["level"] == "INFO"

    unmark = lines[1]["data"]
    assert unmark["action"] == "streaming_unmark"
    assert unmark["streaming_count"] == 4
    assert lines[1]["level"] == "INFO"


# ── OHLCV 캐시 이벤트 ─────────────────────────────────────────────────────────


def test_log_ohlcv_loaded_fields(cache_logger_setup):
    """log_ohlcv_loaded()가 caller, ohlcv_count, latest_date를 INFO로 기록합니다."""
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_ohlcv_loaded("005930", "strategy", 600, "20250401")
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "ohlcv_loaded"
    assert d["code"] == "005930"
    assert d["caller"] == "strategy"
    assert d["ohlcv_count"] == 600
    assert d["latest_date"] == "20250401"
    assert lines[0]["level"] == "INFO"


def test_log_ohlcv_hit_fields(cache_logger_setup):
    """log_ohlcv_hit()이 ohlcv_count, has_today_candle을 DEBUG로 기록합니다."""
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_ohlcv_hit("005930", "backtest", ohlcv_count=601, has_today_candle=True)
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "ohlcv_hit"
    assert d["ohlcv_count"] == 601
    assert d["has_today_candle"] is True
    assert lines[0]["level"] == "DEBUG"


def test_log_ohlcv_miss_fields(cache_logger_setup):
    """log_ohlcv_miss()가 code, caller를 DEBUG로 기록합니다."""
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_ohlcv_miss("000660", "momentum_strategy")
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "ohlcv_miss"
    assert d["code"] == "000660"
    assert d["caller"] == "momentum_strategy"
    assert lines[0]["level"] == "DEBUG"


def test_log_ohlcv_evicted_is_warning(cache_logger_setup):
    """log_ohlcv_evicted()가 freq, ohlcv_count, capacity를 WARNING으로 기록합니다."""
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_ohlcv_evicted("012345", freq=2, ohlcv_count=300, capacity=500)
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "ohlcv_evicted"
    assert d["code"] == "012345"
    assert d["freq"] == 2
    assert d["ohlcv_count"] == 300
    assert d["capacity"] == 500
    assert lines[0]["level"] == "WARNING"


def test_log_ohlcv_invalidated(cache_logger_setup):
    """log_ohlcv_invalidated()가 code를 INFO로 기록합니다."""
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_ohlcv_invalidated("005930")
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "ohlcv_invalidated"
    assert d["code"] == "005930"
    assert lines[0]["level"] == "INFO"


def test_log_ohlcv_upsert_fields(cache_logger_setup):
    """log_ohlcv_upsert()가 record_count, code_count, sorted invalidated_codes를 INFO로 기록합니다."""
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_ohlcv_upsert(
        record_count=1200,
        code_count=2,
        invalidated_codes=["000660", "005930"],
    )
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "ohlcv_upsert"
    assert d["record_count"] == 1200
    assert d["code_count"] == 2
    assert d["invalidated_codes"] == ["000660", "005930"]  # sorted
    assert lines[0]["level"] == "INFO"


def test_log_today_candle_update(cache_logger_setup):
    """log_today_candle()이 before/after price, high, low, is_new_candle을 DEBUG로 기록합니다."""
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_today_candle("005930", 74000, 75000, high=75500, low=73000, is_new_candle=False)
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "today_candle"
    assert d["before_price"] == 74000
    assert d["after_price"] == 75000
    assert d["high"] == 75500
    assert d["low"] == 73000
    assert d["is_new_candle"] is False
    assert lines[0]["level"] == "DEBUG"


def test_log_today_candle_new(cache_logger_setup):
    """log_today_candle()이 is_new_candle=True(ohlcv_today 신규 생성)를 기록합니다."""
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_today_candle("005930", None, 75000, high=75000, low=75000, is_new_candle=True)
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["is_new_candle"] is True
    assert d["before_price"] is None


# ── 통합 통계 ─────────────────────────────────────────────────────────────────


def test_log_stats_combined_hit_rate(cache_logger_setup):
    """log_stats()가 price/ohlcv 통계와 combined hit_rate를 INFO로 기록합니다."""
    cache_logger, cache_log_dir = cache_logger_setup

    price_stats = {"hits": 80, "misses": 20, "hit_rate": 80.0, "current_size": 100}
    ohlcv_stats = {"hits": 60, "misses": 40, "hit_rate": 60.0, "current_size": 50}
    cache_logger.log_stats(price_stats, ohlcv_stats)
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    d = lines[0]["data"]
    assert d["action"] == "cache_stats"
    assert d["price"]["hits"] == 80
    assert d["ohlcv"]["hits"] == 60
    assert d["combined"]["hits"] == 140
    assert d["combined"]["misses"] == 60
    assert d["combined"]["hit_rate"] == 70.0  # 140/(140+60)*100
    assert lines[0]["level"] == "INFO"


def test_log_stats_zero_requests(cache_logger_setup):
    """요청이 0건일 때 hit_rate가 0.0으로 기록됩니다."""
    cache_logger, cache_log_dir = cache_logger_setup

    cache_logger.log_stats(
        {"hits": 0, "misses": 0, "hit_rate": 0.0, "current_size": 0},
        {"hits": 0, "misses": 0, "hit_rate": 0.0, "current_size": 0},
    )
    _flush_cache_logger()

    lines = _read_json_lines(cache_log_dir)
    assert lines[0]["data"]["combined"]["hit_rate"] == 0.0


# ── _LRUCache / _LFUCache eviction 콜백 연결 검증 ────────────────────────────


def test_lru_cache_eviction_callback_fires():
    """_LRUCache 용량 초과 시 on_evict 콜백이 제거된 key와 함께 호출됩니다."""
    evicted = []
    lru = _LRUCache(capacity=2, on_evict=lambda k: evicted.append(k))
    lru.put("A", 1)
    lru.put("B", 2)
    lru.put("C", 3)  # A가 LRU이므로 evict

    assert evicted == ["A"]


def test_lru_cache_eviction_callback_none_does_not_raise():
    """on_evict=None이어도 용량 초과 시 예외가 발생하지 않습니다."""
    lru = _LRUCache(capacity=1)
    lru.put("A", 1)
    lru.put("B", 2)  # A evict, callback 없음 → 예외 없어야 함


def test_lfu_cache_eviction_callback_fires_with_freq_and_ohlcv_count():
    """_LFUCache 용량 초과 시 on_evict 콜백이 key, freq, ohlcv_count를 전달합니다."""
    evicted = []
    lfu = _LFUCache(capacity=2, on_evict=lambda k, f, c: evicted.append((k, f, c)))

    lfu.put("A", {"ohlcv_historical": [1, 2, 3]})  # freq=0
    lfu.put("B", {"ohlcv_historical": [1]})          # freq=0
    lfu.get("A")                                      # A freq=1, B freq=0
    lfu.put("C", {})                                  # B(freq=0) evicted

    assert len(evicted) == 1
    key, freq, ohlcv_count = evicted[0]
    assert key == "B"
    assert freq == 0
    assert ohlcv_count == 1  # ohlcv_historical 길이


def test_lfu_cache_eviction_callback_non_dict_value():
    """캐시 값이 dict가 아닐 때 ohlcv_count=0으로 콜백이 호출됩니다."""
    evicted = []
    lfu = _LFUCache(capacity=1, on_evict=lambda k, f, c: evicted.append((k, f, c)))
    lfu.put("A", "not_a_dict")
    lfu.put("B", {})  # A evicted

    assert evicted[0][2] == 0  # ohlcv_count=0


def test_lfu_cache_eviction_callback_exception_does_not_propagate():
    """on_evict 콜백이 예외를 던져도 put()이 정상 완료됩니다."""
    def bad_callback(k, f, c):
        raise RuntimeError("callback error")

    lfu = _LFUCache(capacity=1, on_evict=bad_callback)
    lfu.put("A", {})
    lfu.put("B", {})  # eviction → callback 예외 → 무시 → put 완료
    assert "B" in lfu._cache


# ── 레포지토리 통합 — StockPriceRepository ───────────────────────────────────


def test_stock_price_repo_logs_price_set_new():
    """set_current_price() 호출 시 새 항목이면 log_price_set(is_new=True)가 호출됩니다."""
    mock_cache_logger = MagicMock(spec=CacheEventLogger)
    repo = StockPriceRepository(cache_logger=mock_cache_logger)

    repo.set_current_price("005930", {"output": {"stck_prpr": "75000"}})

    mock_cache_logger.log_price_set.assert_called_once()
    _, kwargs = mock_cache_logger.log_price_set.call_args
    # positional args 처리
    args = mock_cache_logger.log_price_set.call_args[0]
    assert args[0] == "005930"      # code
    assert args[4] is True          # is_new


def test_stock_price_repo_logs_price_set_update():
    """set_current_price() 두 번째 호출 시 is_new=False로 기록됩니다."""
    mock_cache_logger = MagicMock(spec=CacheEventLogger)
    repo = StockPriceRepository(cache_logger=mock_cache_logger)

    repo.set_current_price("005930", {"output": {"stck_prpr": "74000"}})
    repo.set_current_price("005930", {"output": {"stck_prpr": "75000"}})

    calls = mock_cache_logger.log_price_set.call_args_list
    assert calls[0][0][4] is True   # 첫 호출: is_new=True
    assert calls[1][0][4] is False  # 두 번째: is_new=False
    assert calls[1][0][2] == "74000"  # before_price
    assert calls[1][0][3] == "75000"  # after_price


def test_stock_price_repo_logs_price_miss_not_found():
    """get_current_price()가 캐시 미존재 시 log_price_miss(reason='not_found')를 호출합니다."""
    mock_cache_logger = MagicMock(spec=CacheEventLogger)
    repo = StockPriceRepository(cache_logger=mock_cache_logger)

    result = repo.get_current_price("999999", caller="test")

    assert result is None
    mock_cache_logger.log_price_miss.assert_called_once_with("999999", "test", "not_found")


def test_stock_price_repo_logs_price_miss_ttl_expired():
    """get_current_price()가 TTL 만료 시 log_price_miss(reason='ttl_expired')를 호출합니다."""
    mock_cache_logger = MagicMock(spec=CacheEventLogger)
    repo = StockPriceRepository(cache_logger=mock_cache_logger)

    repo.set_current_price("005930", {"output": {"stck_prpr": "75000"}})
    # price_updated_at을 아주 오래 전으로 조작
    cached = repo._price_cache.get("005930", count_stats=False)
    cached["price_updated_at"] = time.time() - 9999

    result = repo.get_current_price("005930", max_age_sec=3.0, caller="test")

    assert result is None
    mock_cache_logger.log_price_miss.assert_called_once_with("005930", "test", "ttl_expired")


def test_stock_price_repo_logs_price_hit():
    """get_current_price()가 캐시 히트 시 log_price_hit()을 호출합니다."""
    mock_cache_logger = MagicMock(spec=CacheEventLogger)
    repo = StockPriceRepository(cache_logger=mock_cache_logger)

    repo.set_current_price("005930", {"output": {"stck_prpr": "75000"}})
    result = repo.get_current_price("005930", caller="test")

    assert result is not None
    mock_cache_logger.log_price_hit.assert_called_once()
    args = mock_cache_logger.log_price_hit.call_args[0]
    assert args[0] == "005930"   # code
    assert args[1] == "test"     # caller
    assert args[3] is False      # is_streaming


def test_stock_price_repo_logs_streaming_mark_unmark():
    """mark_streaming/unmark_streaming이 streaming_count와 함께 로깅됩니다."""
    mock_cache_logger = MagicMock(spec=CacheEventLogger)
    repo = StockPriceRepository(cache_logger=mock_cache_logger)

    repo.mark_streaming("005930")
    repo.mark_streaming("000660")
    repo.unmark_streaming("005930")

    mark_calls = mock_cache_logger.log_streaming_mark.call_args_list
    assert mark_calls[0][0] == ("005930", 1)
    assert mark_calls[1][0] == ("000660", 2)

    unmark_args = mock_cache_logger.log_streaming_unmark.call_args[0]
    assert unmark_args[0] == "005930"
    assert unmark_args[1] == 1  # 해제 후 1개 남음


def test_stock_price_repo_logs_price_update_tick_only_on_change():
    """update_current_price()가 가격 변동 시에만 log_price_update_tick()을 호출합니다."""
    mock_cache_logger = MagicMock(spec=CacheEventLogger)
    repo = StockPriceRepository(cache_logger=mock_cache_logger)

    repo.set_current_price("005930", {"output": {"stck_prpr": "75000"}})
    mock_cache_logger.reset_mock()

    # 같은 가격 → 로그 없음
    repo.update_current_price("005930", 75000)
    mock_cache_logger.log_price_update_tick.assert_not_called()

    # 다른 가격 → 로그 있음
    repo.update_current_price("005930", 76000)
    mock_cache_logger.log_price_update_tick.assert_called_once()
    args = mock_cache_logger.log_price_update_tick.call_args[0]
    assert args[1] == "75000"   # before
    assert args[2] == "76000"   # after


def test_stock_price_repo_eviction_logs_warning():
    """LRU 용량 초과로 eviction 발생 시 log_price_evicted()가 호출됩니다."""
    mock_cache_logger = MagicMock(spec=CacheEventLogger)
    repo = StockPriceRepository(cache_logger=mock_cache_logger)
    repo._price_cache.capacity = 2  # 테스트용 용량 축소

    repo.set_current_price("A", {"output": {"stck_prpr": "1000"}})
    repo.set_current_price("B", {"output": {"stck_prpr": "2000"}})
    mock_cache_logger.reset_mock()
    repo.set_current_price("C", {"output": {"stck_prpr": "3000"}})  # A evicted

    mock_cache_logger.log_price_evicted.assert_called_once()
    args = mock_cache_logger.log_price_evicted.call_args[0]
    assert args[0] == "A"


# ── 레포지토리 통합 — StockOhlcvRepository ───────────────────────────────────


def test_stock_ohlcv_repo_logs_upsert_and_invalidation(tmp_path):
    """upsert_ohlcv() 호출 시 log_ohlcv_upsert와 log_ohlcv_invalidated가 각 종목마다 호출됩니다."""
    import asyncio

    mock_cache_logger = MagicMock(spec=CacheEventLogger)
    db_path = str(tmp_path / "test.db")
    repo = StockOhlcvRepository(db_path=db_path, cache_logger=mock_cache_logger)

    records = [
        {"code": "005930", "date": "20250401", "open": 74000, "high": 75000, "low": 73000, "close": 74500, "volume": 100000},
        {"code": "000660", "date": "20250401", "open": 90000, "high": 91000, "low": 89000, "close": 90500, "volume": 50000},
    ]

    async def run():
        await repo.upsert_ohlcv(records)
        await repo.close()

    asyncio.run(run())

    assert mock_cache_logger.log_ohlcv_invalidated.call_count == 2
    invalidated_codes = {c[0][0] for c in mock_cache_logger.log_ohlcv_invalidated.call_args_list}
    assert invalidated_codes == {"005930", "000660"}

    mock_cache_logger.log_ohlcv_upsert.assert_called_once()
    args = mock_cache_logger.log_ohlcv_upsert.call_args[1]
    assert args["record_count"] == 2
    assert args["code_count"] == 2


async def test_stock_ohlcv_repo_logs_ohlcv_loaded_and_hit(tmp_path):
    """get_stock_data() 최초 호출 시 log_ohlcv_loaded, 두 번째 호출 시 log_ohlcv_hit가 호출됩니다."""
    mock_cache_logger = MagicMock(spec=CacheEventLogger)
    db_path = str(tmp_path / "test.db")
    repo = StockOhlcvRepository(db_path=db_path, cache_logger=mock_cache_logger)

    records = [
        {"code": "005930", "date": "20250401", "open": 74000, "high": 75000, "low": 73000, "close": 74500, "volume": 100000},
        {"code": "005930", "date": "20250331", "open": 73000, "high": 74000, "low": 72000, "close": 73500, "volume": 90000},
    ]
    await repo.upsert_ohlcv(records)
    mock_cache_logger.reset_mock()

    # 첫 조회: DB miss → DB load → log_ohlcv_loaded
    result = await repo.get_stock_data("005930", ohlcv_limit=2, caller="test")
    assert result is not None
    mock_cache_logger.log_ohlcv_miss.assert_called_once_with("005930", "test")
    mock_cache_logger.log_ohlcv_loaded.assert_called_once()
    loaded_args = mock_cache_logger.log_ohlcv_loaded.call_args[0]
    assert loaded_args[0] == "005930"   # code
    assert loaded_args[2] == 2          # ohlcv_count (2일치)
    assert loaded_args[3] == "20250401" # latest_date

    mock_cache_logger.reset_mock()

    # 두 번째 조회: 캐시 히트 → log_ohlcv_hit
    result2 = await repo.get_stock_data("005930", ohlcv_limit=2, caller="test")
    assert result2 is not None
    mock_cache_logger.log_ohlcv_hit.assert_called_once()
    mock_cache_logger.log_ohlcv_loaded.assert_not_called()

    await repo.close()


async def test_stock_ohlcv_repo_logs_ohlcv_eviction(tmp_path):
    """LFU 용량 초과 시 log_ohlcv_evicted()가 freq, ohlcv_count와 함께 호출됩니다."""
    mock_cache_logger = MagicMock(spec=CacheEventLogger)
    db_path = str(tmp_path / "test.db")
    repo = StockOhlcvRepository(db_path=db_path, cache_logger=mock_cache_logger)
    repo._ohlcv_cache.capacity = 1  # 용량을 1로 축소

    for code in ["005930", "000660"]:
        await repo.upsert_ohlcv([
            {"code": code, "date": "20250401", "open": 1000, "high": 1100, "low": 900, "close": 1050, "volume": 1000}
        ])

    mock_cache_logger.reset_mock()

    await repo.get_stock_data("005930", caller="test")  # 005930 캐시 로드
    await repo.get_stock_data("000660", caller="test")  # 용량 초과 → 005930 evict

    mock_cache_logger.log_ohlcv_evicted.assert_called_once()
    call = mock_cache_logger.log_ohlcv_evicted.call_args
    assert call[0][0] == "005930"            # evicted code
    assert call[1]["capacity"] == 1          # capacity (keyword arg)

    await repo.close()
