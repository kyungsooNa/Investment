"""미국장 체결 대사 자동 실행 태스크 (P1-3).

`OverseasFillReconcileService` 는 브로커 체결내역을 진실로 삼아 USD 원장의 과다
계상(미체결 지정가가 HOLD 로 잡히는 것)을 보정한다. 그런데 지금까지 **수동 라우트
(`POST /api/overseas/trades/reconcile`)에서만** 호출됐다 — 사람이 누르지 않으면
원장이 계속 틀린 채로 쌓인다. 마감 후 하루 1회 자동 실행한다.

`OverseasDryRunTask` 와 같은 US TimeDispatcher 경로를 쓴다(마감 감지 후 티켓 발행,
거래일 dedup). 보정은 서비스가 **줄이는 방향만** 하므로 자동 적용해도 브로커에 없는
보유를 만들지 않는다.
"""
from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from common.overseas_types import OverseasExchange
from common.types import ErrorCode
from interfaces.schedulable_task import TaskPriority
from services.notification_service import NotificationCategory, NotificationLevel
from task.background.after_market.after_market_task_base import AfterMarketTask

if TYPE_CHECKING:
    from core.market_clock import MarketClock
    from services.market_calendar_service import MarketCalendarService


class OverseasFillReconcileTask(AfterMarketTask):
    def __init__(
        self,
        reconcile_service,
        market_calendar_service: Optional["MarketCalendarService"] = None,
        market_clock: Optional["MarketClock"] = None,
        logger=None,
        notification_service=None,
        worker_pool=None,
        exchange: OverseasExchange = OverseasExchange.NASD,
        apply: bool = True,
    ) -> None:
        super().__init__(
            mcs=market_calendar_service,
            market_clock=market_clock,
            logger=logger or logging.getLogger(__name__),
            worker_pool=worker_pool,
        )
        self._service = reconcile_service
        self._notification_service = notification_service
        self._exchange = exchange
        self._apply = apply
        self._last_run_date: Optional[str] = None
        self._last_result: dict = {}

    @property
    def task_name(self) -> str:
        return "overseas_fill_reconcile"

    @property
    def _scheduler_label(self) -> str:
        return "OverseasFillReconcile"

    # ── 트리거 표기용 메타데이터 (17:00 ET) ──
    # dry-run(16:30) 뒤에 돌려 체결내역이 브로커에 반영될 시간을 준다.
    @property
    def _loop_timezone(self) -> str:
        return "America/New_York"

    @property
    def _loop_cron_hour(self) -> int:
        return 17

    @property
    def _loop_cron_minute(self) -> int:
        return 0

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    def get_progress(self) -> dict:
        return {"last_run_date": self._last_run_date, "last_result": self._last_result}

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        if self._last_run_date == latest_trading_date:
            self._logger.info({"event": "overseas_fill_reconcile_skip",
                               "market_date": latest_trading_date, "reason": "already_run"})
            return
        try:
            resp = await self._service.reconcile(
                start_date=latest_trading_date,
                end_date=latest_trading_date,
                exchange=self._exchange.value,
                apply=self._apply,
            )
        except Exception as e:
            # 날짜를 마킹하지 않는다 — 다음 거래일 트리거에서 재시도된다.
            self._logger.error({"event": "overseas_fill_reconcile_error",
                                "market_date": latest_trading_date, "error": str(e)},
                               exc_info=True)
            self._last_result = {"error": str(e)}
            await self._alert("미국장 체결 대사 실패", str(e), NotificationLevel.ERROR)
            return

        if getattr(resp, "rt_cd", None) != ErrorCode.SUCCESS.value:
            msg = getattr(resp, "msg1", "")
            self._logger.warning({"event": "overseas_fill_reconcile_failed",
                                  "market_date": latest_trading_date, "msg": msg})
            self._last_result = {"error": msg}
            await self._alert(
                "미국장 체결 대사 실패",
                f"체결내역을 조회하지 못해 원장을 보정하지 않았습니다 ({msg}).",
                NotificationLevel.WARNING,
            )
            return

        data = getattr(resp, "data", None) or {}
        counts = data.get("counts") or {}
        self._last_result = {"market_date": latest_trading_date,
                             "checked": data.get("checked"), "counts": counts}
        self._logger.info({"event": "overseas_fill_reconcile_done",
                           "market_date": latest_trading_date,
                           "checked": data.get("checked"), "counts": counts,
                           "applied": data.get("applied")})
        self._last_run_date = latest_trading_date

        corrected = int(counts.get("unfilled", 0) or 0) + int(counts.get("partial", 0) or 0)
        if corrected:
            await self._alert(
                "미국장 체결 대사 — 원장 보정",
                self._format_body(latest_trading_date, data),
                NotificationLevel.WARNING,
            )

    @staticmethod
    def _format_body(trade_date: str, data: dict) -> str:
        counts = data.get("counts") or {}
        lines = [
            f"미국 거래일 {trade_date} 원장 보정 결과 "
            f"(대상 {data.get('checked', 0)}건)",
            f"• 미체결: {counts.get('unfilled', 0)}건",
            f"• 부분체결: {counts.get('partial', 0)}건",
            f"• 정상: {counts.get('ok', 0)}건",
        ]
        unknown = int(counts.get("unknown", 0) or 0)
        if unknown:
            lines.append(f"• 판정 불가: {unknown}건 (무조작 — 실계좌 확인 권장)")
        return "\n".join(lines)

    async def _alert(self, title: str, body: str, level) -> None:
        if self._notification_service is None:
            return
        try:
            await self._notification_service.emit(
                NotificationCategory.BACKGROUND, level, title, body,
            )
        except Exception as e:
            self._logger.warning({"event": "overseas_fill_reconcile_alert_error",
                                  "error": str(e)})
