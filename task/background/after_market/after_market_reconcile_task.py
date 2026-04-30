"""After-market broker/order reconciliation task."""
from __future__ import annotations

import logging
from typing import Dict, Optional

from task.background.after_market.after_market_task_base import AfterMarketTask
from services.notification_service import NotificationCategory, NotificationLevel


class AfterMarketReconcileTask(AfterMarketTask):
    MAX_HISTORY = 60

    def __init__(
        self,
        *,
        order_execution_service,
        notification_service=None,
        market_calendar_service=None,
        market_clock=None,
        logger: Optional[logging.Logger] = None,
        worker_pool=None,
    ) -> None:
        super().__init__(
            mcs=market_calendar_service,
            market_clock=market_clock,
            logger=logger,
            worker_pool=worker_pool,
        )
        self._oes = order_execution_service
        self._ns = notification_service
        self._last_result: Dict = {"mismatch_count": None, "error": None}
        self._history: list[Dict] = []

    @property
    def task_name(self) -> str:
        return "after_market_reconcile"

    @property
    def _scheduler_label(self) -> str:
        return "after_market_reconcile"

    def get_progress(self) -> Dict:
        return {
            "running": self.state.value == "running",
            "last_result": self._last_result,
            "history_count": len(self._history),
        }

    def get_history(self, count: int = 20) -> list[Dict]:
        return list(reversed(self._history[-count:]))

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        await self.run_once(latest_trading_date)

    async def run_once(self, latest_trading_date: str = "") -> Dict:
        try:
            mismatch_count = await self._oes.reconcile_orders_with_broker()
            self._last_result = {
                "date": latest_trading_date,
                "mismatch_count": mismatch_count,
                "error": None,
            }
            self._record_history(self._last_result)
            if mismatch_count:
                msg = f"장 종료 후 주문/브로커 불일치 {mismatch_count}건"
                self._logger.warning(f"[AfterMarketReconcile] {msg}")
                if self._ns:
                    await self._ns.emit(
                        NotificationCategory.TRADE,
                        NotificationLevel.ERROR,
                        "장 종료 후 미체결/주문 검증 불일치",
                        msg,
                        metadata=self._last_result,
                    )
            elif self._ns:
                await self._ns.emit(
                    NotificationCategory.TRADE,
                    NotificationLevel.INFO,
                    "장 종료 후 미체결/주문 검증 완료",
                    "불일치 없음",
                    metadata=self._last_result,
                )
            return self._last_result
        except Exception as exc:
            self._last_result = {
                "date": latest_trading_date,
                "mismatch_count": None,
                "error": str(exc),
            }
            self._record_history(self._last_result)
            self._logger.error(f"[AfterMarketReconcile] 실패: {exc}", exc_info=True)
            if self._ns:
                await self._ns.emit(
                    NotificationCategory.TRADE,
                    NotificationLevel.ERROR,
                    "장 종료 후 미체결/주문 검증 실패",
                    str(exc),
                    metadata=self._last_result,
                )
            return self._last_result

    async def force_run(self) -> None:
        async with self._running_state():
            await self.run_once("")

    def _record_history(self, result: Dict) -> None:
        self._history.append(dict(result))
        if len(self._history) > self.MAX_HISTORY:
            self._history = self._history[-self.MAX_HISTORY:]
