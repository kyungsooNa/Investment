import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.types import ErrorCode, ResCommonResponse
from interfaces.schedulable_task import TaskPriority, TaskState
from task.background.intraday.theme_intraday_leader_alert_task import (
    ThemeIntradayLeaderAlertTask,
)


class _Clock:
    def __init__(self, now: datetime, open_now: bool = True):
        self.now = now
        self.open_now = open_now

    def get_current_kst_time(self):
        return self.now

    def get_current_kst_date_str(self):
        return self.now.strftime("%Y%m%d")

    def is_market_operating_hours(self, _now):
        return self.open_now


def _make_response(data):
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="성공",
        data=data,
    )


def _make_task(*, now=None, open_now=True, basic_rankings=None, service_response=None):
    clock = _Clock(now or datetime(2026, 7, 6, 10, 10, 0), open_now=open_now)
    ranking_task = MagicMock()
    ranking_task.refresh_basic_ranking = AsyncMock()
    basic_rankings = basic_rankings if basic_rankings is not None else {
        "rise": [
            {
                "stck_shrn_iscd": "005930",
                "hts_kor_isnm": "삼성전자",
                "stck_prpr": "80000",
                "prdy_ctrt": "3.1",
                "acml_vol": "1000",
            }
        ],
        "trading_value": [
            {
                "mksc_shrn_iscd": "000660",
                "hts_kor_isnm": "SK하이닉스",
                "stck_prpr": "200000",
                "prdy_ctrt": "2.5",
                "acml_tr_pbmn": "500000000",
            }
        ],
        "volume": [],
    }

    def _basic_cache(category):
        return _make_response(basic_rankings.get(category, []))

    ranking_task.get_basic_ranking_cache.side_effect = _basic_cache
    theme_service = MagicMock()
    theme_service.build_intraday_theme_report = AsyncMock(
        return_value=service_response or ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="성공",
            data=[{"normalized_name": "반도체", "leaders": []}],
        )
    )
    telegram_reporter = MagicMock()
    telegram_reporter.send_daily_theme_report = AsyncMock()
    mcs = MagicMock()
    mcs.is_business_day = AsyncMock(return_value=True)
    task = ThemeIntradayLeaderAlertTask(
        ranking_task=ranking_task,
        theme_daily_leader_service=theme_service,
        telegram_reporter=telegram_reporter,
        market_calendar_service=mcs,
        market_clock=clock,
        logger=MagicMock(),
    )
    return SimpleNamespace(
        task=task,
        clock=clock,
        ranking_task=ranking_task,
        theme_service=theme_service,
        telegram_reporter=telegram_reporter,
        mcs=mcs,
    )


@pytest.mark.asyncio
async def test_open_tick_sends_intraday_theme_report_for_current_hourly_slot():
    deps = _make_task()

    await deps.task._tick()

    deps.ranking_task.refresh_basic_ranking.assert_awaited_once_with(notify=False)
    rankings = deps.theme_service.build_intraday_theme_report.await_args.args[0]
    assert rankings["report_date"] == "20260706"
    assert rankings["program_all_stocks"] == []
    assert {item["stck_shrn_iscd"] for item in rankings["all_stocks"]} == {"005930", "000660"}
    samsung = next(item for item in rankings["all_stocks"] if item["stck_shrn_iscd"] == "005930")
    assert samsung["acml_tr_pbmn"] == "80000000"
    deps.theme_service.build_intraday_theme_report.assert_awaited_once()
    assert deps.theme_service.build_intraday_theme_report.await_args.kwargs["report_time"] == "20260706 10:10"
    deps.telegram_reporter.send_daily_theme_report.assert_awaited_once_with(
        [{"normalized_name": "반도체", "leaders": []}],
        report_date="20260706 10:10",
        show_flow_ratio=False,
    )
    progress = deps.task.get_progress()
    assert progress["last_report_slot"] == "20260706 10:10"
    assert progress["sent_count"] == 1


