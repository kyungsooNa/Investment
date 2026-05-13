"""운영자 알림 dedup 서비스.

(source, dedup_key) 쌍별로 NEW / ESCALATED / RESOLVED 전이만 emit하여
동일 조건 반복 시 알림 폭주를 방지한다.
상태는 data/operator_alert_state.json에 영속화하여 재시작 후에도 복원된다.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

import pytz

from common.operator_alert_types import (
    AlertSource,
    AlertTransition,
    OperatorAlert,
    severity_is_higher,
)
from services.notification_service import NotificationCategory, NotificationLevel

if TYPE_CHECKING:
    from services.notification_service import NotificationService
    from core.market_clock import MarketClock

KST = pytz.timezone("Asia/Seoul")
_MAX_HISTORY = 200
_DEFAULT_STATE_FILE = "data/operator_alert_state.json"

_LEVEL_MAP: dict[str, NotificationLevel] = {
    "block": NotificationLevel.WARNING,
    "warning": NotificationLevel.WARNING,
    "error": NotificationLevel.ERROR,
    "critical": NotificationLevel.CRITICAL,
}


def _now_iso() -> str:
    return datetime.now(tz=KST).isoformat()


class OperatorAlertService:
    """운영자 알림 dedup 허브.

    사용법:
        svc = OperatorAlertService(notification_service, market_clock)
        await svc.report(AlertSource.KILL_SWITCH, "kill_switch:global",
                         "critical", "Kill Switch 트립", "사유: ...")
        await svc.resolve(AlertSource.KILL_SWITCH, "kill_switch:global", "운영자 해제")
    """

    def __init__(
        self,
        notification_service: "NotificationService",
        market_clock: "MarketClock",
        state_file_path: str = _DEFAULT_STATE_FILE,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._notif = notification_service
        self._market_clock = market_clock
        self._state_path = Path(state_file_path)
        self._logger = logger or logging.getLogger(__name__)

        # 현재 active 차단 셋: dedup_key → active entry dict
        self._active: dict[str, dict[str, Any]] = {}
        # 최근 전이 이력 ring buffer
        self._history: list[dict[str, Any]] = []

        self._load_state()

    # ── 공개 API ─────────────────────────────────────────────────────

    async def report(
        self,
        source: AlertSource,
        dedup_key: str,
        severity: str,
        title: str,
        message: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[AlertTransition]:
        """차단 이벤트 보고. 전이가 발생했을 때만 emit하고 전이 유형을 반환. 중복이면 None."""
        metadata = metadata or {}
        now = _now_iso()

        existing = self._active.get(dedup_key)

        if existing is None:
            # 신규 차단
            transition = AlertTransition.NEW
        elif severity_is_higher(severity, existing["severity"]):
            # 동일 key severity 상승
            transition = AlertTransition.ESCALATED
        else:
            # 동일 severity 반복 — last_seen만 갱신하고 emit 안 함
            existing["last_seen"] = now
            self._save_state()
            return None

        entry = {
            "source": source.value,
            "severity": severity,
            "title": title,
            "message": message,
            "metadata": metadata,
            "first_seen": existing["first_seen"] if existing else now,
            "last_seen": now,
        }
        self._active[dedup_key] = entry
        self._append_history(dedup_key, transition, source, severity, title, message, now)
        self._save_state()

        alert = OperatorAlert(
            source=source,
            dedup_key=dedup_key,
            severity=severity,
            title=title,
            message=message,
            transition=transition,
            timestamp=now,
            metadata=metadata,
        )
        await self._emit(alert)
        return transition

    async def resolve(
        self,
        source: AlertSource,
        dedup_key: str,
        reason: str = "",
    ) -> bool:
        """차단 해제. active에 있었으면 RESOLVED emit 후 True 반환."""
        if dedup_key not in self._active:
            return False

        entry = self._active.pop(dedup_key)
        now = _now_iso()
        self._append_history(
            dedup_key,
            AlertTransition.RESOLVED,
            source,
            entry["severity"],
            f"{entry['title']} 해제",
            reason or "자동 해제",
            now,
        )
        self._save_state()

        alert = OperatorAlert(
            source=source,
            dedup_key=dedup_key,
            severity=entry["severity"],
            title=f"{entry['title']} 해제",
            message=reason or "자동 해제",
            transition=AlertTransition.RESOLVED,
            timestamp=now,
            metadata=entry.get("metadata", {}),
        )
        await self._emit(alert)
        return True

    def get_active_alerts(self) -> list[dict]:
        """현재 active 차단 목록 반환."""
        result = []
        for key, entry in self._active.items():
            result.append({
                "dedup_key": key,
                **entry,
            })
        return result

    def get_history(self, limit: int = 100) -> list[dict]:
        """최근 전이 이력 반환 (최신 순)."""
        return list(reversed(self._history[-limit:]))

    # ── 내부 ─────────────────────────────────────────────────────────

    async def _emit(self, alert: OperatorAlert) -> None:
        """NotificationService 로 이벤트 전파. metadata에 transition/dedup_key/source 주입."""
        level = _LEVEL_MAP.get(alert.severity, NotificationLevel.WARNING)
        meta = {
            **alert.metadata,
            "transition": alert.transition.value,
            "dedup_key": alert.dedup_key,
            "source": alert.source.value,
        }
        await self._notif.emit(
            NotificationCategory.SYSTEM,
            level,
            alert.title,
            alert.message,
            meta,
        )

    def _append_history(
        self,
        dedup_key: str,
        transition: AlertTransition,
        source: AlertSource,
        severity: str,
        title: str,
        message: str,
        timestamp: str,
    ) -> None:
        self._history.append({
            "timestamp": timestamp,
            "transition": transition.value,
            "source": source.value,
            "dedup_key": dedup_key,
            "severity": severity,
            "title": title,
            "message": message,
        })
        if len(self._history) > _MAX_HISTORY:
            self._history = self._history[-_MAX_HISTORY:]

    def _save_state(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "active": self._active,
                "history": self._history,
            }
            self._state_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            self._logger.error("[OperatorAlert] 상태 저장 실패: %s", e)

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._active = state.get("active", {})
            self._history = state.get("history", [])
            if self._active:
                self._logger.info(
                    "[OperatorAlert] 재시작 후 active 차단 %d건 복원", len(self._active)
                )
        except Exception as e:
            self._logger.error("[OperatorAlert] 상태 복원 실패: %s", e)
