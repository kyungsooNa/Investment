# scheduler/strategy_scheduler.py
from __future__ import annotations

import asyncio
import json

from repositories.streaming_stock_repo import StreamingType
try:
    import orjson as _orjson
    def _dumps(obj) -> str: return _orjson.dumps(obj).decode('utf-8')
except ImportError:
    _orjson = None
    def _dumps(obj) -> str: return json.dumps(obj, ensure_ascii=False)

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from interfaces.live_strategy import LiveStrategy
from common.types import TradeSignal, ErrorCode, Exchange
from services.market_calendar_service import MarketCalendarService
from services.virtual_trade_service import VirtualTradeService
from services.notification_service import NotificationService, NotificationCategory, NotificationLevel
from services.order_execution_service import OrderExecutionService
from repositories.stock_code_repository import StockCodeRepository
from services.stock_query_service import StockQueryService
from core.market_clock import MarketClock
from core.performance_profiler import PerformanceProfiler

from scheduler.strategy_scheduler_store import StrategySchedulerStore, SCHEDULER_DB_FILE
from services.price_subscription_service import SubscriptionPriority
from core.loggers.trace_context import trace_scope, get_trace_id, new_trace_id
from services.kill_switch_service import KillSwitchService
from core.account_snapshot import AccountSnapshotCache
from services.position_sizing_service import PositionSizingService


@dataclass
class SignalRecord:
    """실행된 시그널 이력 레코드."""
    strategy_name: str
    code: str
    name: str
    action: str          # BUY / SELL
    price: int
    qty: int = 1
    reason: str = ""
    timestamp: str = ""       # ISO format
    api_success: bool = True
    return_rate: Optional[float] = None
    trace_id: str = ""


@dataclass
class StrategySchedulerConfig:
    """전략별 스케줄링 설정."""
    strategy: LiveStrategy
    interval_minutes: int = 5       # 실행 주기 (분)
    max_positions: int = 3          # 최대 동시 보유 포지션 수
    order_qty: int = 1              # 주문 수량
    enabled: bool = True            # 개별 전략 활성/비활성
    allow_pyramiding: bool = False  # 불타기(추가매수) 허용 여부
    force_exit_on_close: bool = False       # 당일 청산 여부


