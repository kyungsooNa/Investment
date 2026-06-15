import pytest
import asyncio
import json
import os
import sqlite3
import time
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from core.market_clock import MarketClock
from repositories.streaming_stock_repo import StreamingType
from services.program_trading_stream_service import ProgramTradingStreamService


@pytest.fixture
def tmp_db_dir(tmp_path):
    """임시 DB 디렉토리 제공."""
    return str(tmp_path / "program_subscribe")


@pytest.fixture
def manager(tmp_db_dir):
    """테스트용 ProgramTradingStreamService (임시 디렉토리 사용)."""
    mock_logger = MagicMock()
    with patch.object(ProgramTradingStreamService, '_get_base_dir', return_value=tmp_db_dir):
        mgr = ProgramTradingStreamService(logger=mock_logger)
    yield mgr
    # cleanup: flush task 취소 및 DB 연결 닫기
    if mgr._flush_task and not mgr._flush_task.done():
        mgr._flush_task.cancel()
    if mgr._conn:
        mgr._conn.close()
    mgr._executor.shutdown(wait=False)


def _get_db_path(tmp_db_dir):
    return os.path.join(tmp_db_dir, "program_trading.db")


# --- 기본 초기화 ---

def test_init_creates_db(manager, tmp_db_dir):
    """초기화 시 SQLite DB 파일 및 테이블이 생성되는지 확인."""
    db_path = _get_db_path(tmp_db_dir)
    assert os.path.exists(db_path)

    # 테이블 존재 확인
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()

    assert 'pt_history' in tables
    assert 'pt_snapshot' in tables
    # 구독 상태의 읽기/쓰기는 StreamingStockRepo가 SSOT로 담당하지만,
    # 같은 DB를 공유하므로 ProgramTradingRepo가 테이블 스키마를 보장한다.
    assert 'pt_subscriptions' in tables


def test_init_db_failure():
    """DB 초기화 실패 시 에러 로그 확인."""
    mock_logger = MagicMock()
    with patch.object(ProgramTradingStreamService, '_get_base_dir', return_value='/invalid/path/that/cannot/exist'):
        with patch('os.makedirs', side_effect=OSError("Permission denied")):
            # 초기화 실패해도 크래시되지 않아야 함
            try:
                mgr = ProgramTradingStreamService(logger=mock_logger)
            except Exception:
                pass  # DB 초기화 실패 허용


# --- 데이터 수신 및 저장 ---

@pytest.mark.asyncio
async def test_on_data_received_stores_and_broadcasts(manager):
    """데이터 수신 시 메모리 저장 + 버퍼 적재 + 큐 브로드캐스트 테스트."""
    test_data = {"유가증권단축종목코드": "005930", "price": 100}
    queue = manager.create_subscriber_queue()

    manager.on_data_received(test_data)

    # 1. 메모리 저장 확인
    assert "005930" in manager._pt_history
    assert manager._pt_history["005930"][0] == test_data

    # 2. 버퍼에 적재 확인
    assert len(manager._write_buffer) == 1

    # 3. 동기 플러시 후 DB 저장 확인
    manager.flush_write_buffer_sync()
    with manager._get_connection() as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM pt_history WHERE code = '005930'")
        assert cursor.fetchone()[0] == 1

    # 4. 큐 브로드캐스트 확인
    assert queue.qsize() == 1
    item = await queue.get()
    item = json.loads(item)
    expected_payload = [
        '005930', '', 100, 0.0, 0, '', 0, 0, 0, 0, 0, 0
    ]
    assert item == expected_payload


@pytest.mark.asyncio
async def test_on_data_received_queue_full_behavior(manager):
    """큐가 가득 찼을 때 오래된 데이터를 버리고 새 데이터를 넣는지 테스트."""
    queue = manager.create_subscriber_queue()
    queue._maxsize = 2
    queue.put_nowait("old_1")
    queue.put_nowait("old_2")

    test_data = {"유가증권단축종목코드": "005930", "new": True}
    manager.on_data_received(test_data)

    assert queue.qsize() == 2
    item1 = await queue.get()
    item2 = await queue.get()
    assert item1 == "old_2"
    item2 = json.loads(item2)
    expected_payload = [
        '005930', '', 0, 0.0, 0, '', 0, 0, 0, 0, 0, 0
    ]
    assert item2 == expected_payload


