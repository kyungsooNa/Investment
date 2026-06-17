"""MultiDayDailyBreakoutBacktest 단위 테스트 (R-1 생존편향 PnL 정량화용).

오버나잇 보유 돌파 전략(20일 신고가 돌파 진입 + 손절/트레일링/시간청산 + 종말
강제청산)의 일봉 근사 엔진. 핵심 검증:
  - 돌파 진입 / 미돌파 무진입
  - hard stop (gap-through: 갭다운이 stop 관통 시 시가 체결 → 상폐 붕괴 포착)
  - 트레일링 stop / 시간청산
  - 종말 강제청산(상폐일까지 보유 시 마지막 봉 종가 청산)
"""
from __future__ import annotations

from strategies.multiday_daily_breakout_backtest import MultiDayDailyBreakoutBacktest


def _bar(d, o, h, l, c):
    return {"date": d, "open": o, "high": h, "low": l, "close": c}


def _flat(n, price, start_day=1):
    """변동 없는 평탄 봉 n개 (돌파 기준선 형성용)."""
    return [_bar(f"2025-01-{start_day+i:02d}", price, price, price, price) for i in range(n)]


def test_no_breakout_no_trades():
    eng = MultiDayDailyBreakoutBacktest(breakout_lookback=5)
    bars = _flat(10, 1000)  # 종가가 prior high를 못 넘음
    res = eng.run_symbol(bars)
    assert res["trades"] == []
    assert res["summary"]["total_trades"] == 0


def test_breakout_entry_then_hard_stop_no_gap():
    eng = MultiDayDailyBreakoutBacktest(
        breakout_lookback=5, stop_loss_pct=-5.0, trailing_stop_pct=None, time_stop_days=None
    )
    bars = _flat(5, 1000)
    # day5(인덱스5): 종가 1100 > prior 5일 high(1000) → 진입 @1100
    bars.append(_bar("2025-01-06", 1050, 1120, 1040, 1100))
    # day6: 저가가 stop(1045=1100*0.95) 관통, 시가는 stop 위 → stop 가격 체결
    bars.append(_bar("2025-01-07", 1080, 1085, 1000, 1010))
    res = eng.run_symbol(bars)
    assert len(res["trades"]) == 1
    t = res["trades"][0]
    assert t["entry_price"] == 1100
    assert t["exit_reason"] == "stop"
    assert t["exit_price"] == 1045.0  # 1100 * 0.95, 갭 없음
    assert round(t["gross_return_pct"], 2) == -5.0


def test_gap_through_stop_fills_at_open_captures_collapse():
    """갭다운이 stop을 관통하면 시가 체결 → 상폐 붕괴 손실 포착(R-4 동일 원리)."""
    eng = MultiDayDailyBreakoutBacktest(
        breakout_lookback=5, stop_loss_pct=-5.0, trailing_stop_pct=None, time_stop_days=None
    )
    bars = _flat(5, 1000)
    bars.append(_bar("2025-01-06", 1050, 1120, 1040, 1100))  # 진입 @1100, stop=1045
    # 정리매매/거래정지 해제 갭다운: 시가 500(=stop 1045 한참 아래)
    bars.append(_bar("2025-01-07", 500, 520, 400, 450))
    res = eng.run_symbol(bars)
    t = res["trades"][0]
    assert t["exit_reason"] == "stop"
    assert t["exit_price"] == 500  # stop이 아닌 갭 시가에 체결 (관통)
    assert round(t["gross_return_pct"], 1) == round((500 / 1100 - 1) * 100, 1)
    assert t["gross_return_pct"] < -50  # 붕괴 포착


def test_terminal_force_close_at_last_bar():
    """상폐일까지 보유(미청산) 시 마지막 봉 종가로 강제청산."""
    eng = MultiDayDailyBreakoutBacktest(
        breakout_lookback=5, stop_loss_pct=-90.0, trailing_stop_pct=None, time_stop_days=None
    )
    bars = _flat(5, 1000)
    bars.append(_bar("2025-01-06", 1000, 1100, 990, 1050))  # 진입 @1050, stop 매우 낮음(-90%)
    # 완만히 하락하지만 stop(105) 위 → 미청산 → 마지막 봉에서 종말 청산
    bars.append(_bar("2025-01-07", 1040, 1045, 900, 950))
    bars.append(_bar("2025-01-08", 940, 950, 800, 850))
    res = eng.run_symbol(bars)
    t = res["trades"][0]
    assert t["exit_reason"] == "terminal"
    assert t["exit_price"] == 850
    assert t["exit_date"] == "2025-01-08"


def test_time_stop_exits_at_close():
    eng = MultiDayDailyBreakoutBacktest(
        breakout_lookback=5, stop_loss_pct=-50.0, trailing_stop_pct=None, time_stop_days=2
    )
    bars = _flat(5, 1000)
    bars.append(_bar("2025-01-06", 1000, 1100, 990, 1050))  # 진입 @1050
    bars.append(_bar("2025-01-07", 1050, 1060, 1010, 1040))  # hold 1
    bars.append(_bar("2025-01-08", 1040, 1070, 1030, 1060))  # hold 2 → time stop, 종가 1060
    bars.append(_bar("2025-01-09", 1060, 1080, 1050, 1070))
    res = eng.run_symbol(bars)
    t = res["trades"][0]
    assert t["exit_reason"] == "time"
    assert t["exit_price"] == 1060
    assert t["holding_days"] == 2


def test_summary_aggregates():
    eng = MultiDayDailyBreakoutBacktest(breakout_lookback=5, stop_loss_pct=-5.0, trailing_stop_pct=None, time_stop_days=None)
    bars = _flat(5, 1000)
    bars.append(_bar("2025-01-06", 1050, 1120, 1040, 1100))
    bars.append(_bar("2025-01-07", 1080, 1085, 1000, 1010))  # stop -5%
    res = eng.run_symbol(bars)
    s = res["summary"]
    assert s["total_trades"] == 1
    assert s["wins"] == 0
    assert s["win_rate"] == 0.0
    assert "avg_net_return_pct" in s and "total_net_return_pct" in s
