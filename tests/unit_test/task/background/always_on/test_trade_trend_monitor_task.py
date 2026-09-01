import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from interfaces.schedulable_task import TaskPriority, TaskState
from services.trade_trend_service import TradeStatItem
from services.trade_trend_service import NationalTradeTrendRelease
from task.background.always_on.trade_trend_monitor_task import (
    TradeTrendMonitorTask,
    _latest_available_month,
    _month_delta,
)


class DummyClock:
    def __init__(self, now: datetime):
        self._now = now

    def get_current_kst_time(self):
        return self._now


@pytest.mark.asyncio
async def test_tick_sends_jeju_semiconductor_report_once(tmp_path):
    current = TradeStatItem("2026.05", "집적회로 반도체", "8542", 46585000, 0, 0)
    previous_month = TradeStatItem("2026.04", "집적회로 반도체", "8542", 30000000, 0, 0)
    previous_year = TradeStatItem("2025.05", "집적회로 반도체", "8542", 10000000, 0, 0)
    total = TradeStatItem("2026.05", "제주 전체", "-", 63590000, 0, 0)
    async def fetch_sido_item_month(yyyymm, item_code):
        return {
            "202605": [current],
            "202604": [previous_month],
            "202505": [previous_year],
        }[yyyymm]

    client = SimpleNamespace(
        fetch_sido_item_month=AsyncMock(side_effect=fetch_sido_item_month),
        fetch_sido_total_month=AsyncMock(return_value=[total]),
    )
    reporter = SimpleNamespace(send_jeju_semiconductor_trade_report=AsyncMock(return_value=True))
    task = TradeTrendMonitorTask(
        customs_client=client,
        repository_path=str(tmp_path / "trade_trend_state.json"),
        telegram_reporter=reporter,
        config=SimpleNamespace(item_code="8542"),
        market_clock=DummyClock(datetime(2026, 6, 16, 9, 30)),
        logger=MagicMock(),
    )

    await task._tick()
    await task._tick()

    reporter.send_jeju_semiconductor_trade_report.assert_awaited_once()
    assert task.get_progress()["sent_count"] == 1


@pytest.mark.asyncio
async def test_tick_sends_national_trade_releases_once(tmp_path):
    release = NationalTradeTrendRelease(
        source="customs",
        phase="customs_10d",
        title="2026년 7월 1일 ~ 7월 10일 수출입 현황 [잠정치]",
        url="https://customs.example/10d",
        period_label="2026년 7월 1~10일",
        export_amount_100m_usd=298,
        export_yoy_pct=53.9,
        import_amount_100m_usd=235,
        import_yoy_pct=17.4,
        trade_balance_100m_usd=64,
        trade_balance_label="흑자",
    )
    client = SimpleNamespace(
        fetch_sido_item_month=AsyncMock(return_value=[]),
        fetch_sido_total_month=AsyncMock(return_value=[]),
    )
    national_client = SimpleNamespace(fetch_recent_releases=AsyncMock(return_value=[release]))
    reporter = SimpleNamespace(
        send_jeju_semiconductor_trade_report=AsyncMock(return_value=True),
        send_national_trade_trend_report=AsyncMock(return_value=True),
    )
    task = TradeTrendMonitorTask(
        customs_client=client,
        national_client=national_client,
        repository_path=str(tmp_path / "trade_trend_state.json"),
        telegram_reporter=reporter,
        config=SimpleNamespace(
            item_code="8542",
            jeju_semiconductor_enabled=False,
            national_enabled=True,
        ),
        market_clock=DummyClock(datetime(2026, 7, 11, 9, 30)),
        logger=MagicMock(),
    )

    await task._tick()
    await task._tick()

    reporter.send_national_trade_trend_report.assert_awaited_once_with(release)
    assert task.get_progress()["national_sent_count"] == 1


@pytest.mark.asyncio
async def test_start_stop_cancels_background_loop(tmp_path):
    client = SimpleNamespace(
        fetch_sido_item_month=AsyncMock(return_value=[]),
        fetch_sido_total_month=AsyncMock(return_value=[]),
    )
    task = TradeTrendMonitorTask(
        customs_client=client,
        repository_path=str(tmp_path / "trade_trend_state.json"),
        telegram_reporter=None,
        config=SimpleNamespace(poll_interval_sec=3600),
        market_clock=DummyClock(datetime(2026, 6, 16, 9, 30)),
        logger=MagicMock(),
    )

    await task.start()
    assert task.state == TaskState.RUNNING
    await task.stop()

    assert task.state == TaskState.STOPPED


