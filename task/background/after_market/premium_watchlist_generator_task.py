# task/background/after_market/premium_watchlist_generator_task.py
"""
전일 기준 우량주 생성 백그라운드 태스크.

장 마감 후 자동으로 OneilUniverseService.generate_premium_watchlist()를 실행하여
오닐 전략 전일 기준 우량주 풀을 갱신한다.
"""
import logging
import time
from typing import Dict, Optional, TYPE_CHECKING

from task.background.after_market.after_market_task_base import AfterMarketTask
from services.notification_service import NotificationService, NotificationCategory, NotificationLevel

if TYPE_CHECKING:
    from services.oneil_universe_service import OneilUniverseService
    from services.market_calendar_service import MarketCalendarService
    from core.market_clock import MarketClock
    from services.telegram_notifier import TelegramReporter


class PremiumWatchlistGeneratorTask(AfterMarketTask):
    """장 마감 후 전일 기준 우량주 풀을 자동 생성하는 백그라운드 태스크."""

    def __init__(
        self,
        universe_service: "OneilUniverseService",
        market_calendar_service: Optional["MarketCalendarService"] = None,
        market_clock: Optional["MarketClock"] = None,
        logger=None,
        notification_service: Optional["NotificationService"] = None,
        worker_pool=None,
        telegram_reporter: Optional["TelegramReporter"] = None,
    ):
        super().__init__(
            mcs=market_calendar_service,
            market_clock=market_clock,
            logger=logger or logging.getLogger(__name__),
            worker_pool=worker_pool,
        )
        self._universe_service = universe_service
        self._ns = notification_service
        self._telegram_reporter = telegram_reporter

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
        return "전일기준주도주_생성"

    @property
    def _scheduler_label(self) -> str:
        return "전일기준우량주생성"

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

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        """장 마감 후 콜백: 해당 거래일의 생성이 필요하면 실행.

        인메모리 기록과 파일 메타데이터를 모두 확인하여
        이미 생성된 경우 재실행을 생략한다 (서버 재시작 후에도 유효).
        """
        if self._last_generated_date == latest_trading_date:
            if self._ns:
                await self._ns.emit(
                    NotificationCategory.BACKGROUND, NotificationLevel.INFO, "전일기준우량주 생성 스킵",
                    f"{latest_trading_date} 이미 생성 완료된 상태입니다."
                )
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
            if self._ns:
                await self._ns.emit(
                    NotificationCategory.BACKGROUND, NotificationLevel.INFO, "전일기준우량주 생성 스킵",
                    f"{latest_trading_date} 이미 생성 완료된 상태입니다."
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
            result = await self._universe_service.generate_premium_watchlist(trading_date=trading_date)
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
            if self._ns:
                await self._ns.emit(
                    NotificationCategory.BACKGROUND, NotificationLevel.INFO, "전일기준우량주 생성 완료",
                    f"KOSPI {result.get('kospi_count')}개, KOSDAQ {result.get('kosdaq_count')}개 종목 수집 완료 (소요: {elapsed:.1f}초)"
                )
            if self._telegram_reporter:
                await self._telegram_reporter.send_premium_watchlist_report(
                    kospi=result.get("kospi_stocks", []),
                    kosdaq=result.get("kosdaq_stocks", []),
                    report_date=trading_date,
                )
        except Exception as e:
            self._logger.error(f"전일 기준 우량주 생성 실패: {e}", exc_info=True)
            if self._ns:
                await self._ns.emit(NotificationCategory.BACKGROUND, NotificationLevel.ERROR, "전일기준우량주 생성 실패", str(e))
        finally:
            self._is_generating = False
            self._progress["running"] = False

    async def force_run(self) -> None:
        """강제 생성: skip 조건을 무시하고 전일 기준 우량주를 재생성한다."""
        self._logger.info("PremiumWatchlistGeneratorTask 강제 생성 요청")
        async with self._running_state():
            target_date = None
            if self._mcs:
                target_date = await self._mcs.get_latest_trading_date()
            if not target_date:
                self._logger.error("최근 거래일을 확인할 수 없어 강제 생성을 중단합니다.")
                return
            await self._run_generation(target_date)
