# task/background/after_market/overseas_dryrun_task.py
"""해외 dry-run after-market 태스크 (Phase 3c).

해외 dry-run suite 의 일봉 기반 신호 산출을 after-market 스케줄러에 얹어 매일 1회
실행하고, shadow 저널을 파일로 flush 한다.

트리거는 미국장 전용 TimeDispatcher(time_dispatcher_us)가 담당한다 — NY 정규장
마감(16:00 ET) 감지 후 task delay(30분)만큼 대기해 16:30 ET 효과로 티켓을 발행하면
WorkerPool 이 execute() 를 호출한다(Ticket-driven, worker_pool 주입). O-1: dispatcher
와 이 태스크에 규칙 기반 NYSE 캘린더(`USMarketCalendarService`)가 주입되어 미국
휴장일에는 latest_trading_date 가 직전 거래일로 유지되고 중복 발행이 차단된다.
(_loop_* 프로퍼티는 시스템 상태 화면의 트리거 표기용 메타데이터로 유지된다.)

**주문 경로 없음** — dry-run 서비스들은 order_execution 의존을 갖지 않아 실주문이
발생하지 않는다.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Optional, TYPE_CHECKING

from common.overseas_types import OverseasExchange
from interfaces.schedulable_task import TaskPriority
from services.notification_service import NotificationCategory, NotificationLevel
from task.background.after_market.after_market_task_base import AfterMarketTask

if TYPE_CHECKING:
    from core.market_clock import MarketClock
    from services.market_calendar_service import MarketCalendarService


class OverseasDryRunTask(AfterMarketTask):
    """해외 dry-run 신호를 장 마감 후 1회 산출·기록하는 태스크."""

    def __init__(
        self,
        dryrun_service,
        shadow_journal=None,
        market_calendar_service: Optional["MarketCalendarService"] = None,
        market_clock: Optional["MarketClock"] = None,
        logger=None,
        notification_service=None,
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
        self._notification_service = notification_service
        self._exchange = exchange
        self._last_run_date: Optional[str] = None

    @property
    def task_name(self) -> str:
        return "overseas_vbo_dryrun"

    @property
    def _scheduler_label(self) -> str:
        return "OverseasVBODryRun"

    # ── 트리거 표기용 메타데이터 (16:30 ET) ──
    # Ticket-driven 모드(worker_pool 주입)에서는 스케줄링에 사용되지 않으며,
    # 시스템 상태 화면(/api/background/status)의 trigger 표기에만 쓰인다.
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

    @staticmethod
    def _format_market_date(yyyymmdd: str) -> str:
        if len(yyyymmdd) == 8 and yyyymmdd.isdigit():
            return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
        return yyyymmdd

    @classmethod
    def _strategy_label(cls, signal: dict) -> str:
        strategy = str(signal.get("strategy") or "")
        reason = str(signal.get("reason") or signal.get("entry_reason") or "")
        if "BGU" in strategy or "buyable_gap_up" in reason:
            return "BGU"
        if "PP" in strategy or "pocket_pivot" in reason:
            return "PP"
        if "VBO" in strategy or "vbo" in reason:
            return "VBO"
        return "기타"

    @classmethod
    def _summarize_signals(cls, signals: list[dict]) -> tuple[dict[str, int], str]:
        counts: Counter[str] = Counter()
        names_by_label: dict[str, list[str]] = defaultdict(list)
        for sig in signals or []:
            label = cls._strategy_label(sig)
            counts[label] += 1
            name = str(sig.get("name") or sig.get("code") or "").strip()
            if name:
                names_by_label[label].append(name)

        ordered_labels = [label for label in ("VBO", "PP", "BGU", "기타") if counts.get(label)]
        summary = {label: counts[label] for label in ordered_labels}
        lines = []
        for label in ordered_labels:
            examples = ", ".join(names_by_label[label][:3])
            suffix = f" ({examples})" if examples else ""
            lines.append(f"- {label}: {counts[label]}개{suffix}")
        return summary, "\n".join(lines)

    @classmethod
    def _format_notification_body(cls, market_date_text: str, signals: list[dict]) -> str:
        _, detail = cls._summarize_signals(signals)
        total = len(signals or [])
        body = f"미국 거래일 {market_date_text} 기준 dry-run 리포트: 총 {total}개 신호"
        if detail:
            body = f"{body}\n{detail}"
        return body

    async def _on_market_closed(self, latest_trading_date: str) -> None:
        market_date_text = self._format_market_date(latest_trading_date)
        exchange_value = self._exchange.value
        if self._last_run_date == latest_trading_date:
            self._logger.info(
                {
                    "event": "overseas_dryrun_skip",
                    "market_date": latest_trading_date,
                    "market_date_text": market_date_text,
                    "exchange": exchange_value,
                    "reason": "already_run",
                    "reason_text": "이미 처리한 미국 거래일이므로 dry-run을 스킵합니다.",
                }
            )
            return
        try:
            signals = await self._dryrun_service.scan_dry_run(self._exchange)
            summary, _ = self._summarize_signals(signals or [])
            if self._journal is not None:
                self._journal.flush_to_file(latest_trading_date)
            self._logger.info(
                {
                    "event": "overseas_dryrun_done",
                    "market_date": latest_trading_date,
                    "market_date_text": market_date_text,
                    "exchange": exchange_value,
                    "signals": len(signals or []),
                    "summary": summary,
                }
            )
            if self._notification_service:
                await self._notification_service.emit(
                    NotificationCategory.BACKGROUND,
                    NotificationLevel.INFO,
                    "해외 dry-run 완료",
                    self._format_notification_body(market_date_text, signals or []),
                )
            self._last_run_date = latest_trading_date  # 성공 시에만 dedup 마킹 → 실패 시 재시도
        except Exception as e:
            self._logger.error(
                {
                    "event": "overseas_dryrun_error",
                    "market_date": latest_trading_date,
                    "market_date_text": market_date_text,
                    "exchange": exchange_value,
                    "error": str(e),
                },
                exc_info=True,
            )
            if self._notification_service:
                await self._notification_service.emit(
                    NotificationCategory.BACKGROUND,
                    NotificationLevel.ERROR,
                    "해외 dry-run 실패",
                    str(e),
                )
