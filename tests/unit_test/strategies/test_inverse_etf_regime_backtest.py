# tests/unit_test/strategies/test_inverse_etf_regime_backtest.py
"""인버스 ETF 레짐 슬리브 백테스트 엔진 (R-2 Phase 2) 단위 테스트.

엔진은 데이터 소스 무관(`run(index_bars, inverse_bars)`)이며 합성 봉으로
레짐 게이팅·청산 우선순위·ETF 비용 모델·MDD·기간 분해를 검증한다.
"""
from strategies.inverse_etf_regime_backtest import InverseEtfRegimeBacktest


def _bars(closes, *, highs=None, lows=None, opens=None, start_day=1):
    """오름차순 일봉 리스트 생성. 기본은 시가=고가=저가=종가(갭/스파이크 없음)."""
    out = []
    for k, c in enumerate(closes):
        out.append({
            "date": f"2022-01-{start_day + k:02d}",
            "open": (opens[k] if opens else c),
            "high": (highs[k] if highs else c),
            "low": (lows[k] if lows else c),
            "close": c,
        })
    return out


def _down_index(n=40, start=10000.0, step=-100.0):
    """단조 하락 지수(종가 < 하락 MA → bear)."""
    return _bars([start + step * k for k in range(n)])


def _up_index(n=40, start=10000.0, step=100.0):
    """단조 상승 지수(bear 아님)."""
    return _bars([start + step * k for k in range(n)])


# ── 진입 게이트 ────────────────────────────────────────────────────

def test_no_entry_when_index_not_bear():
    """지수가 상승추세면 인버스 ETF가 올라도 진입하지 않는다."""
    bt = InverseEtfRegimeBacktest()
    index = _up_index()
    inverse = _bars([5000.0 + 50 * k for k in range(40)])  # 인버스도 상승
    result = bt.run(index, inverse)
    assert result["summary"]["total_trades"] == 0


def test_no_entry_when_inverse_below_trend_ma():
    """지수가 bear여도 인버스 ETF가 추세 미확인(종가 < MA)이면 진입하지 않는다."""
    bt = InverseEtfRegimeBacktest()
    index = _down_index()
    inverse = _bars([5000.0 - 50 * k for k in range(40)])  # 인버스 하락 → MA 아래
    result = bt.run(index, inverse)
    assert result["summary"]["total_trades"] == 0


def test_entry_when_bear_and_inverse_uptrend():
    """지수 bear + 인버스 ETF 상승추세(종가 > MA)면 진입한다."""
    bt = InverseEtfRegimeBacktest()
    index = _down_index()
    inverse = _bars([5000.0 + 50 * k for k in range(40)])
    result = bt.run(index, inverse)
    assert result["summary"]["total_trades"] >= 1
    assert result["trades"][0]["entry_price"] > 0


# ── 청산 ──────────────────────────────────────────────────────────

def test_exit_on_regime_flip_to_non_bear():
    """보유 중 지수가 bear에서 이탈하면 종가 청산(reason=regime)."""
    bt = InverseEtfRegimeBacktest(trend_ma_period=5, regime_ma_period=5)
    # 전반 하락(bear)에서 진입 → 후반 강한 상승으로 레짐 이탈
    idx_closes = [10000 - 100 * k for k in range(20)] + [8000 + 400 * k for k in range(20)]
    inv_closes = [5000 + 50 * k for k in range(20)] + [6000 - 10 * k for k in range(20)]
    result = bt.run(_bars(idx_closes), _bars(inv_closes))
    reasons = [t["exit_reason"] for t in result["trades"]]
    assert "regime" in reasons