@pytest.mark.asyncio
async def test_same_hourly_slot_does_not_send_twice():
    deps = _make_task()

    await deps.task._tick()
    deps.clock.now = datetime(2026, 7, 6, 11, 9, 0)
    await deps.task._tick()

    deps.telegram_reporter.send_daily_theme_report.assert_awaited_once()
    assert deps.theme_service.build_intraday_theme_report.await_count == 2


@pytest.mark.asyncio
async def test_next_hourly_slot_sends_again():
    deps = _make_task()

    await deps.task._tick()
    deps.clock.now = datetime(2026, 7, 6, 11, 10, 0)
    await deps.task._tick()

    assert deps.telegram_reporter.send_daily_theme_report.await_count == 2
    assert deps.task.get_progress()["last_report_slot"] == "20260706 11:10"


@pytest.mark.asyncio
async def test_late_restart_inside_hourly_slot_does_not_send_catchup_alert():
    deps = _make_task(now=datetime(2026, 7, 6, 10, 41, 0))

    await deps.task._tick()

    deps.theme_service.build_intraday_theme_report.assert_awaited_once()
    deps.telegram_reporter.send_daily_theme_report.assert_not_called()
    assert deps.task.get_progress()["last_report_slot"] is None


@pytest.mark.asyncio
async def test_before_0910_skips_alert():
    deps = _make_task(now=datetime(2026, 7, 6, 9, 9, 0))

    await deps.task._tick()

    deps.theme_service.build_intraday_theme_report.assert_awaited_once()
    deps.telegram_reporter.send_daily_theme_report.assert_not_called()


@pytest.mark.asyncio
async def test_market_closed_skips_alert():
    deps = _make_task(open_now=False)

    await deps.task._tick()

    deps.theme_service.build_intraday_theme_report.assert_not_called()
    deps.telegram_reporter.send_daily_theme_report.assert_not_called()


@pytest.mark.asyncio
async def test_empty_ranking_cache_skips_without_marking_slot_sent():
    deps = _make_task(basic_rankings={"rise": [], "trading_value": [], "volume": []})

    await deps.task._tick()

    deps.theme_service.build_intraday_theme_report.assert_not_called()
    deps.telegram_reporter.send_daily_theme_report.assert_not_called()
    assert deps.task.get_progress()["last_error"] == "intraday_ranking_empty"
    assert deps.task.get_progress()["last_report_slot"] is None


def test_task_identity_and_initial_progress():
    deps = _make_task()

    assert deps.task.task_name == "intraday_theme_leader_alert"
    assert deps.task.get_progress()["running"] is False


@pytest.mark.asyncio
async def test_latest_report_is_refreshed_each_minute_without_extra_telegram():
    deps = _make_task(now=datetime(2026, 7, 6, 10, 13, 0))

    await deps.task._tick()
    deps.clock.now = datetime(2026, 7, 6, 10, 14, 0)
    await deps.task._tick()

    assert deps.theme_service.build_intraday_theme_report.await_count == 2
    deps.telegram_reporter.send_daily_theme_report.assert_not_called()
    latest = deps.task.get_latest_report()
    assert latest["captured_at"] == "20260706 10:14"
    assert latest["data"][0]["normalized_name"] == "반도체"


@pytest.mark.asyncio
async def test_tick_returns_without_market_clock():
    task = ThemeIntradayLeaderAlertTask(
        ranking_task=MagicMock(),
        theme_daily_leader_service=MagicMock(),
        logger=MagicMock(),
    )

    await task._tick()

    assert task.get_latest_report() == {"captured_at": None, "data": []}


@pytest.mark.asyncio
async def test_market_open_check_skips_on_non_business_day():
    deps = _make_task()
    deps.mcs.is_business_day = AsyncMock(return_value=False)

    assert await deps.task._is_market_open_now() is False


@pytest.mark.asyncio
async def test_market_open_check_skips_when_calendar_raises():
    deps = _make_task()
    deps.mcs.is_business_day = AsyncMock(side_effect=RuntimeError("calendar down"))

    assert await deps.task._is_market_open_now() is False
    deps.task._logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_market_open_check_passes_without_calendar_service():
    deps = _make_task()
    deps.task._mcs = None

    assert await deps.task._is_market_open_now() is True


