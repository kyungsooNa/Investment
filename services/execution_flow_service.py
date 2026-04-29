import inspect
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Protocol

from common.types import ErrorCode, Exchange, ResCommonResponse


class ExecutionFlowDataProvider(Protocol):
    async def get_stock_conclusion(self, stock_code: str, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        ...

    async def get_time_concluded_prices(self, stock_code: str, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        ...


@dataclass
class ExecutionFlowSnapshot:
    code: str
    measured_at: datetime
    source: str
    execution_strength_pct: Optional[float] = None
    recent_trade_count: Optional[int] = None
    recent_trade_volume: Optional[int] = None
    recent_trade_value_won: Optional[int] = None
    trade_velocity_per_min: Optional[float] = None
    volume_velocity_per_min: Optional[float] = None
    buy_sell_count_ratio: Optional[float] = None
    buy_sell_volume_ratio: Optional[float] = None
    last_trade_age_sec: Optional[float] = None
    data_age_sec: Optional[float] = None
    sample_window_sec: int = 60
    quality_flags: list[str] = field(default_factory=list)

    def to_policy_context(self) -> dict[str, Any]:
        return {
            "trade_flow_source": self.source,
            "execution_strength_pct": self.execution_strength_pct,
            "recent_trade_count": self.recent_trade_count,
            "recent_trade_volume": self.recent_trade_volume,
            "recent_trade_value_won": self.recent_trade_value_won,
            "trade_velocity_per_min": self.trade_velocity_per_min,
            "volume_velocity_per_min": self.volume_velocity_per_min,
            "buy_sell_count_ratio": self.buy_sell_count_ratio,
            "buy_sell_volume_ratio": self.buy_sell_volume_ratio,
            "last_trade_age_sec": self.last_trade_age_sec,
            "data_age_sec": self.data_age_sec,
            "trade_flow_sample_window_sec": self.sample_window_sec,
            "trade_flow_quality_flags": list(self.quality_flags),
        }


class ExecutionFlowService:
    """Build a normalized recent execution-flow snapshot for order policy checks."""

    def __init__(
        self,
        data_provider: ExecutionFlowDataProvider,
        *,
        market_clock=None,
        logger: Optional[logging.Logger] = None,
        cache_ttl_sec: float = 3.0,
        sample_window_sec: int = 60,
    ):
        self._data_provider = data_provider
        self._market_clock = market_clock
        self._logger = logger or logging.getLogger(__name__)
        self._cache_ttl_sec = max(0.0, float(cache_ttl_sec))
        self._sample_window_sec = max(1, int(sample_window_sec))
        self._cache: dict[tuple[str, str], tuple[float, ExecutionFlowSnapshot]] = {}

    async def get_snapshot(
        self,
        stock_code: str,
        exchange: Exchange = Exchange.KRX,
        *,
        force_refresh: bool = False,
    ) -> ExecutionFlowSnapshot:
        cache_key = (stock_code, exchange.value)
        now_monotonic = time.monotonic()
        cached = self._cache.get(cache_key)
        if not force_refresh and cached and now_monotonic - cached[0] <= self._cache_ttl_sec:
            return cached[1]

        measured_at = self._now()
        conclusion_resp = await self._safe_call("get_stock_conclusion", stock_code, exchange)
        time_resp = await self._safe_call("get_time_concluded_prices", stock_code, exchange)

        quality_flags: list[str] = []
        if not self._is_success(conclusion_resp):
            quality_flags.append("conclusion_unavailable")
        if not self._is_success(time_resp):
            quality_flags.append("time_concluded_unavailable")

        conclusion_row = self._first_row(conclusion_resp.data if self._is_success(conclusion_resp) else None)
        time_rows = self._rows(time_resp.data if self._is_success(time_resp) else None)

        execution_strength = self._to_float(self._pick(
            conclusion_row,
            "tday_rltv",
            "cgld",
            "execution_strength",
            "체결강도",
        ))

        recent_rows = self._recent_rows(time_rows, measured_at)
        recent_trade_count = len(recent_rows) if time_rows else None
        recent_trade_volume = None
        recent_trade_value = None
        if time_rows:
            recent_trade_volume = sum(self._row_volume(row) for row in recent_rows)
            recent_trade_value = sum(self._row_trade_value(row) for row in recent_rows)

        trade_velocity = (
            recent_trade_count / (self._sample_window_sec / 60)
            if recent_trade_count is not None
            else None
        )
        volume_velocity = (
            recent_trade_volume / (self._sample_window_sec / 60)
            if recent_trade_volume is not None
            else None
        )

        last_trade_at = self._latest_trade_time(time_rows, measured_at)
        last_trade_age = None
        if last_trade_at is not None:
            last_trade_age = max(0.0, (measured_at - last_trade_at).total_seconds())
        elif time_rows:
            quality_flags.append("last_trade_time_unavailable")

        data_age = last_trade_age
        buy_sell_count_ratio = self._ratio_from_fields(
            conclusion_row,
            buy_keys=("shnu_cntg_csnu", "buy_conclusion_count", "buy_count"),
            sell_keys=("seln_cntg_csnu", "sell_conclusion_count", "sell_count"),
        )
        buy_sell_volume_ratio = self._ratio_from_fields(
            conclusion_row,
            buy_keys=("shnu_cntg_smtn", "total_buy_qty", "buy_qty"),
            sell_keys=("seln_cntg_smtn", "total_sell_qty", "sell_qty"),
        )

        snapshot = ExecutionFlowSnapshot(
            code=stock_code,
            measured_at=measured_at,
            source="rest",
            execution_strength_pct=execution_strength,
            recent_trade_count=recent_trade_count,
            recent_trade_volume=recent_trade_volume,
            recent_trade_value_won=recent_trade_value,
            trade_velocity_per_min=round(trade_velocity, 3) if trade_velocity is not None else None,
            volume_velocity_per_min=round(volume_velocity, 3) if volume_velocity is not None else None,
            buy_sell_count_ratio=buy_sell_count_ratio,
            buy_sell_volume_ratio=buy_sell_volume_ratio,
            last_trade_age_sec=round(last_trade_age, 3) if last_trade_age is not None else None,
            data_age_sec=round(data_age, 3) if data_age is not None else None,
            sample_window_sec=self._sample_window_sec,
            quality_flags=quality_flags,
        )
        self._cache[cache_key] = (now_monotonic, snapshot)
        return snapshot

    async def _safe_call(self, method_name: str, stock_code: str, exchange: Exchange) -> Optional[ResCommonResponse]:
        method = getattr(self._data_provider, method_name)
        try:
            result = method(stock_code, exchange=exchange)
            if inspect.isawaitable(result):
                result = await result
            return result
        except TypeError:
            try:
                result = method(stock_code)
                if inspect.isawaitable(result):
                    result = await result
                return result
            except Exception as exc:
                self._logger.warning(f"[ExecutionFlow] {method_name} failed code={stock_code} error={exc}")
                return None
        except Exception as exc:
            self._logger.warning(f"[ExecutionFlow] {method_name} failed code={stock_code} error={exc}")
            return None

    def _now(self) -> datetime:
        if self._market_clock and hasattr(self._market_clock, "get_current_kst_time"):
            return self._market_clock.get_current_kst_time()
        return datetime.now()

    @staticmethod
    def _is_success(resp: Optional[ResCommonResponse]) -> bool:
        return bool(resp and getattr(resp, "rt_cd", None) == ErrorCode.SUCCESS.value)

    @classmethod
    def _rows(cls, data: Any) -> list[dict[str, Any]]:
        if hasattr(data, "to_dict"):
            data = data.to_dict()
        if isinstance(data, dict):
            for key in ("output", "output1", "output2"):
                value = data.get(key)
                if isinstance(value, list):
                    return [row for row in value if isinstance(row, dict)]
                if isinstance(value, dict):
                    return [value]
            return [data]
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        return []

    @classmethod
    def _first_row(cls, data: Any) -> dict[str, Any]:
        rows = cls._rows(data)
        return rows[0] if rows else {}

    @staticmethod
    def _pick(data: dict[str, Any], *keys: str):
        for key in keys:
            if key in data:
                return data.get(key)
        return None

    @staticmethod
    def _to_float(value) -> Optional[float]:
        if value is None or value == "":
            return None
        try:
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _to_int(cls, value) -> int:
        parsed = cls._to_float(value)
        return int(parsed) if parsed is not None else 0

    @classmethod
    def _row_volume(cls, row: dict[str, Any]) -> int:
        return cls._to_int(cls._pick(row, "cntg_vol", "acml_vol", "volume", "체결거래량"))

    @classmethod
    def _row_trade_value(cls, row: dict[str, Any]) -> int:
        explicit = cls._to_int(cls._pick(row, "cntg_pbmn", "trade_value", "tr_pbmn", "체결거래대금"))
        if explicit > 0:
            return explicit
        price = cls._to_int(cls._pick(row, "stck_prpr", "cntg_prpr", "price", "체결가"))
        return price * cls._row_volume(row)

    def _recent_rows(self, rows: list[dict[str, Any]], measured_at: datetime) -> list[dict[str, Any]]:
        recent = []
        for row in rows:
            trade_at = self._parse_trade_time(row, measured_at)
            if trade_at is None:
                continue
            age = (measured_at - trade_at).total_seconds()
            if 0 <= age <= self._sample_window_sec:
                recent.append(row)
        if recent:
            return recent
        return rows if rows and all(self._parse_trade_time(row, measured_at) is None for row in rows) else []

    def _latest_trade_time(self, rows: list[dict[str, Any]], measured_at: datetime) -> Optional[datetime]:
        times = [self._parse_trade_time(row, measured_at) for row in rows]
        times = [trade_at for trade_at in times if trade_at is not None]
        return max(times) if times else None

    @classmethod
    def _parse_trade_time(cls, row: dict[str, Any], measured_at: datetime) -> Optional[datetime]:
        raw_time = str(cls._pick(row, "stck_cntg_hour", "cntg_hour", "time", "체결시간") or "").strip()
        if not raw_time:
            return None
        digits = "".join(ch for ch in raw_time if ch.isdigit())
        if len(digits) < 6:
            return None
        hour = int(digits[:2])
        minute = int(digits[2:4])
        second = int(digits[4:6])
        if hour > 23 or minute > 59 or second > 59:
            return None
        return measured_at.replace(hour=hour, minute=minute, second=second, microsecond=0)

    @classmethod
    def _ratio_from_fields(
        cls,
        row: dict[str, Any],
        *,
        buy_keys: tuple[str, ...],
        sell_keys: tuple[str, ...],
    ) -> Optional[float]:
        buy = cls._to_float(cls._pick(row, *buy_keys))
        sell = cls._to_float(cls._pick(row, *sell_keys))
        if buy is None or sell is None or sell <= 0:
            return None
        return round(buy / sell, 3)
