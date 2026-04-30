# task/background/after_market/cache_warmup_task.py
"""
Watchlist / 보유종목 위주로 캐시를 사전 구성하는 백그라운드 태스크.

장전 또는 장중 기동 직후 전략 실행에 자주 쓰이는 종목들의 가격 요약 데이터를
미리 캐시에 적재하여 장중 전략이 빠르게 데이터에 접근할 수 있도록 한다.

대상 종목 (우선순위 순):
  1. OneilUniverseService watchlist — 전략이 직접 참조하는 핵심 관심 풀
  2. 보유종목(계좌 잔고 output2.pdno) — 리스크 관리 최우선
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Dict, List, Optional, Set, TYPE_CHECKING

from common.types import ErrorCode
from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState
from services.notification_service import NotificationService, NotificationCategory, NotificationLevel

if TYPE_CHECKING:
    from services.market_data_service import MarketDataService
    from services.stock_query_service import StockQueryService
    from services.oneil_universe_service import OneilUniverseService
    from services.market_calendar_service import MarketCalendarService
    from core.market_clock import MarketClock

# 청크당 병렬 호출 수 — 가격 조회는 가벼우므로 다소 넉넉하게 설정
_API_CHUNK_SIZE = 10
_CHUNK_SLEEP_SEC = 0.8


def _chunked(lst: list, size: int):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


class CacheWarmupTask(SchedulableTask):
    """장 시작 전/장중 주요 관심 종목의 가격 데이터를 캐시에 사전 적재하는 태스크."""

    CHECK_INTERVAL_SEC = 60
    PRE_OPEN_WINDOW_MIN = 30

    def __init__(
        self,
        market_data_service: "MarketDataService",
        stock_query_service: "StockQueryService",
        universe_service: Optional["OneilUniverseService"] = None,
        market_calendar_service: Optional["MarketCalendarService"] = None,
        market_clock: Optional["MarketClock"] = None,
        notification_service: Optional["NotificationService"] = None,
        logger=None,
        worker_pool=None,
    ) -> None:
        self._mcs = market_calendar_service
        self._market_clock = market_clock
        self._logger = logger or logging.getLogger(__name__)
        self._mds = market_data_service
        self._sqs = stock_query_service
        self._universe_service = universe_service
        self._ns = notification_service
        self._worker_pool = worker_pool
        self._state: TaskState = TaskState.IDLE
        self._tasks: List[asyncio.Task] = []
        self._running_depth: int = 0

        self._suspend_event: asyncio.Event = asyncio.Event()
        self._suspend_event.set()

        self._is_warming: bool = False
        self._last_warmed_date: Optional[str] = None
        self._progress: Dict = {
            "running": False,
            "processed": 0,
            "total": 0,
            "cached": 0,
            "failed": 0,
            "elapsed": 0.0,
            "last_warmed_date": None,
        }

    # ── SchedulableTask 인터페이스 ────────────────────────────────

    @property
    def task_name(self) -> str:
        return "cache_warmup"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    @property
    def state(self) -> TaskState:
        return self._state

    async def start(self) -> None:
        self._tasks = [task for task in self._tasks if not task.done()]
        if self._tasks:
            return
        if self._state == TaskState.STOPPED:
            self._state = TaskState.IDLE
        await self._on_start_hook()
        self._tasks.append(asyncio.create_task(self._loop()))
        self._logger.info("CacheWarmupTask 장전/장중 웜업 루프 시작")

    async def stop(self) -> None:
        self._logger.info(f"cache_warmup 종료 시작: {len(self._tasks)}개 태스크")
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._state = TaskState.STOPPED
        self._logger.info("cache_warmup 종료 완료")

    async def _on_start_hook(self) -> None:
        self._suspend_event.set()

    async def suspend(self) -> None:
        if self._state == TaskState.RUNNING:
            self._suspend_event.clear()
            self._state = TaskState.SUSPENDED
            self._logger.info("CacheWarmupTask 일시 중지")

    async def resume(self) -> None:
        if self._state == TaskState.SUSPENDED:
            self._suspend_event.set()
            self._state = TaskState.IDLE
            self._logger.info("CacheWarmupTask 재개")

    def get_progress(self) -> Dict:
        return dict(self._progress)

    @asynccontextmanager
    async def _running_state(self):
        entered = self._state not in (TaskState.SUSPENDED, TaskState.STOPPED)
        if entered:
            self._running_depth += 1
            self._state = TaskState.RUNNING
        try:
            yield
        finally:
            if entered:
                self._running_depth -= 1
                if self._running_depth == 0 and self._state == TaskState.RUNNING:
                    self._state = TaskState.IDLE

    # ── 장전/장중 스케줄 ─────────────────────────────────────────

    async def _loop(self) -> None:
        while True:
            try:
                if self._state != TaskState.SUSPENDED:
                    should_run, trading_date = await self._should_run_now()
                    if should_run and trading_date:
                        async with self._running_state():
                            await self._run_warmup(trading_date)
                await asyncio.sleep(self.CHECK_INTERVAL_SEC)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._logger.error(f"CacheWarmupTask 루프 오류: {exc}", exc_info=True)
                await asyncio.sleep(self.CHECK_INTERVAL_SEC)

    async def _should_run_now(self) -> tuple[bool, Optional[str]]:
        if not self._market_clock or not self._mcs:
            return False, None
        now = self._market_clock.get_current_kst_time()
        date_key = now.strftime("%Y%m%d")
        if self._last_warmed_date == date_key:
            return False, date_key
        try:
            if not await self._mcs.is_business_day(date_key):
                return False, date_key
        except Exception:
            return False, date_key

        if self._market_clock.is_market_operating_hours():
            return True, date_key

        open_time = self._market_clock.get_market_open_time()
        should_run = open_time - timedelta(minutes=self.PRE_OPEN_WINDOW_MIN) <= now < open_time
        return should_run, date_key

    # ── 강제 실행 ─────────────────────────────────────────────────

    async def force_run(self) -> None:
        """skip 조건을 무시하고 즉시 캐시 웜업을 실행한다."""
        self._logger.info("CacheWarmupTask 강제 웜업 요청")
        async with self._running_state():
            target_date = None
            if self._mcs:
                target_date = await self._mcs.get_latest_trading_date()
            if not target_date:
                self._logger.error("최근 거래일을 확인할 수 없어 강제 웜업을 중단합니다.")
                return
            await self._run_warmup(target_date)

    # ── 핵심 웜업 로직 ────────────────────────────────────────────

    async def _run_warmup(self, trading_date: str) -> None:
        if self._is_warming:
            self._logger.info("CacheWarmupTask: 웜업 이미 진행 중 — 스킵")
            return

        self._is_warming = True
        start_time = time.time()
        self._progress = {
            "running": True,
            "processed": 0,
            "total": 0,
            "cached": 0,
            "failed": 0,
            "elapsed": 0.0,
            "last_warmed_date": self._last_warmed_date,
        }

        try:
            codes = await self._collect_target_codes()
            total = len(codes)
            self._progress["total"] = total
            self._logger.info(
                f"CacheWarmupTask 웜업 시작 (기준일: {trading_date}, 대상 {total}개 종목)"
            )

            cached = 0
            failed = 0
            processed = 0

            for chunk in _chunked(list(codes), _API_CHUNK_SIZE):
                await self._suspend_event.wait()

                tasks = [self._warmup_code(code) for code in chunk]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                chunk_had_api_call = False
                for result in results:
                    if isinstance(result, Exception):
                        failed += 1
                    elif result is True:
                        cached += 1
                        chunk_had_api_call = True
                    else:
                        failed += 1

                processed += len(chunk)
                elapsed = time.time() - start_time
                self._progress.update({
                    "processed": processed,
                    "cached": cached,
                    "failed": failed,
                    "elapsed": round(elapsed, 1),
                })

                if chunk_had_api_call:
                    await asyncio.sleep(_CHUNK_SLEEP_SEC)
                else:
                    await asyncio.sleep(0)

            self._last_warmed_date = trading_date
            elapsed = time.time() - start_time
            self._progress["last_warmed_date"] = trading_date
            self._logger.info(
                f"CacheWarmupTask 웜업 완료 (기준일: {trading_date}) "
                f"캐시 적재: {cached}/{total}, 실패: {failed}, 소요: {elapsed:.1f}s"
            )
            if self._ns:
                await self._ns.emit(
                    NotificationCategory.BACKGROUND, NotificationLevel.INFO,
                    "캐시 웜업 완료",
                    f"{total}개 종목 중 {cached}개 캐시 적재 완료 (소요: {elapsed:.1f}초)",
                )

        except Exception as e:
            self._logger.error(f"CacheWarmupTask 웜업 실패: {e}", exc_info=True)
            if self._ns:
                await self._ns.emit(
                    NotificationCategory.BACKGROUND, NotificationLevel.ERROR,
                    "캐시 웜업 실패", str(e),
                )
        finally:
            self._is_warming = False
            self._progress["running"] = False

    async def _warmup_code(self, code: str) -> bool:
        """단일 종목의 가격 요약을 조회하여 캐시에 적재한다.

        Returns:
            True  — API 호출(캐시 적재) 성공
            False — 실패 또는 응답 오류
        """
        try:
            resp = await self._mds.get_price_summary(code)
            if resp and resp.rt_cd == ErrorCode.SUCCESS.value:
                return True
            return False
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._logger.debug(f"CacheWarmupTask: {code} 웜업 실패: {e}")
            return False

    # ── 대상 종목 수집 ────────────────────────────────────────────

    async def _collect_target_codes(self) -> Set[str]:
        """watchlist + 보유종목 + 우량주(관심종목) 코드를 중복 없이 수집한다."""
        codes: Set[str] = set()

        watchlist_codes = await self._get_watchlist_codes()
        holdings_codes = await self._get_holdings_codes()

        codes.update(watchlist_codes)
        codes.update(holdings_codes)

        self._logger.info(
            f"CacheWarmupTask 대상 종목: watchlist {len(watchlist_codes)}개 "
            f"+ 보유 {len(holdings_codes)}개 "
            f"= 합산(중복제거) {len(codes)}개"
        )
        return codes

    async def _get_watchlist_codes(self) -> List[str]:
        """OneilUniverseService watchlist 종목 코드를 반환한다."""
        if not self._universe_service:
            return []
        try:
            watchlist = await self._universe_service.get_watchlist()
            return list(watchlist.keys())
        except Exception as e:
            self._logger.warning(f"CacheWarmupTask: watchlist 조회 실패: {e}")
            return []

    async def _get_holdings_codes(self) -> List[str]:
        """계좌 잔고(output2)에서 보유 종목 코드를 반환한다."""
        try:
            resp = await self._sqs.handle_get_account_balance()
            if not (resp and resp.rt_cd == ErrorCode.SUCCESS.value and resp.data):
                return []
            holdings = (
                resp.data.get("output1", [])
                if isinstance(resp.data, dict)
                else []
            )
            codes = []
            for item in holdings:
                code = item.get("pdno", "").strip() if isinstance(item, dict) else ""
                if code:
                    codes.append(code)
            return codes
        except Exception as e:
            self._logger.warning(f"CacheWarmupTask: 보유종목 조회 실패: {e}")
            return []
