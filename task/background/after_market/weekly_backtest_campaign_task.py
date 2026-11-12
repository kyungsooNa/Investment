"""주 마지막 거래일에 유효 캡처 기반 전략 검증 캠페인을 실행한다."""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from task.background.after_market.after_market_task_base import AfterMarketTask


class WeeklyBacktestCampaignTask(AfterMarketTask):
    """충분한 검증 데이터가 쌓인 주에 한 번만 백테스트 캠페인을 실행한다."""

    _STATE_KEY = "weekly_backtest_campaign_last_completed"

    def __init__(
        self,
        *,
        campaign_service,
        scheduler_store=None,
        min_valid_dates: int = 30,
        mcs=None,
        market_clock=None,
        logger=None,
        worker_pool=None,
    ) -> None:
        super().__init__(
            mcs=mcs,
            market_clock=market_clock,
            logger=logger or logging.getLogger(__name__),
            worker_pool=worker_pool,
        )
        self._campaign_service = campaign_service
        self._scheduler_store = scheduler_store
        self._min_valid_dates = max(1, int(min_valid_dates))
        self._last_completed_date: Optional[str] = self._load_last_completed_date()
        self._progress = {
            "running": False,
            "status": "idle",
            "last_completed_date": self._last_completed_date,
            "valid_date_count": 0,
            "required_valid_dates": self._min_valid_dates,
            "summary": {},
            "error": None,
        }

    @property
    def task_name(self) -> str:
        return "weekly_backtest_campaign"

    @property
    def _scheduler_label(self) -> str:
        return "WeeklyBacktestCampaignTask"

    def get_progress(self) -> dict:
        return dict(self._progress)

    def _load_last_completed_date(self) -> Optional[str]:
        if self._scheduler_store is None:
            return None
        try:
            return self._scheduler_store.load_keyed(self._STATE_KEY)
        except Exception as exc:
            self._logger.warning(f"주간 백테스트 마지막 완료일 로드 실패: {exc}")
            return None

    def _save_last_completed_date(self, date_str: str) -> None:
        if self._scheduler_store is None:
            return
        self._scheduler_store.save_keyed(self._STATE_KEY, date_str)

    @staticmethod
    def _week_key(date_str: str) -> tuple[int, int]:
        iso = datetime.strptime(date_str, "%Y%m%d").isocalendar()
        return iso.year, iso.week

    def _already_completed_this_week(self, date_str: str) -> bool:
        if not self._last_completed_date:
            return False
        try:
            return self._week_key(self._last_completed_date) == self._week_key(date_str)
        except ValueError:
            return False

    async def _is_last_trading_day_of_week(self, date_str: str) -> bool:
        current = datetime.strptime(date_str, "%Y%m%d")
        if self._mcs is None:
            return current.weekday() == 4
        next_open = await self._mcs.get_next_open_day(date_str)
        if not next_open or next_open == date_str:
            self._logger.warning(f"주간 백테스트 다음 개장일 확인 실패: {date_str}")
            return False
        return self._week_key(next_open) != self._week_key(date_str)

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        if self._already_completed_this_week(latest_trading_date):
            self._progress["status"] = "skipped_already_completed"
            return
        if not await self._is_last_trading_day_of_week(latest_trading_date):
            self._progress["status"] = "skipped_not_week_end"
            return

        self._progress.update(running=True, status="planning", error=None)
        try:
            plan = await asyncio.to_thread(self._campaign_service.build_plan)
            valid_date_count = int(plan.get("valid_date_count") or 0)
            self._progress["valid_date_count"] = valid_date_count
            if valid_date_count < self._min_valid_dates:
                self._progress["status"] = "waiting_for_data"
                self._logger.info(
                    f"주간 백테스트 데이터 대기: {valid_date_count}/{self._min_valid_dates}일"
                )
                return

            self._progress["status"] = "running"
            result = await asyncio.to_thread(self._campaign_service.run)
            if result.get("status") != "completed":
                raise RuntimeError(f"campaign status={result.get('status')}")

            self._save_last_completed_date(latest_trading_date)
            self._last_completed_date = latest_trading_date
            self._progress.update(
                status="completed",
                last_completed_date=latest_trading_date,
                summary=result.get("summary") or {},
            )
        except Exception as exc:
            self._progress.update(status="failed", error=str(exc))
            self._logger.error(f"주간 백테스트 캠페인 실패: {exc}", exc_info=True)
        finally:
            self._progress["running"] = False