@pytest.mark.asyncio
async def test_send_report_marks_empty_theme_error_without_telegram():
    deps = _make_task()

    await deps.task._send_report("20260706 10:00")

    deps.telegram_reporter.send_daily_theme_report.assert_not_called()
    assert deps.task.get_progress()["last_error"] == "intraday_theme_empty"
    assert deps.task.get_progress()["last_report_slot"] is None


@pytest.mark.asyncio
async def test_send_report_keeps_existing_error_when_theme_data_empty():
    deps = _make_task()
    deps.task._progress["last_error"] = "intraday_ranking_empty"

    await deps.task._send_report("20260706 10:00")

    assert deps.task.get_progress()["last_error"] == "intraday_ranking_empty"


@pytest.mark.asyncio
async def test_send_report_without_telegram_reporter_still_marks_slot():
    deps = _make_task()
    deps.task._telegram_reporter = None
    deps.task._latest_report = {"captured_at": "x", "data": [{"normalized_name": "반도체"}]}

    await deps.task._send_report("20260706 10:00")

    assert deps.task.get_progress()["last_report_slot"] == "20260706 10:00"
    assert deps.task.get_progress()["sent_count"] == 1


@pytest.mark.asyncio
async def test_refresh_marks_error_when_theme_service_response_fails():
    deps = _make_task(service_response=ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="테마 생성 실패", data=None
    ))

    await deps.task._tick()

    assert deps.task.get_progress()["last_error"] == "theme_service_error:테마 생성 실패"
    deps.telegram_reporter.send_daily_theme_report.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_marks_error_when_theme_service_returns_nothing():
    deps = _make_task()
    deps.theme_service.build_intraday_theme_report = AsyncMock(return_value=None)

    await deps.task._tick()

    assert deps.task.get_progress()["last_error"] == "theme_service_error:응답 없음"


@pytest.mark.asyncio
async def test_tick_does_not_resend_report_for_already_sent_slot():
    deps = _make_task()

    await deps.task._tick()
    assert deps.telegram_reporter.send_daily_theme_report.await_count == 1

    deps.clock.now = datetime(2026, 7, 6, 10, 11, 0)
    await deps.task._tick()

    assert deps.telegram_reporter.send_daily_theme_report.await_count == 1


@pytest.mark.asyncio
async def test_build_rankings_skips_items_without_code():
    deps = _make_task(basic_rankings={
        "rise": [{"hts_kor_isnm": "코드없음"}],
        "trading_value": [],
        "volume": [],
    })

    rankings = await deps.task._build_intraday_rankings("20260706 10:10")

    assert rankings["all_stocks"] == []
    assert rankings["report_date"] == "20260706"


@pytest.mark.asyncio
async def test_build_rankings_supports_sync_refresh_hook():
    deps = _make_task()
    deps.ranking_task.refresh_basic_ranking = MagicMock(return_value=None)

    rankings = await deps.task._build_intraday_rankings("20260706 10:10")

    deps.ranking_task.refresh_basic_ranking.assert_called_once_with(notify=False)
    assert len(rankings["all_stocks"]) == 2


@pytest.mark.asyncio
async def test_build_rankings_without_refresh_hook():
    deps = _make_task()
    del deps.ranking_task.refresh_basic_ranking

    rankings = await deps.task._build_intraday_rankings("20260706 10:10")

    assert len(rankings["all_stocks"]) == 2


def test_basic_ranking_items_returns_empty_without_getter():
    deps = _make_task()
    del deps.ranking_task.get_basic_ranking_cache

    assert deps.task._get_basic_ranking_items("rise") == []


def test_basic_ranking_items_returns_empty_on_failed_response():
    deps = _make_task()
    deps.ranking_task.get_basic_ranking_cache.side_effect = None
    deps.ranking_task.get_basic_ranking_cache.return_value = ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="실패", data=None
    )

    assert deps.task._get_basic_ranking_items("rise") == []


