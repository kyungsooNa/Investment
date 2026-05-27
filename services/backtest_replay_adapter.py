"""Replay adapters for period backtests."""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Optional, Sequence, Tuple

from common.market_snapshot import ConclusionSnapshot, MarketSnapshot
from common.types import ErrorCode, ResCommonResponse, TradeSignal
from services.backtest_execution_simulator import BacktestBar
from services.data_quality_service import DataQualityService


class StockQueryBacktestReplayService:
    """StockQueryService proxy that replays date-scoped market snapshots.

    Active live strategies call StockQueryService methods directly. During a
    period backtest this proxy prevents those calls from leaking into today's
    live snapshot by synthesizing the minimum response shape from historical
    intraday rows.
    """

    def __init__(
        self,
        stock_query_service: Any,
        *,
        program_provider: Any | None = None,
        market_clock: Any | None = None,
        session: str = "REGULAR",
    ) -> None:
        self._stock_query_service = stock_query_service
        self._program_provider = program_provider
        self._market_clock = market_clock
        self._session = session
        self._backtest_date: str | None = None
        self._row_cache: dict[tuple[str, str, str, str], list[dict]] = {}
        self._program_cache: dict[tuple[str, str], dict] = {}

    def set_backtest_date(self, date_ymd: str) -> None:
        self._backtest_date = str(date_ymd)

    async def get_current_price(self, stock_code: str, *args, **kwargs) -> ResCommonResponse:
        date_ymd = self._require_date()
        rows = await self._get_intraday_rows(stock_code, date_ymd)
        if not rows:
            return ResCommonResponse(
                rt_cd=ErrorCode.EMPTY_VALUES.value,
                msg1=f"intraday rows not found: {stock_code} {date_ymd}",
                data={"output": {}},
            )

        output = await self._build_current_price_output(stock_code, date_ymd, rows)
        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="backtest replay current price",
            data={"output": output},
        )

    async def get_stock_conclusion(self, stock_code: str) -> ResCommonResponse:
        date_ymd = self._require_date()
        rows = await self._get_intraday_rows(stock_code, date_ymd)
        latest = rows[-1] if rows else {}
        strength = self._first(
            latest,
            "tday_rltv",
            "execution_strength",
            "cnqn",
            "체결강도",
        )
        if strength is None:
            return ResCommonResponse(
                rt_cd=ErrorCode.EMPTY_VALUES.value,
                msg1=f"execution strength not found: {stock_code} {date_ymd}",
                data={"output": []},
            )
        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="backtest replay conclusion",
            data={"output": [{"tday_rltv": str(strength)}]},
        )

    async def get_recent_daily_ohlcv(
        self,
        stock_code: str,
        limit: int = 60,
        end_date: str | None = None,
    ) -> ResCommonResponse:
        replay_end_date = end_date or self._backtest_date
        return await self._stock_query_service.get_recent_daily_ohlcv(
            stock_code,
            limit=limit,
            end_date=replay_end_date,
        )

    async def get_day_intraday_minutes_list(self, stock_code: str, **kwargs) -> list[dict]:
        if "date_ymd" not in kwargs or kwargs.get("date_ymd") is None:
            kwargs["date_ymd"] = self._backtest_date
        if "session" not in kwargs:
            kwargs["session"] = self._session
        rows = await self._stock_query_service.get_day_intraday_minutes_list(stock_code, **kwargs)
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            return []
        date_ymd = str(kwargs.get("date_ymd") or self._backtest_date or "")
        return self._normalize_and_cutoff_rows(
            [dict(row) for row in rows if isinstance(row, dict)],
            date_ymd=date_ymd,
        )

    def get_market_snapshot(
        self,
        code: str,
        max_age_sec: Optional[float] = None,
        force_fresh: bool = False,
    ) -> Tuple[Optional[MarketSnapshot], Optional[str]]:
        """Replay row cache → MarketSnapshot (sync, cache-only).

        live PriceStreamService 캐시 대신 이미 조회된 row_cache 에서 빌드.
        캐시 미스 시 REASON_SNAPSHOT_MISSING 반환.
        """
        if force_fresh or not self._backtest_date:
            return None, DataQualityService.REASON_SNAPSHOT_MISSING

        key = (code, self._backtest_date, self._session, self._cutoff_hhmmss())
        rows = self._row_cache.get(key)
        if not rows:
            return None, DataQualityService.REASON_SNAPSHOT_MISSING

        latest = rows[-1]
        closes = [value for value in (self._to_int(self._first(row, "stck_prpr", "prpr", "close", "price")) for row in rows) if value is not None]
        latest_close = closes[-1] if closes else 0
        highs = [value for value in (self._to_int(self._first(row, "stck_hgpr", "hgpr", "high")) for row in rows) if value is not None]
        lows = [value for value in (self._to_int(self._first(row, "stck_lwpr", "lwpr", "low")) for row in rows) if value is not None]
        volume = self._to_int(self._first(latest, "acml_vol")) or sum(
            self._to_int(self._first(row, "cntg_vol", "volume")) or 0 for row in rows
        )
        trade_value = self._to_int(self._first(latest, "acml_tr_pbmn")) or 0

        snap = MarketSnapshot(
            code=code,
            price=float(latest_close),
            change=0.0,
            rate=0.0,
            sign="3",
            acml_vol=volume or 0,
            acml_tr_pbmn=trade_value or 0,
            high=float(max(highs)) if highs else None,
            low=float(min(lows)) if lows else None,
            open=None,
            received_at=time.time(),
            latency_sec=0.0,
            quality_status="ok",
            quality_reason="backtest_replay",
            source="backtest_replay",
        )
        return snap, None

    async def get_conclusion_snapshot(
        self,
        code: str,
        max_age_sec: float = 10.0,
        force_fresh: bool = False,
    ) -> Tuple[Optional[ConclusionSnapshot], Optional[str]]:
        """Replay row cache → ConclusionSnapshot (async, uses get_stock_conclusion)."""
        if not self._backtest_date:
            return None, DataQualityService.REASON_CONCLUSION_MISSING

        key = (code, self._backtest_date, self._session, self._cutoff_hhmmss())
        rows = self._row_cache.get(key)
        if rows:
            latest = rows[-1]
            strength_raw = self._first(latest, "tday_rltv", "execution_strength", "cnqn", "체결강도")
            if strength_raw is not None:
                try:
                    strength = float(strength_raw)
                except (ValueError, TypeError):
                    strength = 0.0
                return ConclusionSnapshot(
                    code=code,
                    execution_strength_pct=strength,
                    received_at=time.time(),
                    source="backtest_replay",
                ), None

        resp = await self.get_stock_conclusion(code)
        if resp is None or resp.rt_cd != ErrorCode.SUCCESS.value:
            return None, DataQualityService.REASON_CONCLUSION_MISSING

        try:
            output = (resp.data or {}).get("output")
            if isinstance(output, list) and output:
                output = output[0]
            if isinstance(output, dict):
                strength_raw = output.get("tday_rltv") or "0"
            else:
                strength_raw = "0"
            strength = float(strength_raw)
        except (ValueError, TypeError, AttributeError):
            strength = 0.0

        return ConclusionSnapshot(
            code=code,
            execution_strength_pct=strength,
            received_at=time.time(),
            source="backtest_replay",
        ), None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stock_query_service, name)

    async def _build_current_price_output(
        self,
        stock_code: str,
        date_ymd: str,
        rows: list[dict],
    ) -> dict:
        latest = rows[-1]
        closes = [value for value in (self._to_int(self._first(row, "stck_prpr", "prpr", "close", "price")) for row in rows) if value is not None]
        latest_close = closes[-1] if closes else 0
        first_open = self._first_valid_int(rows, "stck_oprc", "oprc", "open") or latest_close
        highs = [
            value for value in (
                self._to_int(self._first(row, "stck_hgpr", "hgpr", "high")) or
                self._to_int(self._first(row, "stck_prpr", "prpr", "close", "price"))
                for row in rows
            )
            if value is not None
        ]
        lows = [
            value for value in (
                self._to_int(self._first(row, "stck_lwpr", "lwpr", "low")) or
                self._to_int(self._first(row, "stck_prpr", "prpr", "close", "price"))
                for row in rows
            )
            if value is not None
        ]
        volume = self._to_int(self._first(latest, "acml_vol")) or sum(
            self._to_int(self._first(row, "cntg_vol", "volume")) or 0 for row in rows
        )
        trade_value = self._to_int(self._first(latest, "acml_tr_pbmn")) or sum(
            (self._to_int(self._first(row, "cntg_vol", "volume")) or 0)
            * (self._to_int(self._first(row, "stck_prpr", "prpr", "close", "price")) or 0)
            for row in rows
        )
        prev_close = self._to_int(
            self._first(latest, "stck_sdpr", "sdpr", "prev_close", "prdy_clpr")
        ) or latest_close
        diff = latest_close - prev_close
        program_qty = (
            self._to_int(self._first(latest, "pgtr_ntby_qty", "program_net_buy_qty"))
            or await self._get_program_net_buy_qty(stock_code, date_ymd)
            or 0
        )

        return {
            "stck_prpr": str(latest_close),
            "stck_oprc": str(first_open),
            "stck_hgpr": str(max(highs) if highs else latest_close),
            "stck_lwpr": str(min(lows) if lows else latest_close),
            "stck_sdpr": str(prev_close),
            "prdy_vrss": str(abs(diff)),
            "prdy_vrss_sign": "2" if diff > 0 else ("5" if diff < 0 else "3"),
            "prdy_ctrt": str(round((diff / prev_close * 100), 2)) if prev_close else "0.0",
            "acml_vol": str(volume),
            "acml_tr_pbmn": str(trade_value),
            "pgtr_ntby_qty": str(program_qty),
            "stck_shrn_iscd": stock_code,
        }

    async def _get_intraday_rows(self, stock_code: str, date_ymd: str) -> list[dict]:
        key = (stock_code, date_ymd, self._session, self._cutoff_hhmmss())
        if key in self._row_cache:
            return self._row_cache[key]

        rows = await self._stock_query_service.get_day_intraday_minutes_list(
            stock_code,
            date_ymd=date_ymd,
            session=self._session,
        )
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            rows = []
        normalized = self._normalize_and_cutoff_rows(
            [dict(row) for row in rows if isinstance(row, dict)],
            date_ymd=date_ymd,
        )
        normalized.sort(key=lambda row: str(self._first(row, "stck_bsop_date", "date") or date_ymd) + str(self._first(row, "stck_cntg_hour", "time") or ""))
        self._row_cache[key] = normalized
        return normalized

    def _normalize_and_cutoff_rows(self, rows: list[dict], *, date_ymd: str) -> list[dict]:
        cutoff = self._cutoff_hhmmss()
        if not cutoff:
            return rows
        result: list[dict] = []
        for row in rows:
            row_time = str(self._first(row, "stck_cntg_hour", "cntg_hour", "time") or "").zfill(6)
            if not row_time or row_time > cutoff:
                continue
            result.append(row)
        return result

    def _cutoff_hhmmss(self) -> str:
        if self._market_clock is None:
            return ""
        try:
            return self._market_clock.get_current_kst_time().strftime("%H%M%S")
        except Exception:
            return ""

    async def _get_program_net_buy_qty(self, stock_code: str, date_ymd: str) -> int | None:
        if self._program_provider is None:
            return None
        key = (stock_code, date_ymd)
        if key not in self._program_cache:
            getter = getattr(self._program_provider, "get_program_trade_by_stock_daily", None)
            if not callable(getter):
                return None
            response = await getter(stock_code, date_ymd)
            if isinstance(response, ResCommonResponse):
                data = response.data if response.rt_cd == ErrorCode.SUCCESS.value else None
            else:
                data = response
            self._program_cache[key] = data if isinstance(data, dict) else {}

        row = self._program_cache[key]
        return self._to_int(self._first(
            row,
            "whol_smtn_ntby_qty",
            "pgtr_ntby_qty",
            "program_net_buy_qty",
            "net_buy_qty",
        ))

    def _require_date(self) -> str:
        if not self._backtest_date:
            raise ValueError("backtest date is not set")
        return self._backtest_date

    @staticmethod
    def _first(row: dict, *keys: str):
        for key in keys:
            value = row.get(key)
            if value not in (None, "", "-"):
                return value
        return None

    @classmethod
    def _first_valid_int(cls, rows: list[dict], *keys: str) -> int | None:
        for row in rows:
            value = cls._to_int(cls._first(row, *keys))
            if value is not None:
                return value
        return None

    @staticmethod
    def _to_int(value) -> int | None:
        try:
            if value in (None, "", "-"):
                return None
            return int(float(str(value).replace(",", "")))
        except (TypeError, ValueError):
            return None