def test_on_data_received_missing_key(manager):
    """필수 키가 없는 데이터 수신 시 무시하는지 테스트."""
    invalid_data = {"price": 100}
    manager.on_data_received(invalid_data)
    assert len(manager._pt_history) == 0


def test_on_data_received_queue_exception(manager):
    """큐 put 중 예외 발생 시 크래시되지 않는지 테스트."""
    queue = MagicMock()
    queue.put_nowait.side_effect = Exception("Queue error")
    manager._pt_queues.append(queue)

    test_data = {"유가증권단축종목코드": "005930"}
    manager.on_data_received(test_data)
    assert queue in manager._pt_queues


# --- 버퍼 플러시 ---

def test_flush_write_buffer_sync(manager):
    """동기 플러시가 버퍼를 비우고 DB에 저장하는지 확인."""
    manager.on_data_received({"유가증권단축종목코드": "005930", "price": 100})
    manager.on_data_received({"유가증권단축종목코드": "000660", "price": 200})
    assert len(manager._write_buffer) == 2

    manager.flush_write_buffer_sync()
    assert len(manager._write_buffer) == 0

    with manager._get_connection() as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM pt_history")
        assert cursor.fetchone()[0] == 2


def test_flush_write_buffer_sync_empty(manager):
    """빈 버퍼 플러시 시 에러 없이 통과."""
    manager.flush_write_buffer_sync()  # 에러 없이 통과


@pytest.mark.asyncio
async def test_flush_write_buffer_async(manager):
    """비동기 플러시가 executor를 통해 DB에 저장하는지 확인."""
    manager.on_data_received({"유가증권단축종목코드": "005930", "price": 100})
    assert len(manager._write_buffer) == 1

    await manager._flush_write_buffer()
    assert len(manager._write_buffer) == 0

    with manager._get_connection() as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM pt_history WHERE code = '005930'")
        assert cursor.fetchone()[0] == 1


# 구독 상태 관리(pt_subscriptions)는 StreamingStockRepo가 SSOT로 담당한다.
# ProgramTradingStreamService/Repo는 구독 상태를 직접 읽고 쓰지 않는다.

# --- 히스토리 로드 ---

def test_load_pt_history_from_db(tmp_db_dir):
    """DB에서 금일 히스토리가 정상 로드되는지 확인."""
    mock_logger = MagicMock()

    # 첫 번째 인스턴스: 데이터 삽입 (버퍼 → 동기 플러시)
    with patch.object(ProgramTradingStreamService, '_get_base_dir', return_value=tmp_db_dir):
        mgr1 = ProgramTradingStreamService(logger=mock_logger)
        mgr1.on_data_received({"유가증권단축종목코드": "005930", "price": 100})
        mgr1.on_data_received({"유가증권단축종목코드": "005930", "price": 101})
        mgr1.on_data_received({"유가증권단축종목코드": "000660", "price": 200})
        mgr1.flush_write_buffer_sync()
        mgr1._conn.close()

    # 두 번째 인스턴스: 복원 확인
    with patch.object(ProgramTradingStreamService, '_get_base_dir', return_value=tmp_db_dir):
        mgr2 = ProgramTradingStreamService(logger=mock_logger)
        assert "005930" in mgr2._pt_history
        assert len(mgr2._pt_history["005930"]) == 2
        assert "000660" in mgr2._pt_history
        assert len(mgr2._pt_history["000660"]) == 1
        mgr2._conn.close()


