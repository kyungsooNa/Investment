"""ProgramTradingStreamService 의 분기/헬퍼/생명주기 경로 커버리지 보강."""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.program_trading_stream_service import ProgramTradingStreamService

MODULE = "services.program_trading_stream_service"


@pytest.fixture
def svc(tmp_path):
    base = str(tmp_path / "program_subscribe")
    with patch.object(ProgramTradingStreamService, "_get_base_dir", return_value=base):
        s = ProgramTradingStreamService(logger=MagicMock())
    yield s
    if s._flush_task and not s._flush_task.done():
        s._flush_task.cancel()
    try:
        if s._conn:
            s._conn.close()
    except Exception:
        pass
    s._executor.shutdown(wait=False)


# --- 포워딩 / 정적 헬퍼 ---

def test_property_forwards(svc):
    assert svc._buffer_lock is svc._repo._buffer_lock
    assert svc._lock is svc._repo._lock


def test_bulk_insert_forwards(svc):
    svc._repo._bulk_insert_to_db = MagicMock()
    svc._bulk_insert_to_db([{"a": 1}])
    svc._repo._bulk_insert_to_db.assert_called_once_with([{"a": 1}])


@pytest.mark.asyncio
async def test_flush_loop_forwards(svc):
    svc._repo._flush_loop = AsyncMock()
    await svc._flush_loop()
    svc._repo._flush_loop.assert_awaited_once()


def test_safe_int_and_float_invalid(svc):
    assert ProgramTradingStreamService._safe_int("x") == 0
    assert ProgramTradingStreamService._safe_int(None) == 0
    assert ProgramTradingStreamService._safe_float("x") == 0.0
    assert ProgramTradingStreamService._safe_float(None) == 0.0


def test_on_data_received_queue_inner_exception(svc):
    q = MagicMock()
    q.put_nowait.side_effect = asyncio.QueueFull()
    q.get_nowait.side_effect = Exception("empty")
    svc._pt_queues.append(q)
    svc.on_data_received({"유가증권단축종목코드": "005930"})
    # 내부 예외를 삼키고 큐는 그대로 유지된다.
    assert q in svc._pt_queues


# --- 포맷터 ---

def test_format_helpers_invalid_inputs():
    assert ProgramTradingStreamService._format_int(None) == "0"
    assert ProgramTradingStreamService._format_int("12,345") == "12,345"
    assert ProgramTradingStreamService._format_rate("abc") == "0.00%"
    assert ProgramTradingStreamService._format_rate("1.5") == "+1.50%"
    assert ProgramTradingStreamService._format_eok("abc") == "0.0억"
    assert ProgramTradingStreamService._format_eok("100000000") == "1.0억"


def test_format_stock_label_variants(svc):
    # repo 없음 → code 그대로
    assert svc._format_stock_label("005930") == "005930"
    # repo 정상
    repo = MagicMock()
    repo.get_name_by_code.return_value = "삼성전자"
    svc._stock_code_repository = repo
    assert svc._format_stock_label("005930") == "삼성전자"
    # repo 예외 → code 폴백
    repo.get_name_by_code.side_effect = RuntimeError("boom")
    assert svc._format_stock_label("005930") == "005930"


# --- 구독 코드 / 스냅샷 ---

def test_get_subscribed_program_codes_variants(svc):
    assert svc._get_subscribed_program_codes() == []  # streaming repo 없음
    repo = MagicMock()
    repo.get_desired.return_value = {"000660", "005930"}
    svc._streaming_stock_repo = repo
    assert svc._get_subscribed_program_codes() == ["000660", "005930"]
    repo.get_desired.side_effect = RuntimeError("db down")
    assert svc._get_subscribed_program_codes() == []


def test_extract_latest_program_snapshot(svc):
    assert svc._extract_latest_program_snapshot("not-dict") is None
    assert svc._extract_latest_program_snapshot({"순매수거래대금": 0}) is None
    snap = svc._extract_latest_program_snapshot(
        {"순매수거래대금": "5000", "price": "100", "prdy_ctrt": "1.2"}
    )
    assert snap == {"순매수거래대금": "5000", "price": "100", "rate": "1.2"}


@pytest.mark.asyncio
async def test_get_latest_program_snapshot_variants(svc):
    assert await svc._get_latest_program_snapshot("005930") is None  # provider 없음

    provider = MagicMock(spec=[])  # getter 없음
    svc._program_trade_provider = provider
    assert await svc._get_latest_program_snapshot("005930") is None

    # rt_cd != "0"
    provider = MagicMock()
    provider.get_program_trade_by_stock_daily = AsyncMock(return_value=MagicMock(rt_cd="1"))
    svc._program_trade_provider = provider
    assert await svc._get_latest_program_snapshot("005930") is None

    # 성공
    resp = MagicMock(rt_cd="0", data={"순매수거래대금": "5000"})
    provider.get_program_trade_by_stock_daily = AsyncMock(return_value=resp)
    snap = await svc._get_latest_program_snapshot("005930")
    assert snap["순매수거래대금"] == "5000"

    # 예외
    provider.get_program_trade_by_stock_daily = AsyncMock(side_effect=RuntimeError("rest down"))
    assert await svc._get_latest_program_snapshot("005930") is None


# --- tick 리포트 포맷 ---

