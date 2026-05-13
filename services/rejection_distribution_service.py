"""전략별/일자별 거절 사유 분포를 수집하고 파일로 플러시하는 서비스."""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime
from typing import Dict, Optional


class RejectionDistributionService:
    """실전 거래 중 발생하는 거절 사유를 날짜/전략별로 누적하고 파일로 플러시.

    사용법:
      svc = RejectionDistributionService(reason_labels=_REASON_KR)
      svc.attach_to_strategy_logger()   # 전략 logger에 자동 수집 핸들러 부착
      ...
      svc.flush_to_file("20260513")     # 장 마감 후 JSONL 저장
    """

    def __init__(self, reason_labels: Optional[Dict[str, str]] = None) -> None:
        # {date: {strategy_name: {reason_code: count}}}
        self._counts: Dict[str, Dict[str, Dict[str, int]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(int))
        )
        self._reason_labels: Dict[str, str] = reason_labels or {}

    def record(
        self,
        strategy_name: str,
        reason_code: str,
        stock_code: str = "",
        date: Optional[str] = None,
    ) -> None:
        """거절 사유 1건을 누적한다."""
        if not strategy_name or not reason_code:
            return
        key = date or datetime.now().strftime("%Y%m%d")
        self._counts[key][strategy_name][reason_code] += 1

    def get_distribution(self, strategy_name: str, date: str) -> Dict[str, int]:
        """{reason_code: count} 반환. 해당 날짜/전략 데이터 없으면 빈 dict."""
        return dict(self._counts.get(date, {}).get(strategy_name, {}))

    def get_all_strategies(self, date: str) -> Dict[str, Dict[str, int]]:
        """{strategy_name: {reason_code: count}} 전략 전체 반환."""
        return {
            strategy: dict(reasons)
            for strategy, reasons in self._counts.get(date, {}).items()
        }

    def flush_to_file(
        self,
        date: str,
        log_dir: str = "logs/strategies/rejections",
    ) -> None:
        """누적 데이터를 logs/strategies/rejections/YYYYMMDD.jsonl 에 저장한다.

        각 line 형식:
          {"strategy": ..., "date": ..., "reason_code": ..., "count": ..., "label_kr": ...}
        """
        strategy_map = self._counts.get(date, {})
        if not strategy_map:
            return
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, f"{date}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for strategy_name, reason_map in sorted(strategy_map.items()):
                for reason_code, count in sorted(reason_map.items()):
                    row = {
                        "strategy": strategy_name,
                        "date": date,
                        "reason_code": reason_code,
                        "count": count,
                        "label_kr": self._reason_labels.get(reason_code, reason_code),
                    }
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def attach_to_strategy_logger(self) -> None:
        """strategy.* 네임스페이스 로거에 핸들러를 부착해 거절 이벤트를 자동 수집한다.

        strategy.* 로거들은 propagate=True 이므로 부모 "strategy" 로거에 한 번만 부착하면 된다.
        중복 호출해도 핸들러가 두 번 부착되지 않는다.
        """
        handler = _StrategyRejectionHandler(self)
        logger = logging.getLogger("strategy")
        if not any(
            isinstance(h, _StrategyRejectionHandler) and h._service is self
            for h in logger.handlers
        ):
            logger.addHandler(handler)


# ── Internal handler ──────────────────────────────────────────────────────

_REJECTION_EVENTS: frozenset[str] = frozenset({
    "pp_rejected",
    "bgu_rejected",
    "entry_rejected",
    "entry_rejected_by_smart_money",
    "smart_money_rejected",
    "stage_blocked",
    "liquidity_blocked",
})


class _StrategyRejectionHandler(logging.Handler):
    """strategy.* 로거의 거절 이벤트를 RejectionDistributionService로 라우팅."""

    def __init__(self, service: RejectionDistributionService) -> None:
        super().__init__(level=logging.DEBUG)
        self._service = service

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.msg
        if not isinstance(msg, dict):
            return
        event = msg.get("event", "")
        if event not in _REJECTION_EVENTS:
            return
        reason = str(msg.get("reason", event))
        # logger name: "strategy.OneilPocketPivot" → "OneilPocketPivot"
        parts = record.name.split(".", 1)
        strategy_name = parts[1] if len(parts) > 1 else record.name
        self._service.record(strategy_name=strategy_name, reason_code=reason)
