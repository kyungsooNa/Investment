"""전략 거절 사유 분포 리포트 API."""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import List

from fastapi import APIRouter

from view.web.api_common import _get_ctx

router = APIRouter()

_DEFAULT_LOG_DIR = os.path.join("logs", "strategies", "rejections")


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
