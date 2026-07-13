"""주 마지막 거래일 장 마감 후 YTD 상승률 랭킹을 전송한다."""

import logging
from datetime import datetime
from typing import Optional

from task.background.after_market.after_market_task_base import AfterMarketTask


class YtdRankingReportTask(AfterMarketTask):
    """다음 개장일이 다음 주인 거래일에만 YTD 주간 리포트를 전송한다."""

    _STATE_KEY = "ytd_ranking_report_last_sent"
    REPORT_LIMIT = 20

    def __init__(
        self,
        stock_repository,
        telegram_reporter=None,
        market_calendar_service=None,
        market_clock=None,
        scheduler_store=None,
        worker_pool=None,
        logger=None,
    ):
        super().__init__(
            mcs=market_calendar_service,
            market_clock=market_clock,
            logger=logger or logging.getLogger(__name__),
            worker_pool=worker_pool,
        )
        self._stock_repository = stock_repository
        self._telegram_reporter = telegram_reporter
        self._scheduler_store = scheduler_store
        self._last_reported_date: Optional[str] = self._load_last_reported_date()
        self._progress = {
            "running": False,
            "last_reported_date": self._last_reported_date,
            "last_result_count": 0,
        }

    @property
    def task_name(self) -> str:
        return "ytd_ranking_report"

    @property
    def _scheduler_label(self) -> str:
        return "YtdRankingReportTask"

    def get_progress(self) -> dict:
        return dict(self._progress)

    def _load_last_reported_date(self) -> Optional[str]:
        if self._scheduler_store is None:
            return None
        try:
            return self._scheduler_store.load_keyed(self._STATE_KEY)
        except Exception as exc:
            self._logger.warning(f"YTD 주간 리포트 마지막 전송일 로드 실패: {exc}")
            return None

    def _save_last_reported_date(self, date_str: str) -> None:
        if self._scheduler_store is None:
            return
        try:
            self._scheduler_store.save_keyed(self._STATE_KEY, date_str)
        except Exception as exc:
            self._logger.warning(f"YTD 주간 리포트 마지막 전송일 저장 실패: {exc}")

    @staticmethod
    def _week_key(date_str: str) -> tuple[int, int]:
        iso = datetime.strptime(date_str, "%Y%m%d").isocalendar()
        return iso.year, iso.week

    def _already_reported_this_week(self, date_str: str) -> bool:
        if not self._last_reported_date:
            return False
        try:
            return self._week_key(self._last_reported_date) == self._week_key(date_str)
        except ValueError:
            return False

    async def _is_last_trading_day_of_week(self, date_str: str) -> bool:
        current = datetime.strptime(date_str, "%Y%m%d")
        if self._mcs is None:
            return current.weekday() == 4

        next_open = await self._mcs.get_next_open_day(date_str)
        if not next_open or next_open == date_str:
            self._logger.warning(f"YTD 주간 리포트 다음 개장일 확인 실패: {date_str}")
            return False
        return self._week_key(next_open) != self._week_key(date_str)

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        if self._already_reported_this_week(latest_trading_date):
            self._logger.info(f"YTD 주간 리포트: {latest_trading_date} 주차 이미 전송 완료 — 스킵")
            return
        if not await self._is_last_trading_day_of_week(latest_trading_date):
            self._logger.info(f"YTD 주간 리포트: {latest_trading_date} 주 마지막 거래일 아님 — 스킵")
            return
        if self._telegram_reporter is None:
            self._logger.warning("YTD 주간 리포트: TelegramReporter 미설정 — 스킵")
            return

        self._progress["running"] = True
        try:
            rows = await self._stock_repository.get_ytd_return_ranking(limit=self.REPORT_LIMIT)
            if not rows:
                self._logger.warning("YTD 주간 리포트: 비교 데이터 없음 — 전송 보류")
                return

            sent = await self._telegram_reporter.send_ytd_ranking_report(
                rows,
                latest_trading_date,
            )
            if not sent:
                self._logger.warning("YTD 주간 리포트: Telegram 전송 실패 — 완료 처리하지 않음")
                return

            self._last_reported_date = latest_trading_date
            self._save_last_reported_date(latest_trading_date)
            self._progress["last_reported_date"] = latest_trading_date
            self._progress["last_result_count"] = len(rows)
        except Exception as exc:
            self._logger.error(f"YTD 주간 리포트 전송 실패: {exc}", exc_info=True)
        finally:
            self._progress["running"] = False
