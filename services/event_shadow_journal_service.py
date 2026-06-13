"""EventShadowJournalService — event-driven shadow 신호 기록 서비스 (P2 2-4 PR-2).

shadow 신호는 폴링 루프의 실제 주문을 발생시키지 않고, "만약 event 경로로 실행했다면
이 시각에 BUY 신호가 났을 것" 이라는 사실만 별도 journal 파일에 남긴다.

용도:
  - 폴링 신호 (`metadata.signal_source="polling"`) vs event 신호 (`...="event_shadow"`)
    의 시각·종목 차이 오프라인 비교
  - 1주 운영 후 PR-3 진입 여부 결정 데이터

저장 경로:
  `<log_root>/event_shadow/<YYYYMMDD>.jsonl`

각 record (JSON line):
  {
    "recorded_at": <epoch_seconds>,
    "strategy": "<name>",
    "code": "<code>",
    "signal_source": "event_shadow",
    "signal": {<TradeSignal.model_dump 결과>},
    "snapshot": {<router 로 전달된 snapshot dict>}
  }
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class EventShadowJournalService:
    SIGNAL_SOURCE = "event_shadow"

    def __init__(
        self,
        log_root: Path | str = "logs/strategies",
        logger: Optional[logging.Logger] = None,
    ):
        self._log_dir = Path(log_root) / "event_shadow"
        self._logger = logger or logging.getLogger(__name__)
        self._buffer: List[Dict] = []

    def record(
        self,
        *,
        strategy_name: str,
        code: str,
        signal: Dict,
        snapshot: Dict,
        signal_source: Optional[str] = None,
    ) -> None:
        if not strategy_name or not code:
            return
        self._buffer.append({
            "recorded_at": time.time(),
            "strategy": strategy_name,
            "code": code,
            # signal_source 미지정 시 기존 entry shadow 기본값 유지. exit shadow 는
            # "event_shadow_exit" 를 전달해 동일 jsonl 내에서 구분한다 (P2 2-4 exit).
            "signal_source": signal_source or self.SIGNAL_SOURCE,
            "signal": signal or {},
            "snapshot": snapshot or {},
        })

    def get_records(self) -> List[Dict]:
        return list(self._buffer)

    def record_status(
        self,
        *,
        strategy_name: str,
        event: str,
        details: Optional[Dict[str, Any]] = None,
        signal_source: str = "event_shadow_status",
    ) -> None:
        if not strategy_name or not event:
            return
        self._buffer.append({
            "recorded_at": time.time(),
            "strategy": strategy_name,
            "signal_source": signal_source,
            "event": event,
            "details": details or {},
        })

    def flush_to_file(self, date_str: str) -> Optional[Path]:
        if not self._buffer:
            return None
        # flush 가 worker thread 로 오프로드될 수 있으므로(이벤트 루프의 record 와 경합),
        # 버퍼를 통째로 교체한 뒤 쓰고, 실패 시 되돌려 유실을 막는다.
        records, self._buffer = self._buffer, []
        path = self._log_dir / f"{date_str}.jsonl"
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec, ensure_ascii=False))
                    f.write("\n")
            return path
        except OSError as e:
            self._buffer = records + self._buffer
            self._logger.warning(f"[EventShadowJournal] flush 실패: {e}")
            return None
