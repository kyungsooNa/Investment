# scheduler/ticket_queue/dlq_manager.py
"""
DlqManager — 영구 실패 티켓(Dead Letter Queue) 관리.

최대 재시도를 초과한 티켓을 data/dlq_log.jsonl에 기록하고
Logger.critical()로 관리자에게 알린다.
추후 TelegramNotifier 연동 지점.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from scheduler.ticket_queue.ticket import Ticket


_DEFAULT_DLQ_LOG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "data", "dlq_log.jsonl",
)


class DlqManager:
    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
        log_path: Optional[str] = None,
    ) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._log_path = log_path or _DEFAULT_DLQ_LOG_PATH

    async def handle_failed_ticket(self, ticket: Ticket, error: str) -> None:
        """영구 실패 티켓을 DLQ에 기록하고 크리티컬 로그를 남긴다."""
        self._logger.critical(
            f"[DLQ] 최대 재시도 초과 — task={ticket.task_name}, "
            f"attempt={ticket.attempt}, error={error}, payload={ticket.payload}"
        )
        self._append_to_log(ticket, error)

    def _append_to_log(self, ticket: Ticket, error: str) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_name": ticket.task_name,
            "priority": ticket.priority,
            "attempt": ticket.attempt,
            "payload": ticket.payload,
            "error": error,
            "created_at": ticket.created_at,
        }
        try:
            os.makedirs(os.path.dirname(self._log_path), exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            self._logger.error(f"[DLQ] 로그 파일 기록 실패: {e}")
