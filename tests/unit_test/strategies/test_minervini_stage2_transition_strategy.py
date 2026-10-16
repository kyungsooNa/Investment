from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ResCommonResponse
from strategies.minervini_stage2_transition_strategy import (
    MinerviniStage2TransitionConfig,
    MinerviniStage2TransitionStrategy,
)


def _daily_rows(count: int = 260, *, last_high: int = 120) -> list[dict]:
    rows = []
    day = datetime(2025, 10, 15)
    for idx in range(count):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        close = 90 + idx * 0.1
        rows.append({
            "date": day.strftime("%Y%m%d"),
            "open": close - 1,
            "high": close + 1,
            "low": close - 2,
            "close": close,
            "volume": 100_000,
        })
        day += timedelta(days=1)
    for row in rows[-20:]:
        row["high"] = last_high
    return rows


def _current_price(price: int, trading_value: int = 40_000_000_000) -> ResCommonResponse:
    return ResCommonResponse(
        rt_cd="0",
        msg1="성공",
        data={"output": {"stck_prpr": str(price), "acml_tr_pbmn": str(trading_value)}},
    )


def _build_strategy(*, stages: list[int], price: int = 121, rows: list[dict] | None = None):
    sqs = SimpleNamespace(
        get_current_price=AsyncMock(return_value=_current_price(price)),
        get_recent_daily_ohlcv=AsyncMock(
            return_value=ResCommonResponse(rt_cd="0", msg1="성공", data=rows or _daily_rows())
        ),
        prefetch_prices=AsyncMock(),
    )
    stage_service = SimpleNamespace(
        get_stage2_list=AsyncMock(return_value=ResCommonResponse(
            rt_cd="0",
            msg1="성공",
            data=[{
                "code": "005930", "name": "삼성전자", "stage": 2,
                "rs_rating": 92, "market_cap": 40_000,
            }],
        )),
        classify_stage_series=MagicMock(return_value=stages),
        get_stage_for_code=AsyncMock(return_value=(2, "Stage 2")),
    )
    clock = SimpleNamespace(get_current_kst_time=lambda: datetime(2026, 10, 15, 10, 0))
    strategy = MinerviniStage2TransitionStrategy(
        stock_query_service=sqs,
        minervini_stage_service=stage_service,
        market_clock=clock,
        logger=MagicMock(),
    )
    return strategy, sqs, stage_service


@pytest.mark.asyncio
async def test_scan_buys_recent_stage2_transition_on_20day_breakout():
    stages = [1] * 255 + [2] * 5
    strategy, _, _ = _build_strategy(stages=stages, price=121)

    signals = await strategy.scan()

    assert len(signals) == 1
    signal = signals[0]
    assert signal.action == "BUY"
    assert signal.strategy_id == "minervini_stage2_transition"
    assert signal.entry_reason == "recent_stage2_transition_20d_breakout"
    assert signal.stop_loss_price == pytest.approx(114.95)
    assert signal.target_price == pytest.approx(125.84)


@pytest.mark.asyncio
async def test_scan_rejects_stage2_that_did_not_transition_recently():
    strategy, _, _ = _build_strategy(stages=[2] * 260, price=121)

    assert await strategy.scan() == []

    assert strategy.current_candidate_codes() == ["005930"]
    strategy._logger.info.assert_any_call({
        "event": "entry_rejected",
        "code": "005930",
        "name": "삼성전자",
        "reason": "no_recent_stage2_transition",
        "transition_lookback_days": 10,
    })


@pytest.mark.asyncio
async def test_scan_rejects_breakout_more_than_five_percent_above_pivot():
    strategy, _, _ = _build_strategy(stages=[1] * 255 + [2] * 5, price=127)

    assert await strategy.scan() == []

    strategy._logger.info.assert_any_call({
        "event": "entry_rejected",
        "code": "005930",
        "name": "삼성전자",
        "reason": "breakout_over_extended",
        "current": 127,
        "pivot": 120,
        "extension_pct": pytest.approx(5.83, abs=0.01),
        "max_extension_pct": 5.0,
    })


