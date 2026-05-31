"""EntryRejectionCounter — scan() 동안 발행된 entry_rejected 이벤트의 reason 분포 누적.

P2 2-2 1차 (scan cycle 성능 계측). `StrategyScheduler`가 strategy.scan() 호출 직전
strategy logger 에 attach 하고, 직후 detach 해 그 한 cycle 동안의 reason 카운트를
`scan_metrics` log event 의 `rejected_reasons` 필드로 노출한다.

logging.Handler 를 상속해 logger 파이프라인에 자연 통합한다. record.msg 가 dict 이고
`event == "entry_rejected"` 인 경우에만 `reason` 필드를 카운트한다. 다른 event 나
non-dict msg 는 무시한다.

전략 본문은 수정하지 않는다 — 기존 `{"event": "entry_rejected", "reason": ...}`
구조화 로그를 그대로 사용한다.

한계: 캡처 범위는 logger.effective_level 에 따른다. 운영 `LOG_LEVEL=INFO` 인 경우
strategy 의 `_logger.debug({...})` 로 emit 되는 entry_rejected (예: FirstPullback)는
counter 에 도달하지 않는다. INFO 이상으로 emit 되는 entry_rejected (예: HighTightFlag
의 `_log_entry_rejected` → `info`) 만 카운트된다. dev (`LOG_LEVEL=DEBUG`) 환경에서는
모두 캡처된다. 전 전략을 INFO 로 표준화하는 보강은 별도 후속 PR.
"""
from __future__ import annotations

import logging
from typing import Dict, Set


class EntryRejectionCounter(logging.Handler):
    """전략 scan cycle 동안 entry_rejected reason 별 카운트를 누적한다."""

    def __init__(self) -> None:
        super().__init__()
        self._counts: Dict[str, int] = {}

    def record(self, reason: str) -> None:
        """수동 record (테스트 또는 strategy 본문 직접 호출용)."""
        if not reason:
            return
        self._counts[reason] = self._counts.get(reason, 0) + 1

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.msg
        if not isinstance(msg, dict):
            return
        if msg.get("event") != "entry_rejected":
            return
        reason = msg.get("reason")
        if not isinstance(reason, str) or not reason:
            return
        self._counts[reason] = self._counts.get(reason, 0) + 1

    def snapshot(self) -> Dict[str, int]:
        """현재 카운터 사본을 반환한다."""
        return dict(self._counts)

    def reset(self) -> None:
        self._counts.clear()


class StrategyCalcFailureCounter(logging.Handler):
    """전략 scan/check_exits 동안 per-code 계산 실패 이벤트를 누적한다.

    `event` 이름에 error/failed/exception 이 포함되고 `code` 가 있는 구조화 로그만
    카운트한다. 전략 단위 장애나 state load/save 같은 code-less 이벤트는 실패율
    분모와 맞지 않으므로 제외한다.
    """

    _FAILURE_MARKERS = ("error", "failed", "exception")

    def __init__(self) -> None:
        super().__init__()
        self._counts: Dict[str, int] = {}
        self._failed_codes: Set[str] = set()

    def record(self, event: str, code: str) -> None:
        if not event or not code:
            return
        self._counts[event] = self._counts.get(event, 0) + 1
        self._failed_codes.add(code)

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.msg
        if not isinstance(msg, dict):
            return
        event = msg.get("event")
        if not isinstance(event, str) or not event:
            return
        event_key = event.casefold()
        if not any(marker in event_key for marker in self._FAILURE_MARKERS):
            return
        code = msg.get("code")
        if not isinstance(code, str):
            return
        code = code.strip()
        if not code:
            return
        self.record(event, code)

    def snapshot(self) -> Dict[str, int]:
        return dict(self._counts)

    def total_count(self) -> int:
        return sum(self._counts.values())

    def failed_code_count(self) -> int:
        return len(self._failed_codes)

    def failure_rate_pct(self, denominator: int) -> float:
        if denominator <= 0:
            return 0.0
        return round(self.failed_code_count() / denominator * 100.0, 4)

    def reset(self) -> None:
        self._counts.clear()
        self._failed_codes.clear()