@pytest.mark.asyncio
async def test_format_last_tick_report_async_with_and_without_history(svc):
    svc._pt_history = {"005930": [{"주식체결시간": "090000", "price": "100", "rate": "1.0"}]}
    svc._last_tick_ts_by_code = {"005930": time.time()}
    svc._get_latest_program_snapshot = AsyncMock(return_value={"순매수거래대금": "100000000"})
    report = await svc._format_last_tick_report_async(["005930", "000660"])
    assert "REST보정" in report
    assert "수신 없음" in report  # 000660 은 history 없음


def test_format_last_tick_report_sync(svc):
    svc._pt_history = {"005930": [{"주식체결시간": "090000", "price": "100"}]}
    svc._last_tick_ts_by_code = {"005930": time.time()}
    report = svc._format_last_tick_report(["005930", "000660"])
    assert "원" in report
    assert "수신 없음" in report


# --- 텔레그램 전송 ---

@pytest.mark.asyncio
async def test_send_telegram_message_variants(svc):
    assert await svc._send_telegram_message("hi") is False  # reporter 없음

    reporter = MagicMock(spec=[])  # _send_message 없음
    svc._telegram_reporter = reporter
    assert await svc._send_telegram_message("hi") is False

    reporter = MagicMock()
    reporter._send_message = AsyncMock(return_value=True)
    svc._telegram_reporter = reporter
    assert await svc._send_telegram_message("hi") is True

    reporter._send_message = AsyncMock(side_effect=RuntimeError("net down"))
    assert await svc._send_telegram_message("hi") is False


@pytest.mark.asyncio
async def test_send_subscribed_last_tick_alert_no_codes(svc):
    assert await svc.send_subscribed_last_tick_alert() is False


@pytest.mark.asyncio
async def test_send_db_persistence_report_no_codes(svc):
    assert await svc.send_db_persistence_report("20260505") is False


# --- 윈도우 / 분 파싱 ---

def test_build_trading_window_defaults_to_today_for_short_date(svc):
    start_dt, end_dt, minutes, tz = svc._build_trading_window("abc")
    assert tz is None
    assert minutes[0] == "09:00"
    assert minutes[-1] == "15:30"


def test_minute_from_trade_time_invalid(svc):
    assert svc._minute_from_trade_time("0900") == "09:00"
    assert svc._minute_from_trade_time("99") is None       # 자릿수 부족
    assert svc._minute_from_trade_time("9999") is None       # 시/분 범위 초과


# --- 상태/장중 여부 ---

def test_get_background_task_status_with_last_received(svc):
    svc.last_data_ts = time.time()
    status = svc.get_background_task_status()
    assert status["last_received_at"] is not None
    assert status["running"] is False


@pytest.mark.asyncio
async def test_is_market_open_for_tick_alert_variants(svc):
    assert await svc._is_market_open_for_tick_alert() is True  # mcs 없음

    mcs = MagicMock()
    mcs.is_market_open_now = AsyncMock(return_value=False)
    svc._market_calendar_service = mcs
    assert await svc._is_market_open_for_tick_alert() is False

    mcs.is_market_open_now = AsyncMock(side_effect=RuntimeError("cal down"))
    assert await svc._is_market_open_for_tick_alert() is False


# --- 백그라운드 루프 ---

@pytest.mark.asyncio
async def test_hourly_tick_alert_loop_iterates_then_cancels(svc):
    svc._is_market_open_for_tick_alert = AsyncMock(side_effect=[True, False])
    svc.send_subscribed_last_tick_alert = AsyncMock(return_value=True)
    with patch(f"{MODULE}.asyncio.sleep",
               new=AsyncMock(side_effect=[None, None, asyncio.CancelledError()])):
        with pytest.raises(asyncio.CancelledError):
            await svc._hourly_tick_alert_loop()
    svc.send_subscribed_last_tick_alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_hourly_tick_alert_loop_swallows_generic_error(svc):
    with patch(f"{MODULE}.asyncio.sleep", new=AsyncMock(side_effect=RuntimeError("boom"))):
        await svc._hourly_tick_alert_loop()
    svc.logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_after_market_db_check_loop_reports_once_per_date(svc):
    svc._market_calendar_service = MagicMock()
    svc._market_clock = MagicMock()
    svc.send_db_persistence_report = AsyncMock(return_value=True)

    async def fake_loop(**kwargs):
        cb = kwargs["on_market_closed"]
        await cb("20260505")
        await cb("20260505")  # 같은 날짜 → 조기 반환

    svc._after_market_runner = fake_loop
    await svc._after_market_db_check_loop()

    assert svc._last_db_check_report_date == "20260505"
    svc.send_db_persistence_report.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_alert_tasks_creates_loops(svc):
    svc._hourly_tick_alert_loop = AsyncMock()
    svc._after_market_db_check_loop = AsyncMock()
    svc._telegram_reporter = MagicMock()
    svc._market_calendar_service = MagicMock()
    svc._market_clock = MagicMock()

    svc._start_alert_tasks()
    try:
        assert len(svc._alert_tasks) == 2
    finally:
        await asyncio.gather(*svc._alert_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_shutdown_cancels_pending_alert_tasks(svc):
    pending = asyncio.create_task(asyncio.Event().wait())
    svc._alert_tasks = [pending]
    svc._repo.shutdown = AsyncMock()

    await svc.shutdown()

    assert pending.cancelled()
    assert svc._alert_tasks == []
    svc._repo.shutdown.assert_awaited_once()