def test_basic_ranking_items_unwraps_dict_output():
    deps = _make_task()
    deps.ranking_task.get_basic_ranking_cache.side_effect = None
    deps.ranking_task.get_basic_ranking_cache.return_value = _make_response(
        {"output": [{"stck_shrn_iscd": "005930"}]}
    )

    assert deps.task._get_basic_ranking_items("rise") == [{"stck_shrn_iscd": "005930"}]


def test_normalize_stock_fills_name_and_trade_value():
    deps = _make_task()

    stock = deps.task._normalize_stock({
        "code": "005930",
        "name": "삼성전자",
        "stck_prpr": "80,000",
        "acml_vol": "1,000",
    })

    assert stock["stck_shrn_iscd"] == "005930"
    assert stock["mksc_shrn_iscd"] == "005930"
    assert stock["hts_kor_isnm"] == "삼성전자"
    assert stock["acml_tr_pbmn"] == str(80000 * 1000)


def test_normalize_stock_accepts_object_with_to_dict():
    deps = _make_task()
    item = MagicMock()
    item.to_dict.return_value = {"iscd": "000660", "stck_prpr": "0", "acml_vol": "0"}

    stock = deps.task._normalize_stock(item)

    assert stock["stck_shrn_iscd"] == "000660"
    assert "acml_tr_pbmn" not in stock


@pytest.mark.parametrize("value, expected", [("1,000", 1000), ("", 0), (None, 0), ("abc", 0)])
def test_to_int_parsing(value, expected):
    assert ThemeIntradayLeaderAlertTask._to_int(value) == expected


@pytest.mark.asyncio
async def test_loop_runs_tick_logs_error_and_exits_on_cancel():
    deps = _make_task()
    task = deps.task
    task._tick = AsyncMock(side_effect=[None, RuntimeError("boom"), asyncio.CancelledError()])

    with patch(
        "task.background.intraday.theme_intraday_leader_alert_task.asyncio.sleep",
        new_callable=AsyncMock,
    ):
        await task._loop()

    assert task._tick.await_count == 3
    assert task.get_progress()["last_error"] == "boom"
    assert task.get_progress()["running"] is False
    task._logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_loop_skips_tick_while_suspended():
    deps = _make_task()
    task = deps.task
    task._tick = AsyncMock()
    task._state = TaskState.SUSPENDED
    sleep_mock = AsyncMock(side_effect=[None, asyncio.CancelledError()])

    with patch(
        "task.background.intraday.theme_intraday_leader_alert_task.asyncio.sleep",
        sleep_mock,
    ):
        with pytest.raises(asyncio.CancelledError):
            await task._loop()

    task._tick.assert_not_awaited()
    assert task.state == TaskState.SUSPENDED


@pytest.mark.asyncio
async def test_start_is_idempotent_and_stop_clears_tasks():
    deps = _make_task()
    task = deps.task

    await task.start()
    assert len(task._tasks) == 1
    await task.start()
    assert len(task._tasks) == 1

    await task.stop()

    assert task._tasks == []
    assert task.state == TaskState.STOPPED


@pytest.mark.asyncio
async def test_restart_after_stop_resets_state_to_idle():
    deps = _make_task()
    task = deps.task

    await task.stop()
    assert task.state == TaskState.STOPPED

    await task.start()
    assert task.state == TaskState.IDLE

    await task.stop()


@pytest.mark.asyncio
async def test_suspend_only_applies_while_running_and_resume_restores_idle():
    deps = _make_task()
    task = deps.task

    await task.suspend()
    assert task.state == TaskState.IDLE

    task._state = TaskState.RUNNING
    await task.suspend()
    assert task.state == TaskState.SUSPENDED

    await task.resume()
    assert task.state == TaskState.IDLE

    await task.resume()
    assert task.state == TaskState.IDLE


def test_task_priority_is_low():
    deps = _make_task()

    assert deps.task.priority == TaskPriority.LOW