class StrategyScheduler:
    """asyncio 기반 단일 스레드 전략 스케줄러.

    등록된 전략들을 장중에 주기적으로 실행하고,
    발생한 TradeSignal을 CSV 기록 + API 주문으로 처리한다.
    """

    LOOP_INTERVAL_SEC = 1           # 메인 루프 깨어나는 주기
    MARKET_CLOSED_SLEEP_SEC = 60    # 장 외 시간 sleep
    FORCE_EXIT_MINUTES_BEFORE = 30  # 장 마감 N분 전 강제 청산
    STAGGER_INTERVAL_SEC = 60       # 전략 간 실행 시차 (초)
    ORDER_POLL_INTERVAL_SEC = 15    # 활성 주문 체결조회 보정 주기 (초)

    def __init__(
        self,
        virtual_trade_service: VirtualTradeService,
        order_execution_service: OrderExecutionService,
        stock_query_service: StockQueryService,
        stock_code_repository: StockCodeRepository,
        market_clock: MarketClock,
        market_calendar_service: MarketCalendarService,
        logger: Optional[logging.Logger] = None,
        dry_run: bool = False,
        notification_service: Optional[NotificationService] = None,
        performance_profiler: Optional[PerformanceProfiler] = None,
        store: Optional[StrategySchedulerStore] = None,
        price_subscription_service=None,
        kill_switch_service: Optional[KillSwitchService] = None,
        account_snapshot_cache: Optional[AccountSnapshotCache] = None,
        position_sizing_service: Optional[PositionSizingService] = None,
    ):
        self._virtual_trade_service = virtual_trade_service
        self._oes = order_execution_service
        self._sqs = stock_query_service
        self.stock_code_repository = stock_code_repository
        self._tm = market_clock
        self._logger = logger or logging.getLogger(__name__)
        self._dry_run = dry_run
        self._notification_service = notification_service
        self._mcs = market_calendar_service
        self._pm = performance_profiler if performance_profiler else PerformanceProfiler(enabled=False)

        self._store = store or StrategySchedulerStore(db_path=SCHEDULER_DB_FILE, logger=self._logger)
        self._price_sub_svc = price_subscription_service
        self._kill_switch = kill_switch_service
        self._account_snapshot_cache = account_snapshot_cache
        self._position_sizer = position_sizing_service

        self._strategies: List[StrategySchedulerConfig] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._stop_event: asyncio.Event = asyncio.Event()
        self._last_run: Dict[str, datetime] = {}
        self._last_execution_time: Optional[datetime] = None  # 전략 간 실행 쿨다운용
        self._last_order_poll_time: Optional[datetime] = None
        self._force_exit_done: set = set()  # 당일 강제 청산 완료된 전략
        self._reconciled_dates: set = set()  # 원장 대사 완료된 날짜 (YYYY-MM-DD)
        self.MAX_HISTORY = 200  # 최대 보관 이력 수
        self._signal_history: List[SignalRecord] = self._load_signal_history()
        self._subscriber_queues: List[asyncio.Queue] = []

    # ── 전략 등록 ──

    def register(self, config: StrategySchedulerConfig):
        self._strategies.append(config)
        self._logger.info(
            f"[Scheduler] 전략 등록: {config.strategy.name} "
            f"(주기={config.interval_minutes}분, 최대포지션={config.max_positions})"
        )

    # ── 생명주기 ──

    async def start(self):
        if self._running:
            self._logger.warning("[Scheduler] 이미 실행 중")
            return
        if self._kill_switch:
            allowed, reason = await self._kill_switch.check_strategies_allowed()
            if not allowed:
                self._logger.warning(f"[Scheduler] Kill Switch 활성 상태로 시작 — 전략 실행은 차단됨: {reason}")
        for cfg in self._strategies:
            cfg.enabled = True
        self._running = True
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop())
        self._logger.info("[Scheduler] 시작 (전체 전략 활성화)")
        if self._notification_service:
            names = [c.strategy.name for c in self._strategies if c.enabled]
            await self._notification_service.emit(NotificationCategory.SYSTEM, NotificationLevel.INFO, "스케줄러 시작", f"활성 전략: {', '.join(names)}")

    async def stop(self, save_state: bool = False):
        if save_state:
            self._save_scheduler_state()

        self._running = False
        self._stop_event.set()
        for cfg in self._strategies:
            cfg.enabled = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # 상태 저장을 동반한 종료(재시작 등)라면 강제 청산을 하지 않는다.
        # save_state=True -> perform_exit=False (청산 스킵)
        # save_state=False -> perform_exit=True (청산 수행)
        perform_exit = not save_state
        for cfg in self._strategies:
            await self.stop_strategy(cfg.strategy.name, perform_force_exit=perform_exit)

        self.close()
        self._logger.info("[Scheduler] 정지 (전체 전략 비활성화)")
        if self._notification_service:
            await self._notification_service.emit(NotificationCategory.SYSTEM, NotificationLevel.INFO, "스케줄러 정지", "전체 전략 비활성화")

    # ── 메인 루프 ──

    async def _loop(self):
        self._logger.info("스케줄러 메인 루프 시작.")
        while self._running and not self._stop_event.is_set():
            try:
                market_open = await self._mcs.is_market_open_now()

                if not market_open:
                    # 장이 닫힌 직후(15:40~) 아직 강제 청산 미완료된 전략이 있으면 실행
                    if self._force_exit_done is not None:
                        for cfg in self._strategies:
                            if (cfg.enabled and cfg.force_exit_on_close
                                    and cfg.strategy.name not in self._force_exit_done):
                                name = cfg.strategy.name
                                self._force_exit_done.add(name)
                                self._logger.info(
                                    f"[Scheduler] {name}: 장 마감 후 미처리 강제 청산 실행"
                                )
                                try:
                                    await self._run_strategy(cfg, force_exit_only=True)
                                except Exception as e:
                                    self._logger.error(
                                        f"[Scheduler] {name} 강제 청산 오류: {e}", exc_info=True
                                    )

                    self._logger.info("현재는 휴장일이거나 장 운영 시간이 아닙니다.")
                    self._force_exit_done.clear()
                    await self._mcs.wait_until_next_open()
                    continue

                # 장중: 시간 계산
                now = self._tm.get_current_kst_time()
                close_time = self._tm.get_market_close_time()
                minutes_to_close = (close_time - now).total_seconds() / 60
                await self._poll_active_orders_if_due(now)

                # 원장 대사: 당일 첫 장 진입 시 1회 실행
                today_str = now.strftime("%Y-%m-%d")
                if today_str not in self._reconciled_dates:
                    self._reconciled_dates.add(today_str)
                    await self._run_reconciliation()
                in_force_exit_window = minutes_to_close <= self.FORCE_EXIT_MINUTES_BEFORE

                # Kill switch 체크: 트립 시 일반 전략 실행 차단 (force_exit 청산은 유지)
                _ks_allowed = True
                if self._kill_switch:
                    _ks_allowed, _ks_reason = await self._kill_switch.check_strategies_allowed()
                    if not _ks_allowed:
                        self._logger.warning(
                            f"[Scheduler] Kill Switch 활성 ({_ks_reason}) — 일반 전략 실행 차단 (청산은 유지)"
                        )

                # 1. 실행이 필요한 전략들을 수집 (기아 현상 방지를 위해 나중에 우선순위 정렬)
                evaluations = []
                for cfg in self._strategies:
                    if not cfg.enabled:
                        continue
                    name = cfg.strategy.name
                    last = self._last_run.get(name)
                    elapsed = (now - last).total_seconds() if last else float('inf')

                    # 강제 청산: 마감 N분 전이면 즉시 실행 (1회만) — kill switch와 무관하게 허용
                    force_exit = (cfg.force_exit_on_close
                                  and in_force_exit_window
                                  and name not in self._force_exit_done)

                    # 정규 실행: force_exit_on_close 전략은 마감 전 구간에서 새 매수 금지
                    # kill switch 활성 시 신규 전략 실행 차단
                    should_run = (not force_exit
                                  and not (cfg.force_exit_on_close and in_force_exit_window)
                                  and elapsed >= cfg.interval_minutes * 60
                                  and _ks_allowed)

                    if should_run or force_exit:
                        # 지연 시간(초) 계산 - 처음 실행 시(last가 None) 무한대로 처리
                        overdue = elapsed - (cfg.interval_minutes * 60) if last else float('inf')
                        if force_exit:
                            overdue = float('inf') # 강제 청산은 최우선순위
                        evaluations.append((overdue, cfg, force_exit))

                # 2. 가장 오래 지연된(overdue가 큰) 전략부터 내림차순 정렬
                evaluations.sort(key=lambda x: x[0], reverse=True)

                for overdue, cfg, force_exit in evaluations:
                    name = cfg.strategy.name

                    # 전략 간 API 자원 충돌 방지 (강제 청산은 쿨다운 무시)
                    if not force_exit and self._last_execution_time:
                        since_last_exec = (now - self._last_execution_time).total_seconds()
                        if since_last_exec < self.STAGGER_INTERVAL_SEC:
                            continue

                    self._last_run[name] = now
                    if force_exit:
                        self._force_exit_done.add(name)
                        self._logger.info(f"[Scheduler] {name}: 장 마감 {minutes_to_close:.1f}분 전 — 강제 청산 실행")
                    
                    try:
                        await self._run_strategy(cfg, force_exit_only=force_exit)
                    except Exception as e:
                        self._logger.error(f"[Scheduler] {name} 실행 오류: {e}", exc_info=True)
                    finally:
                        # 3. 전략 실행이 끝난 이후 시점을 기준으로 쿨다운 타이머를 갱신하여 
                        # 실행 시간이 긴 전략 이후에도 확실하게 60초의 휴지기 보장
                        if not force_exit:
                            self._last_execution_time = self._tm.get_current_kst_time()

                await asyncio.sleep(self.LOOP_INTERVAL_SEC)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"[Scheduler] 루프 오류: {e}", exc_info=True)
                if self._notification_service:
                    await self._notification_service.emit(NotificationCategory.SYSTEM, NotificationLevel.ERROR, "스케줄러 루프 오류", str(e))
                await asyncio.sleep(self.LOOP_INTERVAL_SEC)

    # ── 전략 실행 ──

    async def _poll_active_orders_if_due(self, now: Optional[datetime] = None) -> int:
        """기존 스케줄러 루프에서 활성 주문 상태를 주기적으로 보정합니다."""
        if self._dry_run or not hasattr(self._oes, "poll_active_orders_once"):
            return 0

        now = now or self._tm.get_current_kst_time()
        poll_interval_sec = self.ORDER_POLL_INTERVAL_SEC
        if hasattr(self._oes, "get_active_order_poll_interval_sec"):
            poll_interval_hint = self._oes.get_active_order_poll_interval_sec(
                now,
                default_interval_sec=self.ORDER_POLL_INTERVAL_SEC,
            )
            if poll_interval_hint is None:
                return 0
            poll_interval_sec = poll_interval_hint

        if self._last_order_poll_time is not None:
            elapsed = (now - self._last_order_poll_time).total_seconds()
            if elapsed < poll_interval_sec:
                return 0

        self._last_order_poll_time = now
        try:
            applied_count = await self._oes.poll_active_orders_once()
            if hasattr(self._oes, "check_stuck_orders_once"):
                await self._oes.check_stuck_orders_once(now)
            if applied_count:
                self._logger.info(f"[Scheduler] 활성 주문 polling 보정: {applied_count}건")
            return applied_count
        except Exception as e:
            self._logger.warning(f"[Scheduler] 활성 주문 polling 실패: {e}", exc_info=True)
            return 0

    async def _run_strategy(self, cfg: StrategySchedulerConfig, force_exit_only: bool = False):
        name = cfg.strategy.name
        t_run = self._pm.start_timer()
        self._logger.info(f"[Scheduler] {name} 실행 시작 (force_exit_only={force_exit_only})")

        # 강제 청산 모드: 전략의 check_exits 로직 무시, 보유 종목 전량 시장가 매도
        if force_exit_only:
            await self._force_liquidate_strategy(cfg)
            self._pm.log_timer(f"{name}.run_strategy(force_exit)", t_run)
            return

        # 1) 보유 종목 청산 조건 체크
        holdings = self._get_strategy_holdings(cfg)
        if holdings:
            t_exit = self._pm.start_timer()
            sell_signals = await cfg.strategy.check_exits(holdings)
            self._pm.log_timer(f"{name}.check_exits({len(holdings)}건)", t_exit)
            if sell_signals:
                tasks = [self._execute_signal(sig) for sig in sell_signals]
                for f in asyncio.as_completed(tasks):
                    await f

        # 2) 새 매수 스캔
        current_holdings = self._get_strategy_holdings(cfg)
        current_holds_count = len(current_holdings)

        if current_holds_count >= cfg.max_positions:
            self._logger.info(
                f"[Scheduler] {name}: 최대 포지션 도달 "
                f"({current_holds_count}/{cfg.max_positions}), 스캔 스킵"
            )
            self._pm.log_timer(f"{name}.run_strategy", t_run)
            return

        t_scan = self._pm.start_timer()
        buy_signals = await cfg.strategy.scan()
        self._pm.log_timer(f"{name}.scan()", t_scan)

        # 이미 보유 중인 종목은 추가 매수(불타기) 방지
        if cfg.allow_pyramiding:
            valid_signals = buy_signals
        else:
            holding_codes = {str(h.get('code')) for h in current_holdings if h.get('code')}
            valid_signals = [s for s in buy_signals if str(s.code) not in holding_codes]

        remaining = cfg.max_positions - current_holds_count
        target_signals = valid_signals[:remaining]

        if target_signals:
            for sig in target_signals:
                if sig.qty <= 1:
                    sig.qty = cfg.order_qty

            # target_signals는 remaining 기준으로 이미 슬라이싱됨 → 병렬 실행 안전
            await asyncio.gather(
                *[self._execute_signal(sig) for sig in target_signals],
                return_exceptions=True,
            )

        self._pm.log_timer(f"{name}.run_strategy", t_run)
        self._logger.info(f"[Scheduler] {name} 실행 완료")

    # ── 시그널 실행 ──

    async def _execute_signal(self, signal: TradeSignal):
        tid = get_trace_id() or new_trace_id(signal.strategy_name)
        with trace_scope(tid):
            await self._execute_signal_inner(signal, tid)

    async def _execute_signal_inner(self, signal: TradeSignal, tid: str):
        self._logger.info(
            f"[Scheduler] 시그널 실행: [{signal.strategy_name}] {signal.action} {signal.name}({signal.code}) "
            f"@ {signal.price:,}원 | {signal.reason}"
        )

        # 기록용 가격 결정 (시장가 0원인 경우 현재가 조회 시도하여 기록 정확도 향상)
        log_price = signal.price
        if log_price == 0:
            try:
                # StockQueryService를 통해 현재가 조회
                resp = await self._sqs.get_current_price(signal.code, caller="StrategyScheduler")
                if resp and resp.rt_cd == ErrorCode.SUCCESS.value:
                    data = resp.data
                    output = data.get("output") if isinstance(data, dict) else getattr(data, "output", None)
                    if output:
                        val = output.get("stck_prpr") if isinstance(output, dict) else getattr(output, "stck_prpr", 0)
                        log_price = int(val)
            except Exception:
                pass  # 조회 실패 시 0원으로 기록 유지

        # 종목명 보정 (이름이 비어있거나, 종목 코드와 동일하게 들어온 경우)
        if not signal.name or signal.name == signal.code:
            signal.name = self.stock_code_repository.get_name_by_code(signal.code) or signal.code

        return_rate = None
        category_key = f"scheduler_{signal.strategy_name}"
        api_success = True
        resp = None

        if self._dry_run:
            if signal.action == "BUY":
                await self._virtual_trade_service.log_buy_async(signal.strategy_name, signal.code, log_price, signal.qty)
                if self._price_sub_svc:
                    await self._price_sub_svc.add_subscription(signal.code, SubscriptionPriority.HIGH, category_key, StreamingType.UNIFIED_PRICE)
            elif signal.action == "SELL":
                return_rate = await self._virtual_trade_service.log_sell_by_strategy_async(signal.strategy_name, signal.code, log_price, signal.qty)
                if self._price_sub_svc:
                    await self._price_sub_svc.remove_subscription(signal.code, category_key)
        else:
            try:
                try:
                    signal_exchange = Exchange(signal.exchange) if signal.exchange else Exchange.KRX
                except ValueError:
                    signal_exchange = Exchange.KRX
                if signal.action == "BUY":
                    # 포지션 사이징 보정
                    buy_qty = signal.qty
                    if self._position_sizer is not None:
                        buy_qty, sizing_reason = await self._position_sizer.adjust_buy_qty(
                            signal, signal_exchange
                        )
                        if buy_qty == 0:
                            self._logger.warning(
                                f"[Scheduler] 포지션 사이징 결과 qty=0, 주문 skip: "
                                f"{signal.code} reason={sizing_reason}"
                            )
                            _skip_now = self._tm.get_current_kst_time()
                            _skip_record = SignalRecord(
                                strategy_name=signal.strategy_name,
                                code=signal.code,
                                name=signal.name,
                                action=signal.action,
                                price=signal.price,
                                qty=0,
                                reason=f"sizing_skip:{sizing_reason}",
                                timestamp=_skip_now.strftime("%Y-%m-%d %H:%M:%S"),
                                api_success=False,
                                trace_id=tid,
                            )
                            self._signal_history.append(_skip_record)
                            if len(self._signal_history) > self.MAX_HISTORY:
                                self._signal_history = self._signal_history[-self.MAX_HISTORY:]
                            await self._append_signal_db(_skip_record)
                            await self._notify_subscribers(_skip_record)
                            return

                    resp = await self._oes.handle_place_buy_order(
                        signal.code,
                        signal.price,
                        buy_qty,
                        exchange=signal_exchange,
                        source=f"strategy:{signal.strategy_name}",
                        finalize_immediately=False,
                        trace_id=tid,
                    )
                else:
                    resp = await self._oes.handle_place_sell_order(
                        signal.code,
                        signal.price,
                        signal.qty,
                        exchange=signal_exchange,
                        source=f"strategy:{signal.strategy_name}",
                        finalize_immediately=False,
                        trace_id=tid,
                    )

                if resp and resp.rt_cd == ErrorCode.SUCCESS.value:
                    if signal.action == "BUY":
                        if self._price_sub_svc:
                            await self._price_sub_svc.add_subscription(signal.code, SubscriptionPriority.HIGH, category_key, StreamingType.UNIFIED_PRICE)
                    else:
                        if self._price_sub_svc:
                            await self._price_sub_svc.remove_subscription(signal.code, category_key)
                    self._logger.info(
                        f"[Scheduler] API 주문 성공: {signal.action} {signal.code}"
                    )
                else:
                    api_success = False
                    msg = resp.msg1 if resp else "응답 없음"
                    self._logger.warning(
                        f"[Scheduler] API 주문 실패: {signal.action} {signal.code} - {msg} "
                        f"(CSV는 기록됨)"
                    )
            except Exception as e:
                api_success = False
                self._logger.error(
                    f"[Scheduler] API 주문 예외: {signal.action} {signal.code} - {e} "
                    f"(CSV는 기록됨)"
                )

        # 시그널 이력 기록 (메모리 + CSV 영속화)
        now = self._tm.get_current_kst_time()
        record = SignalRecord(
            strategy_name=signal.strategy_name,
            code=signal.code,
            name=signal.name,
            action=signal.action,
            price=log_price,
            qty=signal.qty,
            reason=signal.reason,
            timestamp=now.strftime("%Y-%m-%d %H:%M:%S"),
            api_success=api_success,
            return_rate=return_rate,
            trace_id=tid,
        )
        self._signal_history.append(record)
        if len(self._signal_history) > self.MAX_HISTORY:
            self._signal_history = self._signal_history[-self.MAX_HISTORY:]
        await self._append_signal_db(record)
        await self._notify_subscribers(record)

        if self._notification_service:
            action_kr = "매수" if signal.action == "BUY" else "매도"
            level = NotificationLevel.CRITICAL if api_success else NotificationLevel.ERROR
            title = f"[{signal.strategy_name}] {signal.name} {action_kr} {'성공' if api_success else '실패'}"
            msg = (f"종목: {signal.name}({signal.code})\n"
                   f"주문: {log_price:,}원 × {signal.qty}주\n"
                   f"사유: {signal.reason}")
            if not api_success:
                title = f"[{signal.strategy_name}] {signal.name} {action_kr} 실패"
            await self._notification_service.emit(NotificationCategory.STRATEGY, level, title, msg, metadata={
                "strategy_name": signal.strategy_name,
                "code": signal.code,
                "action": signal.action,
                "price": log_price,
                "qty": signal.qty,
                "reason": signal.reason,
                "api_success": api_success,
                "return_rate": return_rate,
                "trace_id": tid,
            })

    async def _force_liquidate_strategy(self, cfg: StrategySchedulerConfig):
        """전략 중지 시 보유 종목 강제 청산 (force_exit_on_close=True)."""
        name = cfg.strategy.name
        holdings = self._get_strategy_holdings(cfg)
        if not holdings:
            return

        self._logger.info(f"[Scheduler] {name} 종료로 인한 강제 청산 실행 (보유 {len(holdings)}건)")

        for hold in holdings:
            code = hold.get("code")
            if not code:
                continue

            stock_name = hold.get("name", code)

            holding_qty = int(hold.get("qty") or 0)
            if holding_qty <= 0:
                holding_qty = cfg.order_qty

            # 최우선매수호가(bidp1) 조회 → 지정가 청산, 실패 시 시장가 fallback
            sell_price = 0
            reason = "전략 종료 강제 청산 (시장가)"
            try:
                resp = await self._oes.broker_api_wrapper.get_asking_price(code)
                if resp and resp.rt_cd == ErrorCode.SUCCESS.value:
                    best_bid = int(resp.data.get("bidp1", 0) or 0)
                    if best_bid > 0:
                        sell_price = best_bid
                        reason = f"전략 종료 강제 청산 (지정가 {best_bid:,}원)"
            except Exception as e:
                self._logger.warning(f"[Scheduler] {code} 호가 조회 실패, 시장가로 청산: {e}")

            signal = TradeSignal(
                strategy_name=name,
                code=code,
                name=stock_name,
                action="SELL",
                price=sell_price,
                qty=holding_qty,
                reason=reason,
            )
            await self._execute_signal(signal)

    # ── 원장 대사 ──

    async def _run_reconciliation(self):
        """실제 증권사 잔고와 로컬 DB를 비교하여 불일치를 처리한다 (장 시작 시 1회)."""
        # 계좌 스냅샷 갱신 — 장 시작 직후 1회, 이후 포지션 사이징에서 캐시 사용
        if self._account_snapshot_cache is not None:
            try:
                await self._account_snapshot_cache.warm_up()
            except Exception as _e:
                self._logger.warning(f"[Scheduler] 계좌 스냅샷 warm_up 실패: {_e}")
        try:
            resp = await self._oes.broker_api_wrapper.get_account_balance()
            if not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
                self._logger.warning("[Reconciliation] 잔고 조회 실패, 대사 생략")
                return
            holdings = (resp.data or {}).get("output1") or []
            result = await self._virtual_trade_service.reconcile_with_broker(
                holdings, logger=self._logger
            )
            if result["force_closed"] or result["unknown_in_broker"]:
                msg = (
                    f"강제종결: {result['force_closed']}, "
                    f"미등록: {result['unknown_in_broker']}"
                )
                if self._notification_service:
                    await self._notification_service.emit(
                        NotificationCategory.SYSTEM, NotificationLevel.WARNING,
                        "원장 대사 불일치", msg,
                    )
        except Exception as e:
            self._logger.error(f"[Reconciliation] 대사 실패: {e}", exc_info=True)

    # ── 개별 전략 제어 ──

    async def start_strategy(self, name: str) -> bool:
        """개별 전략 활성화. 루프가 돌고 있지 않으면 자동 시작. 성공 시 True 반환."""
        for cfg in self._strategies:
            if cfg.strategy.name == name:
                cfg.enabled = True
                self._logger.info(f"[Scheduler] 전략 활성화: {name}")
                # 루프가 안 돌고 있으면 자동으로 시작
                if not self._running:
                    self._running = True
                    self._task = asyncio.create_task(self._loop())
                    self._logger.info("[Scheduler] 루프 자동 시작 (개별 전략 활성화)")
                return True
        return False

    async def stop_strategy(self, name: str, perform_force_exit: bool = True) -> bool:
        """개별 전략 비활성화. 성공 시 True 반환."""
        for cfg in self._strategies:
            if cfg.strategy.name == name:
                if perform_force_exit and cfg.enabled and cfg.force_exit_on_close:
                    await self._force_liquidate_strategy(cfg)

                cfg.enabled = False
                self._logger.info(f"[Scheduler] 전략 비활성화: {name}")

                if self._price_sub_svc:
                    await self._price_sub_svc.remove_category(f"scheduler_{name}")

                return True
        return False

    async def update_max_positions(self, name: str, new_max: int) -> bool:
        """개별 전략의 최대 포지션 수를 동적으로 변경하고 상태를 저장합니다."""
        if new_max < 1:
            return False
        for cfg in self._strategies:
            if cfg.strategy.name == name:
                cfg.max_positions = new_max
                self._logger.info(f"[Scheduler] '{name}' 전략 최대 포지션 수 변경: {new_max}")
                self._save_scheduler_state()
                return True
        return False

    def _get_signal_net_qty(self, strategy_name: str, code: str, *, only_success: bool = True) -> int:
        """신호 이력 기준으로 전략별 순수량을 추정한다."""
        net_qty = 0
        target_code = str(code)
        for record in self._signal_history:
            if record.strategy_name != strategy_name or str(record.code) != target_code:
                continue
            if only_success and not record.api_success:
                continue
            qty = int(record.qty or 0)
            if record.action == "BUY":
                net_qty += qty
            elif record.action == "SELL":
                net_qty -= qty
        return net_qty

    def _get_latest_open_buy_record(
        self,
        strategy_name: str,
        code: str,
        *,
        only_success: bool = True,
    ) -> Optional[SignalRecord]:
        """현재 미청산 포지션에 대응하는 가장 최근 BUY 신호를 찾는다."""
        remaining_sell_qty = 0
        target_code = str(code)
        for record in reversed(self._signal_history):
            if record.strategy_name != strategy_name or str(record.code) != target_code:
                continue
            if only_success and not record.api_success:
                continue
            qty = int(record.qty or 0)
            if record.action == "SELL":
                remaining_sell_qty += qty
                continue
            if record.action != "BUY":
                continue
            if remaining_sell_qty >= qty:
                remaining_sell_qty -= qty
                continue
            return record
        return None

    @staticmethod
    def _is_valid_strategy_code(code: str) -> bool:
        """전략 state에 남은 비정상 코드값을 걸러낸다."""
        return code.isdigit() and len(code) == 6

    def _get_strategy_position_state(self, strategy: LiveStrategy) -> Dict[str, object]:
        """전략이 자체 관리하는 position_state를 반환한다."""
        state = getattr(strategy, "_position_state", None)
        return state if isinstance(state, dict) else {}

    def _persist_strategy_position_state(self, strategy: LiveStrategy):
        """전략이 제공하는 state 저장 함수를 통해 position_state를 즉시 반영한다."""
        save_state = getattr(strategy, "_save_state", None)
        if not callable(save_state):
            return
        try:
            save_state()
        except Exception as e:
            self._logger.warning(f"[Scheduler] 전략 state 저장 실패: {strategy.name} - {e}")

    def _has_open_position_evidence(
        self,
        strategy_name: str,
        code: str,
        *,
        repo_holdings: Optional[List[dict]] = None,
    ) -> bool:
        """가상매매 DB 또는 시그널 이력에 현재 포지션 근거가 남아 있는지 확인한다."""
        target_code = str(code).strip()
        holdings = repo_holdings
        if holdings is None:
            holdings = self._virtual_trade_service.get_holds_by_strategy(strategy_name) or []

        for hold in holdings:
            if str(hold.get("code", "")).strip() == target_code:
                return True

        return (
            self._get_signal_net_qty(strategy_name, target_code, only_success=True) > 0
            or self._get_signal_net_qty(strategy_name, target_code, only_success=False) > 0
        )

    def _prune_disabled_force_exit_state(
        self,
        cfg: StrategySchedulerConfig,
        *,
        repo_holdings: Optional[List[dict]] = None,
    ) -> bool:
        """비활성 force-exit 전략에 남은 stale position_state를 정리한다."""
        if cfg.enabled or not cfg.force_exit_on_close:
            return False

        position_state = self._get_strategy_position_state(cfg.strategy)
        if not position_state:
            return False

        stale_codes: List[str] = []
        for raw_code in list(position_state.keys()):
            norm_code = str(raw_code).strip()
            if not norm_code or not self._is_valid_strategy_code(norm_code):
                stale_codes.append(raw_code)
                continue
            if self._has_open_position_evidence(
                cfg.strategy.name,
                norm_code,
                repo_holdings=repo_holdings,
            ):
                continue
            stale_codes.append(raw_code)

        if not stale_codes:
            return False

        for raw_code in stale_codes:
            position_state.pop(raw_code, None)

        self._persist_strategy_position_state(cfg.strategy)
        self._logger.warning(
            f"[Scheduler] stale position_state cleared: strategy={cfg.strategy.name}, codes={stale_codes}"
        )
        return True

    def _build_strategy_state_holding(
        self,
        strategy_name: str,
        code: str,
        state: object,
        existing: Optional[dict] = None,
    ) -> dict:
        """전략 position_state를 scheduler holding 포맷으로 맞춘다."""
        holding = dict(existing or {})
        holding["strategy"] = strategy_name
        holding["code"] = code

        if not holding.get("buy_price"):
            entry_price = getattr(state, "entry_price", None)
            if entry_price is not None:
                holding["buy_price"] = entry_price

        latest_buy = self._get_latest_open_buy_record(strategy_name, code, only_success=True)
        if latest_buy is None:
            latest_buy = self._get_latest_open_buy_record(strategy_name, code, only_success=False)

        if latest_buy is not None:
            if not holding.get("buy_price"):
                holding["buy_price"] = latest_buy.price
            if not holding.get("buy_date") and latest_buy.timestamp:
                holding["buy_date"] = latest_buy.timestamp
            if not holding.get("name") and latest_buy.name:
                holding["name"] = latest_buy.name

        if not holding.get("qty"):
            qty = self._get_signal_net_qty(strategy_name, code, only_success=True)
            if qty <= 0:
                qty = self._get_signal_net_qty(strategy_name, code, only_success=False)
            holding["qty"] = qty if qty > 0 else 1

        if not holding.get("buy_date"):
            entry_date = str(getattr(state, "entry_date", "") or "")
            if len(entry_date) == 8 and entry_date.isdigit():
                holding["buy_date"] = f"{entry_date[:4]}-{entry_date[4:6]}-{entry_date[6:8]} 00:00:00"
            elif entry_date:
                holding["buy_date"] = entry_date

        holding["status"] = "HOLD"
        if not holding.get("name"):
            holding["name"] = self.stock_code_repository.get_name_by_code(code) or code

        return holding

    def _get_strategy_holdings(self, cfg: StrategySchedulerConfig) -> List[dict]:
        """가상매매 DB와 전략 내부 position_state를 병합한 보유 목록."""
        strategy_name = cfg.strategy.name
        merged: Dict[str, dict] = {}

        repo_holdings = self._virtual_trade_service.get_holds_by_strategy(strategy_name) or []
        for hold in repo_holdings:
            code = str(hold.get("code", "")).strip()
            if code:
                merged[code] = dict(hold)

        self._prune_disabled_force_exit_state(cfg, repo_holdings=repo_holdings)

        for code, state in list(self._get_strategy_position_state(cfg.strategy).items()):
            norm_code = str(code).strip()
            if not norm_code:
                continue
            if not self._is_valid_strategy_code(norm_code):
                self._logger.warning(
                    f"[Scheduler] invalid position_state code ignored: strategy={strategy_name}, code={norm_code}"
                )
                continue
            merged[norm_code] = self._build_strategy_state_holding(
                strategy_name,
                norm_code,
                state,
                existing=merged.get(norm_code),
            )

        return list(merged.values())

    def _get_all_current_positions(self) -> List[dict]:
        """등록된 전략 전체 보유 목록을 합친다."""
        positions: List[dict] = []
        for cfg in self._strategies:
            positions.extend(self._get_strategy_holdings(cfg))
        return positions

    # ── 상태 조회 ──

    def get_status(self) -> dict:
        strategies = []
        for cfg in self._strategies:
            name = cfg.strategy.name
            last = self._last_run.get(name)
            holdings = self._get_strategy_holdings(cfg)
            strategies.append({
                "name": name,
                "interval_minutes": cfg.interval_minutes,
                "max_positions": cfg.max_positions,
                "enabled": cfg.enabled,
                "current_holds": len(holdings),
                "holdings": holdings,  # 상세 보유 내역 추가
                "last_run": last.strftime("%H:%M:%S") if last else None,
            })
        return {
            "running": self._running,
            "dry_run": self._dry_run,
            "strategies": strategies,
        }

    # ── DB 영속화 ──

    def close(self):
        """DB 연결을 닫습니다."""
        self._store.close()

    def _load_signal_history(self) -> List[SignalRecord]:
        """DB에서 시그널 이력 복원."""
        try:
            records_data = self._store.load_signal_history(limit=self.MAX_HISTORY)
            records = [
                SignalRecord(
                    strategy_name=d["strategy_name"],
                    code=d["code"],
                    name=d["name"],
                    action=d["action"],
                    price=d["price"],
                    qty=d["qty"],
                    return_rate=d["return_rate"],
                    reason=d["reason"],
                    timestamp=d["timestamp"],
                    api_success=d["api_success"],
                )
                for d in records_data
            ]
            self._logger.info(f"[Scheduler] 시그널 이력 {len(records)}건 로드 완료")
            return records
        except Exception as e:
            self._logger.error(f"[Scheduler] 시그널 이력 로드 실패: {e}")
            return []

    def _save_scheduler_state(self):
        """활성 전략 목록 및 설정을 DB에 저장."""
        enabled_names = [cfg.strategy.name for cfg in self._strategies if cfg.enabled]
        current_positions = self._get_all_current_positions()
        state = {
            "running": self._running,
            "enabled_strategies": enabled_names,
            "current_positions": current_positions,
            "strategy_configs": {
                cfg.strategy.name: {"max_positions": cfg.max_positions}
                for cfg in self._strategies
            },
        }
        try:
            self._store.save_state(state)
            self._logger.info(
                f"[Scheduler] 상태 저장 완료: {enabled_names}, 보유종목 {len(current_positions)}건"
            )
        except Exception as e:
            self._logger.error(f"[Scheduler] 상태 저장 실패: {e}")

    def clear_saved_state(self):
        """저장된 상태 삭제 (수동 정지 시 호출)."""
        try:
            self._store.clear_state()
            self._logger.info("[Scheduler] 저장된 상태 삭제")
        except Exception as e:
            self._logger.error(f"[Scheduler] 상태 삭제 실패: {e}")

    async def restore_state(self):
        """이전 실행 상태 복원. 활성 전략이 있으면 자동 시작."""
        try:
            state = self._store.load_state()
        except Exception as e:
            self._logger.error(f"[Scheduler] 상태 복원 파일 읽기 실패: {e}")
            return
        if not state:
            return

        try:
            enabled_names = state.get("enabled_strategies", [])
            saved_positions = state.get("current_positions", [])
            strategy_configs = state.get("strategy_configs", {})
            stale_state_cleared = False

            if saved_positions:
                self._logger.info(
                    f"[Scheduler] 이전 상태 파일에 저장된 보유 포지션: {len(saved_positions)}건"
                )

            restored = []
            for cfg in self._strategies:
                if cfg.strategy.name in strategy_configs:
                    cfg.max_positions = strategy_configs[cfg.strategy.name].get(
                        "max_positions", cfg.max_positions
                    )
                if cfg.strategy.name in enabled_names:
                    cfg.enabled = True
                    restored.append(cfg.strategy.name)
                elif self._prune_disabled_force_exit_state(cfg):
                    stale_state_cleared = True

            if restored:
                self._running = True
                self._task = asyncio.create_task(self._loop())
                self._logger.info(f"[Scheduler] 이전 상태 복원 — 자동 시작: {restored}")

            if stale_state_cleared:
                self._save_scheduler_state()

            # 복원된 전략의 가상 보유 종목을 스트리밍 구독에 재등록
            if self._price_sub_svc and restored:
                for cfg in self._strategies:
                    if cfg.strategy.name not in restored:
                        continue
                    name = cfg.strategy.name
                    holdings = self._get_strategy_holdings(cfg)
                    for hold in holdings:
                        code = hold.get("code")
                        if code:
                            await self._price_sub_svc.add_subscription(
                                code, SubscriptionPriority.HIGH, f"scheduler_{name}", StreamingType.UNIFIED_PRICE
                            )
                    if holdings:
                        self._logger.info(
                            f"[Scheduler] '{name}' 보유 종목 스트리밍 구독 복원: "
                            f"{[h.get('code') for h in holdings]}"
                        )

        except Exception as e:
            self._logger.error(f"[Scheduler] 상태 복원 실패: {e}")

    async def _append_signal_db(self, record: SignalRecord):
        """시그널 1건을 DB에 비동기 삽입."""
        try:
            await asyncio.to_thread(self._store.append_signal, record)
        except Exception as e:
            self._logger.error(f"[Scheduler] 시그널 DB 저장 실패: {e}")

    def get_signal_history(self, strategy_name: str = None) -> list:
        """시그널 실행 이력 반환. strategy_name 지정 시 해당 전략만 필터."""
        records = self._signal_history
        if strategy_name:
            records = [r for r in records if r.strategy_name == strategy_name]
        # 최신순으로 반환
        return [
            {
                "strategy_name": r.strategy_name,
                "code": r.code,
                "name": r.name,
                "action": r.action,
                "price": r.price,
                "qty": r.qty,
                "return_rate": r.return_rate,
                "reason": r.reason,
                "timestamp": r.timestamp,
                "api_success": r.api_success,
            }
            for r in reversed(records)
        ]

    # ── SSE 구독자 관리 ──

    def create_subscriber_queue(self) -> asyncio.Queue:
        """SSE 클라이언트용 큐 생성 및 등록."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscriber_queues.append(queue)
        return queue

    def remove_subscriber_queue(self, queue: asyncio.Queue):
        """SSE 클라이언트 연결 해제 시 큐 제거."""
        if queue in self._subscriber_queues:
            self._subscriber_queues.remove(queue)

    async def _notify_subscribers(self, record: SignalRecord):
        """새 시그널을 모든 SSE 구독자에게 전파."""
        json_data = _dumps({
            "strategy_name": record.strategy_name,
            "code": record.code,
            "name": record.name,
            "action": record.action,
            "price": record.price,
            "return_rate": record.return_rate,
            "reason": record.reason,
            "timestamp": record.timestamp,
            "api_success": record.api_success,
        })
        for queue in list(self._subscriber_queues):
            try:
                queue.put_nowait(json_data)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(json_data)
                except Exception:
                    pass
