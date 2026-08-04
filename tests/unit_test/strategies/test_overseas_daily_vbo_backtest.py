"""해외 일봉 VBO 백테스트 테스트 (Phase 2).

VBO는 본래 장중 전략이나, 해외는 분봉/실시간이 없으므로 고전적 Larry Williams
일봉 근사로 검증한다:
  target = 당일시가 + K×전일Range(전일고-전일저)
  당일고 >= target → target 진입 → (저 <= 손절가) 손절 / else 종가 청산
배선/실주문 전 "해외에서 의미있는 신호를 내는가"를 확인하는 게이트.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from strategies.overseas_daily_vbo_backtest import OverseasDailyVBOBacktest
from common.types import ErrorCode, ResCommonResponse
from common.overseas_types import OverseasExchange


def _bar(d, o, h, l, c, v=1000):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}


# 전일 range=10 (100~110)
_PREV = _bar("20260511", 100, 110, 100, 105)


def test_entry_when_high_breaks_target_exits_at_close():
    bt = OverseasDailyVBOBacktest(k_value=0.5, stop_loss_pct=-3.0, round_trip_cost_pct=0.0)
    # target = 100 + 0.5*10 = 105, high 120>=105 진입, 저 104 > 손절(101.85) → 종가 115 청산
    bars = [_PREV, _bar("20260512", 100, 120, 104, 115)]

    res = bt.run_symbol(bars)

    assert res["summary"]["total_trades"] == 1
    t = res["trades"][0]
    assert t["entry_price"] == 105.0
    assert t["exit_price"] == 115.0
    assert t["exit_reason"] == "eod"
    assert round(t["net_return_pct"], 3) == round((115 / 105 - 1) * 100, 3)


def test_no_entry_when_high_below_target():
    bt = OverseasDailyVBOBacktest(k_value=0.5, stop_loss_pct=-3.0, round_trip_cost_pct=0.0)
    bars = [_PREV, _bar("20260512", 100, 104, 100, 103)]  # high 104 < target 105

    res = bt.run_symbol(bars)

    assert res["summary"]["total_trades"] == 0
    assert res["trades"] == []


def test_stop_touch_is_undecided_with_bracket():
    """저 <= 손절가는 진입 전/후 순서를 일봉으로 판정할 수 없어 bracket 으로 남긴다."""
    # target=105 진입, 저 100 <= 손절가 101.85 → 판정 불가(비관 -3% / 낙관 종가 110)
    bars = [_PREV, _bar("20260512", 100, 120, 100, 110)]

    bt = OverseasDailyVBOBacktest(k_value=0.5, stop_loss_pct=-3.0, round_trip_cost_pct=0.0)
    res = bt.run_symbol(bars)

    t = res["trades"][0]
    assert t["exit_reason"] == "undecided"
    assert t["exit_decidable"] is False
    assert round(t["net_return_pct"], 4) == -3.0  # 하위호환: 비관값 유지
    assert round(t["net_return_pct_optimistic"], 3) == round((110 / 105 - 1) * 100, 3)


def test_stop_not_forced_when_stop_price_above_open():
    """회귀: 손절가 >= 시가면 당일저가는 자동으로 손절가 이하 → 구모델은 손절 강제."""
    # 전일 range 20 → target 110, 손절가 106.7 > 시가 100
    bars = [_bar("20260511", 100, 120, 100, 110), _bar("20260512", 100, 130, 99, 128)]

    bt = OverseasDailyVBOBacktest(k_value=0.5, stop_loss_pct=-3.0, round_trip_cost_pct=0.0)
    res = bt.run_symbol(bars)

    t = res["trades"][0]
    assert t["exit_reason"] == "undecided"
    assert round(t["gross_return_pct_optimistic"], 3) == round((128 / 110 - 1) * 100, 3)


def test_summary_separates_undecided_and_optimistic_average():
    bars = [
        _bar("20260511", 100, 110, 100, 105),  # prev(range10) for 0512
        _bar("20260512", 100, 120, 104, 115),  # 판정 가능: target105, eod 115
        _bar("20260513", 100, 101, 100, 100),  # 진입 없음
        _bar("20260514", 100, 120, 95, 110),   # 판정 불가: target100.5, 저95<=97.485
    ]

    bt = OverseasDailyVBOBacktest(k_value=0.5, stop_loss_pct=-3.0, round_trip_cost_pct=0.0)
    s = bt.run_symbol(bars)["summary"]

    assert s["total_trades"] == 2
    assert s["decided_trades"] == 1
    assert s["undecided_trades"] == 1
    # 낙관 평균은 판정 불가 건을 종가 청산으로 가정한 값 — 비관 평균보다 크다.
    assert s["avg_net_return_pct_optimistic"] > s["avg_net_return_pct"]


def test_round_trip_cost_reduces_net_return():
    bt = OverseasDailyVBOBacktest(k_value=0.5, stop_loss_pct=-3.0, round_trip_cost_pct=0.5)
    bars = [_PREV, _bar("20260512", 100, 120, 104, 115)]

    res = bt.run_symbol(bars)
    t = res["trades"][0]
    gross = (115 / 105 - 1) * 100
    assert round(t["gross_return_pct"], 3) == round(gross, 3)
    assert round(t["net_return_pct"], 3) == round(gross - 0.5, 3)


def test_summary_aggregates_win_rate_and_avg():
    bt = OverseasDailyVBOBacktest(k_value=0.5, stop_loss_pct=-3.0, round_trip_cost_pct=0.0)
    bars = [
        _bar("20260511", 100, 110, 100, 105),  # prev(range10) for 0512
        _bar("20260512", 100, 120, 104, 115),  # win: target105, eod 115
        _bar("20260513", 100, 101, 100, 100),  # no entry (prev range16 → target108 > high101)
        _bar("20260514", 100, 120, 95, 110),   # stop: prev range1 → target100.5, 저95<=97.485
    ]

    res = bt.run_symbol(bars)
    s = res["summary"]
    assert s["total_trades"] == 2
    assert s["wins"] == 1
    assert s["win_rate"] == 0.5
    assert res["trades"][1]["exit_reason"] == "undecided"


@pytest.mark.asyncio
async def test_run_backtest_fetches_via_overseas_adapter_and_aggregates():
    bt = OverseasDailyVBOBacktest(k_value=0.5, stop_loss_pct=-3.0, round_trip_cost_pct=0.0)
    sqs = MagicMock()
    bars = [_PREV, _bar("20260512", 100, 120, 104, 115)]
    sqs.get_ohlcv_range = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=bars)
    )

    res = await bt.run_backtest(
        sqs, symbols=["AAPL"], exchange=OverseasExchange.NASD,
        start_date="20260501", end_date="20260512",
    )

    # 일봉 조회가 해외 거래소 인자로 위임되는지
    _, kwargs = sqs.get_ohlcv_range.await_args
    assert kwargs.get("exchange") == OverseasExchange.NASD
    assert res["summary"]["total_trades"] == 1
    assert res["per_symbol"]["AAPL"]["summary"]["total_trades"] == 1