def _task(tmp_path, *, client=None, national_client=None, reporter=None, config=None,
          now=datetime(2026, 6, 16, 9, 30)):
    if client is None:
        client = SimpleNamespace(
            fetch_sido_item_month=AsyncMock(return_value=[]),
            fetch_sido_total_month=AsyncMock(return_value=[]),
        )
    return TradeTrendMonitorTask(
        customs_client=client,
        national_client=national_client,
        repository_path=str(tmp_path / "trade_trend_state.json"),
        telegram_reporter=reporter,
        config=config if config is not None else SimpleNamespace(),
        market_clock=DummyClock(now),
        logger=MagicMock(),
    )


@pytest.mark.parametrize(
    "yyyymm, delta, expected",
    [
        ("202603", -1, "202602"),
        ("202601", -1, "202512"),
        ("202601", -12, "202501"),
        ("202611", 2, "202701"),
        ("202601", -13, "202412"),
        ("202612", 13, "202801"),
    ],
)
def test_month_delta_wraps_across_year_boundaries(yyyymm, delta, expected):
    assert _month_delta(yyyymm, delta) == expected


def test_latest_available_month_is_previous_month():
    assert _latest_available_month(datetime(2026, 6, 16)) == "202605"
    assert _latest_available_month(datetime(2026, 1, 3)) == "202512"


@pytest.mark.asyncio
async def test_tick_derives_target_month_when_config_has_none(tmp_path):
    client = SimpleNamespace(
        fetch_sido_item_month=AsyncMock(return_value=[]),
        fetch_sido_total_month=AsyncMock(return_value=[]),
    )
    task = _task(tmp_path, client=client, config=SimpleNamespace(national_enabled=False))

    await task._tick()

    assert task.get_progress()["last_period"] == "202605"
    assert task.get_progress()["last_checked_at"] is not None


@pytest.mark.asyncio
async def test_tick_skips_jeju_section_when_disabled(tmp_path):
    client = SimpleNamespace(
        fetch_sido_item_month=AsyncMock(return_value=[]),
        fetch_sido_total_month=AsyncMock(return_value=[]),
    )
    task = _task(
        tmp_path,
        client=client,
        config=SimpleNamespace(jeju_semiconductor_enabled=False, national_enabled=False),
    )

    await task._tick()

    client.fetch_sido_item_month.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_skips_jeju_section_without_customs_client(tmp_path):
    task = _task(tmp_path, config=SimpleNamespace(national_enabled=False))
    task._client = None

    await task._tick()

    assert task.get_progress()["sent_count"] == 0


@pytest.mark.asyncio
async def test_tick_stops_when_current_month_row_is_missing(tmp_path):
    client = SimpleNamespace(
        fetch_sido_item_month=AsyncMock(return_value=[]),
        fetch_sido_total_month=AsyncMock(return_value=[]),
    )
    reporter = SimpleNamespace(
        send_jeju_trade_pending_report=AsyncMock(return_value=True),
    )
    task = _task(
        tmp_path,
        client=client,
        reporter=reporter,
        config=SimpleNamespace(national_enabled=False, item_code="85"),
    )

    await task._tick()
    await task._tick()

    assert client.fetch_sido_item_month.await_count == 2
    client.fetch_sido_total_month.assert_not_awaited()
    reporter.send_jeju_trade_pending_report.assert_awaited_once_with(
        "202605",
        "85",
        "제주 2026.05 전기기기류 수출입 API 데이터가 아직 0건입니다.",
    )
    assert task.get_progress()["last_jeju_status"] == "pending"
    assert task.get_progress()["last_jeju_message"] == (
        "제주 2026.05 전기기기류 수출입 API 데이터가 아직 0건입니다."
    )


@pytest.mark.asyncio
async def test_tick_is_skipped_while_a_previous_tick_holds_the_lock(tmp_path):
    task = _task(tmp_path, config=SimpleNamespace(national_enabled=False))
    lock = task._get_tick_lock()
    assert task._get_tick_lock() is lock

    await lock.acquire()
    try:
        await task._tick()
    finally:
        lock.release()

    assert task.get_progress()["last_checked_at"] is None


@pytest.mark.asyncio
async def test_national_releases_need_both_client_and_reporter(tmp_path):
    national_client = SimpleNamespace(fetch_recent_releases=AsyncMock(return_value=[]))
    task = _task(tmp_path, national_client=national_client, reporter=None)

    await task._send_national_releases()

    national_client.fetch_recent_releases.assert_not_awaited()

    task._national_client = None
    task._reporter = SimpleNamespace()
    await task._send_national_releases()


