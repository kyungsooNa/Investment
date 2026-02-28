# scheduler/strategy_scheduler.py
from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from interfaces.live_strategy import LiveStrategy
from common.types import TradeSignal, ErrorCode
from managers.virtual_trade_manager import VirtualTradeManager
from services.order_execution_service import OrderExecutionService
from core.time_manager import TimeManager

SIGNAL_HISTORY_FILE = "data/signal_history.csv"
SIGNAL_COLUMNS = ["strategy_name", "code", "name", "action", "price", "reason", "timestamp", "api_success"]
SCHEDULER_STATE_FILE = "data/scheduler_state.json"


@dataclass
class SignalRecord:
    """실행된 시그널 이력 레코드."""
    strategy_name: str
    code: str
    name: str
    action: str          # BUY / SELL
    price: int
    reason: str
    timestamp: str       # ISO format
    api_success: bool = True


@dataclass
class StrategySchedulerConfig:
    """전략별 스케줄링 설정."""
    strategy: LiveStrategy
    interval_minutes: int = 5       # 실행 주기 (분)
    max_positions: int = 3          # 최대 동시 보유 포지션 수
    order_qty: int = 1              # 주문 수량
    enabled: bool = True            # 개별 전략 활성/비활성
    force_exit_on_close: bool = False       # 당일 청산 여부


