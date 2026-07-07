from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, ResCommonResponse
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
    theme_service.build_daily_theme_report = AsyncMock(
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

    deps.ranking_task.refresh_basic_ranking.assert_awaited_once()
    rankings = deps.theme_service.build_daily_theme_report.await_args.args[0]
    assert rankings["report_date"] == "20260706"
    assert rankings["program_all_stocks"] == []
    assert {item["stck_shrn_iscd"] for item in rankings["all_stocks"]} == {"005930", "000660"}
    samsung = next(item for item in rankings["all_stocks"] if item["stck_shrn_iscd"] == "005930")
    assert samsung["acml_tr_pbmn"] == "80000000"
    deps.theme_service.build_daily_theme_report.assert_awaited_once()
    assert deps.theme_service.build_daily_theme_report.await_args.kwargs["report_date"] == "20260706 10:10"
    deps.telegram_reporter.send_daily_theme_report.assert_awaited_once_with(
        [{"normalized_name": "반도체", "leaders": []}],
        report_date="20260706 10:10",
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


@pytest.mark.asyncio
async def test_next_hourly_slot_sends_again():
    deps = _make_task()

    await deps.task._tick()
    deps.clock.now = datetime(2026, 7, 6, 11, 10, 0)
    await deps.task._tick()

    assert deps.telegram_reporter.send_daily_theme_report.await_count == 2
    assert deps.task.get_progress()["last_report_slot"] == "20260706 11:10"


@pytest.mark.asyncio
async def test_before_0910_skips_alert():
    deps = _make_task(now=datetime(2026, 7, 6, 9, 9, 0))

    await deps.task._tick()

    deps.theme_service.build_daily_theme_report.assert_not_called()
    deps.telegram_reporter.send_daily_theme_report.assert_not_called()


@pytest.mark.asyncio
async def test_market_closed_skips_alert():
    deps = _make_task(open_now=False)

    await deps.task._tick()

    deps.theme_service.build_daily_theme_report.assert_not_called()
    deps.telegram_reporter.send_daily_theme_report.assert_not_called()


@pytest.mark.asyncio
async def test_empty_ranking_cache_skips_without_marking_slot_sent():
    deps = _make_task(basic_rankings={"rise": [], "trading_value": [], "volume": []})

    await deps.task._tick()

    deps.theme_service.build_daily_theme_report.assert_not_called()
    deps.telegram_reporter.send_daily_theme_report.assert_not_called()
    assert deps.task.get_progress()["last_error"] == "intraday_ranking_empty"
    assert deps.task.get_progress()["last_report_slot"] is None


def test_task_identity_and_initial_progress():
    deps = _make_task()

    assert deps.task.task_name == "intraday_theme_leader_alert"
    assert deps.task.get_progress()["running"] is False
