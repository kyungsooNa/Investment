# task/background/after_market/theme_classification_task.py
"""
장 마감 후 네이버 테마 분류를 주기적으로 수집하는 백그라운드 태스크.

테마 구성은 천천히 변하므로 기본 7일 간격으로만 수집한다(최근 수집 후 간격 미경과 시 skip).
수집 자체는 ThemeClassificationCollectorService가 담당하고, 본 태스크는 스케줄링/주기 가드만 맡는다.
"""
import logging
from datetime import datetime
from typing import Dict, Optional, TYPE_CHECKING

from task.background.after_market.after_market_task_base import AfterMarketTask

if TYPE_CHECKING:
    from core.market_clock import MarketClock
    from services.market_calendar_service import MarketCalendarService
    from services.theme_classification_collector_service import ThemeClassificationCollectorService
    from repositories.stock_classification_repository import StockClassificationRepository


class ThemeClassificationTask(AfterMarketTask):
    """장 마감 후 네이버 테마 분류를 주기적으로 수집하는 태스크."""

    def __init__(
        self,
        collector_service: "ThemeClassificationCollectorService",
        classification_repository: "StockClassificationRepository",
        market_calendar_service: Optional["MarketCalendarService"] = None,
        market_clock: Optional["MarketClock"] = None,
        logger=None,
        refresh_interval_days: int = 7,
        worker_pool=None,
    ):
        super().__init__(
            mcs=market_calendar_service,
            market_clock=market_clock,
            logger=logger or logging.getLogger(__name__),
            worker_pool=worker_pool,
        )
        self._collector = collector_service
        self._repo = classification_repository
        self._refresh_interval_days = refresh_interval_days
        self._progress: Dict = {
            "running": False,
            "last_collected_at": None,
            "record_count": 0,
            "industry_record_count": 0,
            "status": None,
            "last_error": None,
        }

    @property
    def task_name(self) -> str:
        return "theme_classification"

    @property
    def _scheduler_label(self) -> str:
        return "ThemeClassificationTask"

    def get_progress(self) -> Dict:
        return dict(self._progress)

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        if await self._is_fresh():
            self._logger.info({"event": "theme_collect_skip", "reason": "interval_not_elapsed"})
            self._progress["status"] = "skipped"
            return
        await self._collect()

    async def force_run(self) -> None:
        """주기 가드를 무시하고 즉시 수집한다."""
        async with self._running_state():
            await self._collect()

    async def _collect(self) -> None:
        self._progress["running"] = True
        self._progress["last_error"] = None
        try:
            count = await self._collector.collect_naver_themes()
            self._progress["record_count"] = count
            # 업종은 같은 사이트·같은 주기라 한 번의 실행에서 이어서 수집한다. 실패해도
            # 테마 결과는 남긴다 — 둘은 서로 독립적인 분류다.
            self._progress["industry_record_count"] = await self._collect_industries()
            self._progress["last_collected_at"] = await self._repo.get_latest_collected_at()
            self._progress["status"] = "done"
        except Exception as e:
            self._progress["status"] = "error"
            self._progress["last_error"] = str(e)
            self._logger.error({"event": "theme_collect_error", "error": str(e)})
        finally:
            self._progress["running"] = False

    async def _collect_industries(self) -> int:
        try:
            return await self._collector.collect_naver_industries()
        except Exception as e:
            self._progress["last_error"] = f"업종 수집 실패: {e}"
            self._logger.error({"event": "industry_collect_error", "error": str(e)})
            return 0

    async def _is_fresh(self) -> bool:
        """최근 수집 시각이 refresh_interval_days 이내면 True."""
        last = await self._repo.get_latest_collected_at()
        if not last:
            return False
        try:
            last_dt = datetime.fromisoformat(last)
        except (ValueError, TypeError):
            return False
        return (datetime.now() - last_dt).days < self._refresh_interval_days
