"""인버스 ETF 레짐 슬리브 다중 하락 사이클 백테스트 러너 (R-2 Phase 2).

지수(KODEX 200, 069500), 인버스 ETF(KODEX 인버스, 114800), 반도체 대형주 일봉은
FinanceDataReader로 받고 KOSPI 투자자 수급은 KIS 시장별 투자자매매동향으로 받아
`InverseEtfRegimeBacktest` 로 4개 과거 하락 사이클의 PnL·MDD 를 리포트한다.

실행(데이터-ops, 네트워크 필요):
    conda activate py310
    python -m scripts.run_inverse_etf_regime_backtest               # 콘솔 리포트
    python -m scripts.run_inverse_etf_regime_backtest --output json # JSON 출력

ETF 비용 모델: 증권거래세(0.2%) 비과세 → round_trip_cost_pct 기본 0.1%(수수료+슬리피지).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List

from strategies.inverse_etf_regime_backtest import InverseEtfRegimeBacktest

# 이 독립 백테스트에서 사용하는 KOSPI 추종 ETF
INDEX_ETF_CODE = "069500"   # KODEX 200
INVERSE_ETF_CODE = "114800"  # KODEX 인버스 (-1x)
SEMICONDUCTOR_CODES = ("005930", "000660")  # 삼성전자, SK하이닉스

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


def normalize_kis_investor_flows(
    rows: List[Dict[str, Any]],
    start: str,
    end: str,
) -> List[Dict[str, Any]]:
    """KIS 시장별 투자자 일별 행을 억원 단위의 오름차순 수급으로 정규화한다."""
    normalized: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            date = datetime.strptime(str(row.get("stck_bsop_date", "")), "%Y%m%d").strftime("%Y-%m-%d")
            foreign = float(row["frgn_ntby_tr_pbmn"]) / 100.0
            institution = float(row["orgn_ntby_tr_pbmn"]) / 100.0
        except (KeyError, TypeError, ValueError):
            continue
        if start <= date <= end:
            normalized.append({
                "date": date,
                "foreign": foreign,
                "institution": institution,
            })
    normalized.sort(key=lambda row: row["date"])
    return normalized


async def _fetch_kis_investor_flows(periods: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """각 하락 사이클 종료일 기준 KIS 300일 응답에서 KOSPI 수급을 모은다."""
    from scripts._bootstrap import bootstrap_pp_strategy, make_stdout_logger

    logger = make_stdout_logger("inverse_etf_backtest", level=logging.WARNING)
    stock_query_service, _, _ = await bootstrap_pp_strategy(
        is_paper_trading=True,
        logger=logger,
    )
    by_date: Dict[str, Dict[str, Any]] = {}
    for period in periods:
        response = await stock_query_service.broker.inquire_investor_daily_by_market(
            "0001",
            date=period["end"].replace("-", ""),
        )
        if not response or response.rt_cd != "0" or not isinstance(response.data, list):
            raise RuntimeError(f"KIS KOSPI 투자자 수급 조회 실패: {period['label']}")
        period_rows = normalize_kis_investor_flows(
            response.data,
            period["start"],
            period["end"],
        )
        if not period_rows:
            raise RuntimeError(f"KIS KOSPI 투자자 수급 데이터 없음: {period['label']}")
        by_date.update({row["date"]: row for row in period_rows})
    return [by_date[date] for date in sorted(by_date)]


def _fetch_kis_investor_flows_sync(periods: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    return asyncio.run(_fetch_kis_investor_flows(periods))


def run(output: str = "console") -> Dict[str, Any]:
    start = min(p["start"] for p in BEAR_PERIODS)
    end = max(p["end"] for p in BEAR_PERIODS)
    index_bars = _fetch(INDEX_ETF_CODE, start, end)
    inverse_bars = _fetch(INVERSE_ETF_CODE, start, end)
    semiconductor_bars = {
        code: _fetch(code, start, end)
        for code in SEMICONDUCTOR_CODES
    }
    investor_flows = _fetch_kis_investor_flows_sync(BEAR_PERIODS)

    bt = InverseEtfRegimeBacktest()
    result = bt.run_periods(
        index_bars,
        inverse_bars,
        BEAR_PERIODS,
        investor_flows=investor_flows,
        semiconductor_bars=semiconductor_bars,
    )

    if output == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif output == "console":
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
    coverage = result["confirmation_coverage"]
    print(
        "\n확인 데이터 커버리지: "
        f"{coverage['fully_available_dates']}/{coverage['candidate_dates']} 후보일, "
        f"필터 통과 {coverage['confirmed_dates']}일"
    )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="인버스 ETF 레짐 슬리브 백테스트 (R-2 Phase 2)")
    parser.add_argument("--output", choices=["console", "json"], default="console")
    args = parser.parse_args()
    run(output=args.output)


if __name__ == "__main__":
    main()
