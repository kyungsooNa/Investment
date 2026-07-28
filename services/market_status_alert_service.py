"""장운영정보 실시간 이벤트를 운영자 알림으로 변환한다."""
from __future__ import annotations

import logging
from typing import Any, Optional

from common.operator_alert_types import AlertSource


class MarketStatusAlertService:
    """KIS 장운영정보(H0* MKO0)에서 시장 안전장치 발동을 감지한다."""

    _CIRCUIT_KEYWORDS = ("서킷", "circuit", "매매거래중단", "거래중단")
    _SIDECAR_KEYWORDS = ("사이드카", "sidecar")

    def __init__(
        self,
        operator_alert_service=None,
        notification_service=None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._operator_alert_service = operator_alert_service
        self._notification_service = notification_service
        self._logger = logger or logging.getLogger(__name__)
        self._active_keys_by_code: dict[str, set[str]] = {}

    async def on_market_status(self, data: dict[str, Any]) -> None:
        """StreamingService handler entrypoint."""
        event_type = self._classify_event(data)
        if event_type is None:
            await self._resolve_for_code(data)
            return

        code = str(data.get("유가증권단축종목코드") or data.get("종목코드") or "UNKNOWN")
        exchange = str(data.get("거래소구분코드") or data.get("EXCH_CLS_CODE") or "UNKNOWN")
        reason = str(data.get("거래정지사유내용") or "").strip()
        dedup_key = f"market_status:{event_type}:{exchange}:{code}"
        severity = "critical" if event_type == "circuit_breaker" else "warning"
        title = "서킷브레이커 감지" if event_type == "circuit_breaker" else "사이드카 감지"
        message = f"{exchange} {code}: {reason or '장운영정보 특수 상태 감지'}"
        metadata = {
            "event_type": event_type,
            "stock_code": code,
            "exchange": exchange,
            "reason": reason,
            "market_status": dict(data),
        }

        self._active_keys_by_code.setdefault(code, set()).add(dedup_key)
        if self._operator_alert_service is not None:
            await self._operator_alert_service.report(
                AlertSource.MARKET_STATUS,
                dedup_key,
                severity,
                title,
                message,
                metadata=metadata,
            )
            return

        if self._notification_service is not None:
            from services.notification_service import NotificationCategory, NotificationLevel

            level = NotificationLevel.CRITICAL if severity == "critical" else NotificationLevel.WARNING
            await self._notification_service.emit(
                NotificationCategory.SYSTEM,
                level,
                title,
                message,
                metadata=metadata,
            )

    def _classify_event(self, data: dict[str, Any]) -> Optional[str]:
        reason = str(data.get("거래정지사유내용") or "").lower()
        if any(keyword.lower() in reason for keyword in self._SIDECAR_KEYWORDS):
            return "sidecar"
        if any(keyword.lower() in reason for keyword in self._CIRCUIT_KEYWORDS):
            return "circuit_breaker"
        return None

    async def _resolve_for_code(self, data: dict[str, Any]) -> None:
        if self._operator_alert_service is None:
            return
        code = str(data.get("유가증권단축종목코드") or data.get("종목코드") or "")
        if not code:
            return
        active_keys = self._active_keys_by_code.pop(code, set())
        for dedup_key in active_keys:
            await self._operator_alert_service.resolve(
                AlertSource.MARKET_STATUS,
                dedup_key,
                "장운영정보 정상화",
            )