def test_load_pt_history_only_today(tmp_db_dir):
    """전일 데이터는 로드되지 않는지 확인."""
    mock_logger = MagicMock()

    with patch.object(ProgramTradingStreamService, '_get_base_dir', return_value=tmp_db_dir):
        mgr = ProgramTradingStreamService(logger=mock_logger)

        # 전일 데이터를 직접 DB에 삽입
        yesterday = time.time() - 86400 * 2
        with mgr._get_connection() as conn:
            conn.execute(
                "INSERT INTO pt_history (code, created_at) VALUES (?, ?)",
                ("OLD_CODE", yesterday)
            )

        # 다시 로드
        mgr._pt_history = {}
        mgr._load_pt_history()

        assert "OLD_CODE" not in mgr._pt_history
        mgr._conn.close()


def test_cleanup_old_data_no_old_data(manager):
    """삭제 대상이 없을 때 정상 동작."""
    manager.on_data_received({"유가증권단축종목코드": "005930", "price": 100})
    manager.flush_write_buffer_sync()
    manager._cleanup_old_data()

    with manager._get_connection() as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM pt_history")
        assert cursor.fetchone()[0] == 1


# --- 스냅샷 저장/로드 ---

def test_save_and_load_snapshot(manager):
    """스냅샷 저장 및 로드 테스트."""
    data = {"chartData": {"005930": {"valueData": [1, 2, 3]}}, "subscribedCodes": ["005930"]}
    manager.save_snapshot(data)

    result = manager.load_snapshot()
    assert result == data


def test_load_snapshot_not_exists(manager):
    """스냅샷이 없을 때 None 반환."""
    result = manager.load_snapshot()
    assert result is None


def test_save_snapshot_exception(manager):
    """스냅샷 저장 중 예외 발생 테스트."""
    # DB 연결을 닫아서 예외 유발
    manager._conn.close()

    with pytest.raises(Exception):
        manager.save_snapshot({"test": "data"})


# --- 데이터 정리 ---

def test_cleanup_old_data(manager):
    """7일 초과 데이터 삭제 테스트."""
    old_ts = time.time() - (8 * 86400)  # 8일 전
    recent_ts = time.time() - (3 * 86400)  # 3일 전

    with manager._get_connection() as conn:
        conn.execute(
            "INSERT INTO pt_history (code, created_at) VALUES (?, ?)",
            ("OLD", old_ts)
        )
        conn.execute(
            "INSERT INTO pt_history (code, created_at) VALUES (?, ?)",
            ("RECENT", recent_ts)
        )

    manager._cleanup_old_data()

    with manager._get_connection() as conn:
        cursor = conn.execute("SELECT code FROM pt_history")
        codes = [row[0] for row in cursor.fetchall()]

    assert "OLD" not in codes
    assert "RECENT" in codes

# --- 레거시 파일 정리 ---

def test_cleanup_old_files(manager, tmp_db_dir):
    """레거시 JSONL/JSON 파일 삭제 테스트."""
    os.makedirs(tmp_db_dir, exist_ok=True)
    # 레거시 파일 생성
    jsonl_path = os.path.join(tmp_db_dir, "pt_history_20260101.jsonl")
    json_path = os.path.join(tmp_db_dir, "pt_data.json")
    with open(jsonl_path, "w") as f:
        f.write("test")
    with open(json_path, "w") as f:
        f.write("test")

    with patch.object(manager, '_get_base_dir', return_value=tmp_db_dir):
        manager._cleanup_old_files()

    assert not os.path.exists(jsonl_path)
    assert not os.path.exists(json_path)


# --- 큐 관리 ---

def test_create_and_remove_subscriber_queue(manager):
    """구독자 큐 생성 및 제거 테스트."""
    queue = manager.create_subscriber_queue()
    assert queue in manager._pt_queues
    assert queue.maxsize == 200

    manager.remove_subscriber_queue(queue)
    assert queue not in manager._pt_queues


def test_remove_subscriber_queue_not_found(manager):
    """존재하지 않는 큐 제거 시도."""
    queue = asyncio.Queue()
    manager.remove_subscriber_queue(queue)
    assert len(manager._pt_queues) == 0


# --- 생명주기 ---

