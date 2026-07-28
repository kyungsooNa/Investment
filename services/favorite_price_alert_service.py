"""관심종목 실시간 등락률 알림 서비스."""
from __future__ import annotations

import logging
import time
from typing import Optional

from services.notification_service import (
    NotificationCategory,
    NotificationLevel,
    NotificationService,
)


class FavoritePriceAlertService:
    """관심종목이 전일대비율 5% 단위 구간을 새로 밟을 때 알림을 발행한다."""

    def __init__(
        self,
        favorite_repository,
        notification_service: Optional[NotificationService],
        stock_code_repository=None,
        *,
        threshold_step_pct: float = 5.0,
        favorite_cache_ttl_sec: float = 30.0,
        logger=None,
    ) -> None:
        self._favorite_repository = favorite_repository
        self._notification_service = notification_service
        self._stock_code_repository = stock_code_repository
        self._threshold_step_pct = float(threshold_step_pct)
        self._favorite_cache_ttl_sec = float(favorite_cache_ttl_sec)
        self._logger = logger or logging.getLogger(__name__)
        self._favorite_codes: set[str] = set()
        self._favorite_cache_ts: float = 0.0
        self._last_alert_bucket: dict[str, int] = {}

    async def add_favorite(self, code: str) -> None:
        normalized = self._normalize_code(code)
        if not normalized:
            return
        await self._refresh_favorites(force=True)
        self._favorite_codes.add(normalized)

    async def remove_favorite(self, code: str) -> None:
        normalized = self._normalize_code(code)
        if not normalized:
            return
        self._favorite_codes.discard(normalized)
        self._last_alert_bucket.pop(normalized, None)

    async def handle_price_tick(self, code: str, *, price, rate) -> bool:
        """실시간 현재가 틱을 평가하고 알림 발행 여부를 반환한다."""
        normalized = self._normalize_code(code)
        if not normalized or self._notification_service is None:
            return False
        if self._threshold_step_pct <= 0:
            return False

        await self._refresh_favorites()
        if normalized not in self._favorite_codes:
            return False

        rate_value = self._to_float(rate)
        if rate_value is None:
            return False

        bucket = self._rate_bucket(rate_value)
        if bucket == 0:
            self._last_alert_bucket[normalized] = 0
            return False
        if self._last_alert_bucket.get(normalized) == bucket:
            return False

        self._last_alert_bucket[normalized] = bucket
        threshold_pct = int(bucket * self._threshold_step_pct)
        name = self._stock_name(normalized)
        direction = "상승" if threshold_pct > 0 else "하락"
        signed_threshold = self._format_signed_pct(threshold_pct)
        signed_rate = self._format_signed_pct(rate_value)
        formatted_price = self._format_price(price)

        await self._notification_service.emit(
            NotificationCategory.SYSTEM,
            NotificationLevel.WARNING,
            f"[관심종목] {name} {signed_threshold} {direction}",
            f"{normalized} {name} 현재 {formatted_price}, 전일대비 {signed_rate}",
            metadata={
                "alert_type": "favorite_price_threshold",
                "code": normalized,
                "name": name,
                "price": self._to_float(price),
                "rate": rate_value,
                "threshold_pct": threshold_pct,
                "dedup_key": f"favorite_price:{normalized}:{threshold_pct}",
                "force_external": True,
            },
        )
        return True

    async def _refresh_favorites(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if (
            not force
            and self._favorite_cache_ts > 0
            and now - self._favorite_cache_ts < self._favorite_cache_ttl_sec
        ):
            return
        try:
            codes = await self._favorite_repository.get_all()
        except Exception as exc:
            self._logger.warning(f"관심종목 알림용 목록 조회 실패: {exc}")
            return
        self._favorite_codes = {
            normalized for code in codes if (normalized := self._normalize_code(code))
        }
        self._favorite_cache_ts = now

    def _rate_bucket(self, rate: float) -> int:
        bucket = int(abs(rate) // self._threshold_step_pct)
        if bucket < 1:
            return 0
        return bucket if rate > 0 else -bucket

    def _stock_name(self, code: str) -> str:
        if self._stock_code_repository is None:
            return code
        try:
            return self._stock_code_repository.get_name_by_code(code) or code
        except Exception:
            return code

    @staticmethod
    def _normalize_code(code) -> str:
        return str(code or "").strip()

    @staticmethod
    def _to_float(value) -> Optional[float]:
        try:
            text = str(value).strip().replace(",", "")
            if not text:
                return None
            return float(text)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _format_price(cls, value) -> str:
        number = cls._to_float(value)
        if number is None:
            return "-"
        if number.is_integer():
            return f"{int(number):,}원"
        return f"{number:,.2f}원"

    @staticmethod
    def _format_signed_pct(value: float | int) -> str:
        number = float(value)
        sign = "+" if number > 0 else ""
        if number.is_integer():
            return f"{sign}{int(number)}%"
        return f"{sign}{number:.2f}%"
