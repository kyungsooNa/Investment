from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class RejectionEvent:
    """전략 필터링 단계에서 발생한 탈락/통과 이벤트."""
    event: str       # "pp_rejected" | "bgu_rejected" | "entry_rejected" | ...
    code: str        # 종목 코드 (없으면 "")
    reason: str      # logger payload의 "reason" 값 (없으면 event 값)
    details: dict    # logger payload 전체 (참고용)
    timestamp: datetime
    level: int       # logging.DEBUG / INFO / WARNING


class RejectionLogHandler(logging.Handler):
    """LogRecord.msg가 dict이고 event in CAPTURED_EVENTS면 수집하는 로그 핸들러."""

    # 실제 OneilPocketPivotStrategy에 존재하는 구조화 로그 이벤트만 포함.
    # stage_blocked(StrategyExecutor f-string)는 1차 범위 외.
    CAPTURED_EVENTS = {
        "pp_rejected",
        "bgu_rejected",
        "entry_rejected",
        "entry_rejected_by_smart_money",
        "smart_money_rejected",
        "buy_signal_generated",
        "scan_skipped",
        "cgld_check_failed",
    }

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self._events: List[RejectionEvent] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.msg
        if not isinstance(msg, dict):
            return
        event = msg.get("event", "")
        if event not in self.CAPTURED_EVENTS:
            return
        self._events.append(RejectionEvent(
            event=event,
            code=str(msg.get("code", "")),
            reason=str(msg.get("reason", event)),
            details=dict(msg),
            timestamp=datetime.fromtimestamp(record.created),
            level=record.levelno,
        ))

    @property
    def events(self) -> List[RejectionEvent]:
        return list(self._events)


class RejectionCollector:
    """전략 실행 중 발생하는 탈락 이벤트를 수집하는 컨텍스트 매니저.

    권장 사용법: CLI에서 전용 debug logger를 생성해 주입한다.
      debug_logger = logging.getLogger("strategy_debug.OneilPocketPivot")
      strategy = OneilPocketPivotStrategy(..., logger=debug_logger)
      with RejectionCollector(logger=debug_logger) as col:
          await strategy.scan()
      print(col.events)

    Fallback: strategy._logger에 직접 attach할 경우 디버그 실행 중
    기존 전략 로그 파일에도 DEBUG 라인이 추가될 수 있다.
    """

    def __init__(
        self,
        *,
        strategy: object = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._target_logger: Optional[logging.Logger] = logger or getattr(strategy, "_logger", None)
        self._handler: Optional[RejectionLogHandler] = None
        self._saved_level: Optional[int] = None
        self._using_fallback = logger is None and strategy is not None

    def __enter__(self) -> "RejectionCollector":
        if self._target_logger is None:
            raise ValueError(
                "RejectionCollector requires logger= or strategy= with _logger attribute."
            )
        self._handler = RejectionLogHandler()
        self._saved_level = self._target_logger.level

        # 운영 환경 LOG_LEVEL이 INFO여도 debug 이벤트를 빠짐없이 수집하기 위해 임시 강하
        if self._saved_level == logging.NOTSET or self._saved_level > logging.DEBUG:
            self._target_logger.setLevel(logging.DEBUG)

        if self._using_fallback:
            import warnings
            warnings.warn(
                "RejectionCollector: strategy._logger에 직접 attach합니다. "
                "디버그 실행 중 기존 전략 로그 파일에도 DEBUG 라인이 추가될 수 있습니다. "
                "전용 logger 주입을 권장합니다.",
                stacklevel=2,
            )

        self._target_logger.addHandler(self._handler)
        return self

    def __exit__(self, *_) -> None:
        if self._target_logger is not None and self._handler is not None:
            try:
                self._target_logger.removeHandler(self._handler)
            finally:
                if self._saved_level is not None:
                    self._target_logger.setLevel(self._saved_level)

    @property
    def events(self) -> List[RejectionEvent]:
        if self._handler is None:
            return []
        return self._handler.events

    def by_code(self) -> Dict[str, List[RejectionEvent]]:
        result: Dict[str, List[RejectionEvent]] = {}
        for e in self.events:
            result.setdefault(e.code, []).append(e)
        return result
