"""관심종목 실시간 등락률 알림 서비스."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from repositories.favorite_repository import MARKET_DOMESTIC, MARKET_OVERSEAS_US
from services.notification_service import (
    NotificationCategory,
    NotificationLevel,
    NotificationService,
)
from utils.strategy_state_io import StrategyStateIO


class FavoritePriceAlertService:
    """관심종목의 당일 등락률 최고 상승·최저 하락 임계치를 알린다."""

    def __init__(
        self,
        favorite_repository,
        notification_service: Optional[NotificationService],
        stock_code_repository=None,
        *,
        market: str = MARKET_DOMESTIC,
        threshold_step_pct: float = 5.0,
        upper_limit_rate_pct: float = 29.5,
        favorite_cache_ttl_sec: float = 30.0,
        state_file: Optional[str] = None,
        today_provider: Optional[Callable[[], str]] = None,
        logger=None,
    ) -> None:
        self._favorite_repository = favorite_repository
        self._notification_service = notification_service
        self._stock_code_repository = stock_code_repository
        self._market = market
        self._threshold_step_pct = float(threshold_step_pct)
        self._upper_limit_rate_pct = float(upper_limit_rate_pct)
        self._favorite_cache_ttl_sec = float(favorite_cache_ttl_sec)
        self._state_file = state_file
        self._today_provider = today_provider or self._current_kst_date
        self._state_date: Optional[str] = None
        self._logger = logger or logging.getLogger(__name__)
        self._favorite_codes: set[str] = set()
        self._favorite_cache_ts: float = 0.0
        self._highest_positive_alert_bucket: dict[str, int] = {}
        self._lowest_negative_alert_bucket: dict[str, int] = {}
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
        await self._load_alert_state_for_today()
        self._favorite_codes.discard(normalized)
        self._highest_positive_alert_bucket.pop(normalized, None)
        self._lowest_negative_alert_bucket.pop(normalized, None)
        self._upper_limit_alerted_codes.discard(normalized)
        await self._save_alert_state()

    async def handle_price_tick(
        self,
        code: str,
        *,
        price,
        rate,
        change=None,
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
        await self._load_alert_state_for_today()

        rate_value = self._to_float(rate)
        if rate_value is None:
            return False
        rate_value = self._resolve_signed_rate(normalized, rate_value, rate, sign, change)
        if rate_value is None:
            return False

        if self._is_upper_limit(rate_value, sign=sign, is_upper_limit=is_upper_limit):
            if normalized in self._upper_limit_alerted_codes:
                return False
            self._upper_limit_alerted_codes.add(normalized)
            await self._save_alert_state()
            return await self._emit_upper_limit_alert(normalized, price, rate_value)
        if normalized in self._upper_limit_alerted_codes:
            self._upper_limit_alerted_codes.discard(normalized)
            await self._save_alert_state()

        bucket = self._rate_bucket(rate_value)
        if bucket == 0:
            return False
        if bucket > 0:
            highest_bucket = self._highest_positive_alert_bucket.get(normalized, 0)
            if bucket <= highest_bucket:
                return False
            self._highest_positive_alert_bucket[normalized] = bucket
        else:
            lowest_bucket = self._lowest_negative_alert_bucket.get(normalized, 0)
            if bucket >= lowest_bucket:
                return False
            self._lowest_negative_alert_bucket[normalized] = bucket
        await self._save_alert_state()

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
                "change": self._to_float(change),
                "rate": rate_value,
                "sign": None if sign is None else str(sign),
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
            codes = await self._favorite_repository.get_all(market=self._market)
        except Exception as exc:
            self._logger.warning(f"관심종목 알림용 목록 조회 실패: {exc}")
            return
        self._favorite_codes = {
            normalized for code in codes if (normalized := self._normalize_code(code))
        }
        self._favorite_cache_ts = now

    async def _load_alert_state_for_today(self) -> None:
        """당일 알림 임계치를 복원한다. 날짜가 바뀌면 이전 상태는 버린다."""
        today = self._today_provider()
        if self._state_date == today:
            return

        self._highest_positive_alert_bucket.clear()
        self._lowest_negative_alert_bucket.clear()
        self._upper_limit_alerted_codes.clear()
        self._state_date = today
        if not self._state_file:
            return

        try:
            data = await StrategyStateIO.load(self._state_file)
        except Exception as exc:
            self._logger.warning(f"관심종목 알림 상태 로드 실패: {exc}")
            return
        if not isinstance(data, dict) or data.get("date") != today:
            return

        for field_name, target in (
            ("highest_positive_alert_bucket", self._highest_positive_alert_bucket),
            ("lowest_negative_alert_bucket", self._lowest_negative_alert_bucket),
            ("last_alert_bucket", None),
        ):
            buckets = data.get(field_name, {})
            if not isinstance(buckets, dict):
                continue
            for code, bucket in buckets.items():
                normalized = self._normalize_code(code)
                try:
                    if normalized:
                        bucket_value = int(bucket)
                        if target is not None:
                            target[normalized] = bucket_value
                        elif bucket_value > 0:
                            self._highest_positive_alert_bucket.setdefault(normalized, bucket_value)
                        elif bucket_value < 0:
                            self._lowest_negative_alert_bucket.setdefault(normalized, bucket_value)
                except (TypeError, ValueError):
                    continue
        codes = data.get("upper_limit_alerted_codes", [])
        if isinstance(codes, list):
            self._upper_limit_alerted_codes = {
                normalized for code in codes if (normalized := self._normalize_code(code))
            }

    async def _save_alert_state(self) -> None:
        if not self._state_file or not self._state_date:
            return
        payload = {
            "date": self._state_date,
            "highest_positive_alert_bucket": self._highest_positive_alert_bucket,
            "lowest_negative_alert_bucket": self._lowest_negative_alert_bucket,
            "upper_limit_alerted_codes": sorted(self._upper_limit_alerted_codes),
        }
        try:
            await StrategyStateIO.save_atomic(self._state_file, payload)
        except Exception as exc:
            self._logger.warning(f"관심종목 알림 상태 저장 실패: {exc}")

    @staticmethod
    def _current_kst_date() -> str:
        return datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")

    def _rate_bucket(self, rate: float) -> int:
        bucket = int(abs(rate) // self._threshold_step_pct)
        if bucket < 1:
            return 0
        return bucket if rate > 0 else -bucket

    def _is_upper_limit(self, rate: float, *, sign=None, is_upper_limit: bool = False) -> bool:
        if self._market == MARKET_OVERSEAS_US:
            return False  # 미국장은 상한가 제도가 없다
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

    def _resolve_signed_rate(self, code: str, rate: float, raw_rate, sign, change=None) -> Optional[float]:
        """KIS의 절대 등락률 + 별도 부호 형식에서 부호 누락 오판을 방지한다."""
        sign_value = str(sign or "").strip()
        if sign_value:
            return self._apply_kis_sign(rate, sign_value)

        raw_text = str(raw_rate or "").strip()
        if raw_text.startswith(("+", "-")):
            return rate

        change_value = self._to_float(change)
        if change_value is not None:
            if change_value < 0 and rate > 0:
                return -rate
            if change_value > 0 and rate < 0:
                return abs(rate)
            if change_value == 0:
                return 0.0

        if (
            self._market == MARKET_DOMESTIC
            and rate > 0
            and self._lowest_negative_alert_bucket.get(code, 0) < 0
        ):
            return -rate
        return rate

    def _stock_name(self, code: str) -> str:
        if self._stock_code_repository is None:
            return code
        try:
            return self._stock_code_repository.get_name_by_code(code) or code
        except Exception:
            return code

    def _normalize_code(self, code) -> str:
        normalized = str(code or "").strip()
        if self._market == MARKET_OVERSEAS_US:
            return normalized.upper()
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

    def _format_price(self, value) -> str:
        number = self._to_float(value)
        if number is None:
            return "-"
        if self._market == MARKET_OVERSEAS_US:
            return f"${number:,.2f}"
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