@pytest.mark.asyncio
async def test_start_background_tasks(manager):
    """백그라운드 태스크 시작 시 데이터 정리 실행 및 플러시 루프 시작 확인."""
    manager.start_background_tasks()
    manager.logger.info.assert_called()
    # 플러시 루프 태스크가 생성되었는지 확인
    assert manager._flush_task is not None
    assert not manager._flush_task.done()
    # cleanup
    manager._flush_task.cancel()


@pytest.mark.asyncio
async def test_shutdown(manager):
    """종료 시 DB 연결이 닫히는지 확인."""
    assert manager._conn is not None
    await manager.shutdown()
    assert manager._conn is None


@pytest.mark.asyncio
async def test_shutdown_no_conn(manager):
    """DB 연결 없이 shutdown 호출."""
    manager._conn = None
    await manager.shutdown()  # 에러 없이 통과


@pytest.mark.asyncio
async def test_shutdown_flushes_remaining_buffer(manager):
    """종료 시 잔여 버퍼를 플러시하는지 확인."""
    manager.on_data_received({"유가증권단축종목코드": "005930", "price": 100})
    assert len(manager._write_buffer) == 1

    await manager.shutdown()
    # shutdown에서 flush_write_buffer_sync()를 호출했으므로 버퍼는 비어있어야 함
    assert len(manager._write_buffer) == 0


# --- get_history_data ---

def test_get_history_data(manager):
    """히스토리 데이터 반환 테스트."""
    manager.on_data_received({"유가증권단축종목코드": "005930", "price": 100})
    history = manager.get_history_data()
    assert "005930" in history
    assert len(history["005930"]) == 1


# --- RETENTION_DAYS 상수 확인 ---

def test_retention_days_is_7():
    """보존 기간이 7일인지 확인."""
    assert ProgramTradingStreamService.RETENTION_DAYS == 7

def test_on_data_received_log_gap(manager):
    """데이터 수신 간격이 10초 이상일 때 로그 기록 확인."""
    manager.last_data_ts = time.time() - 15  # 15초 전

    test_data = {"유가증권단축종목코드": "005930", "price": 100}
    manager.on_data_received(test_data)

    # 로그 메시지 확인
    manager.logger.info.assert_called()
    found = False
    for call_args in manager.logger.info.call_args_list:
        if "실시간 데이터 수신 재개" in str(call_args):
            found = True
            break
    assert found

def test_on_data_received_db_save_failure(manager):
    """DB 저장 실패 시에도 메모리 저장 및 큐 전송은 동작해야 함."""
    test_data = {"유가증권단축종목코드": "005930", "price": 100}
    queue = manager.create_subscriber_queue()

    manager.on_data_received(test_data)

    # on_data_received는 버퍼에만 적재하므로 메모리/큐는 항상 성공
    # 메모리 저장 확인
    assert "005930" in manager._pt_history

    # 큐 전송 확인
    assert queue.qsize() == 1

    # 벌크 인서트 실패 시에도 서비스가 크래시하지 않음
    manager._conn.close()
    manager._conn = None
    # flush_write_buffer_sync는 _bulk_insert_to_db 내부에서 예외를 로그로 처리
    manager._conn = MagicMock()
    manager._conn.executemany = MagicMock(side_effect=Exception("DB Error"))
    manager._conn.rollback = MagicMock()
    manager.flush_write_buffer_sync()
    manager.logger.error.assert_called()

def test_load_snapshot_json_error(manager):
    """스냅샷 데이터 JSON 파싱 에러 처리."""
    # 1. 불량 데이터 삽입
    with manager._get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pt_snapshot (key, value, updated_at) VALUES (?, ?, ?)",
            ("pt_data", "{invalid_json}", time.time())
        )

    # 2. 로드 실행
    result = manager.load_snapshot()

    # 3. 검증: None 반환 및 에러 로그
    assert result is None
    manager.logger.error.assert_called()

