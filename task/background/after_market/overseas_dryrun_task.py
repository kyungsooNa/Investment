# task/background/after_market/overseas_dryrun_task.py
"""해외 dry-run after-market 태스크.

해외 dry-run suite 의 일봉 기반 신호(VBO/PP/BGU/CB/RSI2/OSB) 산출을 after-market 스케줄러에
얹어 매일 1회 실행하고, shadow 저널을 파일로 flush 한다.

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
        market_regime_service=None,
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
        # 국면 라벨은 **기록 전용**이다. dry-run 은 관측 데이터라 국면으로 차단하지
        # 않는다 — 차단하면 bear 구간이 통째로 비어 게이트의 사후 검증이 불가능해진다.
        self._regime = market_regime_service
        self._last_run_date: Optional[str] = None

    @property
    def task_name(self) -> str:
        return "overseas_dryrun"

    @property
    def _scheduler_label(self) -> str:
        return "OverseasDryRun"

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
        if "CB" in strategy or "channel_breakout" in reason:
            return "CB"
        if "PP" in strategy or "pocket_pivot" in reason:
            return "PP"
        if "RSI2" in strategy or "rsi2" in reason:
            return "RSI2"
        if "OSB" in strategy or "squeeze_breakout" in reason:
            return "OSB"
        if "VBO" in strategy or "vbo" in reason:
            return "VBO"
        return "기타"

    @staticmethod
    def _normalize_run_report(report) -> list[dict]:
        """suite 가 준 전략별 실행 결과만 걸러낸다.

        리포트를 못 주는 dry-run 서비스(단일 서비스 직결·구형 대역)도 있으므로,
        형식이 맞지 않으면 빈 리스트로 떨어뜨려 기존 신호 기반 요약으로 되돌아간다.
        """
        if not isinstance(report, list):
            return []
        return [e for e in report if isinstance(e, dict) and e.get("strategy")]

    @classmethod
    def _summarize_signals(
        cls, signals: list[dict], run_report: list[dict] | None = None,
    ) -> tuple[dict[str, int], str]:
        counts: Counter[str] = Counter()
        names_by_label: dict[str, list[str]] = defaultdict(list)
        for sig in signals or []:
            label = cls._strategy_label(sig)
            counts[label] += 1
            name = str(sig.get("name") or sig.get("code") or "").strip()
            if name:
                names_by_label[label].append(name)

        # 실행 결과가 있으면 그것이 라벨 목록의 기준이다 — 0건 전략도 남겨야
        # "설정에서 빠졌다"와 "오늘 신호가 없었다"가 구분된다.
        failures = {
            cls._strategy_label(e): str(e.get("error") or "unknown")
            for e in (run_report or []) if not e.get("ok")
        }
        ordered_labels: list[str] = []
        for entry in run_report or []:
            label = cls._strategy_label(entry)
            if label not in ordered_labels:
                ordered_labels.append(label)
        # 리포트에 없는 라벨(기타 등)의 신호도 누락시키지 않는다.
        for label in ("VBO", "PP", "BGU", "CB", "RSI2", "OSB", "기타"):
            if counts.get(label) and label not in ordered_labels:
                ordered_labels.append(label)

        summary = {label: counts.get(label, 0) for label in ordered_labels}
        lines = []
        for label in ordered_labels:
            if label in failures:
                lines.append(f"- {label}: 실행 실패 ({failures[label]})")
                continue
            examples = ", ".join(names_by_label[label][:3])
            suffix = f" ({examples})" if examples else ""
            lines.append(f"- {label}: {counts.get(label, 0)}개{suffix}")
        return summary, "\n".join(lines)

    @classmethod
    def _format_notification_body(
        cls, market_date_text: str, signals: list[dict], run_report: list[dict] | None = None,
        regime_label: str | None = None,
    ) -> str:
        _, detail = cls._summarize_signals(signals, run_report)
        total = len(signals or [])
        body = f"미국 거래일 {market_date_text} 기준 dry-run 리포트: 총 {total}개 신호"
        if regime_label:
            body = f"{body}\n시장 국면: {regime_label} (기록 전용 — dry-run 은 차단하지 않음)"
        if detail:
            body = f"{body}\n{detail}"
        return body

    async def _regime_label(self) -> Optional[str]:
        """당일 미국장 국면 라벨. 조회 실패는 흡수한다 — dry-run 스캔을 막지 않는다."""
        if self._regime is None:
            return None
        try:
            return (await self._regime.classify(self._regime.MARKET)).regime_label
        except Exception as e:
            self._logger.warning({"event": "overseas_dryrun_regime_error", "error": str(e)})
            return None

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
            regime_label = await self._regime_label()
            signals = await self._dryrun_service.scan_dry_run(self._exchange)
            run_report = self._normalize_run_report(
                getattr(self._dryrun_service, "last_run_report", None)
            )
            summary, _ = self._summarize_signals(signals or [], run_report)
            failed = [e for e in run_report if not e.get("ok")]
            for entry in failed:
                self._logger.warning(
                    {
                        "event": "overseas_dryrun_strategy_failed",
                        "market_date": latest_trading_date,
                        "exchange": exchange_value,
                        "strategy": entry.get("strategy"),
                        "error": str(entry.get("error") or "unknown"),
                    }
                )
            if self._journal is not None:
                self._journal.flush_to_file(latest_trading_date)
            done_log = {
                "event": "overseas_dryrun_done",
                "market_date": latest_trading_date,
                "market_date_text": market_date_text,
                "exchange": exchange_value,
                "signals": len(signals or []),
                "summary": summary,
            }
            if regime_label:
                done_log["regime"] = regime_label
            self._logger.info(done_log)
            if self._notification_service:
                await self._notification_service.emit(
                    NotificationCategory.BACKGROUND,
                    NotificationLevel.WARNING if failed else NotificationLevel.INFO,
                    "해외 dry-run 완료 (일부 전략 실패)" if failed else "해외 dry-run 완료",
                    self._format_notification_body(
                        market_date_text, signals or [], run_report, regime_label,
                    ),
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