def test_exit_on_hard_stop_with_gap_through():
    """하드 스탑: 저가가 스탑 이하로 갭다운하면 시가에 체결(보수)."""
    bt = InverseEtfRegimeBacktest(stop_loss_pct=-5.0, trend_ma_period=5, regime_ma_period=5)
    index = _down_index(n=30)
    # 인버스: 상승추세로 진입 유도 후, 한 봉에서 시가가 스탑 아래로 급락(갭다운)
    inv_closes = [5000 + 50 * k for k in range(10)]
    entry_close = inv_closes[-1]
    # 다음 봉 갭다운: 시가 4000(=entry*0.8), 저가 3900
    inv_closes += [4000.0]
    opens = inv_closes[:-1] + [4000.0]
    lows = inv_closes[:-1] + [3900.0]
    highs = inv_closes[:]
    inverse = _bars(inv_closes, opens=opens, lows=lows, highs=highs)
    result = bt.run(index, inverse)
    stop_trades = [t for t in result["trades"] if t["exit_reason"] == "stop"]
    assert stop_trades
    # 갭다운이므로 스탑가(entry*0.95)가 아니라 시가(4000)에 체결
    assert abs(stop_trades[0]["exit_price"] - 4000.0) < 1e-6


def test_exit_on_trailing_stop():
    """트레일링 스톱: 이익 구간에서 고점 대비 하락 시 청산."""
    bt = InverseEtfRegimeBacktest(stop_loss_pct=-20.0, trailing_stop_pct=8.0,
                                  trend_ma_period=5, regime_ma_period=5)
    index = _down_index(n=40)
    # 인버스: 진입 후 상승해 고점 형성 → -8% 하락
    inv_closes = [5000 + 50 * k for k in range(10)]  # 진입 유도
    inv_closes += [6000, 6500, 7000]  # 고점 7000
    inv_closes += [6400]  # 7000 대비 -8.57% → trailing
    inverse = _bars(inv_closes)
    result = bt.run(index, inverse)
    reasons = [t["exit_reason"] for t in result["trades"]]
    assert "trailing" in reasons


# ── ETF 비용 모델 ─────────────────────────────────────────────────

def test_etf_cost_model_excludes_transaction_tax():
    """ETF 비용 기본값은 거래세(0.2%) 미포함 — 주식 round-trip(0.2%)보다 작아야 한다."""
    bt = InverseEtfRegimeBacktest()
    assert bt.round_trip_cost_pct < 0.2


def test_net_return_subtracts_round_trip_cost():
    """net = gross - round_trip_cost_pct 가 적용된다."""
    bt = InverseEtfRegimeBacktest(round_trip_cost_pct=0.1, trend_ma_period=5, regime_ma_period=5)
    index = _down_index(n=20)
    inv_closes = [5000 + 50 * k for k in range(8)] + [5500]  # 진입 후 종료까지 보유(terminal)
    inverse = _bars(inv_closes)
    result = bt.run(index, inverse)
    assert result["trades"]
    t = result["trades"][0]
    assert abs((t["gross_return_pct"] - t["net_return_pct"]) - 0.1) < 1e-6


# ── 요약 / MDD ────────────────────────────────────────────────────

def test_summary_includes_max_drawdown():
    """요약에 max_drawdown_pct(거래 시퀀스 복리 자산곡선 기준)가 포함된다."""
    bt = InverseEtfRegimeBacktest(trend_ma_period=5, regime_ma_period=5)
    index = _down_index(n=40)
    inverse = _bars([5000 + 50 * k for k in range(40)])
    summary = bt.run(index, inverse)["summary"]
    assert "max_drawdown_pct" in summary
    assert summary["max_drawdown_pct"] <= 0.0


def test_empty_summary_on_no_trades():
    bt = InverseEtfRegimeBacktest()
    summary = bt.run(_up_index(), _bars([5000.0] * 40))["summary"]
    assert summary["total_trades"] == 0
    assert summary["max_drawdown_pct"] == 0.0


# ── 기간 분해 (다중 하락 사이클) ──────────────────────────────────

def test_run_periods_splits_by_date_window():
    """run_periods 는 라벨별 날짜 윈도우로 잘라 기간별 요약을 낸다."""
    bt = InverseEtfRegimeBacktest(trend_ma_period=3, regime_ma_period=3)
    index = _bars([10000 - 100 * k for k in range(40)], start_day=1)
    inverse = _bars([5000 + 50 * k for k in range(40)], start_day=1)
    periods = [
        {"label": "early", "start": "2022-01-01", "end": "2022-01-20"},
        {"label": "late", "start": "2022-01-21", "end": "2022-02-28"},
    ]
    out = bt.run_periods(index, inverse, periods)
    labels = [p["label"] for p in out["periods"]]
    assert labels == ["early", "late"]
    assert "overall" in out
