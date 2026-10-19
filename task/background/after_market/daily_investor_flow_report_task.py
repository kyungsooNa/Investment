"""장 마감 후 코스피·코스닥의 투자자별 당일 순매수 금액을 리포트한다."""

import asyncio
import logging
from typing import Optional

from common.types import ErrorCode
from task.background.after_market.after_market_task_base import AfterMarketTask


class DailyInvestorFlowReportTask(AfterMarketTask):
    """개인·외국인·기관의 당일 시장별 순매수/순매도를 한 번 전송한다."""

    _STATE_KEY = "daily_investor_flow_report_last_sent"
    _MARKETS = (("kospi", "0001"), ("kosdaq", "1001"))
    _FIELDS = {
        "individual": "prsn_ntby_tr_pbmn",
        "foreign": "frgn_ntby_tr_pbmn",
        "institution": "orgn_ntby_tr_pbmn",
    }

    def __init__(
        self,
        broker_api_wrapper,
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
        self._broker = broker_api_wrapper
        self._telegram_reporter = telegram_reporter
        self._scheduler_store = scheduler_store
        self._last_reported_date = self._load_last_reported_date()
        self._send_lock = asyncio.Lock()
        self._progress = {
            "running": False,
            "last_reported_date": self._last_reported_date,
        }

    @property
    def task_name(self) -> str:
        return "daily_investor_flow_report"

    @property
    def _scheduler_label(self) -> str:
        return "DailyInvestorFlowReportTask"

    def get_progress(self) -> dict:
        return dict(self._progress)

    def _load_last_reported_date(self) -> Optional[str]:
        if self._scheduler_store is None:
            return None
        try:
            return self._scheduler_store.load_keyed(self._STATE_KEY)
        except Exception as exc:
            self._logger.warning(f"당일 투자자 수급 리포트 전송일 로드 실패: {exc}")
            return None

    def _save_last_reported_date(self, date_str: str) -> None:
        if self._scheduler_store is None:
            return
        try:
            self._scheduler_store.save_keyed(self._STATE_KEY, date_str)
        except Exception as exc:
            self._logger.warning(f"당일 투자자 수급 리포트 전송일 저장 실패: {exc}")

    async def _fetch_market(self, index_code: str, report_date: str) -> Optional[dict]:
        try:
            response = await self._broker.inquire_investor_daily_by_market(
                index_code,
                date=report_date,
            )
        except Exception as exc:
            self._logger.warning(f"당일 투자자 수급 조회 실패 ({index_code}): {exc}")
            return None

        if not response or response.rt_cd != ErrorCode.SUCCESS.value:
            self._logger.warning(f"당일 투자자 수급 응답 실패 ({index_code})")
            return None

        rows = response.data if isinstance(response.data, list) else []
        row = next(
            (
                item for item in rows
                if isinstance(item, dict)
                and str(item.get("stck_bsop_date") or "") == report_date
            ),
            None,
        )
        if row is None:
            self._logger.warning(
                f"당일 투자자 수급에 기준일 데이터 없음 ({index_code}, {report_date})"
            )
            return None

        values = {}
        try:
            for name, field in self._FIELDS.items():
                # KIS 응답 단위는 백만원이므로 100으로 나눠 억원으로 변환한다.
                values[name] = int(round(float(row[field]) / 100))
        except (KeyError, TypeError, ValueError) as exc:
            self._logger.warning(f"당일 투자자 수급 파싱 실패 ({index_code}): {exc}")
            return None
        return values

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        if self._last_reported_date == latest_trading_date:
            self._logger.info(
                f"당일 투자자 수급 리포트: {latest_trading_date} 이미 전송 완료 — 스킵"
            )
            return
        if self._telegram_reporter is None:
            self._logger.warning("당일 투자자 수급 리포트: TelegramReporter 미설정 — 스킵")
            return

        async with self._send_lock:
            if self._last_reported_date == latest_trading_date:
                return
            self._progress["running"] = True
            try:
                results = await asyncio.gather(*(
                    self._fetch_market(index_code, latest_trading_date)
                    for _, index_code in self._MARKETS
                ))
                if any(result is None for result in results):
                    self._logger.warning(
                        "당일 투자자 수급 리포트: 양 시장 데이터가 완전하지 않아 전송 보류"
                    )
                    return

                flows = {
                    market: result
                    for (market, _), result in zip(self._MARKETS, results)
                }
                sent = await self._telegram_reporter.send_daily_investor_flow_report(
                    flows,
                    latest_trading_date,
                )
                if not sent:
                    self._logger.warning(
                        "당일 투자자 수급 리포트: Telegram 전송 실패 — 완료 처리하지 않음"
                    )
                    return

                self._last_reported_date = latest_trading_date
                self._save_last_reported_date(latest_trading_date)
                self._progress["last_reported_date"] = latest_trading_date
            except Exception as exc:
                self._logger.error(
                    f"당일 투자자 수급 리포트 전송 실패: {exc}",
                    exc_info=True,
                )
            finally:
                self._progress["running"] = False
