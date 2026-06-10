"""전략별 오버나이트(멀티세션 보유) 노출 요약.

순수 함수 모듈 — 외부 서비스 의존성 없이 standard journal records 입력만 받아 집계한다.
(strategy_correlation_service / regime_performance_service 와 동일한 형태.)

집계 대상:
  - 현재 노출(open_holds): status==HOLD 인 미청산 포지션. 일일 리포트는 장 마감 후
    생성되므로, 남아 있는 모든 HOLD 는 익일 시가 갭에 그대로 노출된다.
  - 실현 오버나이트(realized_overnight): status==SOLD 이며 매수일!=매도일인 멀티세션
    보유. 실현 수익 분포(평균/최저)는 갭 영향의 *사후* downside proxy 다.

범위 밖(forward gap): 실제 익일 시가 갭(전일 종가→당일 시가)의 정량 측정은 종목별
OHLCV 조인이 필요하며, journal 만으로는 산출할 수 없다. 본 모듈은 노출 *규모*와 실현
다운사이드만 노출하고, forward gap 측정은 별도(미래) 작업으로 남긴다.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Mapping, Optional


def compute_overnight_exposure_summary(
    records: Optional[Iterable[Mapping[str, Any]]],
    *,
    today: Optional[str] = None,
) -> dict[str, Any]:
    """오버나이트 노출 요약을 산출한다.

    Args:
        records: standard journal records (get_standard_journal_records 출력).
        today: 현재 노출 보유 경과일 계산 기준일 ("YYYY-MM-DD"). None 이면 오늘.
    """
    today_date = _parse_date(today) if today else date.today()

    open_by_strategy: dict[str, list[int]] = {}        # {strategy: [holding_days...]}
    realized_by_strategy: dict[str, dict[str, list]] = {}  # {strategy: {"days": [...], "rets": [...]}}

    for record in records or []:
        if not isinstance(record, Mapping):
            continue
        status = str(record.get("status") or "").upper()
        strategy = str(record.get("strategy") or "").strip()
        if not strategy:
            continue

        buy_date = _parse_date(record.get("signal_time")) or _parse_date(
            _meta_get(record, "buy_date")
        )

        if status == "HOLD":
            holding_days = _days_between(buy_date, today_date) if buy_date else 0
            open_by_strategy.setdefault(strategy, []).append(holding_days)
            continue

        if status == "SOLD":
            sell_date = _parse_date(_meta_get(record, "sell_date"))
            if buy_date is None or sell_date is None:
                continue
            span = _days_between(buy_date, sell_date)
            if span < 1:
                continue  # 당일 청산 = 오버나이트 아님
            bucket = realized_by_strategy.setdefault(strategy, {"days": [], "rets": []})
            bucket["days"].append(span)
            net = _to_float(record.get("net_return"))
            if net is not None:
                bucket["rets"].append(net)

    open_rows = []
    for strategy, days_list in open_by_strategy.items():
        open_rows.append({
            "strategy": strategy,
            "count": len(days_list),
            "max_holding_days": max(days_list) if days_list else 0,
            "avg_holding_days": round(sum(days_list) / len(days_list), 1) if days_list else 0.0,
        })
    open_rows.sort(key=lambda row: (-row["count"], row["strategy"]))

    realized_rows = []
    for strategy, bucket in realized_by_strategy.items():
        days_list = bucket["days"]
        rets = bucket["rets"]
        realized_rows.append({
            "strategy": strategy,
            "count": len(days_list),
            "avg_holding_days": round(sum(days_list) / len(days_list), 1) if days_list else 0.0,
            "avg_net_return": round(sum(rets) / len(rets), 2) if rets else 0.0,
            "worst_net_return": round(min(rets), 2) if rets else 0.0,
        })
    realized_rows.sort(key=lambda row: (-row["count"], row["strategy"]))

    return {
        "open_holds": {
            "total": sum(row["count"] for row in open_rows),
            "by_strategy": open_rows,
        },
        "realized_overnight": {
            "total": sum(row["count"] for row in realized_rows),
            "by_strategy": realized_rows,
        },
    }


def _parse_date(value: Any) -> Optional[date]:
    """'YYYY-MM-DD HH:MM:SS' / 'YYYY-MM-DD' / 'YYYYMMDD' 에서 날짜 부분을 추출한다."""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    head = raw[:10]
    try:
        return datetime.strptime(head, "%Y-%m-%d").date()
    except ValueError:
        pass
    digits = "".join(ch for ch in raw[:8] if ch.isdigit())
    if len(digits) == 8:
        try:
            return datetime.strptime(digits, "%Y%m%d").date()
        except ValueError:
            return None
    return None


def _days_between(start: date, end: date) -> int:
    return max((end - start).days, 0)


def _meta_get(record: Mapping[str, Any], key: str) -> Any:
    metadata = record.get("metadata")
    if isinstance(metadata, Mapping):
        return metadata.get(key)
    return None


def _to_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
