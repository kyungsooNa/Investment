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
        upper_limit_rate_pct: float = 29.5,
        favorite_cache_ttl_sec: float = 30.0,
        alert_cooldown_sec: float = 300.0,
        logger=None,
    ) -> None:
        self._favorite_repository = favorite_repository
        self._notification_service = notification_service
        self._stock_code_repository = stock_code_repository
        self._threshold_step_pct = float(threshold_step_pct)
        self._upper_limit_rate_pct = float(upper_limit_rate_pct)
        self._favorite_cache_ttl_sec = float(favorite_cache_ttl_sec)
        self._alert_cooldown_sec = float(alert_cooldown_sec)
        self._logger = logger or logging.getLogger(__name__)
        self._favorite_codes: set[str] = set()
        self._favorite_cache_ts: float = 0.0
        self._last_alert_bucket: dict[str, int] = {}
        self._last_alert_ts_by_bucket: dict[tuple[str, int], float] = {}
        self._upper_limit_alerted_codes: set[str] = set()

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
        for key in [key for key in self._last_alert_ts_by_bucket if key[0] == normalized]:
            self._last_alert_ts_by_bucket.pop(key, None)
        self._upper_limit_alerted_codes.discard(normalized)

    async def handle_price_tick(
        self,
        code: str,
        *,
        price,
        rate,
        sign=None,
        is_upper_limit: bool = False,
    ) -> bool:
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
        rate_value = self._apply_kis_sign(rate_value, sign)

        if self._is_upper_limit(rate_value, sign=sign, is_upper_limit=is_upper_limit):
            if normalized in self._upper_limit_alerted_codes:
                return False
            self._upper_limit_alerted_codes.add(normalized)
            return await self._emit_upper_limit_alert(normalized, price, rate_value)
        self._upper_limit_alerted_codes.discard(normalized)

        bucket = self._rate_bucket(rate_value)
        if bucket == 0:
            self._last_alert_bucket[normalized] = 0
            return False
        if self._last_alert_bucket.get(normalized) == bucket:
            return False

        self._last_alert_bucket[normalized] = bucket
        cooldown_key = (normalized, bucket)
        now = time.monotonic()
        last_alert_ts = self._last_alert_ts_by_bucket.get(cooldown_key, 0.0)
        if (
            self._alert_cooldown_sec > 0
            and last_alert_ts > 0
            and now - last_alert_ts < self._alert_cooldown_sec
        ):
            return False
        self._last_alert_ts_by_bucket[cooldown_key] = now

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
                "telegram_channel": "report",
            },
        )
        return True

    async def _emit_upper_limit_alert(self, code: str, price, rate_value: float) -> bool:
        name = self._stock_name(code)
        signed_rate = self._format_signed_pct(rate_value)
        formatted_price = self._format_price(price)

        await self._notification_service.emit(
            NotificationCategory.SYSTEM,
            NotificationLevel.WARNING,
            f"[관심종목] {name} 상한가",
            f"{code} {name} 현재 {formatted_price}, 전일대비 {signed_rate}",
            metadata={
                "alert_type": "favorite_upper_limit",
                "code": code,
                "name": name,
                "price": self._to_float(price),
                "rate": rate_value,
                "threshold_pct": 30,
                "is_upper_limit": True,
                "dedup_key": f"favorite_price:{code}:upper_limit",
                "force_external": True,
                "telegram_channel": "report",
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

    def _is_upper_limit(self, rate: float, *, sign=None, is_upper_limit: bool = False) -> bool:
        if is_upper_limit:
            return True
        if str(sign or "").strip() == "1":
            return True
        return self._upper_limit_rate_pct > 0 and rate >= self._upper_limit_rate_pct

    @staticmethod
    def _apply_kis_sign(rate: float, sign) -> float:
        sign_value = str(sign or "").strip()
        if sign_value in {"4", "5"} and rate > 0:
            return -rate
        if sign_value in {"1", "2"} and rate < 0:
            return abs(rate)
        return rate

    def _stock_name(self, code: str) -> str:
        if self._stock_code_repository is None:
            return code
        try:
            return self._stock_code_repository.get_name_by_code(code) or code
        except Exception:
            return code

    @staticmethod
    def _normalize_code(code) -> str:
        normalized = str(code or "").strip()
        if normalized.isdigit() and len(normalized) <= 6:
            return normalized.zfill(6)
        return normalized

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
