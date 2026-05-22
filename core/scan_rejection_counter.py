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
from typing import Dict


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
