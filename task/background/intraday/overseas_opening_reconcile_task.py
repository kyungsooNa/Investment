"""미국장 개장 대사 태스크 (P1-2).

`OverseasReconcileService` 는 로컬 기대 포지션과 브로커 해외 잔고를 비교해 drift 를
산출하지만, 지금까지 **아무도 호출하지 않았다**. 실주문 경로에서는 장중 재시작·부분
체결·수동 개입으로 시스템 상태와 실계좌가 갈라질 수 있으므로, 개장 직후 한 번
맞춰보고 어긋나면 알린다(국내 `OpeningPositionReconcileTask` 와 같은 역할).

**비교만 한다 — 자동 보정은 하지 않는다.** 어느 쪽이 진실인지는 상황마다 다르고,
잘못 맞추면 없는 포지션을 만들거나 실보유를 지운다. 사람이 판단하도록 알림만 낸다.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState
from services.notification_service import NotificationCategory, NotificationLevel


class OverseasOpeningReconcileTask(SchedulableTask):
    CHECK_INTERVAL_SEC = 60
    OPEN_DELAY_MIN = 3
    RUN_WINDOW_MIN = 30

    def __init__(
        self,
        *,
        reconcile_service,
        strategy_services,
        broker,
        market_clock,
        us_market_calendar_service=None,
        notification_service=None,
        check_interval_sec: Optional[int] = None,
        open_delay_min: Optional[int] = None,
        run_window_min: Optional[int] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._service = reconcile_service
        self._strategies = list(strategy_services or [])
        self._broker = broker
        self._market_clock = market_clock
        self._us_mcs = us_market_calendar_service
        self._ns = notification_service
        self._check_interval_sec = check_interval_sec or self.CHECK_INTERVAL_SEC
        self._open_delay_min = (
            open_delay_min if open_delay_min is not None else self.OPEN_DELAY_MIN
        )
        self._run_window_min = (
            run_window_min if run_window_min is not None else self.RUN_WINDOW_MIN
        )
        self._logger = logger or logging.getLogger(__name__)
        self._state = TaskState.IDLE
        self._task: Optional[asyncio.Task] = None
        self._last_checked_date: Optional[str] = None
        self._last_result: Dict[str, Any] = {}

    @property
    def task_name(self) -> str:
        return "overseas_opening_reconcile"

    @property
    def priority(self) -> TaskPriority:
        # 상태 불일치는 이후 모든 매매 판단을 오염시키므로 우선 처리한다.
        return TaskPriority.HIGH

    @property
    def state(self) -> TaskState:
        return self._state

    def get_progress(self) -> dict:
        return {
            "running": self._state == TaskState.RUNNING,
            "last_checked_date": self._last_checked_date,
            "last_result": self._last_result,
        }

    async def start(self) -> None:
        if self._task is None or self._task.done():
            if self._state == TaskState.STOPPED:
                self._state = TaskState.IDLE
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        self._state = TaskState.STOPPED

    async def suspend(self) -> None:
        self._state = TaskState.SUSPENDED

    async def resume(self) -> None:
        if self._state == TaskState.SUSPENDED:
            self._state = TaskState.IDLE

    async def _loop(self) -> None:
        while True:
            try:
                if self._state != TaskState.SUSPENDED:
                    self._state = TaskState.RUNNING
                    await self._tick()
                    if self._state == TaskState.RUNNING:
                        self._state = TaskState.IDLE
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._logger.error("%s: loop error — %s", self.task_name, exc, exc_info=True)
            await asyncio.sleep(self._check_interval_sec)

    # ── 실행 ────────────────────────────────────────────────────────────

    async def _tick(self) -> None:
        now = self._market_clock.get_current_kst_time()
        today = self._market_clock.get_current_kst_date_str()
        if self._last_checked_date == today:
            return
        if self._us_mcs is not None and not self._us_mcs.is_trading_day(today):
            return
        if not self._in_window(now):
            return

        try:
            await self._reconcile(today)
        except Exception as exc:
            # 날짜를 마킹하지 않는다 — 다음 폴링에서 재시도돼야 한다.
            self._last_result = {"error": str(exc)}
            self._logger.error({"event": "overseas_opening_reconcile_error",
                                "trade_date": today, "error": str(exc)}, exc_info=True)
            return
        self._last_checked_date = today

    def _in_window(self, now) -> bool:
        """개장 + delay ~ + window 사이. 개장 직후엔 체결 반영이 덜 돼 오탐이 난다."""
        open_dt = self._market_clock.get_market_open_time(now)
        elapsed_min = (now - open_dt).total_seconds() / 60.0
        return self._open_delay_min <= elapsed_min <= self._run_window_min

    async def _reconcile(self, trade_date: str) -> None:
        local = self._local_positions()
        balance = await self._broker.get_overseas_balance()
        report = self._service.reconcile(local, balance)

        drift_items = (
            list(report.get("missing_in_broker") or [])
            + list(report.get("extra_in_broker") or [])
            + list(report.get("qty_mismatch") or [])
        )
        self._last_result = {
            "trade_date": trade_date,
            "local_positions": local,
            "drift_count": len(drift_items),
            "error": report.get("error"),
        }
        self._logger.info({"event": "overseas_opening_reconcile_done",
                           "trade_date": trade_date, "local": local,
                           "drift_count": len(drift_items), "error": report.get("error")})

        if not report.get("ok"):
            await self._alert(
                "미국장 개장 대사 실패",
                f"브로커 잔고 조회에 실패해 대사하지 못했습니다 "
                f"(사유: {report.get('error') or '알 수 없음'}).\n"
                f"조회 불가는 미보유와 다르므로 drift 로 처리하지 않았습니다.",
                NotificationLevel.WARNING,
            )
            return
        if drift_items:
            await self._alert(
                "미국장 개장 대사 불일치",
                self._format_drift(report),
                NotificationLevel.WARNING,
            )

    def _local_positions(self) -> Dict[str, int]:
        """전략별 보유를 심볼 기준으로 합산한다.

        브로커 잔고는 전략을 구분하지 않으므로, 두 전략이 같은 심볼을 들고 있으면
        합쳐야 수량 불일치 오탐이 나지 않는다.
        """
        totals: Dict[str, int] = {}
        for svc in self._strategies:
            try:
                positions = (svc.get_state() or {}).get("positions") or {}
            except Exception as exc:
                self._logger.warning({"event": "overseas_opening_reconcile_state_error",
                                      "strategy": getattr(svc, "STRATEGY_NAME", "?"),
                                      "error": str(exc)})
                continue
            for code, held in positions.items():
                try:
                    qty = int(float((held or {}).get("qty") or 0))
                except (TypeError, ValueError):
                    continue
                if qty > 0:
                    totals[str(code).upper()] = totals.get(str(code).upper(), 0) + qty
        return totals

    @staticmethod
    def _format_drift(report: Dict[str, Any]) -> str:
        lines: List[str] = ["로컬 포지션과 브로커 잔고가 일치하지 않습니다.", ""]
        for item in report.get("missing_in_broker") or []:
            lines.append(f"• 브로커에 없음: {item.get('symbol')} (로컬 {item.get('local_qty')}주)")
        for item in report.get("extra_in_broker") or []:
            lines.append(f"• 로컬에 없음: {item.get('symbol')} (브로커 {item.get('broker_qty')}주)")
        for item in report.get("qty_mismatch") or []:
            lines.append(
                f"• 수량 불일치: {item.get('symbol')} "
                f"(로컬 {item.get('local_qty')}주 / 브로커 {item.get('broker_qty')}주)"
            )
        lines.append("")
        lines.append("자동 보정하지 않았습니다 — 실계좌를 확인하세요.")
        return "\n".join(lines)

    async def _alert(self, title: str, body: str, level) -> None:
        if self._ns is None:
            return
        try:
            await self._ns.emit(NotificationCategory.STRATEGY, level, title, body,
                                metadata={"force_external": True, "market": "overseas_us",
                                          "event": "overseas_opening_reconcile"})
        except Exception as exc:
            self._logger.warning({"event": "overseas_opening_reconcile_alert_error",
                                  "error": str(exc)})