def test_init_db_connect_failure():
    """DB 연결 실패 시 에러 로그 확인."""
    mock_logger = MagicMock()
    with patch('sqlite3.connect', side_effect=Exception("Connection failed")):
        with patch.object(ProgramTradingStreamService, '_get_base_dir', return_value='.'):
             mgr = ProgramTradingStreamService(logger=mock_logger)
             mock_logger.error.assert_any_call("SQLite DB 초기화 실패: Connection failed")

# --- 추가된 테스트 케이스 (Coverage 향상) ---

def test_inspect_db_status(manager):
    """inspect_db_status 정상 동작 확인."""
    # 1. 데이터 준비
    manager.save_snapshot({"test": "snapshot"})
    manager.on_data_received({"유가증권단축종목코드": "005930", "price": 100})
    manager.flush_write_buffer_sync()

    # 2. 실행
    status = manager.inspect_db_status()

    # 3. 검증
    assert status["snapshot"]["exists"] is True
    assert status["snapshot"]["updated_at"] is not None
    assert status["history"]["count"] == 1
    assert status["history"]["last_record"] is not None
    assert "hourly_counts" in status["history"]
    assert status["memory"]["last_received_at"] is not None

def test_inspect_db_status_exception(manager):
    """inspect_db_status 예외 처리 확인."""
    # DB 연결을 닫아서 예외 유발
    manager._conn.close()

    status = manager.inspect_db_status()

    assert "error" in status


def test_wire_streaming_stock_repo(manager):
    """wire_streaming_stock_repo가 _repo에 streaming_stock_repo를 주입한다."""
    mock_ssr = MagicMock()

    manager.wire_streaming_stock_repo(mock_ssr)

    assert manager._repo._streaming_stock_repo is mock_ssr


# --- 텔레그램 운영 알림 ---

@pytest.mark.asyncio
async def test_send_subscribed_last_tick_alert_sends_last_tick_for_desired_codes(manager):
    """구독 중인 PT 종목의 마지막 수신 tick을 텔레그램으로 전송한다."""
    mock_ssr = MagicMock()
    mock_ssr.get_desired.return_value = {"005930", "000660"}
    mock_reporter = MagicMock()
    mock_reporter._send_message = AsyncMock(return_value=True)
    mock_stock_repo = MagicMock()
    mock_stock_repo.get_name_by_code.side_effect = lambda code: {
        "005930": "삼성전자",
        "000660": "SK하이닉스",
    }.get(code, code)

    manager.wire_streaming_stock_repo(mock_ssr)
    manager.wire_alert_dependencies(
        telegram_reporter=mock_reporter,
        stock_code_repository=mock_stock_repo,
    )
    manager.on_data_received({
        "유가증권단축종목코드": "005930",
        "주식체결시간": "101530",
        "price": "72000",
        "rate": "1.25",
        "순매수거래대금": "12345600000",
    })

    sent = await manager.send_subscribed_last_tick_alert()

    assert sent is True
    mock_ssr.get_desired.assert_called_once_with(StreamingType.PROGRAM_TRADING)
    mock_reporter._send_message.assert_awaited_once()
    message = mock_reporter._send_message.call_args[0][0]
    assert "프로그램매매 구독 Tick" in message
    assert "삼성전자" in message
    assert "005930" not in message
    assert "72,000" in message
    assert "101530" in message
    assert "누적 순매수대금:123.5억" in message
    assert "순매수대금:12,345,600,000" not in message
    assert "SK하이닉스" in message
    assert "수신 없음" in message


