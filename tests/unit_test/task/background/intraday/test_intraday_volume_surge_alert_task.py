from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, ResCommonResponse
from task.background.intraday.intraday_volume_surge_alert_task import (
    IntradayVolumeSurgeAlertTask,
)


def _response(data):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="success", data=data)


def _make_task(
    *, now=None, cumulative_volume=600_000, trading_value=12_000_000_000,
    current_price=99_800,
):
    now = now or datetime(2026, 8, 25, 10, 30)
    stock = {
        "stck_shrn_iscd": "052690",
        "hts_kor_isnm": "한전기술",
        "stck_prpr": str(current_price),
        "prdy_ctrt": "7.31",
        "acml_vol": str(cumulative_volume),
        "acml_tr_pbmn": str(trading_value),
    }
    ranking_task = MagicMock()
    ranking_task.refresh_basic_ranking = AsyncMock()
    ranking_task.get_basic_ranking_cache.side_effect = lambda category: _response(
        [stock] if category in {"rise", "volume", "trading_value"} else []
    )
    rows = [
        {"date": f"202607{day:02d}", "close": "80000", "volume": "100000"}
        for day in range(1, 31)
    ] + [
        {"date": f"202608{day:02d}", "close": "95000", "volume": "100000"}
        for day in range(1, 21)
    ]
    stock_query_service = MagicMock()
    stock_query_service.get_recent_daily_ohlcv = AsyncMock(return_value=_response(rows))
    telegram_reporter = MagicMock()
    telegram_reporter.send_intraday_volume_surge_alert = AsyncMock(return_value=True)
    clock = MagicMock()
    clock.get_current_kst_time.return_value = now
    clock.is_market_operating_hours.return_value = True
    mcs = MagicMock()
    mcs.is_business_day = AsyncMock(return_value=True)
    task = IntradayVolumeSurgeAlertTask(
        ranking_task=ranking_task,
        stock_query_service=stock_query_service,
        telegram_reporter=telegram_reporter,
        market_calendar_service=mcs,
        market_clock=clock,
        logger=MagicMock(),
    )
    return SimpleNamespace(
        task=task,
        ranking_task=ranking_task,
        stock_query_service=stock_query_service,
        telegram_reporter=telegram_reporter,
        clock=clock,
    )


@pytest.mark.asyncio
async def test_alerts_only_for_aligned_stock_with_projected_volume_at_least_ten_times():
    deps = _make_task()

    await deps.task._tick()

    deps.ranking_task.refresh_basic_ranking.assert_awaited_once_with(
        notify=False, min_interval_sec=45
    )
    deps.telegram_reporter.send_intraday_volume_surge_alert.assert_awaited_once()
    alerts = deps.telegram_reporter.send_intraday_volume_surge_alert.await_args.args[0]
    assert alerts[0]["code"] == "052690"
    assert alerts[0]["tier"] == 10
    assert alerts[0]["projected_volume_ratio"] >= 10.0
    assert alerts[0]["trend_filter"] == "정배열 충족"


@pytest.mark.asyncio
async def test_ten_times_tier_uses_projected_volume_not_current_cumulative_volume():
    """10배 단계는 현재 누적 배수가 아니라 장 마감 예상 배수의 경계값으로 판정한다."""
    deps = _make_task(
        now=datetime(2026, 8, 25, 10, 30),
        cumulative_volume=230_769,
    )

    below_threshold = await deps.task._evaluate(
        deps.task._collect_candidates()[0], deps.clock.get_current_kst_time()
    )

    assert below_threshold is None

    stock = deps.ranking_task.get_basic_ranking_cache("volume").data[0]
    stock["acml_vol"] = "230770"
    at_threshold = await deps.task._evaluate(
        deps.task._collect_candidates()[0], deps.clock.get_current_kst_time()
    )

    assert at_threshold["tier"] == 10
    assert at_threshold["cumulative_volume_ratio"] == 2.31
    assert at_threshold["projected_volume_ratio"] == 10.0
    assert at_threshold["elapsed_minutes"] == 90
    assert at_threshold["market_progress_percent"] == 23.1


@pytest.mark.asyncio
async def test_does_not_alert_below_minimum_trading_value():
    deps = _make_task(trading_value=9_999_999_999)

    await deps.task._tick()

    deps.telegram_reporter.send_intraday_volume_surge_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_alerts_only_once_per_stock_per_day():
    deps = _make_task(cumulative_volume=300_000)

    await deps.task._tick()
    await deps.task._tick()
    deps.telegram_reporter.send_intraday_volume_surge_alert.assert_awaited_once()

    stock = deps.ranking_task.get_basic_ranking_cache("volume").data[0]
    stock["acml_vol"] = "900000"
    await deps.task._tick()

    assert deps.telegram_reporter.send_intraday_volume_surge_alert.await_count == 1


@pytest.mark.asyncio
async def test_does_not_alert_before_0905():
    deps = _make_task(now=datetime(2026, 8, 25, 9, 4))

    await deps.task._tick()

    deps.telegram_reporter.send_intraday_volume_surge_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_does_not_alert_when_trend_is_not_aligned():
    deps = _make_task(current_price=85_000)

    await deps.task._tick()

    deps.telegram_reporter.send_intraday_volume_surge_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_market_closed_skips_refresh_and_alert():
    deps = _make_task()
    deps.clock.is_market_operating_hours.return_value = False

    await deps.task._tick()

    deps.ranking_task.refresh_basic_ranking.assert_not_awaited()
    deps.telegram_reporter.send_intraday_volume_surge_alert.assert_not_awaited()


def test_task_identity():
    deps = _make_task()

    assert deps.task.task_name == "intraday_volume_surge_alert"