class StockQueryIntradayReplayBarProvider:
    """Convert StockQueryService intraday minute rows into BacktestBar.

    The provider chooses the first minute bar that can touch a signal's limit
    price. If no bar reaches the price, it returns the last valid bar so the
    execution simulator can produce an unfilled report.
    """

    def __init__(self, stock_query_service: Any, *, session: str = "REGULAR") -> None:
        self._stock_query_service = stock_query_service
        self._session = session
        self._cache: dict[tuple[str, str, str], list[BacktestBar]] = {}
        self._row_cache: dict[tuple[str, str, str], list[dict]] = {}

    def set_backtest_date(self, date_ymd: str) -> None:
        setter = getattr(self._stock_query_service, "set_backtest_date", None)
        if callable(setter):
            setter(date_ymd)

    async def get_bar(
        self,
        *,
        signal: TradeSignal,
        date_ymd: str,
        side: str,
        execution_policy: str = "current_bar",
    ) -> BacktestBar:
        bars = await self._get_bars(signal.code, date_ymd)
        if not bars:
            raise ValueError(f"intraday rows not found: {signal.code} {date_ymd}")
        self._validate_required_data(signal=signal, date_ymd=date_ymd)

        target_price = float(signal.price or 0)
        normalized_side = str(side or signal.action or "").upper()
        for idx, bar in enumerate(bars):
            if self._price_reached(bar, target_price, normalized_side):
                if _policy_value(execution_policy) == "next_bar" and idx + 1 < len(bars):
                    return bars[idx + 1]
                return bar
        return bars[-1]

    async def _get_bars(self, code: str, date_ymd: str) -> list[BacktestBar]:
        key = (code, date_ymd, self._session)
        if key in self._cache:
            return self._cache[key]

        rows = await self._stock_query_service.get_day_intraday_minutes_list(
            code,
            date_ymd=date_ymd,
            session=self._session,
        )
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            rows = []
        rows = [row for row in rows if isinstance(row, dict)]
        self._row_cache[key] = rows

        bars = [
            bar for bar in (self._row_to_bar(row, default_date=date_ymd) for row in rows)
            if bar is not None
        ]
        bars.sort(key=lambda bar: bar.timestamp)
        self._cache[key] = bars
        return bars

    def _validate_required_data(self, *, signal: TradeSignal, date_ymd: str) -> None:
        required = [
            str(key).strip()
            for key in (getattr(signal, "required_data", None) or [])
            if str(key).strip()
        ]
        if not required:
            return
        key = (signal.code, date_ymd, self._session)
        rows = self._row_cache.get(key, [])
        missing = [
            field
            for field in required
            if not any(row.get(field) not in (None, "", "-") for row in rows)
        ]
        if missing:
            raise ValueError(
                f"required replay data missing: code={signal.code} date={date_ymd} "
                f"fields={missing}"
            )

    def _row_to_bar(self, row: Any, *, default_date: str) -> BacktestBar | None:
        if not isinstance(row, dict):
            return None

        close = self._to_float(self._first(row, "stck_prpr", "prpr", "close", "price"))
        if close is None:
            return None

        open_price = self._to_float(self._first(row, "stck_oprc", "oprc", "open")) or close
        high = self._to_float(self._first(row, "stck_hgpr", "hgpr", "high")) or max(open_price, close)
        low = self._to_float(self._first(row, "stck_lwpr", "lwpr", "low")) or min(open_price, close)
        volume = self._to_int(self._first(row, "cntg_vol", "acml_vol", "volume"))
        date = str(self._first(row, "stck_bsop_date", "bsop_date", "date") or default_date)
        time = str(self._first(row, "stck_cntg_hour", "cntg_hour", "time") or "000000").zfill(6)

        return BacktestBar(
            timestamp=f"{date} {time}",
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )

    @staticmethod
    def _price_reached(bar: BacktestBar, target_price: float, side: str) -> bool:
        if target_price <= 0:
            return False
        if side == "BUY":
            return bar.low <= target_price <= bar.high
        if side == "SELL":
            return bar.low <= target_price <= bar.high
        return bar.low <= target_price <= bar.high

    @staticmethod
    def _first(row: dict, *keys: str):
        for key in keys:
            value = row.get(key)
            if value not in (None, "", "-"):
                return value
        return None

    @staticmethod
    def _to_float(value) -> float | None:
        try:
            if value in (None, "", "-"):
                return None
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _to_int(cls, value) -> int | None:
        result = cls._to_float(value)
        return int(result) if result is not None else None