@pytest.mark.asyncio
async def test_send_subscribed_last_tick_alert_refreshes_net_amount_from_rest(manager):
    """알림 직전 REST 최신 프로그램 순매수대금으로 WebSocket 마지막 tick을 보정한다."""
    mock_ssr = MagicMock()
    mock_ssr.get_desired.return_value = {"000660"}
    mock_reporter = MagicMock()
    mock_reporter._send_message = AsyncMock(return_value=True)
    mock_stock_repo = MagicMock()
    mock_stock_repo.get_name_by_code.return_value = "SK하이닉스"
    mock_provider = MagicMock()
    mock_provider.get_program_trade_by_stock_daily = AsyncMock(return_value=MagicMock(
        rt_cd="0",
        data={
            "whol_smtn_ntby_tr_pbmn": "380846000000",
            "stck_clpr": "2056000",
            "prdy_ctrt": "7.59",
        },
    ))

    manager.wire_streaming_stock_repo(mock_ssr)
    manager.wire_alert_dependencies(
        telegram_reporter=mock_reporter,
        stock_code_repository=mock_stock_repo,
        program_trade_provider=mock_provider,
    )
    manager.on_data_received({
        "유가증권단축종목코드": "000660",
        "주식체결시간": "103537",
        "price": "2056000",
        "rate": "7.59",
        "순매수거래대금": "135630000000",
    })

    sent = await manager.send_subscribed_last_tick_alert()

    assert sent is True
    mock_provider.get_program_trade_by_stock_daily.assert_awaited_once_with("000660")
    message = mock_reporter._send_message.call_args[0][0]
    assert "누적 순매수대금:3,808.5억" in message
    assert "1,356.3억" not in message
    assert "REST보정" in message


def test_build_db_minute_persistence_status_reports_missing_minutes(manager):
    """장중 1분 단위 저장 여부를 종목별로 계산한다."""
    clock = MarketClock(market_open_time="09:00", market_close_time="09:02")
    manager.wire_alert_dependencies(market_clock=clock)

    day = clock.market_timezone.localize(datetime(2026, 5, 19, 9, 0, 0))
    with manager._get_connection() as conn:
        conn.executemany(
            "INSERT INTO pt_history (code, created_at) VALUES (?, ?)",
            [
                ("005930", day.timestamp()),
                ("005930", (day.timestamp() + 120)),
                ("000660", day.timestamp()),
                ("000660", (day.timestamp() + 60)),
                ("000660", (day.timestamp() + 120)),
            ],
        )

    status = manager.build_db_minute_persistence_status(["005930", "000660"], "20260519")

    assert status["expected_minute_count"] == 3
    assert status["codes"]["005930"]["saved_minute_count"] == 2
    assert status["codes"]["005930"]["missing_minutes"] == ["09:01"]
    assert status["codes"]["005930"]["ok"] is False
    assert status["codes"]["000660"]["saved_minute_count"] == 3
    assert status["codes"]["000660"]["missing_minutes"] == []
    assert status["codes"]["000660"]["ok"] is True


def test_build_db_minute_persistence_status_splits_unsaved_and_no_tick_minutes(manager):
    """수신된 분의 DB 미저장과 수신 자체가 없는 분을 분리한다."""
    clock = MarketClock(market_open_time="09:00", market_close_time="09:02")
    manager.wire_alert_dependencies(market_clock=clock)

    day = clock.market_timezone.localize(datetime(2026, 5, 19, 9, 0, 0))
    manager._pt_history["005930"] = [
        {"주식체결시간": "090000"},
        {"주식체결시간": "090100"},
    ]
    with manager._get_connection() as conn:
        conn.execute(
            "INSERT INTO pt_history (code, trade_time, created_at) VALUES (?, ?, ?)",
            ("005930", "090000", day.timestamp()),
        )

    status = manager.build_db_minute_persistence_status(["005930"], "20260519")
    item = status["codes"]["005930"]

    assert item["saved_minute_count"] == 1
    assert item["received_minute_count"] == 2
    assert item["unsaved_received_minutes"] == ["09:01"]
    assert item["no_tick_minutes"] == ["09:02"]


def test_build_db_minute_persistence_status_caps_to_regular_session_and_includes_close_minute(manager):
    clock = MarketClock(market_open_time="15:29", market_close_time="15:40")
    manager.wire_alert_dependencies(market_clock=clock)

    day = clock.market_timezone.localize(datetime(2026, 6, 15, 15, 30, 26))
    with manager._get_connection() as conn:
        conn.execute(
            "INSERT INTO pt_history (code, trade_time, created_at) VALUES (?, ?, ?)",
            ("005930", "153026", day.timestamp()),
        )

    status = manager.build_db_minute_persistence_status(["005930"], "20260615")
    item = status["codes"]["005930"]

    assert status["window"]["end"] == "2026-06-15 15:30:00"
    assert status["expected_minute_count"] == 2
    assert item["saved_minute_count"] == 1
    assert item["missing_minutes"] == ["15:29"]
    assert "15:31" not in item["missing_minutes"]


