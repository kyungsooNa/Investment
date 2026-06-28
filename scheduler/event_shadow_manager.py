"""P2 2-4 event-driven shadow 구독/저널링 관리자.

StrategyScheduler 에서 분리(S-9)된 god class sub-responsibility. 실 주문은 발생시키지
않고, event router 구독 + evaluate_single/evaluate_exit_single 결과를 shadow journal 에
기록하는 역할만 담당한다. 동작은 분리 전과 동일하다.
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable, Dict, List, Optional, TYPE_CHECKING

from repositories.streaming_stock_repo import StreamingType
from services.price_subscription_service import SubscriptionPriority

if TYPE_CHECKING:
    from scheduler.strategy_scheduler import StrategySchedulerConfig


class EventShadowManager:
    """event_driven_shadow 전략의 entry/exit router 구독과 shadow journal 기록을 담당."""

    def __init__(
        self,
        *,
        event_router=None,
        logger=None,
        market_clock=None,
        price_stream_svc=None,
        price_sub_svc=None,
        get_strategy_holdings: Optional[Callable] = None,
        journal=None,
    ):
        self._event_router = event_router
        self._logger = logger
        self._tm = market_clock
        self._price_stream_svc = price_stream_svc
        self._price_sub_svc = price_sub_svc
        self._get_strategy_holdings = get_strategy_holdings
        self._event_shadow_journal = journal
        # strategy_name → 현재 router 에 구독된 종목 set
        self._event_shadow_subscriptions: Dict[str, set[str]] = {}
        # strategy_name → 현재 exit shadow 로 구독된 보유 종목 set (P2 2-4 exit)
        self._exit_shadow_subscriptions: Dict[str, set[str]] = {}

    async def _refresh_event_shadow_subscriptions(self, cfg: StrategySchedulerConfig) -> None:
        """P2 2-4: cfg.event_driven_shadow=True 인 전략의 router 구독을 scan 후 갱신.

        - cfg.event_driven_shadow=False / router 미주입 / shadow journal 미주입 시 no-op.
        - 새 후보 집합 vs 이전 구독 집합을 diff 해 unsubscribe/subscribe.
        - subscribe evaluator wrapper 는 evaluate_single 결과를 shadow journal 에 기록하고
          항상 None 을 반환 (실 주문 미발생 보장).
        """
        if not cfg.event_driven_shadow:
            return
        if self._event_shadow_journal is None:
            return
        if self._event_router is None:
            await self._record_event_shadow_status(
                strategy_name=getattr(cfg.strategy, "name", ""),
                event="subscriptions_skipped",
                details={"reason": "event_router_missing"},
            )
            return

        strategy = cfg.strategy
        name = strategy.name
        try:
            new_codes = set(strategy.current_candidate_codes() or [])
        except Exception as e:
            self._logger.warning(
                f"[Scheduler] {name} current_candidate_codes() 호출 오류: {e}"
            )
            await self._record_event_shadow_status(
                strategy_name=name,
                event="subscriptions_skipped",
                details={"reason": "candidate_codes_error", "error": str(e)},
            )
            return

        old_codes = self._event_shadow_subscriptions.get(name, set())
        to_remove = old_codes - new_codes
        to_add = new_codes - old_codes

        for code in to_remove:
            try:
                self._event_router.unsubscribe(code, name)
            except Exception as e:
                self._logger.warning(
                    f"[Scheduler] {name} router.unsubscribe({code}) 실패: {e}"
                )

        if to_add:
            evaluator = self._build_shadow_evaluator(strategy)
            for code in to_add:
                try:
                    self._event_router.subscribe(
                        code, strategy_name=name, evaluator=evaluator
                    )
                except Exception as e:
                    self._logger.warning(
                        f"[Scheduler] {name} router.subscribe({code}) 실패: {e}"
                    )

        self._event_shadow_subscriptions[name] = new_codes
        await self._sync_event_shadow_price_subscriptions(name, new_codes)
        details = {
            "candidate_count": len(new_codes),
            "added_count": len(to_add),
            "removed_count": len(to_remove),
            "candidate_codes": sorted(new_codes),
            "added_codes": sorted(to_add),
            "removed_codes": sorted(to_remove),
        }
        tick_ingest = self._tick_ingest_snapshot_for(new_codes)
        if tick_ingest is not None:
            details["tick_ingest"] = tick_ingest
        await self._record_event_shadow_status(
            strategy_name=name,
            event="subscriptions_refreshed",
            details=details,
        )

    def _tick_ingest_snapshot_for(self, codes: set[str]) -> Optional[dict]:
        """후보 종목별 tick 처리 카운터 스냅샷 (P2 2-4 shadow no-tick 진단).

        price_stream_service 미주입 또는 스냅샷 메서드 부재 시 None (no-op).
        """
        svc = self._price_stream_svc
        if svc is None:
            return None
        snapshot_fn = getattr(svc, "tick_ingest_stats_snapshot", None)
        if not callable(snapshot_fn):
            return None
        try:
            return snapshot_fn(sorted(codes))
        except Exception as e:
            self._logger.warning(f"[Scheduler] tick_ingest 스냅샷 실패: {e}")
            return None

    async def _sync_event_shadow_price_subscriptions(self, strategy_name: str, codes: set[str],
                                                      category_key: Optional[str] = None) -> None:
        if self._price_sub_svc is None:
            return
        sync_fn = getattr(self._price_sub_svc, "sync_subscriptions", None)
        if not callable(sync_fn):
            return
        try:
            result = sync_fn(
                sorted(codes),
                (category_key or self._event_shadow_category_key(strategy_name)),
                SubscriptionPriority.MEDIUM,
                StreamingType.UNIFIED_PRICE,
            )
            if asyncio.iscoroutine(result):
                await result
        except TypeError:
            try:
                result = sync_fn(
                    sorted(codes),
                    (category_key or self._event_shadow_category_key(strategy_name)),
                    SubscriptionPriority.MEDIUM,
                )
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                self._logger.warning(
                    f"[Scheduler] {strategy_name} event shadow 가격 구독 갱신 실패: {e}"
                )
        except Exception as e:
            self._logger.warning(
                f"[Scheduler] {strategy_name} event shadow 가격 구독 갱신 실패: {e}"
            )

    @staticmethod
    def _event_shadow_category_key(strategy_name: str) -> str:
        return f"event_shadow_{strategy_name}"

    def _build_shadow_evaluator(self, strategy):
        """evaluate_single → shadow journal 기록 → None 반환을 수행하는 wrapper.

        매번 새 wrapper 를 만드는 게 정상이다 (strategy reference 가 closure 에 포함).
        """
        logger = self._logger

        async def _evaluator(code: str, snapshot: dict):
            try:
                signal = await strategy.evaluate_single(code, snapshot)
            except Exception as e:
                logger.warning(
                    f"[EventShadow] evaluate_single 예외 strategy={strategy.name} code={code} err={e}"
                )
                return None
            if signal is None:
                return None
            try:
                payload = signal.model_dump() if hasattr(signal, "model_dump") else dict(signal.__dict__)
            except Exception:
                payload = {"action": getattr(signal, "action", ""), "code": getattr(signal, "code", code)}
            try:
                await self._record_event_shadow_signal(
                    strategy_name=strategy.name,
                    code=code,
                    signal=payload,
                    snapshot=snapshot,
                )
            except Exception as e:
                logger.warning(
                    f"[EventShadow] journal.record 실패 strategy={strategy.name} code={code} err={e}"
                )
            return None  # shadow 는 router 결과로 전파되지 않음 (실 주문 차단)

        return _evaluator

    def _event_shadow_date_str(self) -> str:
        try:
            now = self._tm.get_current_kst_time()
            return now.strftime("%Y%m%d")
        except Exception:
            return time.strftime("%Y%m%d")

    async def _record_event_shadow_signal(
        self,
        *,
        strategy_name: str,
        code: str,
        signal: dict,
        snapshot: dict,
        signal_source: Optional[str] = None,
    ) -> None:
        journal = self._event_shadow_journal
        if journal is None:
            return
        journal.record(
            strategy_name=strategy_name,
            code=code,
            signal=signal,
            snapshot=snapshot,
            signal_source=signal_source,
        )
        await self._flush_event_shadow_journal()

    async def _record_event_shadow_status(
        self,
        *,
        strategy_name: str,
        event: str,
        details: Optional[dict] = None,
    ) -> None:
        journal = self._event_shadow_journal
        if journal is None:
            return
        record_status_fn = getattr(journal, "record_status", None)
        if not callable(record_status_fn):
            return
        record_status_fn(
            strategy_name=strategy_name,
            event=event,
            details=details or {},
        )
        await self._flush_event_shadow_journal()

    async def _flush_event_shadow_journal(self) -> None:
        """journal flush 를 worker thread 로 오프로드 (틱 경로 이벤트 루프 blocking 방지)."""
        flush_fn = getattr(self._event_shadow_journal, "flush_to_file", None)
        if callable(flush_fn):
            await asyncio.to_thread(flush_fn, self._event_shadow_date_str())

    @staticmethod
    def _exit_shadow_category_key(strategy_name: str) -> str:
        return f"event_shadow_exit_{strategy_name}"

    @staticmethod
    def _exit_shadow_subscriber_name(strategy_name: str) -> str:
        # entry shadow 와 같은 종목을 구독해도 router 키가 겹치지 않도록 접미사를 붙인다.
        return f"{strategy_name}__exit"

    async def _refresh_exit_shadow_subscriptions(
        self,
        cfg: StrategySchedulerConfig,
        holdings: Optional[List[dict]] = None,
    ) -> None:
        """P2 2-4 exit: event_driven_shadow 전략의 보유 종목을 손절 shadow 로 router 구독.

        - flag False / router·journal 미주입 시 no-op.
        - 보유 종목 set 변화를 diff 해 unsubscribe. evaluator 는 evaluate_exit_single 결과를
          journal(signal_source="event_shadow_exit")에 기록하고 항상 None 반환(실 주문 미발생).
        - entry shadow 와 구분되는 subscriber name 을 써서 같은 종목 구독이 겹치지 않게 한다.
        - entry gate 와 무관하게 매 사이클 호출되어 보유 종목 변화를 반영한다.
        """
        if not cfg.event_driven_shadow:
            return
        if self._event_router is None or self._event_shadow_journal is None:
            return

        strategy = cfg.strategy
        name = strategy.name
        if holdings is None:
            try:
                holdings = self._get_strategy_holdings(cfg) or []
            except Exception as e:
                self._logger.warning(f"[Scheduler] {name} exit shadow 보유 조회 오류: {e}")
                return

        holdings_by_code: Dict[str, dict] = {}
        for hold in holdings:
            code = str(hold.get("code", "")).strip()
            if code:
                holdings_by_code[code] = hold
        new_codes = set(holdings_by_code)

        sub_name = self._exit_shadow_subscriber_name(name)
        old_codes = self._exit_shadow_subscriptions.get(name, set())

        for code in (old_codes - new_codes):
            try:
                self._event_router.unsubscribe(code, sub_name)
            except Exception as e:
                self._logger.warning(f"[Scheduler] {name} exit shadow unsubscribe({code}) 실패: {e}")

        if new_codes:
            # router.subscribe 는 (code, sub_name) 중복을 evaluator 교체로 처리하므로,
            # 보유 정보를 최신으로 유지하도록 매 사이클 새 evaluator 로 재구독한다.
            evaluator = self._build_exit_shadow_evaluator(strategy, holdings_by_code)
            for code in new_codes:
                try:
                    self._event_router.subscribe(code, strategy_name=sub_name, evaluator=evaluator)
                except Exception as e:
                    self._logger.warning(f"[Scheduler] {name} exit shadow subscribe({code}) 실패: {e}")

        self._exit_shadow_subscriptions[name] = new_codes
        await self._sync_event_shadow_price_subscriptions(
            name, new_codes, category_key=self._exit_shadow_category_key(name)
        )

    def _build_exit_shadow_evaluator(self, strategy, holdings_by_code: Dict[str, dict]):
        """evaluate_exit_single → exit shadow journal 기록 → None 반환 wrapper."""
        logger = self._logger

        async def _evaluator(code: str, snapshot: dict):
            holding = holdings_by_code.get(code)
            if not holding:
                return None
            try:
                signal = await strategy.evaluate_exit_single(code, snapshot, holding)
            except Exception as e:
                logger.warning(
                    f"[EventShadow] evaluate_exit_single 예외 strategy={strategy.name} code={code} err={e}"
                )
                return None
            if signal is None:
                return None
            try:
                payload = signal.model_dump() if hasattr(signal, "model_dump") else dict(signal.__dict__)
            except Exception:
                payload = {"action": getattr(signal, "action", ""), "code": getattr(signal, "code", code)}
            try:
                await self._record_event_shadow_signal(
                    strategy_name=strategy.name,
                    code=code,
                    signal=payload,
                    snapshot=snapshot,
                    signal_source="event_shadow_exit",
                )
            except Exception as e:
                logger.warning(
                    f"[EventShadow] exit journal.record 실패 strategy={strategy.name} code={code} err={e}"
                )
            return None  # shadow 는 router 결과로 전파되지 않음 (실 주문 차단)

        return _evaluator

    async def teardown_strategy(self, name: str) -> None:
        """전략 비활성화 시 entry/exit shadow router 구독을 해제한다 (P2 2-4).

        호출 측(stop_strategy)에서 cfg.event_driven_shadow=True 일 때만 호출한다.
        가격 구독 카테고리(`_price_sub_svc`) 제거는 해당 서비스를 소유한 호출 측에서 수행한다.
        """
        if self._event_router is None:
            return
        for code in self._event_shadow_subscriptions.pop(name, set()):
            try:
                self._event_router.unsubscribe(code, name)
            except Exception as e:
                self._logger.warning(
                    f"[Scheduler] {name} shadow unsubscribe({code}) 실패: {e}"
                )
        exit_sub_name = self._exit_shadow_subscriber_name(name)
        for code in self._exit_shadow_subscriptions.pop(name, set()):
            try:
                self._event_router.unsubscribe(code, exit_sub_name)
            except Exception as e:
                self._logger.warning(
                    f"[Scheduler] {name} exit shadow unsubscribe({code}) 실패: {e}"
                )
