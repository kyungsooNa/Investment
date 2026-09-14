"""당일 거래량 급증 종목을 관찰용 텔레그램 알림으로 전송한다."""
from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from common.types import ErrorCode
from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState

if TYPE_CHECKING:
    from core.market_clock import MarketClock
    from services.market_calendar_service import MarketCalendarService


class IntradayVolumeSurgeAlertTask(SchedulableTask):
    """랭킹 종목 중 예상 일거래량이 평소보다 급증한 종목을 알린다.

    이 태스크는 관찰 알림만 발송하며 매매 신호나 주문을 만들지 않는다.
    """

    CHECK_INTERVAL_SEC = 60
    MIN_TRADING_VALUE = 10_000_000_000
    VOLUME_TIERS = (3, 5, 10)
    MARKET_MINUTES = 390
    MAX_CANDIDATES = 50
    _ETF_NAME_MARKERS = ("ETF", "ETN", "KODEX", "TIGER", "KOSEF", "KBSTAR", "ACE ", "SOL ")

    def __init__(
        self,
        *,
        ranking_task,
        stock_query_service,
        telegram_reporter=None,
        market_calendar_service: Optional["MarketCalendarService"] = None,
        market_clock: Optional["MarketClock"] = None,
        check_interval_sec: Optional[int] = None,
        logger=None,
    ) -> None:
        self._ranking_task = ranking_task
        self._stock_query_service = stock_query_service
        self._telegram_reporter = telegram_reporter
        self._mcs = market_calendar_service
        self._market_clock = market_clock
        self._check_interval_sec = check_interval_sec or self.CHECK_INTERVAL_SEC
        self._logger = logger or logging.getLogger(__name__)
        self._state = TaskState.IDLE
        self._tasks: List[asyncio.Task] = []
        self._baseline_cache: Dict[str, Dict[str, float]] = {}
        self._sent_tiers: Dict[str, int] = {}
        self._trading_date: Optional[str] = None
        self._progress: Dict[str, Any] = {
            "running": False,
            "scanned_count": 0,
            "sent_count": 0,
            "last_alert_at": None,
            "last_error": None,
        }

    @property
    def task_name(self) -> str:
        return "intraday_volume_surge_alert"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    @property
    def state(self) -> TaskState:
        return self._state

    async def start(self) -> None:
        if any(not task.done() for task in self._tasks):
            return
        if self._state == TaskState.STOPPED:
            self._state = TaskState.IDLE
        self._tasks.append(asyncio.create_task(self._loop()))

    async def stop(self) -> None:
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._state = TaskState.STOPPED

    async def suspend(self) -> None:
        if self._state == TaskState.RUNNING:
            self._state = TaskState.SUSPENDED

    async def resume(self) -> None:
        if self._state == TaskState.SUSPENDED:
            self._state = TaskState.IDLE

    def get_progress(self) -> Dict[str, Any]:
        return dict(self._progress)

    async def _loop(self) -> None:
        while True:
            try:
                if self._state != TaskState.SUSPENDED:
                    self._state = TaskState.RUNNING
                    self._progress["running"] = True
                    try:
                        await self._tick()
                    finally:
                        if self._state == TaskState.RUNNING:
                            self._state = TaskState.IDLE
                        self._progress["running"] = False
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._progress["last_error"] = str(exc)
                self._logger.error(f"{self.task_name}: loop error — {exc}", exc_info=True)
            await asyncio.sleep(self._check_interval_sec)

    async def _tick(self) -> None:
        if not await self._is_market_open_now():
            return
        now = self._market_clock.get_current_kst_time()
        trading_date = now.strftime("%Y%m%d")
        if self._trading_date != trading_date:
            self._trading_date = trading_date
            self._baseline_cache.clear()
            self._sent_tiers.clear()

        await self._refresh_rankings()
        candidates = self._collect_candidates()
        self._progress["scanned_count"] = len(candidates)
        alerts: List[Dict[str, Any]] = []
        for stock in candidates[: self.MAX_CANDIDATES]:
            alert = await self._evaluate(stock, now)
            if alert is not None:
                alerts.append(alert)

        if not alerts:
            return
        alerts.sort(key=lambda item: item["projected_volume_ratio"], reverse=True)
        if self._telegram_reporter is not None:
            sent = await self._telegram_reporter.send_intraday_volume_surge_alert(
                alerts, now.strftime("%Y%m%d %H:%M")
            )
            if sent is False:
                self._progress["last_error"] = "telegram_send_failed"
                return
        for alert in alerts:
            self._sent_tiers[alert["code"]] = alert["tier"]
        self._progress["sent_count"] += len(alerts)
        self._progress["last_alert_at"] = now.strftime("%Y%m%d %H:%M")
        self._progress["last_error"] = None

    async def _is_market_open_now(self) -> bool:
        if self._market_clock is None:
            return False
        now = self._market_clock.get_current_kst_time()
        if not self._market_clock.is_market_operating_hours(now):
            return False
        if self._mcs is not None:
            try:
                return bool(await self._mcs.is_business_day(now.strftime("%Y%m%d")))
            except Exception as exc:
                self._logger.warning(f"{self.task_name}: 영업일 확인 실패 — {exc}")
                return False
        return True

    async def _refresh_rankings(self) -> None:
        refresh = getattr(self._ranking_task, "refresh_basic_ranking", None)
        if not callable(refresh):
            return
        result = refresh(notify=False, min_interval_sec=45)
        if inspect.isawaitable(result):
            await result

    def _collect_candidates(self) -> List[Dict[str, Any]]:
        stocks: Dict[str, Dict[str, Any]] = {}
        for category in ("volume", "trading_value", "rise"):
            getter = getattr(self._ranking_task, "get_basic_ranking_cache", None)
            resp = getter(category) if callable(getter) else None
            if not resp or getattr(resp, "rt_cd", None) != ErrorCode.SUCCESS.value:
                continue
            data = getattr(resp, "data", None)
            if isinstance(data, dict):
                data = data.get("output", [])
            for raw in data or []:
                item = raw.to_dict() if hasattr(raw, "to_dict") else dict(raw or {})
                code = str(item.get("stck_shrn_iscd") or item.get("mksc_shrn_iscd") or item.get("code") or "")
                if not code:
                    continue
                merged = stocks.setdefault(code, {})
                merged.update({key: value for key, value in item.items() if value not in (None, "")})
                merged["stck_shrn_iscd"] = code
        return list(stocks.values())

    async def _evaluate(self, stock: Dict[str, Any], now: datetime) -> Optional[Dict[str, Any]]:
        code = stock["stck_shrn_iscd"]
        name = str(stock.get("hts_kor_isnm") or stock.get("name") or code)
        if self._is_excluded(stock, name):
            return None
        cumulative_volume = self._to_int(stock.get("acml_vol"))
        trading_value = self._to_int(stock.get("acml_tr_pbmn"))
        if cumulative_volume <= 0 or trading_value < self.MIN_TRADING_VALUE:
            return None

        baseline = await self._get_baseline(code, now)
        avg_volume = baseline.get("avg_volume_20", 0)
        if avg_volume <= 0:
            return None
        elapsed_minutes = max(1, (now.hour * 60 + now.minute) - (9 * 60))
        progress = min(1.0, elapsed_minutes / self.MARKET_MINUTES)
        projected_ratio = cumulative_volume / progress / avg_volume
        tier = max((value for value in self.VOLUME_TIERS if projected_ratio >= value), default=0)
        if tier <= self._sent_tiers.get(code, 0):
            return None

        current_price = self._to_int(stock.get("stck_prpr"))
        ma20 = baseline.get("ma20", 0)
        ma50 = baseline.get("ma50", 0)
        trend_filter = "정배열 충족" if current_price > ma20 > ma50 > 0 else "정배열 미충족"
        return {
            "code": code,
            "name": name,
            "price": current_price,
            "change_rate": self._to_float(stock.get("prdy_ctrt")),
            "cumulative_volume": cumulative_volume,
            "avg_volume_20": int(avg_volume),
            "projected_volume_ratio": round(projected_ratio, 2),
            "trading_value": trading_value,
            "tier": tier,
            "trend_filter": trend_filter,
        }

    async def _get_baseline(self, code: str, now: datetime) -> Dict[str, float]:
        if code in self._baseline_cache:
            return self._baseline_cache[code]
        resp = await self._stock_query_service.get_recent_daily_ohlcv(
            code, limit=51, end_date=now.strftime("%Y%m%d")
        )
        rows = getattr(resp, "data", None) if resp and getattr(resp, "rt_cd", None) == ErrorCode.SUCCESS.value else []
        normalized = []
        today = now.strftime("%Y%m%d")
        for row in rows or []:
            date = str(row.get("date") or row.get("stck_bsop_date") or "")
            if date == today:
                continue
            volume = self._to_int(row.get("volume") or row.get("acml_vol"))
            close = self._to_float(row.get("close") or row.get("stck_clpr"))
            if volume > 0 and close > 0:
                normalized.append((date, volume, close))
        if normalized and all(item[0] for item in normalized):
            normalized.sort(key=lambda item: item[0])
        volumes = [item[1] for item in normalized[-20:]]
        closes = [item[2] for item in normalized]
        baseline = {
            "avg_volume_20": sum(volumes) / len(volumes) if volumes else 0,
            "ma20": sum(closes[-20:]) / min(len(closes), 20) if closes else 0,
            "ma50": sum(closes[-50:]) / min(len(closes), 50) if closes else 0,
        }
        self._baseline_cache[code] = baseline
        return baseline

    def _is_excluded(self, stock: Dict[str, Any], name: str) -> bool:
        upper_name = name.upper()
        if any(marker in upper_name for marker in self._ETF_NAME_MARKERS):
            return True
        return any(
            str(stock.get(key) or "").strip() not in ("", "0", "N")
            for key in ("mang_issu_cls_code", "mrkt_warn_cls_code", "ssts_yn")
        )

    @staticmethod
    def _to_int(value: Any) -> int:
        try:
            return int(float(str(value or "0").replace(",", "")))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            return float(str(value or "0").replace(",", ""))
        except (TypeError, ValueError):
            return 0.0
