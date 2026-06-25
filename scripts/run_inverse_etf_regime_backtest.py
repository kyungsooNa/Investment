"""인버스 ETF 레짐 슬리브 다중 하락 사이클 백테스트 러너 (R-2 Phase 2).

지수(KODEX 200, 069500)와 인버스 ETF(KODEX 인버스, 114800) 일봉을 FinanceDataReader
로 받아 `InverseEtfRegimeBacktest` 로 4개 과거 하락 사이클의 PnL·MDD 를 리포트한다.

실행(데이터-ops, 네트워크 필요):
    conda activate py310
    python -m scripts.run_inverse_etf_regime_backtest               # 콘솔 리포트
    python -m scripts.run_inverse_etf_regime_backtest --output json # JSON 출력

ETF 비용 모델: 증권거래세(0.2%) 비과세 → round_trip_cost_pct 기본 0.1%(수수료+슬리피지).
"""
from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List

from strategies.inverse_etf_regime_backtest import InverseEtfRegimeBacktest

# KOSPI 레짐 판정용 지수 ETF (MarketRegimeService.kospi_etf_code 와 일치)
INDEX_ETF_CODE = "069500"   # KODEX 200
INVERSE_ETF_CODE = "114800"  # KODEX 인버스 (-1x)

# 다중 하락 사이클 윈도우 (회복 일부 포함 — 레짐 이탈 청산까지 관측)
BEAR_PERIODS: List[Dict[str, str]] = [
    {"label": "2018 미중무역분쟁", "start": "2018-01-01", "end": "2019-01-31"},
    {"label": "2020 COVID 급락",   "start": "2020-01-01", "end": "2020-06-30"},
    {"label": "2022 금리인상 베어", "start": "2022-01-01", "end": "2022-11-30"},
    {"label": "2024 하반기 조정",   "start": "2024-07-01", "end": "2025-01-31"},
]


def normalize_fdr_ohlcv(df) -> List[Dict[str, Any]]:
    """FDR DataReader 결과(DataFrame)를 오름차순 일봉 dict 리스트로 정규화."""
    import pandas as pd

    rows: List[Dict[str, Any]] = []
    if df is None or getattr(df, "empty", True):
        return rows
    frame = df.copy()
    if "Date" not in frame.columns:
        frame = frame.reset_index().rename(columns={"index": "Date"})
    for _, row in frame.iterrows():
        ts = pd.to_datetime(row.get("Date"), errors="coerce")
        if pd.isna(ts):
            continue
        try:
            rows.append({
                "date": ts.strftime("%Y-%m-%d"),
                "open": float(row.get("Open")),
                "high": float(row.get("High")),
                "low": float(row.get("Low")),
                "close": float(row.get("Close")),
            })
        except (TypeError, ValueError):
            continue
    rows.sort(key=lambda r: r["date"])
    return rows


def _fetch(code: str, start: str, end: str) -> List[Dict[str, Any]]:
    import FinanceDataReader as fdr
    return normalize_fdr_ohlcv(fdr.DataReader(code, start, end))


def run(output: str = "console") -> Dict[str, Any]:
    start = min(p["start"] for p in BEAR_PERIODS)
    end = max(p["end"] for p in BEAR_PERIODS)
    index_bars = _fetch(INDEX_ETF_CODE, start, end)
    inverse_bars = _fetch(INVERSE_ETF_CODE, start, end)

    bt = InverseEtfRegimeBacktest()
    result = bt.run_periods(index_bars, inverse_bars, BEAR_PERIODS)

    if output == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_console(result)
    return result


def _print_console(result: Dict[str, Any]) -> None:
    print("\n=== 인버스 ETF 레짐 슬리브 - 다중 하락 사이클 백테스트 (R-2) ===\n")
    header = f"{'기간':<20}{'거래':>5}{'승률':>8}{'총net%':>10}{'복리%':>10}{'MDD%':>9}"
    print(header)
    print("-" * len(header))
    for p in result["periods"]:
        s = p["summary"]
        print(f"{p['label']:<20}{s['total_trades']:>5}{s['win_rate'] * 100:>7.1f}%"
              f"{s['total_net_return_pct']:>10.2f}{s['compound_return_pct']:>10.2f}"
              f"{s['max_drawdown_pct']:>9.2f}")
    o = result["overall"]
    print("-" * len(header))
    print(f"{'전체':<20}{o['total_trades']:>5}{o['win_rate'] * 100:>7.1f}%"
          f"{o['total_net_return_pct']:>10.2f}{o['compound_return_pct']:>10.2f}"
          f"{o['max_drawdown_pct']:>9.2f}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="인버스 ETF 레짐 슬리브 백테스트 (R-2 Phase 2)")
    parser.add_argument("--output", choices=["console", "json"], default="console")
    args = parser.parse_args()
    run(output=args.output)


if __name__ == "__main__":
    main()
