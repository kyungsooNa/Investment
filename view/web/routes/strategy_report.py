"""전략 거절 사유 분포 리포트 API + 시장 국면별 성과 분해 API."""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import List

from fastapi import APIRouter, HTTPException, Query

from common.trade_journal_schema import normalize_virtual_trade
from services.regime_performance_service import (
    BUCKET_KEYS,
    compute_performance_by_regime,
)
from view.web.api_common import _get_ctx

router = APIRouter()

_DEFAULT_LOG_DIR = os.path.join("logs", "strategies", "rejections")


@router.get("/strategies/diagnostic-reports")
async def get_diagnostic_reports(limit: int = Query(100, ge=1, le=500)):
    """전략 상세 진단과 Telegram 발송 이력을 최신순으로 반환한다."""
    ctx = _get_ctx()
    diagnostics = [
        {
            **report,
            "kind": "strategy_diagnostic",
            "title": "전략 상세 진단",
        }
        for report in ctx.strategy_diagnostic_report_repository.list_reports(limit=limit)
    ]
    telegram = ctx.telegram_notification_repository.list_reports(limit=limit)
    reports = sorted(
        [*diagnostics, *telegram],
        key=lambda report: report.get("created_at", ""),
        reverse=True,
    )
    return {"reports": reports[:limit]}


@router.get("/strategies/diagnostic-reports/{report_id}")
async def get_diagnostic_report(report_id: str):
    """전략 상세 진단 또는 Telegram 발송 이력 한 건을 반환한다."""
    ctx = _get_ctx()
    if report_id.startswith("telegram-"):
        report = ctx.telegram_notification_repository.get_report(report_id)
    else:
        report = ctx.strategy_diagnostic_report_repository.get_report(report_id)
        if report is not None:
            report = {
                **report,
                "kind": "strategy_diagnostic",
                "title": "전략 상세 진단",
            }
    if report is None:
        raise HTTPException(status_code=404, detail="상세 리포트를 찾을 수 없습니다.")
    return report


def _read_file_rows(date: str, log_dir: str) -> List[dict]:
    """YYYYMMDD.jsonl 파일에서 행을 읽는다. 파일이 없으면 빈 리스트 반환."""
    path = os.path.join(log_dir, f"{date}.jsonl")
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


@router.get("/strategies/rejected-reasons")
async def get_rejected_reasons(strategy: str = "", date: str = ""):
    """전략별/일자별 거절 사유 분포 조회.

    Args:
        strategy: 전략명 필터. 빈 문자열이면 전체 전략.
        date:     YYYYMMDD. 빈 문자열이면 오늘.

    Returns:
        {
          "strategy": <strategy or "">,
          "date": <YYYYMMDD>,
          "distribution": [
            {"strategy": ..., "reason_code": ..., "count": ..., "label_kr": ...}
          ]
        }

    필드명 규약: format_json()의 events[].reason → reason_code, count, label_kr.
    """
    target_date = date or datetime.now().strftime("%Y%m%d")
    ctx = _get_ctx()
    svc = getattr(ctx, "rejection_distribution_service", None)

    # 1) 메모리 데이터 수집
    distribution: List[dict] = []
    if svc is not None:
        all_strategies = svc.get_all_strategies(target_date)
        reason_labels = getattr(svc, "_reason_labels", {})
        for strat_name, reason_map in all_strategies.items():
            if strategy and strat_name != strategy:
                continue
            for reason_code, count in reason_map.items():
                distribution.append({
                    "strategy": strat_name,
                    "reason_code": reason_code,
                    "count": count,
                    "label_kr": reason_labels.get(reason_code, reason_code),
                })

    # 2) 파일 데이터 병합 (메모리에 없는 과거 날짜 or 재시작 후)
    if not distribution:
        for row in _read_file_rows(target_date, _DEFAULT_LOG_DIR):
            strat_name = row.get("strategy", "")
            if strategy and strat_name != strategy:
                continue
            distribution.append({
                "strategy": strat_name,
                "reason_code": row.get("reason_code", ""),
                "count": row.get("count", 0),
                "label_kr": row.get("label_kr", row.get("reason_code", "")),
            })

    return {
        "strategy": strategy,
        "date": target_date,
        "distribution": distribution,
    }


@router.get("/strategies/performance-by-regime")
async def get_performance_by_regime(
    strategy: str = "",
    from_date: str = "",
    to_date: str = "",
):
    """시장 국면별 전략 성과 분해.

    Args:
        strategy:  전략명 필터. 빈 문자열이면 전체.
        from_date: YYYYMMDD. 빈 문자열이면 전체 기간 시작.
        to_date:   YYYYMMDD. 빈 문자열이면 오늘.

    Returns:
        {
          "strategy": <strategy or "">,
          "from_date": <YYYYMMDD or "">,
          "to_date": <YYYYMMDD>,
          "buckets": {
            "KOSPI_BULL": {trade_count, win_count, win_rate, avg_net_return, total_net_pnl, mdd},
            "KOSDAQ_BULL": {...},
            "SIDEWAYS": {...},
            "BEAR": {...},
            "TRADING_VALUE_SURGE": {...}  # 1차: 항상 0 (overlay)
          }
        }
    """
    target_to = to_date or datetime.now().strftime("%Y%m%d")
    ctx = _get_ctx()
    vts = getattr(ctx, "virtual_trade_service", None)

    normalized: List[dict] = []
    if vts is not None:
        trades = vts.get_all_trades(apply_cost=True)
        for t in trades:
            if strategy and str(t.get("strategy") or "") != strategy:
                continue
            signal_time = str(t.get("buy_date") or "").replace("-", "")[:8]
            if from_date and signal_time < from_date:
                continue
            if signal_time > target_to:
                continue
            normalized.append(normalize_virtual_trade(t))

    buckets = compute_performance_by_regime(normalized)
    return {
        "strategy": strategy,
        "from_date": from_date,
        "to_date": target_to,
        "buckets": {k: buckets[k] for k in BUCKET_KEYS},
    }
