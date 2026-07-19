"""관심종목 OpenDART 공시를 주기적으로 감시하는 저우선순위 태스크."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional

from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState
from repositories.dart_disclosure_repository import StoredDisclosure


class DartDisclosureMonitorTask(SchedulableTask):
    def __init__(
        self,
        *,
        client,
        repository,
        favorite_repository,
        rule_service,
        telegram_reporter,
        config,
        market_clock,
        logger=None,
        ai_analyzer=None,
    ) -> None:
        self._client = client
        self._repository = repository
        self._favorites = favorite_repository
        self._rules = rule_service
        self._reporter = telegram_reporter
        self._config = config
        self._market_clock = market_clock
        self._logger = logger or logging.getLogger(__name__)
        self._ai_analyzer = ai_analyzer
        self._ai_summary_cache: Dict[str, Optional[str]] = {}
        self._state = TaskState.IDLE
        self._tasks: List[asyncio.Task] = []
        self._tick_lock: Optional[asyncio.Lock] = None
        self._progress: Dict = {
            "running": False,
            "last_checked_at": None,
            "last_success_at": None,
            "matched_count": 0,
            "sent_count": 0,
            "pending_digest_count": 0,
            "last_error": None,
        }

    @property
    def task_name(self) -> str:
        return "dart_disclosure_monitor"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    @property
    def state(self) -> TaskState:
        return self._state

    def get_progress(self) -> Dict:
        return dict(self._progress)

    def _get_tick_lock(self) -> asyncio.Lock:
        if self._tick_lock is None:
            self._tick_lock = asyncio.Lock()
        return self._tick_lock

    async def start(self) -> None:
        if any(not task.done() for task in self._tasks):
            return
        self._state = TaskState.RUNNING
        self._progress["running"] = True
        self._tasks.append(asyncio.create_task(self._loop()))

    async def stop(self) -> None:
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._state = TaskState.STOPPED
        self._progress["running"] = False

    async def suspend(self) -> None:
        if self._state == TaskState.RUNNING:
            self._state = TaskState.SUSPENDED

    async def resume(self) -> None:
        if self._state == TaskState.SUSPENDED:
            self._state = TaskState.RUNNING

    async def _loop(self) -> None:
        try:
            while True:
                if self._state == TaskState.RUNNING:
                    try:
                        await self._tick()
                    except Exception as exc:
                        self._progress["last_error"] = f"{type(exc).__name__}: {exc}"
                        self._logger.error(
                            f"{self.task_name}: 공시 조회 실패 — {exc}", exc_info=True
                        )
                now = self._market_clock.get_current_kst_time()
                await asyncio.sleep(self._interval_for(now))
        except asyncio.CancelledError:
            pass

    async def _tick(self) -> None:
        lock = self._get_tick_lock()
        if lock.locked():
            return
        async with lock:
            now = self._market_clock.get_current_kst_time()
            date = now.strftime("%Y%m%d")
            self._progress["last_checked_at"] = now.isoformat()
            self._progress["matched_count"] = 0
            self._progress["sent_count"] = 0
            self._progress["last_error"] = None

            await self._send_digest_if_due(now, date)

            favorite_codes = {str(code) for code in await self._favorites.get_all()}
            if not favorite_codes:
                self._progress["last_success_at"] = now.isoformat()
                return

            initialized = await self._repository.is_initialized()
            disclosures = await self._fetch_recent(date)
            matching = [item for item in disclosures if item.stock_code in favorite_codes]
            self._progress["matched_count"] = len(matching)

            baseline_items = []
            for disclosure in matching:
                importance = self._rules.evaluate(disclosure)
                inserted = await self._repository.save_detected(
                    disclosure,
                    importance,
                    suppress_immediate=not initialized,
                )
                if not initialized and inserted:
                    baseline_items.append(StoredDisclosure(disclosure, importance))

            if not initialized:
                await self._repository.mark_initialized()
                if baseline_items:
                    sent = await self._reporter.send_disclosure_digest(
                        baseline_items, date
                    )
                    if sent:
                        await self._repository.mark_digest_sent(
                            [item.disclosure.receipt_no for item in baseline_items], now
                        )
            else:
                await self._send_pending_immediate(now)

            self._progress["last_success_at"] = now.isoformat()

    async def _fetch_recent(self, date: str) -> list:
        collected = []
        max_pages = int(getattr(self._config, "max_pages_per_poll", 5))
        for page_no in range(1, max_pages + 1):
            page = await self._client.fetch_disclosures(date, page_no=page_no)
            collected.extend(page.items)
            if page_no >= page.total_page:
                break
            receipt_nos = [item.receipt_no for item in page.items]
            known = await self._repository.get_known_receipt_nos(receipt_nos)
            if receipt_nos and all(receipt_no in known for receipt_no in receipt_nos):
                break
        return collected

    async def _send_pending_immediate(self, now: datetime) -> None:
        threshold = int(getattr(self._config, "immediate_alert_score", 70))
        pending = await self._repository.get_pending_immediate(threshold)
        for item in pending:
            receipt_no = item.disclosure.receipt_no
            if receipt_no in self._ai_summary_cache:
                ai_summary = self._ai_summary_cache[receipt_no]
            else:
                ai_summary = None
                if self._ai_analyzer is not None:
                    ai_summary = await self._ai_analyzer.summarize(
                        item.disclosure, item.importance
                    )
                    self._ai_summary_cache[receipt_no] = ai_summary
            sent = await self._reporter.send_disclosure_alert(
                item.disclosure, item.importance, ai_summary=ai_summary
            )
            if sent:
                await self._repository.mark_immediate_sent(
                    receipt_no, now
                )
                self._ai_summary_cache.pop(receipt_no, None)
                self._progress["sent_count"] += 1
            else:
                await self._repository.increment_send_retry(receipt_no)

    async def _send_digest_if_due(self, now: datetime, date: str) -> None:
        if not bool(getattr(self._config, "daily_digest_enabled", True)):
            return
        if now.strftime("%H:%M") < str(
            getattr(self._config, "daily_digest_time", "19:40")
        ):
            return
        threshold = int(getattr(self._config, "immediate_alert_score", 70))
        pending = await self._repository.get_pending_digest(
            date, immediate_threshold=threshold
        )
        self._progress["pending_digest_count"] = len(pending)
        if not pending:
            return
        sent = await self._reporter.send_disclosure_digest(pending, date)
        if sent:
            await self._repository.mark_digest_sent(
                [item.disclosure.receipt_no for item in pending], now
            )
            self._progress["pending_digest_count"] = 0

    def _interval_for(self, now: datetime) -> int:
        hhmm = now.strftime("%H:%M")
        start = str(getattr(self._config, "active_start_time", "07:00"))
        end = str(getattr(self._config, "active_end_time", "19:30"))
        if start <= hhmm <= end:
            interval = int(getattr(self._config, "poll_interval_sec", 300))
        else:
            interval = int(getattr(self._config, "off_hours_interval_sec", 1800))

        if bool(getattr(self._config, "daily_digest_enabled", True)):
            digest_hhmm = str(getattr(self._config, "daily_digest_time", "19:40"))
            try:
                hour, minute = (int(part) for part in digest_hhmm.split(":", 1))
                digest_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if now < digest_at:
                    interval = min(interval, max(1, int((digest_at - now).total_seconds())))
            except (TypeError, ValueError):
                pass
        return interval
