# task/background/premium_watchlist_generator_task.py
"""
전일 기준 우량주 생성 백그라운드 태스크.

장 마감 후 자동으로 OneilUniverseService.generate_premium_watchlist()를 실행하여
오닐 전략 전일 기준 우량주 풀을 갱신한다.
"""
import asyncio
import logging
import time
from typing import Dict, List, Optional, TYPE_CHECKING

from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState
from scheduler.after_market_loop import run_after_market_loop

if TYPE_CHECKING:
    from services.oneil_universe_service import OneilUniverseService
    from services.market_calendar_service import MarketCalendarService
    from core.market_clock import MarketClock


class PremiumWatchlistGeneratorTask(SchedulableTask):
    """장 마감 후 전일 기준 우량주 풀을 자동 생성하는 백그라운드 태스크."""

    def __init__(
        self,
        universe_service: "OneilUniverseService",
        market_calendar_service: Optional["MarketCalendarService"] = None,
        market_clock: Optional["MarketClock"] = None,
        logger=None,
    ):
        self._universe_service = universe_service
        self._mcs = market_calendar_service
        self._market_clock = market_clock
        self._logger = logger or logging.getLogger(__name__)

        self._state: TaskState = TaskState.IDLE
        self._tasks: List[asyncio.Task] = []
        self._is_generating: bool = False
        self._last_generated_date: Optional[str] = None
        self._progress: Dict = {
            "running": False,
            "last_generated_date": None,
            "last_result": None,
        }

    # ── SchedulableTask 인터페이스 구현 ────────────────────────

    @property
    def task_name(self) -> str:
        return "전일기준우량주_생성"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    @property
    def state(self) -> TaskState:
        return self._state

    async def start(self) -> None:
        if self._state == TaskState.RUNNING:
            return
        self._state = TaskState.RUNNING
        self._tasks.append(asyncio.create_task(self._after_market_scheduler()))
        self._logger.info("PremiumWatchlistGeneratorTask 시작")

    async def stop(self) -> None:
        self._logger.info(f"PremiumWatchlistGeneratorTask 종료 시작: {len(self._tasks)}개 태스크")
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._state = TaskState.STOPPED
        self._logger.info("PremiumWatchlistGeneratorTask 종료 완료")

    async def suspend(self) -> None:
        # 배치 특성상 suspend는 지원하지 않음 — 현재 실행 중인 생성은 완료 후 다음 루프에서 반영
        if self._state == TaskState.RUNNING:
            self._state = TaskState.SUSPENDED

    async def resume(self) -> None:
        if self._state == TaskState.SUSPENDED:
            self._state = TaskState.RUNNING

    def get_progress(self) -> Dict:
        result = dict(self._progress)
        gen = self._universe_service.generation_progress
        result.update({
            "phase":     gen.get("phase"),
            "processed": gen.get("processed", 0),
            "total":     gen.get("total", 0),
            "passed":    gen.get("passed", 0),
            "selected":  gen.get("selected", 0),
            "elapsed":   gen.get("elapsed", 0.0),
        })
        return result

    # ── 장마감 후 자동 스케줄러 ────────────────────────────────

    async def _after_market_scheduler(self) -> None:
        await run_after_market_loop(
            mcs=self._mcs,
            market_clock=self._market_clock,
            logger=self._logger,
            on_market_closed=self._on_market_closed,
            label="전일기준우량주생성",
        )

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        """장 마감 후 콜백: 해당 거래일의 생성이 필요하면 실행.

        인메모리 기록과 파일 메타데이터를 모두 확인하여
        이미 생성된 경우 재실행을 생략한다 (서버 재시작 후에도 유효).
        """
        if self._last_generated_date == latest_trading_date:
            return

        # 파일에 이미 당일 기준 우량주가 저장되어 있으면 재생성 불필요
        meta = self._universe_service.get_premium_stocks_meta()
        if meta and meta.get("generated_date") == latest_trading_date:
            self._last_generated_date = latest_trading_date
            self._progress["last_generated_date"] = latest_trading_date
            self._logger.info(
                f"전일 기준 우량주 이미 생성됨 (기준일: {latest_trading_date}, "
                f"생성시각: {meta.get('generated_at', '알 수 없음')}) — 생성 스킵"
            )
            return

        await self._run_generation(latest_trading_date)

    async def _run_generation(self, trading_date: str) -> None:
        if self._is_generating:
            self._logger.info("전일 기준 우량주 생성 이미 진행 중 — 스킵")
            return

        self._is_generating = True
        self._progress["running"] = True
        start_time = time.time()
        self._logger.info(f"전일 기준 우량주 생성 시작 (기준일: {trading_date})")

        try:
            result = await self._universe_service.generate_premium_watchlist()
            elapsed = time.time() - start_time
            self._last_generated_date = trading_date
            self._progress["last_generated_date"] = trading_date
            self._progress["last_result"] = result
            self._logger.info(
                f"전일 기준 우량주 생성 완료: "
                f"KOSPI {result.get('kospi_count')}종목, "
                f"KOSDAQ {result.get('kosdaq_count')}종목, "
                f"소요: {elapsed:.1f}초"
            )
        except Exception as e:
            self._logger.error(f"전일 기준 우량주 생성 실패: {e}", exc_info=True)
        finally:
            self._is_generating = False
            self._progress["running"] = False