@pytest.mark.asyncio
async def test_scan_rejects_illiquid_or_small_cap_stage2_candidate():
    strategy, sqs, stage_service = _build_strategy(stages=[1] * 255 + [2] * 5, price=121)
    stage_service.get_stage2_list.return_value.data[0]["market_cap"] = 29_999
    assert await strategy.scan() == []

    stage_service.get_stage2_list.return_value.data[0]["market_cap"] = 40_000
    sqs.get_current_price.return_value = _current_price(121, trading_value=29_999_999_999)
    assert await strategy.scan() == []
    assert strategy.current_candidate_codes() == ["005930"]
    strategy._logger.info.assert_any_call({
        "event": "entry_rejected",
        "code": "005930",
        "name": "삼성전자",
        "reason": "low_trading_value",
        "trading_value": 29_999_999_999,
        "min_trading_value": 30_000_000_000,
    })


@pytest.mark.asyncio
async def test_scan_clears_candidate_codes_when_stage2_source_is_empty():
    strategy, _, stage_service = _build_strategy(stages=[2] * 260, price=121)
    await strategy.scan()
    assert strategy.current_candidate_codes() == ["005930"]

    stage_service.get_stage2_list.return_value.data = []

    assert await strategy.scan() == []
    assert strategy.current_candidate_codes() == []


@pytest.mark.asyncio
async def test_check_exits_on_hard_stop_before_stage_lookup():
    strategy, sqs, stage_service = _build_strategy(stages=[2] * 260, price=94)

    signals = await strategy.check_exits([
        {"code": "005930", "name": "삼성전자", "buy_price": 100, "qty": 3}
    ])

    assert len(signals) == 1
    assert signals[0].action == "SELL"
    assert signals[0].qty == 3
    assert "하드 스톱" in signals[0].reason
    stage_service.get_stage_for_code.assert_not_awaited()
    sqs.get_recent_daily_ohlcv.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_exits_when_stock_leaves_stage2():
    strategy, _, stage_service = _build_strategy(stages=[2] * 260, price=103)
    stage_service.get_stage_for_code.return_value = (3, "Stage 3")

    signals = await strategy.check_exits([
        {"code": "005930", "name": "삼성전자", "buy_price": 100, "qty": 1}
    ])

    assert len(signals) == 1
    assert "Stage 2 이탈" in signals[0].reason


@pytest.mark.asyncio
async def test_check_exits_at_four_percent_target_before_stage_lookup():
    strategy, _, stage_service = _build_strategy(stages=[2] * 260, price=104)

    signals = await strategy.check_exits([
        {"code": "005930", "name": "삼성전자", "buy_price": 100, "qty": 1}
    ])

    assert len(signals) == 1
    assert "목표 수익" in signals[0].reason
    stage_service.get_stage_for_code.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_exits_after_five_trading_days_without_target():
    rows = _daily_rows()
    strategy, _, _ = _build_strategy(stages=[2] * 260, price=102, rows=rows)

    signals = await strategy.check_exits([{
        "code": "005930", "name": "삼성전자", "buy_price": 100, "qty": 1,
        "buy_date": rows[-5]["date"],
    }])

    assert len(signals) == 1
    assert "시간 청산" in signals[0].reason


def test_config_defaults_keep_stage2_transition_selective():
    config = MinerviniStage2TransitionConfig()

    assert config.min_rs_rating == 80
    assert config.transition_lookback_days == 10
    assert config.breakout_lookback_days == 20
    assert config.max_breakout_extension_pct == 5.0
    assert config.min_market_cap_100m == 30_000
    assert config.min_trading_value == 30_000_000_000
    assert config.take_profit_pct == 4.0
    assert config.hard_stop_pct == -5.0
    assert config.max_holding_days == 5


@pytest.mark.asyncio
async def test_scan_fails_closed_when_stage_service_is_unavailable():
    strategy, _, _ = _build_strategy(stages=[1] * 255 + [2] * 5)
    strategy._stage_service = None

    assert await strategy.scan() == []
