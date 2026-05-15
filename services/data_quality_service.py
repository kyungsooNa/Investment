"""Common data quality checks for realtime trading inputs."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional, TYPE_CHECKING

from common.operator_alert_types import AlertSource
from common.types import ErrorCode, ResCommonResponse
from config.config_loader import DataQualityConfig

if TYPE_CHECKING:
    from services.operator_alert_service import OperatorAlertService


@dataclass
class DataQualityResult:
    ok: bool
    severity: str = "info"
    reason: str = ""
    code: str = ""
    latency_sec: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class DataQualityService:
    """Centralized sanity, latency, and outlier checks."""

    MAX_VIOLATION_HISTORY = 200

    REASON_REST_FAILED = "rest_failed"
    REASON_REST_INVALID = "rest_invalid"
    REASON_NOT_SUBSCRIBED = "not_subscribed"
    REASON_SUBSCRIBED_NO_TICK = "subscribed_no_tick"
    REASON_STALE_PRICE = "stale_price"
    REASON_INVALID_TICK = "invalid_tick"
    REASON_LATENCY_EXCEEDED = "latency_exceeded"
    REASON_SNAPSHOT_MISSING = "snapshot_missing"
    REASON_SNAPSHOT_STALE = "snapshot_stale"
    REASON_CONCLUSION_MISSING = "conclusion_missing"
    REASON_CONCLUSION_STALE = "conclusion_stale"

    def __init__(
        self,
        config: Optional[DataQualityConfig] = None,
        *,
        price_stream_service=None,
        market_clock=None,
        logger=None,
        operator_alert_service: Optional["OperatorAlertService"] = None,
    ) -> None:
        self._cfg = config or DataQualityConfig()
        self._price_stream_service = price_stream_service
        self._market_clock = market_clock
        self._logger = logger
        self._operator_alert_service = operator_alert_service
        self._last_good_price: dict[str, float] = {}
        self._last_result: DataQualityResult = DataQualityResult(ok=True, reason="not_checked")
        self._profile = "base"
        self._violation_history: list[dict[str, Any]] = []
        self._last_operator_alert_ts: dict[str, float] = {}

    @property
    def config(self) -> DataQualityConfig:
        return self._cfg

    def set_price_stream_service(self, svc) -> None:
        self._price_stream_service = svc

    def apply_trading_mode(self, is_paper_trading: bool) -> None:
        """Activate calibrated thresholds for the current broker mode."""
        profile = "paper" if is_paper_trading else "real"
        mappings = {
            "max_tick_age_sec": f"{profile}_max_tick_age_sec",
            "max_rest_age_sec": f"{profile}_max_rest_age_sec",
            "max_price_jump_pct": f"{profile}_max_price_jump_pct",
        }
        applied = {}
        for target, source in mappings.items():
            value = getattr(self._cfg, source, None)
            if value is not None:
                setattr(self._cfg, target, value)
                applied[target] = value
        self._profile = profile
        if self._logger:
            self._logger.info(f"DataQuality 프로파일 적용: {profile} {applied}")

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        try:
            if value is None or value == "" or value == "N/A":
                return None
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        parsed = DataQualityService._to_float(value)
        return int(parsed) if parsed is not None else None

    def _result(self, ok: bool, reason: str = "", severity: str = "warning", **kwargs) -> DataQualityResult:
        result = DataQualityResult(ok=ok, reason=reason, severity=severity, **kwargs)
        self._last_result = result
        if not ok:
            timestamp = time.time()
            self._violation_history.append({
                "timestamp": timestamp,
                **result.to_dict(),
            })
            if len(self._violation_history) > self.MAX_VIOLATION_HISTORY:
                self._violation_history = self._violation_history[-self.MAX_VIOLATION_HISTORY:]
            self._maybe_report_operator_alert(result, timestamp)
        return result

    def _maybe_report_operator_alert(self, result: DataQualityResult, timestamp: float) -> None:
        if self._operator_alert_service is None:
            return
        threshold = int(getattr(self._cfg, "violation_alert_threshold", 0) or 0)
        if threshold <= 0:
            return
        window_sec = float(getattr(self._cfg, "violation_alert_window_sec", 60.0) or 60.0)
        cooldown_sec = float(getattr(self._cfg, "alert_cooldown_sec", 60.0) or 60.0)
        reason = result.reason or "unknown"
        alert_key = f"data_quality:{reason}"
        last_alert_ts = self._last_operator_alert_ts.get(alert_key)
        if last_alert_ts is not None and timestamp - last_alert_ts < cooldown_sec:
            return

        window_start = timestamp - window_sec
        recent = [
            item for item in self._violation_history
            if item.get("reason") == reason and float(item.get("timestamp", 0.0)) >= window_start
        ]
        if len(recent) < threshold:
            return

        self._last_operator_alert_ts[alert_key] = timestamp
        sample_codes = []
        for item in recent[-5:]:
            code = item.get("code")
            if code and code not in sample_codes:
                sample_codes.append(code)
        metadata = {
            "alert_type": "data_quality_violation_threshold",
            "reason": reason,
            "severity": result.severity,
            "violation_count": len(recent),
            "window_sec": window_sec,
            "sample_codes": sample_codes,
            "latest": result.to_dict(),
        }
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        task = loop.create_task(
            self._operator_alert_service.report(
                AlertSource.DATA_QUALITY,
                alert_key,
                result.severity or "error",
                "데이터 품질 위반 증가",
                f"{reason}: 최근 {int(window_sec)}초 {len(recent)}건",
                metadata=metadata,
            )
        )

        def _log_failure(done: asyncio.Task) -> None:
            try:
                exc = done.exception()
            except asyncio.CancelledError:
                return
            if exc and self._logger:
                self._logger.warning(f"DataQuality 운영자 알림 전송 실패: {exc}")

        task.add_done_callback(_log_failure)

    def get_violation_history(
        self,
        *,
        count: int = 50,
        code: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        items = self._violation_history
        if code:
            items = [item for item in items if item.get("code") == code]
        if reason:
            items = [item for item in items if item.get("reason") == reason]
        return list(reversed(items[-count:]))

    def validate_api_response(
        self,
        response: Optional[ResCommonResponse],
        *,
        code: str = "",
        require_output: bool = False,
        required_data_keys: Optional[list[str]] = None,
    ) -> DataQualityResult:
        if not self._cfg.enabled:
            return self._result(True, "disabled", "info", code=code)
        if response is None or response.rt_cd != ErrorCode.SUCCESS.value:
            return self._result(
                False,
                self.REASON_REST_FAILED,
                "error",
                code=code,
                metadata={"rt_cd": getattr(response, "rt_cd", None), "msg1": getattr(response, "msg1", None)},
            )
        data = getattr(response, "data", None)
        if data is None:
            return self._result(False, self.REASON_REST_INVALID, "error", code=code)
        if require_output:
            output = data.get("output") if isinstance(data, dict) else getattr(data, "output", None)
            if not output:
                return self._result(False, self.REASON_REST_INVALID, "error", code=code)
        if required_data_keys:
            if not isinstance(data, dict):
                return self._result(
                    False,
                    self.REASON_REST_INVALID,
                    "error",
                    code=code,
                    metadata={"required_data_keys": required_data_keys},
                )
            missing = [key for key in required_data_keys if not data.get(key)]
            if missing:
                return self._result(
                    False,
                    self.REASON_REST_INVALID,
                    "error",
                    code=code,
                    metadata={"missing_keys": missing, "required_data_keys": required_data_keys},
                )
        return self._result(True, "ok", "info", code=code)

    def validate_price_tick(self, realtime_data: dict, *, received_at: Optional[float] = None) -> DataQualityResult:
        if not self._cfg.enabled:
            return self._result(True, "disabled", "info")

        received_at = received_at or time.time()
        code = str(realtime_data.get("유가증권단축종목코드") or realtime_data.get("code") or "").strip()
        price = self._to_float(realtime_data.get("주식현재가") or realtime_data.get("price"))
        volume = self._to_int(realtime_data.get("누적거래량") or realtime_data.get("volume") or 0)
        high = self._to_float(realtime_data.get("주식최고가") or realtime_data.get("high"))
        low = self._to_float(realtime_data.get("주식최저가") or realtime_data.get("low"))
        source_ts = self._to_float(realtime_data.get("source_ts") or realtime_data.get("received_at"))
        latency = max(received_at - source_ts, 0.0) if source_ts else 0.0

        if not code or price is None or price <= 0 or volume is None or volume < 0:
            return self._result(False, self.REASON_INVALID_TICK, "error", code=code, latency_sec=latency)
        if high is not None and low is not None and high > 0 and low > 0 and (price > high or price < low):
            return self._result(
                False,
                self.REASON_INVALID_TICK,
                "error",
                code=code,
                latency_sec=latency,
                metadata={"price": price, "high": high, "low": low},
            )
        if latency > self._cfg.max_tick_age_sec:
            return self._result(
                False,
                self.REASON_LATENCY_EXCEEDED,
                "error",
                code=code,
                latency_sec=latency,
                metadata={"max_tick_age_sec": self._cfg.max_tick_age_sec},
            )

        previous = self._last_good_price.get(code)
        if previous and previous > 0:
            jump_pct = abs(price - previous) / previous * 100
            if jump_pct > self._cfg.max_price_jump_pct:
                return self._result(
                    False,
                    self.REASON_INVALID_TICK,
                    "error",
                    code=code,
                    latency_sec=latency,
                    metadata={"previous_price": previous, "price": price, "jump_pct": round(jump_pct, 3)},
                )

        self._last_good_price[code] = price
        return self._result(True, "ok", "info", code=code, latency_sec=latency)

    def validate_execution_report(self, data: dict) -> DataQualityResult:
        if not self._cfg.enabled:
            return self._result(True, "disabled", "info")
        fields = {
            "order_no": data.get("주문번호") or data.get("ODER_NO") or data.get("odno"),
            "code": data.get("주식단축종목코드") or data.get("STCK_SHRN_ISCD") or data.get("pdno"),
            "side": data.get("매도매수구분") or data.get("SELN_BYOV_CLS") or data.get("sll_buy_dvsn_cd"),
            "qty": data.get("체결수량") or data.get("CNTG_QTY") or data.get("tot_ccld_qty"),
            "price": data.get("체결단가") or data.get("CNTG_UNPR") or data.get("avg_prvs"),
            "time": data.get("주식체결시간") or data.get("STCK_CNTG_HOUR") or data.get("ord_tmd"),
        }
        missing = [name for name, value in fields.items() if value in (None, "")]
        qty = self._to_int(fields["qty"])
        price = self._to_int(fields["price"])
        if missing or qty is None or qty < 0 or price is None or price < 0:
            return self._result(False, "invalid_execution_report", "error", metadata={"missing": missing})
        return self._result(True, "ok", "info", code=str(fields["code"]))

    async def validate_order_reference(self, *, stock_code: str, price: int, qty: int) -> DataQualityResult:
        if not self._cfg.enabled:
            return self._result(True, "disabled", "info", code=stock_code)
        if qty <= 0 or price < 0:
            return self._result(False, self.REASON_INVALID_TICK, "error", code=stock_code)

        cached = None
        if self._price_stream_service is not None:
            try:
                cached = self._price_stream_service.get_cached_price(stock_code)
            except Exception as exc:
                if self._logger:
                    self._logger.warning(f"DataQuality 가격 캐시 조회 실패 ({stock_code}): {exc}")

        if not cached:
            return self._result(True, "no_realtime_reference", "info", code=stock_code)

        received_at = self._to_float(cached.get("received_at"))
        now_ts = time.time()
        latency = max(now_ts - received_at, 0.0) if received_at else None
        reference_price = self._to_float(cached.get("price"))
        if latency is None or latency > self._cfg.max_tick_age_sec:
            ok = not self._cfg.block_on_stale_price
            return self._result(
                ok,
                self.REASON_STALE_PRICE,
                "error" if not ok else "warning",
                code=stock_code,
                latency_sec=latency,
                metadata={
                    "max_tick_age_sec": self._cfg.max_tick_age_sec,
                    "age_sec": latency,
                    "order_price": price,
                    "reference_price": reference_price,
                    "reference_received_at": received_at,
                    "reference_source": cached.get("quality_reason") or "unknown",
                },
            )

        if price > 0 and reference_price and reference_price > 0:
            jump_pct = abs(float(price) - reference_price) / reference_price * 100
            if jump_pct > self._cfg.max_price_jump_pct:
                return self._result(
                    False,
                    self.REASON_INVALID_TICK,
                    "error",
                    code=stock_code,
                    latency_sec=latency,
                    metadata={"order_price": price, "reference_price": reference_price, "jump_pct": round(jump_pct, 3)},
                )
        return self._result(True, "ok", "info", code=stock_code, latency_sec=latency)

    def get_health(self) -> dict:
        return {
            "enabled": self._cfg.enabled,
            "profile": self._profile,
            "last_result": self._last_result.to_dict(),
            "violation_count": len(self._violation_history),
            "max_tick_age_sec": self._cfg.max_tick_age_sec,
            "max_rest_age_sec": self._cfg.max_rest_age_sec,
            "max_price_jump_pct": self._cfg.max_price_jump_pct,
        }
