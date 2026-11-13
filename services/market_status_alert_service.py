"""장운영정보 실시간 이벤트를 운영자 알림으로 변환한다."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from common.operator_alert_types import AlertSource


class MarketStatusAlertService:
    """KIS 장운영정보(H0* MKO0)에서 시장 안전장치 발동을 감지한다."""

    _MOVE_5_THRESHOLD_PCT = 5.0
    _MOVE_5_RESOLVE_PCT = 4.5
    _MOVE_5_RESOLVE_CONSECUTIVE_SAMPLES = 3
    _BUY_SIDECAR_WATCH_THRESHOLD_PCT = 4.5
    _BUY_SIDECAR_WATCH_RESOLVE_PCT = 4.5
    _FUTURES_SIDECAR_THRESHOLD_PCT = 5.0
    _FUTURES_SIDECAR_DURATION_SEC = 60
    _CIRCUIT_KEYWORDS = ("서킷", "circuit", "매매거래중단", "거래중단")
    _SIDECAR_KEYWORDS = ("사이드카", "sidecar")
    _NORMAL_VI_CODES = {"", "0", "N", "NONE", "NORMAL"}

    def __init__(
        self,
        operator_alert_service=None,
        notification_service=None,
        stock_code_repository=None,
        vi_status_provider: Optional[
            Callable[[str, str], Awaitable[Any]]
        ] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._operator_alert_service = operator_alert_service
        self._notification_service = notification_service
        self._stock_code_repository = stock_code_repository
        self._vi_status_provider = vi_status_provider
        self._logger = logger or logging.getLogger(__name__)
        self._active_keys_by_code: dict[str, set[str]] = {}
        self._active_index_keys_by_code: dict[str, set[str]] = {}
        self._active_futures_keys_by_code: dict[str, set[str]] = {}
        self._index_alert_initial_rate_by_key: dict[str, float] = {}
        self._move_5_recovery_samples_by_code: dict[str, int] = {}
        self._futures_sidecar_started_sec_by_code: dict[str, int] = {}
        self._futures_sidecar_direction_by_code: dict[str, str] = {}
        self._buy_sidecar_watch_reported_keys_by_date: dict[str, set[str]] = {}
        # 이 프로세스에서 지수 샘플을 받은 적 있는 지수. 첫 샘플에서 발행된 경보는
        # 재시작 전에 이미 넘은 임계치일 수 있어 돌파 시각이 아님을 표시한다.
        self._observed_index_codes: set[str] = set()

    async def on_market_status(self, data: dict[str, Any]) -> None:
        """StreamingService handler entrypoint."""
        event_type = self._classify_event(data)
        if event_type is None:
            await self._resolve_for_code(data)
            return

        code = self._normalize_code(data.get("유가증권단축종목코드") or data.get("종목코드")) or "UNKNOWN"
        exchange = str(data.get("거래소구분코드") or data.get("EXCH_CLS_CODE") or "").strip()
        stock_name, exchange = self._stock_identity(code, exchange)
        reason = str(data.get("거래정지사유내용") or "").strip()
        direction = self._classify_direction(reason)
        direction_key = f":{direction}" if direction else ""
        dedup_key = f"market_status:{event_type}{direction_key}:{exchange}:{code}"
        severity = self._severity_for_event(event_type)
        event_name = self._event_name(event_type)
        title = f"{self._direction_label(direction)} {event_name} 감지".strip()
        vi_status = (
            await self._get_vi_status(code)
            if event_type == "stock_vi"
            else None
        )
        message = self._format_event_message(
            event_type, exchange, code, stock_name, reason, data, vi_status
        )
        metadata = {
            "event_type": event_type,
            "stock_code": code,
            "stock_name": stock_name,
            "exchange": exchange,
            "reason": reason,
            "direction": direction,
            "vi_code": str(data.get("VI적용구분코드") or ""),
            "after_hours_vi_code": str(data.get("시간외단일가VI적용구분코드") or ""),
            "vi_status": vi_status,
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

            level = {
                "critical": NotificationLevel.CRITICAL,
                "error": NotificationLevel.ERROR,
            }.get(severity, NotificationLevel.WARNING)
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
        catch_up = index_code not in self._observed_index_codes
        self._observed_index_codes.add(index_code)
        active_keys = self._active_index_keys_by_code.setdefault(index_code, set())
        expected_keys: set[str] = set()
        active_move_5_keys = {
            key for key in active_keys if key.startswith("market_index:move_5:")
        }
        active_buy_sidecar_watch_keys = {
            key for key in active_keys if key.startswith("market_index:buy_sidecar_watch:")
        }

        trading_date = datetime.now().strftime("%Y%m%d")
        self._reset_buy_sidecar_watch_history(trading_date)
        buy_sidecar_watch_key = f"market_index:buy_sidecar_watch:{index_code}:{trading_date}"
        if change_rate >= self._BUY_SIDECAR_WATCH_THRESHOLD_PCT:
            reported_keys = self._buy_sidecar_watch_reported_keys_by_date.setdefault(
                trading_date, set()
            )
            if buy_sidecar_watch_key in active_keys:
                expected_keys.add(buy_sidecar_watch_key)
            elif buy_sidecar_watch_key not in reported_keys:
                expected_keys.add(buy_sidecar_watch_key)
                reported_keys.add(buy_sidecar_watch_key)
                await self._report_index_alert(
                    key=buy_sidecar_watch_key, severity="warning",
                    title=f"{index_name} 매수 사이드카 가능 구간",
                    index_code=index_code, index_name=index_name, change_rate=change_rate,
                    threshold_pct=self._BUY_SIDECAR_WATCH_THRESHOLD_PCT,
                    event_type="buy_sidecar_watch", catch_up=catch_up,
                )
        elif active_buy_sidecar_watch_keys and change_rate > self._BUY_SIDECAR_WATCH_RESOLVE_PCT:
            expected_keys.update(active_buy_sidecar_watch_keys)

        move_5_key = f"market_index:move_5:{direction}:{index_code}"
        if change_rate < 0 and abs(change_rate) >= self._MOVE_5_THRESHOLD_PCT:
            self._move_5_recovery_samples_by_code.pop(index_code, None)
            expected_keys.add(move_5_key)
            await self._report_index_alert(
                key=move_5_key, severity="error",
                title=f"{index_name} {self._direction_label(direction)} 5% 이상 등락",
                index_code=index_code, index_name=index_name, change_rate=change_rate,
                threshold_pct=self._MOVE_5_THRESHOLD_PCT, event_type="move_5", catch_up=catch_up,
            )
        elif active_move_5_keys:
            if abs(change_rate) > self._MOVE_5_RESOLVE_PCT:
                # 경계값 부근의 1분 단위 등락으로 경보/해제가 반복되지 않도록,
                # 발동 후에는 충분히 회복할 때까지 같은 방향의 경보를 유지한다.
                self._move_5_recovery_samples_by_code.pop(index_code, None)
                expected_keys.update(active_move_5_keys)
            else:
                recovery_samples = self._move_5_recovery_samples_by_code.get(index_code, 0) + 1
                if recovery_samples < self._MOVE_5_RESOLVE_CONSECUTIVE_SAMPLES:
                    self._move_5_recovery_samples_by_code[index_code] = recovery_samples
                    expected_keys.update(active_move_5_keys)
                else:
                    self._move_5_recovery_samples_by_code.pop(index_code, None)
        if change_rate <= -8.0:
            key = f"market_index:fall_8:{index_code}"
            expected_keys.add(key)
            await self._report_index_alert(
                key=key, severity="critical",
                title=f"{index_name} 하락 8% 이상 — 서킷브레이커 경고",
                index_code=index_code, index_name=index_name, change_rate=change_rate,
                threshold_pct=8.0, event_type="fall_8", catch_up=catch_up,
            )

        if self._operator_alert_service is not None:
            for key in active_keys - expected_keys:
                initial_rate = self._index_alert_initial_rate_by_key.pop(key, change_rate)
                await self._operator_alert_service.resolve(
                    AlertSource.MARKET_STATUS,
                    key,
                    (
                        "지수 등락률 정상화\n"
                        f"등락률: {initial_rate:+.2f}% → {change_rate:+.2f}%"
                    ),
                )
        self._active_index_keys_by_code[index_code] = expected_keys

    async def on_futures_contract(self, data: dict[str, Any]) -> None:
        """코스피200 지수선물 체결가로 매수·매도 사이드카 조건을 감시한다."""
        code = self._normalize_code(data.get("선물단축종목코드") or data.get("종목코드")) or "UNKNOWN"
        rate = self._signed_rate(
            data,
            rate_keys=("선물전일대비율", "전일대비율"),
            sign_keys=("전일대비부호",),
        )
        tick_sec = self._hhmmss_to_seconds(str(data.get("영업시간") or ""))
        if tick_sec is None:
            now = datetime.now()
            tick_sec = now.hour * 3600 + now.minute * 60 + now.second

        direction = None
        if rate is not None and rate >= self._FUTURES_SIDECAR_THRESHOLD_PCT:
            direction = "buy"
        elif rate is not None and rate <= -self._FUTURES_SIDECAR_THRESHOLD_PCT:
            direction = "sell"

        if direction is None:
            self._futures_sidecar_started_sec_by_code.pop(code, None)
            self._futures_sidecar_direction_by_code.pop(code, None)
            await self._resolve_futures_alerts(code)
            return

        previous_direction = self._futures_sidecar_direction_by_code.get(code)
        if previous_direction is not None and previous_direction != direction:
            self._futures_sidecar_started_sec_by_code[code] = tick_sec
            await self._resolve_futures_alerts(code)
        self._futures_sidecar_direction_by_code[code] = direction
        started_sec = self._futures_sidecar_started_sec_by_code.setdefault(code, tick_sec)
        duration_sec = tick_sec - started_sec
        if duration_sec < 0:
            duration_sec += 24 * 3600
        if duration_sec < self._FUTURES_SIDECAR_DURATION_SEC:
            return

        trading_date = str(data.get("영업일자") or datetime.now().strftime("%Y%m%d"))
        direction_label = "매수" if direction == "buy" else "매도"
        event_type = f"futures_{direction}_sidecar"
        dedup_key = f"market_futures:{direction}_sidecar:{code}:{trading_date}"
        self._active_futures_keys_by_code.setdefault(code, set()).add(dedup_key)
        metadata = {
            "event_type": event_type,
            "direction": direction,
            "futures_code": code,
            "change_rate": rate,
            "duration_sec": duration_sec,
            "threshold_pct": self._FUTURES_SIDECAR_THRESHOLD_PCT,
            "market_status": dict(data),
            "telegram_channel": "report",
        }
        message = (
            f"코스피200 선물({code}) 전일 대비 {rate:+.2f}% "
            f"{duration_sec}초 지속"
        )
        if self._operator_alert_service is not None:
            await self._operator_alert_service.report(
                AlertSource.MARKET_STATUS,
                dedup_key,
                "error",
                f"코스피200 선물 {direction_label} 사이드카 발동 조건",
                message,
                metadata=metadata,
            )
            return
        if self._notification_service is not None:
            from services.notification_service import NotificationCategory, NotificationLevel

            await self._notification_service.emit(
                NotificationCategory.SYSTEM,
                NotificationLevel.ERROR,
                f"코스피200 선물 {direction_label} 사이드카 발동 조건",
                message,
                metadata=metadata,
            )

    async def _report_index_alert(
        self, *, key: str, severity: str, title: str, index_code: str,
        index_name: str, change_rate: float, threshold_pct: float, event_type: str,
        catch_up: bool = False,
    ) -> None:
        self._index_alert_initial_rate_by_key.setdefault(key, change_rate)
        metadata = {
            "event_type": event_type, "index_code": index_code,
            "index_name": index_name, "change_rate": change_rate,
            "threshold_pct": threshold_pct, "pre_alert": True, "catch_up": catch_up,
            "telegram_channel": "report", "force_external": True,
        }
        message = f"{index_name}({index_code}) 전일 대비 {change_rate:+.2f}%"
        if catch_up:
            message += "\n※ 첫 시세 확인 시점에 이미 기준 초과 (실제 돌파 시각 아님)"
        if self._operator_alert_service is not None:
            await self._operator_alert_service.report(
                AlertSource.MARKET_STATUS, key, severity, title, message, metadata=metadata,
            )
            return
        if self._notification_service is not None:
            from services.notification_service import NotificationCategory, NotificationLevel

            level = {
                "critical": NotificationLevel.CRITICAL,
                "error": NotificationLevel.ERROR,
            }.get(severity, NotificationLevel.WARNING)
            await self._notification_service.emit(
                NotificationCategory.SYSTEM, level, title, message, metadata=metadata,
            )

    def _classify_event(self, data: dict[str, Any]) -> Optional[str]:
        if self._is_stock_vi_active(data):
            return "stock_vi"
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

    @classmethod
    def _is_stock_vi_active(cls, data: dict[str, Any]) -> bool:
        vi_code = str(data.get("VI적용구분코드") or "").strip().upper()
        return len(vi_code) == 1 and vi_code not in cls._NORMAL_VI_CODES

    @staticmethod
    def _severity_for_event(event_type: str) -> str:
        if event_type == "circuit_breaker":
            return "critical"
        return "error"

    @staticmethod
    def _event_name(event_type: str) -> str:
        return {
            "circuit_breaker": "서킷브레이커",
            "sidecar": "사이드카",
            "stock_vi": "개별종목 VI 발동",
        }.get(event_type, "장운영정보 특수 상태")

    @staticmethod
    def _format_event_message(
        event_type: str,
        exchange: str,
        code: str,
        stock_name: str,
        reason: str,
        data: dict[str, Any],
        vi_status: Optional[dict[str, Any]] = None,
    ) -> str:
        stock_label = f"{stock_name}({code})" if stock_name else code
        if event_type == "stock_vi":
            vi_code = str(data.get("VI적용구분코드") or "").strip()
            lines = [f"{exchange} {stock_label}"]
            if vi_status:
                static_base = MarketStatusAlertService._positive_number(
                    vi_status.get("vi_stnd_prc")
                )
                dynamic_base = MarketStatusAlertService._positive_number(
                    vi_status.get("vi_dmc_stnd_prc")
                )
                vi_type = (
                    "정적·동적 VI"
                    if static_base and dynamic_base
                    else "정적 VI"
                    if static_base
                    else "동적 VI"
                    if dynamic_base
                    else "VI"
                )
                rate = MarketStatusAlertService._number(vi_status.get("vi_dprt"))
                if not static_base:
                    rate = MarketStatusAlertService._number(
                        vi_status.get("vi_dmc_dprt")
                    )
                direction = "상승" if rate is not None and rate > 0 else "하락" if rate is not None and rate < 0 else ""
                lines.append(f"VI 유형: {direction} {vi_type}".replace("  ", " "))

                trigger_time = MarketStatusAlertService._format_hhmmss(
                    vi_status.get("cntg_vi_hour")
                )
                trigger_price = MarketStatusAlertService._format_won(
                    vi_status.get("vi_prc")
                )
                if trigger_time or trigger_price:
                    details = [value for value in (trigger_time, trigger_price) if value]
                    lines.append(f"발동: {' / 발동가 '.join(details)}")

                base_price = static_base or dynamic_base
                base_text = MarketStatusAlertService._format_won(base_price)
                rate_text = f"{rate:+.2f}%" if rate is not None else ""
                if base_text or rate_text:
                    details = [value for value in (base_text, rate_text) if value]
                    lines.append(f"기준가: {' / 괴리율 '.join(details)}")

                release_time = MarketStatusAlertService._format_hhmmss(
                    vi_status.get("vi_cncl_hour")
                )
                if release_time:
                    lines.append(f"상태: 해제 ({release_time})")
                else:
                    lines.append("상태: 발동 중 (통상 약 2분간 단일가매매)")
                count = MarketStatusAlertService._positive_number(
                    vi_status.get("vi_count")
                )
                if count:
                    lines.append(f"당일 발동: {int(count):,}회")
            else:
                lines.extend([
                    "VI 유형: 상세 조회 불가 (실시간 코드는 적용 여부만 제공)",
                    f"적용 코드: {vi_code or 'UNKNOWN'}",
                    "상태: VI 발동 (통상 약 2분간 단일가매매)",
                ])
            return "\n".join(lines)
        return f"{exchange} {stock_label}: {reason or '장운영정보 특수 상태 감지'}"

    async def _get_vi_status(self, code: str) -> Optional[dict[str, Any]]:
        if self._vi_status_provider is None or code == "UNKNOWN":
            return None
        business_date = datetime.now().strftime("%Y%m%d")
        try:
            response = await self._vi_status_provider(code, business_date)
            if hasattr(response, "data"):
                response = response.data
            if isinstance(response, dict) and "output" in response:
                response = response.get("output")
            rows = response if isinstance(response, list) else [response]
            candidates = [
                row for row in rows
                if isinstance(row, dict)
                and str(row.get("mksc_shrn_iscd") or code).zfill(6) == code
            ]
            if not candidates:
                return None
            return max(
                candidates,
                key=lambda row: str(row.get("cntg_vi_hour") or ""),
            )
        except Exception as exc:
            self._logger.warning("VI 상세 현황 조회 실패(%s): %s", code, exc)
            return None

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        try:
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _positive_number(cls, value: Any) -> Optional[float]:
        number = cls._number(value)
        return number if number is not None and number > 0 else None

    @staticmethod
    def _format_hhmmss(value: Any) -> str:
        digits = str(value or "").strip()
        if len(digits) != 6 or not digits.isdigit():
            return ""
        return f"{digits[:2]}:{digits[2:4]}:{digits[4:]}"

    @classmethod
    def _format_won(cls, value: Any) -> str:
        number = cls._number(value)
        return f"{int(number):,}원" if number is not None else ""

    def _stock_identity(self, code: str, exchange: str) -> tuple[str, str]:
        stock_name = ""
        market = ""
        if self._stock_code_repository is not None:
            try:
                stock_name = str(
                    self._stock_code_repository.get_name_by_code(code) or ""
                ).strip()
                market = str(
                    self._stock_code_repository.get_market_by_code(code) or ""
                ).strip()
            except Exception as exc:
                self._logger.warning("장운영정보 종목 매핑 실패: %s", exc)
        return stock_name, exchange or market or "통합시장"

    @classmethod
    def _signed_rate(
        cls,
        data: dict[str, Any],
        *,
        rate_keys: tuple[str, ...],
        sign_keys: tuple[str, ...],
    ) -> Optional[float]:
        rate_raw = next((data.get(key) for key in rate_keys if data.get(key) not in (None, "")), None)
        try:
            rate = float(str(rate_raw).replace(",", ""))
        except (TypeError, ValueError):
            return None
        sign = str(next((data.get(key) for key in sign_keys if data.get(key)), "")).upper()
        if sign in {"4", "5", "-", "D", "DOWN"}:
            return -abs(rate)
        return rate

    @staticmethod
    def _hhmmss_to_seconds(value: str) -> Optional[int]:
        if len(value) != 6 or not value.isdigit():
            return None
        return int(value[:2]) * 3600 + int(value[2:4]) * 60 + int(value[4:])

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

    @staticmethod
    def _normalize_code(code) -> str:
        """종목코드를 6자리로 맞춘다 (`FavoritePriceAlertService._normalize_code` 와 같은 규칙).

        dedup 키에 코드가 그대로 박히므로, 발동과 해제가 다른 표기로 오면 키가 어긋나
        알림이 해제되지 않고 재발동한다. 선물코드처럼 숫자가 아닌 값은 건드리지 않는다.
        """
        normalized = str(code or "").strip()
        if normalized.isdigit() and len(normalized) <= 6:
            return normalized.zfill(6)
        return normalized

    async def _resolve_for_code(self, data: dict[str, Any]) -> None:
        if self._operator_alert_service is None:
            return
        code = self._normalize_code(data.get("유가증권단축종목코드") or data.get("종목코드"))
        if not code:
            return
        active_keys = self._active_keys_by_code.pop(code, set())
        for dedup_key in active_keys:
            await self._operator_alert_service.resolve(
                AlertSource.MARKET_STATUS,
                dedup_key,
                "장운영정보 정상화",
            )

    async def _resolve_futures_alerts(self, code: str) -> None:
        if self._operator_alert_service is None:
            return
        active_keys = self._active_futures_keys_by_code.pop(code, set())
        for dedup_key in active_keys:
            await self._operator_alert_service.resolve(
                AlertSource.MARKET_STATUS,
                dedup_key,
                "지수선물 등락률 정상화",
            )

    def _reset_buy_sidecar_watch_history(self, trading_date: str) -> None:
        stale_dates = [
            reported_date
            for reported_date in self._buy_sidecar_watch_reported_keys_by_date
            if reported_date != trading_date
        ]
        for reported_date in stale_dates:
            self._buy_sidecar_watch_reported_keys_by_date.pop(reported_date, None)