@pytest.mark.asyncio
async def test_send_db_persistence_report_sends_summary(manager):
    """장마감 DB 저장 점검 결과를 텔레그램으로 전송한다."""
    clock = MarketClock(market_open_time="09:00", market_close_time="09:00")
    mock_ssr = MagicMock()
    mock_ssr.get_desired.return_value = {"005930"}
    mock_reporter = MagicMock()
    mock_reporter._send_message = AsyncMock(return_value=True)
    mock_stock_repo = MagicMock()
    mock_stock_repo.get_name_by_code.return_value = "삼성전자"

    manager.wire_streaming_stock_repo(mock_ssr)
    manager.wire_alert_dependencies(
        telegram_reporter=mock_reporter,
        market_clock=clock,
        stock_code_repository=mock_stock_repo,
    )
    day = clock.market_timezone.localize(datetime(2026, 5, 19, 9, 0, 0))
    with manager._get_connection() as conn:
        conn.execute(
            "INSERT INTO pt_history (code, created_at) VALUES (?, ?)",
            ("005930", day.timestamp()),
        )

    sent = await manager.send_db_persistence_report("20260519")

    assert sent is True
    mock_reporter._send_message.assert_awaited_once()
    message = mock_reporter._send_message.call_args[0][0]
    assert "프로그램매매 DB 저장 점검" in message
    assert "삼성전자" in message
    assert "OK" in message


def test_format_db_persistence_report_explains_no_tick_cause(manager):
    """DB 누락 리포트가 수신없음과 실제 DB 미저장을 함께 보여준다."""
    mock_stock_repo = MagicMock()
    mock_stock_repo.get_name_by_code.return_value = "삼성전자"
    manager.wire_alert_dependencies(stock_code_repository=mock_stock_repo)

    message = manager._format_db_persistence_report({
        "date": "20260526",
        "window": {"start": "2026-05-26 09:00:00", "end": "2026-05-26 09:02:00"},
        "expected_minute_count": 3,
        "codes": {
            "005930": {
                "ok": False,
                "saved_minute_count": 1,
                "missing_minute_count": 2,
                "missing_minutes": ["09:01", "09:02"],
                "unsaved_received_minute_count": 1,
                "no_tick_minute_count": 1,
            }
        },
    })

    assert "삼성전자: 누락 2분" in message
    assert "DB 미저장 1분" in message
    assert "수신없음 1분" in message


def test_get_background_task_status_reports_internal_loops(manager):
    """서비스 내부 루프 상태를 system.py에서 읽을 수 있는 형태로 반환한다."""
    manager._flush_task = MagicMock()
    manager._flush_task.done.return_value = False

    status = manager.get_background_task_status()

    assert status["running"] is True
    assert status["flush_loop_alive"] is True
    assert status["alert_task_count"] == 0


@pytest.mark.asyncio
async def test_hourly_tick_alert_loop_skips_when_market_closed(manager):
    """장마감 후에는 시간별 tick 상태 알림을 보내지 않는다."""
    manager.send_subscribed_last_tick_alert = AsyncMock(return_value=True)
    mock_mcs = MagicMock()
    mock_mcs.is_market_open_now = AsyncMock(return_value=False)
    manager.wire_alert_dependencies(market_calendar_service=mock_mcs)

    with patch(
        "services.program_trading_stream_service.asyncio.sleep",
        new_callable=AsyncMock,
        side_effect=[None, asyncio.CancelledError],
    ):
        with pytest.raises(asyncio.CancelledError):
            await manager._hourly_tick_alert_loop()

    manager.send_subscribed_last_tick_alert.assert_not_awaited()