class StrategyScheduler:
    """asyncio 기반 단일 스레드 전략 스케줄러.

    등록된 전략들을 장중에 주기적으로 실행하고,
    발생한 TradeSignal을 CSV 기록 + API 주문으로 처리한다.
    """

    LOOP_INTERVAL_SEC = 1           # 메인 루프 깨어나는 주기
    MARKET_CLOSED_SLEEP_SEC = 60    # 장 외 시간 sleep
    FORCE_EXIT_MINUTES_BEFORE = 15  # 장 마감 N분 전 강제 청산

    def __init__(
        self,
        virtual_manager: VirtualTradeManager,
        order_execution_service: OrderExecutionService,
        time_manager: TimeManager,
        logger: Optional[logging.Logger] = None,
        dry_run: bool = False,
    ):
        self._vm = virtual_manager
        self._oes = order_execution_service
        self._tm = time_manager
        self._logger = logger or logging.getLogger(__name__)
        self._dry_run = dry_run

        self._strategies: List[StrategySchedulerConfig] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_run: Dict[str, datetime] = {}
        self.MAX_HISTORY = 200  # 최대 보관 이력 수
        self._signal_history: List[SignalRecord] = self._load_signal_history()

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
        for cfg in self._strategies:
            cfg.enabled = True
        self._running = True
        self._task = asyncio.create_task(self._loop())
        self._logger.info("[Scheduler] 시작 (전체 전략 활성화)")

    async def stop(self, save_state: bool = False):
        if save_state:
            self._save_scheduler_state()

        self._running = False
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

        self._logger.info("[Scheduler] 정지 (전체 전략 비활성화)")

    # ── 메인 루프 ──

    async def _loop(self):
        while self._running:
            try:
                now = self._tm.get_current_kst_time()

                if not self._tm.is_market_open(now):
                    await asyncio.sleep(self.MARKET_CLOSED_SLEEP_SEC)
                    continue

                # 장 마감 전 강제 청산 체크
                close_time = self._tm.get_market_close_time()
                minutes_to_close = (close_time - now).total_seconds() / 60

                for cfg in self._strategies:
                    if not cfg.enabled:
                        continue
                    name = cfg.strategy.name
                    last = self._last_run.get(name)
                    elapsed = (now - last).total_seconds() if last else float('inf')

                    # 정규 주기 도래 또는 장 마감 전 강제 실행
                    should_run = elapsed >= cfg.interval_minutes * 60
                    force_exit = False
                    if cfg.force_exit_on_close and (minutes_to_close <= self.FORCE_EXIT_MINUTES_BEFORE):
                        force_exit = True

                    if should_run or force_exit:
                        self._last_run[name] = now
                        try:
                            await self._run_strategy(cfg, force_exit_only=force_exit)
                        except Exception as e:
                            self._logger.error(
                                f"[Scheduler] {name} 실행 오류: {e}", exc_info=True
                            )

                await asyncio.sleep(self.LOOP_INTERVAL_SEC)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error(f"[Scheduler] 루프 오류: {e}", exc_info=True)
                await asyncio.sleep(self.LOOP_INTERVAL_SEC)

    # ── 전략 실행 ──

    async def _run_strategy(self, cfg: StrategySchedulerConfig, force_exit_only: bool = False):
        name = cfg.strategy.name
        self._logger.info(f"[Scheduler] {name} 실행 시작 (force_exit_only={force_exit_only})")

        # 1) 보유 종목 청산 조건 체크
        holdings = self._vm.get_holds_by_strategy(name)
        if holdings:
            sell_signals = await cfg.strategy.check_exits(holdings)
            for sig in sell_signals:
                await self._execute_signal(sig)

        # 2) 새 매수 스캔 (강제 청산 모드가 아닐 때만)
        if not force_exit_only:
            current_holds = len(self._vm.get_holds_by_strategy(name))
            if current_holds >= cfg.max_positions:
                self._logger.info(
                    f"[Scheduler] {name}: 최대 포지션 도달 "
                    f"({current_holds}/{cfg.max_positions}), 스캔 스킵"
                )
                return

            buy_signals = await cfg.strategy.scan()
            remaining = cfg.max_positions - current_holds
            for sig in buy_signals[:remaining]:
                # 전략이 qty를 직접 계산한 경우(>1) 존중, 아니면 config 값 사용
                if sig.qty <= 1:
                    sig.qty = cfg.order_qty
                await self._execute_signal(sig)

        self._logger.info(f"[Scheduler] {name} 실행 완료")

    # ── 시그널 실행 ──

    async def _execute_signal(self, signal: TradeSignal):
        self._logger.info(
            f"[Scheduler] 시그널 실행: {signal.action} {signal.name}({signal.code}) "
            f"@ {signal.price:,}원 | {signal.reason}"
        )

        # 기록용 가격 결정 (시장가 0원인 경우 현재가 조회 시도하여 기록 정확도 향상)
        log_price = signal.price
        if log_price == 0:
            try:
                # OrderExecutionService -> TradingService 접근하여 현재가 조회
                resp = await self._oes.trading_service.get_current_stock_price(signal.code)
                if resp and resp.rt_cd == ErrorCode.SUCCESS.value:
                    data = resp.data
                    output = data.get("output") if isinstance(data, dict) else getattr(data, "output", None)
                    if output:
                        val = output.get("stck_prpr") if isinstance(output, dict) else getattr(output, "stck_prpr", 0)
                        log_price = int(val)
            except Exception:
                pass  # 조회 실패 시 0원으로 기록 유지

        # CSV 기록 (항상)
        if signal.action == "BUY":
            self._vm.log_buy(signal.strategy_name, signal.code, log_price)
        elif signal.action == "SELL":
            self._vm.log_sell_by_strategy(signal.strategy_name, signal.code, log_price)

        api_success = True

        # API 주문 (dry_run이 아닐 때)
        if not self._dry_run:
            try:
                if signal.action == "BUY":
                    resp = await self._oes.handle_place_buy_order(
                        signal.code, signal.price, signal.qty
                    )
                else:
                    resp = await self._oes.handle_place_sell_order(
                        signal.code, signal.price, signal.qty
                    )

                if resp and resp.rt_cd == ErrorCode.SUCCESS.value:
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
            price=signal.price,
            reason=signal.reason,
            timestamp=now.strftime("%Y-%m-%d %H:%M:%S"),
            api_success=api_success,
        )
        self._signal_history.append(record)
        if len(self._signal_history) > self.MAX_HISTORY:
            self._signal_history = self._signal_history[-self.MAX_HISTORY:]
        self._append_signal_csv(record)

    async def _force_liquidate_strategy(self, cfg: StrategySchedulerConfig):
        """전략 중지 시 보유 종목 강제 청산 (force_exit_on_close=True)."""
        name = cfg.strategy.name
        holdings = self._vm.get_holds_by_strategy(name)
        if not holdings:
            return

        self._logger.info(f"[Scheduler] {name} 종료로 인한 강제 청산 실행 (보유 {len(holdings)}건)")

        for hold in holdings:
            code = hold.get("code")
            if not code:
                continue

            stock_name = hold.get("name", code)
            
            # 시장가 매도를 위해 가격 0 설정
            signal = TradeSignal(
                strategy_name=name,
                code=code,
                name=stock_name,
                action="SELL",
                price=0,  # 시장가
                qty=cfg.order_qty,
                reason="전략 종료 강제 청산 (시장가)"
            )
            await self._execute_signal(signal)

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
                return True
        return False

    # ── 상태 조회 ──

    def get_status(self) -> dict:
        strategies = []
        for cfg in self._strategies:
            name = cfg.strategy.name
            last = self._last_run.get(name)
            strategies.append({
                "name": name,
                "interval_minutes": cfg.interval_minutes,
                "max_positions": cfg.max_positions,
                "enabled": cfg.enabled,
                "current_holds": len(self._vm.get_holds_by_strategy(name)),
                "last_run": last.strftime("%H:%M:%S") if last else None,
            })
        return {
            "running": self._running,
            "dry_run": self._dry_run,
            "strategies": strategies,
        }

    # ── CSV 영속화 ──

    def _load_signal_history(self) -> List[SignalRecord]:
        """서버 시작 시 CSV에서 시그널 이력 복원."""
        if not os.path.exists(SIGNAL_HISTORY_FILE):
            return []
        records: List[SignalRecord] = []
        try:
            with open(SIGNAL_HISTORY_FILE, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    records.append(SignalRecord(
                        strategy_name=row["strategy_name"],
                        code=row["code"],
                        name=row["name"],
                        action=row["action"],
                        price=int(row["price"]),
                        reason=row["reason"],
                        timestamp=row["timestamp"],
                        api_success=row.get("api_success", "True") == "True",
                    ))
            # MAX_HISTORY 초과 시 최근 것만 유지
            if len(records) > self.MAX_HISTORY:
                records = records[-self.MAX_HISTORY:]
            self._logger.info(f"[Scheduler] 시그널 이력 {len(records)}건 로드 완료")
        except Exception as e:
            self._logger.error(f"[Scheduler] 시그널 이력 로드 실패: {e}")
            records = []
        return records

    def _save_scheduler_state(self):
        """종료 시 활성 전략 목록을 JSON으로 저장."""
        enabled_names = [
            cfg.strategy.name for cfg in self._strategies if cfg.enabled
        ]
        state = {"running": self._running, "enabled_strategies": enabled_names}
        try:
            with open(SCHEDULER_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
            self._logger.info(f"[Scheduler] 상태 저장 완료: {enabled_names}")
        except Exception as e:
            self._logger.error(f"[Scheduler] 상태 저장 실패: {e}")

    def clear_saved_state(self):
        """저장된 상태 파일 삭제 (수동 정지 시 호출)."""
        if os.path.exists(SCHEDULER_STATE_FILE):
            os.remove(SCHEDULER_STATE_FILE)
            self._logger.info("[Scheduler] 저장된 상태 파일 삭제")

    async def restore_state(self):
        """이전 실행 상태 복원. 활성 전략이 있으면 자동 시작."""
        if not os.path.exists(SCHEDULER_STATE_FILE):
            return
        try:
            with open(SCHEDULER_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            enabled_names = state.get("enabled_strategies", [])
            if not enabled_names:
                return

            # 개별 전략 활성화
            restored = []
            for cfg in self._strategies:
                if cfg.strategy.name in enabled_names:
                    cfg.enabled = True
                    restored.append(cfg.strategy.name)

            if restored:
                self._running = True
                self._task = asyncio.create_task(self._loop())
                self._logger.info(
                    f"[Scheduler] 이전 상태 복원 — 자동 시작: {restored}"
                )
        except Exception as e:
            self._logger.error(f"[Scheduler] 상태 복원 실패: {e}")

    def _append_signal_csv(self, record: SignalRecord):
        """시그널 1건을 CSV에 append."""
        file_exists = os.path.exists(SIGNAL_HISTORY_FILE)
        try:
            with open(SIGNAL_HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=SIGNAL_COLUMNS)
                if not file_exists:
                    writer.writeheader()
                writer.writerow({
                    "strategy_name": record.strategy_name,
                    "code": record.code,
                    "name": record.name,
                    "action": record.action,
                    "price": record.price,
                    "reason": record.reason,
                    "timestamp": record.timestamp,
                    "api_success": record.api_success,
                })
        except Exception as e:
            self._logger.error(f"[Scheduler] 시그널 CSV 저장 실패: {e}")

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
                "reason": r.reason,
                "timestamp": r.timestamp,
                "api_success": r.api_success,
            }
            for r in reversed(records)
        ]
