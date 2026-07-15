"""장중 주도테마 리포트를 1시간 슬롯마다 발송하는 태스크."""
from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from common.types import ErrorCode
from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState

if TYPE_CHECKING:
    from core.market_clock import MarketClock
    from services.market_calendar_service import MarketCalendarService


class ThemeIntradayLeaderAlertTask(SchedulableTask):
    """기본 랭킹 캐시를 갱신해 장중 주도테마 리포트를 주기적으로 보낸다."""

    CHECK_INTERVAL_SEC = 60
    ALERT_INTERVAL_SEC = 60 * 60
    ALERT_GRACE_SEC = 120
    ALERT_START_HOUR = 9
    ALERT_START_MINUTE = 10

    def __init__(
        self,
        *,
        ranking_task,
        theme_daily_leader_service,
        telegram_reporter=None,
        market_calendar_service: Optional["MarketCalendarService"] = None,
        market_clock: Optional["MarketClock"] = None,
        check_interval_sec: Optional[int] = None,
        alert_interval_sec: Optional[int] = None,
        alert_grace_sec: Optional[int] = None,
        logger=None,
    ) -> None:
        self._ranking_task = ranking_task
        self._theme_daily_leader_service = theme_daily_leader_service
        self._telegram_reporter = telegram_reporter
        self._mcs = market_calendar_service
        self._market_clock = market_clock
        self._check_interval_sec = check_interval_sec or self.CHECK_INTERVAL_SEC
        self._alert_interval_sec = alert_interval_sec or self.ALERT_INTERVAL_SEC
        self._alert_grace_sec = alert_grace_sec or self.ALERT_GRACE_SEC
        self._logger = logger or logging.getLogger(__name__)
        self._state = TaskState.IDLE
        self._tasks: List[asyncio.Task] = []
        self._progress: Dict = {
            "running": False,
            "last_report_slot": None,
            "sent_count": 0,
            "last_error": None,
        }
        self._latest_report: Dict[str, Any] = {"captured_at": None, "data": []}
        self._last_snapshot_minute: Optional[str] = None

    @property
    def task_name(self) -> str:
        return "intraday_theme_leader_alert"

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

    def get_progress(self) -> Dict:
        return dict(self._progress)

    def get_latest_report(self) -> Dict[str, Any]:
        return {
            "captured_at": self._latest_report.get("captured_at"),
            "data": [dict(item) for item in self._latest_report.get("data", [])],
        }

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
        captured_at = now.strftime("%Y%m%d %H:%M")
        if self._last_snapshot_minute != captured_at:
            await self._refresh_latest_report(captured_at)
            self._last_snapshot_minute = captured_at

        slot_label = self._slot_label(now)
        if slot_label is None:
            return
        if self._progress["last_report_slot"] == slot_label:
            return
        await self._send_report(slot_label)

    async def _is_market_open_now(self) -> bool:
        if self._market_clock is None:
            return False
        now = self._market_clock.get_current_kst_time()
        if not self._market_clock.is_market_operating_hours(now):
            return False
        if self._mcs is not None:
            try:
                if not await self._mcs.is_business_day(now.strftime("%Y%m%d")):
                    return False
            except Exception as exc:
                self._logger.warning(f"{self.task_name}: 영업일 확인 실패 — {exc}")
                return False
        return True

    def _slot_label(self, now: datetime) -> Optional[str]:
        start = now.replace(
            hour=self.ALERT_START_HOUR,
            minute=self.ALERT_START_MINUTE,
            second=0,
            microsecond=0,
        )
        if now < start:
            return None
        elapsed = int((now - start).total_seconds())
        slot_elapsed = elapsed - (elapsed % self._alert_interval_sec)
        if elapsed - slot_elapsed > self._alert_grace_sec:
            return None
        slot = start + timedelta(seconds=slot_elapsed)
        return slot.strftime("%Y%m%d %H:%M")

    async def _send_report(self, slot_label: str) -> None:
        theme_data = self._latest_report.get("data") or []
        if not theme_data:
            if not self._progress.get("last_error"):
                self._progress["last_error"] = "intraday_theme_empty"
            return
        if self._telegram_reporter:
            await self._telegram_reporter.send_daily_theme_report(
                theme_data,
                report_date=slot_label,
                show_flow_ratio=False,
            )
        self._progress["last_report_slot"] = slot_label
        self._progress["sent_count"] = len(theme_data)
        self._logger.info(
            f"{self.task_name}: {slot_label} 장중 주도테마 알림 발송 완료 "
            f"({len(theme_data)}개 테마)"
        )

    async def _refresh_latest_report(self, captured_at: str) -> None:
        self._progress["last_error"] = None
        rankings = await self._build_intraday_rankings(captured_at)
        if not rankings or not rankings.get("all_stocks"):
            self._progress["last_error"] = "intraday_ranking_empty"
            self._logger.info(f"{self.task_name}: 장중 기본 랭킹 없음 — {captured_at} 스킵")
            return

        theme_resp = await self._theme_daily_leader_service.build_intraday_theme_report(
            rankings,
            report_time=captured_at,
        )
        if not theme_resp or theme_resp.rt_cd != ErrorCode.SUCCESS.value:
            msg = getattr(theme_resp, "msg1", "") if theme_resp else "응답 없음"
            self._progress["last_error"] = f"theme_service_error:{msg}"
            self._logger.warning(f"{self.task_name}: 주도테마 생성 실패 — {msg}")
            return

        theme_data = theme_resp.data or []
        self._latest_report = {"captured_at": captured_at, "data": theme_data}

    async def _build_intraday_rankings(self, slot_label: str) -> Dict[str, Any]:
        refresh = getattr(self._ranking_task, "refresh_basic_ranking", None)
        if callable(refresh):
            result = refresh()
            if inspect.isawaitable(result):
                await result

        stocks: Dict[str, Dict] = {}
        for category in ("rise", "trading_value", "volume"):
            for item in self._get_basic_ranking_items(category):
                stock = self._normalize_stock(item)
                code = stock.get("stck_shrn_iscd") or stock.get("mksc_shrn_iscd")
                if not code:
                    continue
                existing = stocks.setdefault(code, {})
                for key, value in stock.items():
                    if value not in (None, "") or key not in existing:
                        existing[key] = value
                existing["stck_shrn_iscd"] = code
                existing.setdefault("mksc_shrn_iscd", code)

        return {
            "all_stocks": list(stocks.values()),
            "program_all_stocks": [],
            "report_date": slot_label[:8],
        }

    def _get_basic_ranking_items(self, category: str) -> List[Any]:
        getter = getattr(self._ranking_task, "get_basic_ranking_cache", None)
        if not callable(getter):
            return []
        resp = getter(category)
        if not resp or getattr(resp, "rt_cd", None) != ErrorCode.SUCCESS.value:
            return []
        data = getattr(resp, "data", None)
        if isinstance(data, dict):
            data = data.get("output", [])
        return list(data or [])

    def _normalize_stock(self, item: Any) -> Dict:
        stock = item.to_dict() if hasattr(item, "to_dict") else dict(item or {})
        code = (
            stock.get("stck_shrn_iscd")
            or stock.get("mksc_shrn_iscd")
            or stock.get("iscd")
            or stock.get("code")
            or ""
        )
        if code:
            stock["stck_shrn_iscd"] = code
            stock.setdefault("mksc_shrn_iscd", code)
        if "hts_kor_isnm" not in stock and stock.get("name"):
            stock["hts_kor_isnm"] = stock["name"]
        stock.setdefault("frgn_ntby_tr_pbmn", "0")
        stock.setdefault("orgn_ntby_tr_pbmn", "0")
        stock.setdefault("prsn_ntby_tr_pbmn", "0")
        if not stock.get("acml_tr_pbmn"):
            price = self._to_int(stock.get("stck_prpr"))
            volume = self._to_int(stock.get("acml_vol"))
            if price and volume:
                stock["acml_tr_pbmn"] = str(price * volume)
        return stock

    @staticmethod
    def _to_int(value: Any) -> int:
        try:
            return int(str(value or "0").replace(",", ""))
        except (TypeError, ValueError):
            return 0
