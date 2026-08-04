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
        self._active_index_keys_by_code: dict[str, set[str]] = {}

    async def on_market_status(self, data: dict[str, Any]) -> None:
        """StreamingService handler entrypoint."""
        event_type = self._classify_event(data)
        if event_type is None:
            await self._resolve_for_code(data)
            return

        code = str(data.get("유가증권단축종목코드") or data.get("종목코드") or "UNKNOWN")
        exchange = str(data.get("거래소구분코드") or data.get("EXCH_CLS_CODE") or "UNKNOWN")
        reason = str(data.get("거래정지사유내용") or "").strip()
        direction = self._classify_direction(reason)
        direction_key = f":{direction}" if direction else ""
        dedup_key = f"market_status:{event_type}{direction_key}:{exchange}:{code}"
        severity = "critical" if event_type == "circuit_breaker" else "warning"
        event_name = "서킷브레이커" if event_type == "circuit_breaker" else "사이드카"
        title = f"{self._direction_label(direction)} {event_name} 감지".strip()
        message = f"{exchange} {code}: {reason or '장운영정보 특수 상태 감지'}"
        metadata = {
            "event_type": event_type,
            "stock_code": code,
            "exchange": exchange,
            "reason": reason,
            "direction": direction,
            "market_status": dict(data),
            "telegram_channel": "report",
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

    async def on_index_change(self, index_code: str, index_name: str, change_rate: float) -> None:
        """코스피/코스닥 지수 등락률 기반의 사전경고를 발행한다."""
        direction = "up" if change_rate >= 0 else "down"
        active_keys = self._active_index_keys_by_code.setdefault(index_code, set())
        expected_keys: set[str] = set()

        if abs(change_rate) >= 5.0:
            key = f"market_index:move_5:{direction}:{index_code}"
            expected_keys.add(key)
            await self._report_index_alert(
                key=key, severity="warning",
                title=f"{index_name} {self._direction_label(direction)} 5% 이상 등락",
                index_code=index_code, index_name=index_name, change_rate=change_rate,
                threshold_pct=5.0, event_type="move_5",
            )
        if change_rate <= -8.0:
            key = f"market_index:fall_8:{index_code}"
            expected_keys.add(key)
            await self._report_index_alert(
                key=key, severity="critical",
                title=f"{index_name} 하락 8% 이상 — 서킷브레이커 경고",
                index_code=index_code, index_name=index_name, change_rate=change_rate,
                threshold_pct=8.0, event_type="fall_8",
            )

        if self._operator_alert_service is not None:
            for key in active_keys - expected_keys:
                await self._operator_alert_service.resolve(
                    AlertSource.MARKET_STATUS, key, "지수 등락률 정상화"
                )
        self._active_index_keys_by_code[index_code] = expected_keys

    async def _report_index_alert(
        self, *, key: str, severity: str, title: str, index_code: str,
        index_name: str, change_rate: float, threshold_pct: float, event_type: str,
    ) -> None:
        metadata = {
            "event_type": event_type, "index_code": index_code,
            "index_name": index_name, "change_rate": change_rate,
            "threshold_pct": threshold_pct, "pre_alert": True,
            "telegram_channel": "report",
        }
        message = f"{index_name}({index_code}) 전일 대비 {change_rate:+.2f}%"
        if self._operator_alert_service is not None:
            await self._operator_alert_service.report(
                AlertSource.MARKET_STATUS, key, severity, title, message, metadata=metadata,
            )
            return
        if self._notification_service is not None:
            from services.notification_service import NotificationCategory, NotificationLevel

            level = NotificationLevel.CRITICAL if severity == "critical" else NotificationLevel.WARNING
            await self._notification_service.emit(
                NotificationCategory.SYSTEM, level, title, message, metadata=metadata,
            )

    def _classify_event(self, data: dict[str, Any]) -> Optional[str]:
        reason = str(data.get("거래정지사유내용") or "").lower()
        if any(keyword.lower() in reason for keyword in self._SIDECAR_KEYWORDS):
            return "sidecar"
        # 장운영정보는 거래소에 따라 '사이드카'라는 명칭 대신 프로그램매매
        # 호가의 일시 효력정지 문구만 전달할 수 있다.
        if "프로그램" in reason and "호가" in reason and ("정지" in reason or "효력정지" in reason):
            return "sidecar"
        if any(keyword.lower() in reason for keyword in self._CIRCUIT_KEYWORDS):
            return "circuit_breaker"
        return None

    @staticmethod
    def _classify_direction(reason: str) -> Optional[str]:
        normalized = reason.lower()
        if "매수" in reason or "buy" in normalized:
            return "buy"
        if "매도" in reason or "sell" in normalized:
            return "sell"
        return None

    @staticmethod
    def _direction_label(direction: Optional[str]) -> str:
        return {"buy": "매수", "sell": "매도", "up": "상승", "down": "하락"}.get(direction, "")

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