@pytest.mark.asyncio
async def test_national_release_not_counted_when_send_fails(tmp_path):
    release = NationalTradeTrendRelease(
        source="customs",
        phase="customs_10d",
        title="7월 1~10일 수출입 현황",
        url="https://customs.example/10d",
        period_label="2026년 7월 1~10일",
        export_amount_100m_usd=298,
        export_yoy_pct=53.9,
        import_amount_100m_usd=235,
        import_yoy_pct=17.4,
        trade_balance_100m_usd=64,
        trade_balance_label="흑자",
    )
    national_client = SimpleNamespace(fetch_recent_releases=AsyncMock(return_value=[release]))
    reporter = SimpleNamespace(send_national_trade_trend_report=AsyncMock(return_value=False))
    task = _task(tmp_path, national_client=national_client, reporter=reporter)

    await task._send_national_releases()

    assert task.get_progress()["national_sent_count"] == 0
    assert task.get_national_release_history() == []


@pytest.mark.asyncio
async def test_jeju_report_not_counted_when_send_fails(tmp_path):
    current = TradeStatItem("2026.05", "집적회로 반도체", "8542", 46585000, 0, 0)
    total = TradeStatItem("2026.05", "제주 전체", "-", 63590000, 0, 0)
    client = SimpleNamespace(
        fetch_sido_item_month=AsyncMock(return_value=[current]),
        fetch_sido_total_month=AsyncMock(return_value=[total]),
    )
    reporter = SimpleNamespace(send_jeju_semiconductor_trade_report=AsyncMock(return_value=False))
    task = _task(
        tmp_path,
        client=client,
        reporter=reporter,
        config=SimpleNamespace(item_code="8542", national_enabled=False),
    )

    await task._tick()

    reporter.send_jeju_semiconductor_trade_report.assert_awaited_once()
    assert task.get_progress()["sent_count"] == 0
    assert task.get_progress()["last_success_at"] is None


@pytest.mark.asyncio
async def test_jeju_report_is_not_sent_without_reporter(tmp_path):
    current = TradeStatItem("2026.05", "집적회로 반도체", "8542", 46585000, 0, 0)
    total = TradeStatItem("2026.05", "제주 전체", "-", 63590000, 0, 0)
    client = SimpleNamespace(
        fetch_sido_item_month=AsyncMock(return_value=[current]),
        fetch_sido_total_month=AsyncMock(return_value=[total]),
    )
    task = _task(
        tmp_path,
        client=client,
        reporter=None,
        config=SimpleNamespace(item_code="8542", national_enabled=False),
    )

    await task._tick()

    assert task.get_progress()["sent_count"] == 0


@pytest.mark.asyncio
async def test_loop_records_tick_error_and_exits_on_cancel(tmp_path):
    task = _task(tmp_path, config=SimpleNamespace(poll_interval_sec=1))
    task._state = TaskState.RUNNING
    task._tick = AsyncMock(side_effect=[RuntimeError("boom"), None])

    with patch(
        "task.background.always_on.trade_trend_monitor_task.asyncio.sleep",
        AsyncMock(side_effect=[None, asyncio.CancelledError()]),
    ):
        await task._loop()

    assert task.get_progress()["last_error"] == "RuntimeError: boom"
    task._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_loop_skips_tick_when_not_running(tmp_path):
    task = _task(tmp_path, config=SimpleNamespace(poll_interval_sec=1))
    task._state = TaskState.SUSPENDED
    task._tick = AsyncMock()

    with patch(
        "task.background.always_on.trade_trend_monitor_task.asyncio.sleep",
        AsyncMock(side_effect=asyncio.CancelledError()),
    ):
        await task._loop()

    task._tick.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_is_idempotent_and_suspend_resume_toggle_state(tmp_path):
    task = _task(tmp_path, config=SimpleNamespace(poll_interval_sec=3600))

    await task.start()
    await task.start()
    assert len(task._tasks) == 1
    assert task.get_progress()["running"] is True

    await task.suspend()
    assert task.state == TaskState.SUSPENDED
    await task.resume()
    assert task.state == TaskState.RUNNING
    await task.resume()
    assert task.state == TaskState.RUNNING

    await task.stop()
    assert task.get_progress()["running"] is False


def test_task_identity(tmp_path):
    task = _task(tmp_path)

    assert task.task_name == "trade_trend_monitor"
    assert task.priority == TaskPriority.LOW
