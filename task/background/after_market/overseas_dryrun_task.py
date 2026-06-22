# task/background/after_market/overseas_dryrun_task.py
"""해외 VBO dry-run after-market 태스크 (Phase 3c).

OverseasVBODryRunService 의 일봉 기반 dry-run 신호 산출을 after-market 스케줄러에
얹어 매일 1회 실행하고, shadow 저널을 파일로 flush 한다.

트리거는 미국 정규장 마감(America/New_York 16:00) 직후 16:30 ET 로 맞춘다
(AfterMarketLoop 의 timezone/cron 훅 오버라이드). 한국 거래 캘린더(mcs)는 미국장에
적용되지 않으므로 미주입(None)하며, cron 의 mon-fri(NY) 필터 + 클럭 날짜로 거래일을
식별한다. 미국 공휴일에는 직전 완성봉을 재평가할 수 있으나 dry-run 이라 무해하다.

**주문 경로 없음** — OverseasVBODryRunService 가 order_execution 의존을 갖지 않아
실주문이 발생하지 않는다.
"""
from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from common.overseas_types import OverseasExchange
from interfaces.schedulable_task import TaskPriority
from task.background.after_market.after_market_task_base import AfterMarketTask

if TYPE_CHECKING:
    from core.market_clock import MarketClock
    from services.market_calendar_service import MarketCalendarService


class OverseasDryRunTask(AfterMarketTask):
    """해외 VBO dry-run 신호를 장 마감 후 1회 산출·기록하는 태스크."""

    def __init__(
        self,
        dryrun_service,
        shadow_journal=None,
        market_calendar_service: Optional["MarketCalendarService"] = None,
        market_clock: Optional["MarketClock"] = None,
        logger=None,
        worker_pool=None,
        exchange: OverseasExchange = OverseasExchange.NASD,
    ) -> None:
        super().__init__(
            mcs=market_calendar_service,
            market_clock=market_clock,
            logger=logger or logging.getLogger(__name__),
            worker_pool=worker_pool,
        )
        self._dryrun_service = dryrun_service
        self._journal = shadow_journal
        self._exchange = exchange
        self._last_run_date: Optional[str] = None

    @property
    def task_name(self) -> str:
        return "overseas_vbo_dryrun"

    @property
    def _scheduler_label(self) -> str:
        return "OverseasVBODryRun"

    # ── 미국 정규장 마감(16:00 ET) 직후 16:30 ET 트리거 ──
    @property
    def _loop_timezone(self) -> str:
        return "America/New_York"

    @property
    def _loop_cron_hour(self) -> int:
        return 16

    @property
    def _loop_cron_minute(self) -> int:
        return 30

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    def get_progress(self) -> dict:
        return {"last_run_date": self._last_run_date}

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        if self._last_run_date == latest_trading_date:
            self._logger.info(
                {"event": "overseas_dryrun_skip", "date": latest_trading_date, "reason": "already_run"}
            )
            return
        try:
            signals = await self._dryrun_service.scan_dry_run(self._exchange)
            if self._journal is not None:
                self._journal.flush_to_file(latest_trading_date)
            self._logger.info(
                {"event": "overseas_dryrun_done", "date": latest_trading_date, "signals": len(signals or [])}
            )
            self._last_run_date = latest_trading_date  # 성공 시에만 dedup 마킹 → 실패 시 재시도
        except Exception as e:
            self._logger.error(
                {"event": "overseas_dryrun_error", "date": latest_trading_date, "error": str(e)}, exc_info=True
            )