class StockQueryDailyMtmBarProvider:
    """Convert StockQueryService daily OHLCV rows into holding-period MTM bars."""

    def __init__(
        self,
        stock_query_service: Any,
        *,
        lookback_padding_days: int = 10,
    ) -> None:
        self._stock_query_service = stock_query_service
        self._lookback_padding_days = max(int(lookback_padding_days), 0)
        self._cache: dict[tuple[str, str, int], list[BacktestBar]] = {}

    async def get_holding_bars(
        self,
        *,
        code: str,
        start_ymd: str,
        end_ymd: str,
    ) -> list[BacktestBar]:
        if str(start_ymd) >= str(end_ymd):
            return []

        limit = self._lookup_limit(str(start_ymd), str(end_ymd))
        bars = await self._get_daily_bars(code, str(end_ymd), limit)
        return [
            bar for bar in bars
            if str(start_ymd) < _bar_date(bar.timestamp) < str(end_ymd)
        ]

    async def _get_daily_bars(self, code: str, end_ymd: str, limit: int) -> list[BacktestBar]:
        key = (code, end_ymd, limit)
        if key in self._cache:
            return self._cache[key]

        response = await self._stock_query_service.get_recent_daily_ohlcv(
            code,
            limit=limit,
            end_date=end_ymd,
        )
        rows = response.data if isinstance(response, ResCommonResponse) else response
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            rows = []

        bars = [
            bar for bar in (self._row_to_bar(row) for row in rows)
            if bar is not None
        ]
        bars.sort(key=lambda bar: bar.timestamp)
        self._cache[key] = bars
        return bars

    def _lookup_limit(self, start_ymd: str, end_ymd: str) -> int:
        try:
            start = datetime.strptime(start_ymd, "%Y%m%d").date()
            end = datetime.strptime(end_ymd, "%Y%m%d").date()
            return max((end - start).days + 1 + self._lookback_padding_days, 1)
        except ValueError:
            return 60 + self._lookback_padding_days

    def _row_to_bar(self, row: Any) -> BacktestBar | None:
        if not isinstance(row, dict):
            return None

        close = self._to_float(self._first(row, "close", "stck_clpr", "stck_prpr", "prpr"))
        if close is None:
            return None

        date = str(self._first(row, "date", "stck_bsop_date", "bsop_date") or "")
        if not date:
            return None

        open_price = self._to_float(self._first(row, "open", "stck_oprc", "oprc")) or close
        high = self._to_float(self._first(row, "high", "stck_hgpr", "hgpr")) or max(open_price, close)
        low = self._to_float(self._first(row, "low", "stck_lwpr", "lwpr")) or min(open_price, close)
        volume = self._to_int(self._first(row, "volume", "acml_vol", "cntg_vol"))

        return BacktestBar(
            timestamp=date,
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )

    @staticmethod
    def _first(row: dict, *keys: str):
        for key in keys:
            value = row.get(key)
            if value not in (None, "", "-"):
                return value
        return None

    @staticmethod
    def _to_float(value) -> float | None:
        try:
            if value in (None, "", "-"):
                return None
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _to_int(cls, value) -> int | None:
        result = cls._to_float(value)
        return int(result) if result is not None else None


def _policy_value(policy) -> str:
    return str(getattr(policy, "value", policy) or "current_bar")


def _bar_date(timestamp: str) -> str:
    return str(timestamp).split(" ", 1)[0]
