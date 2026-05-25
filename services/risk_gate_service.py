import asyncio
import inspect
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Awaitable, Callable, Optional, Protocol, TYPE_CHECKING, Union

from common.operator_alert_types import AlertSource
from common.strategy_identity import STRATEGY_IDENTITY_RESOLVER
from common.types import ErrorCode, Exchange, OrderSide, ResCommonResponse
from config.config_loader import RiskGateConfig
from core.account_snapshot import AccountSnapshotCache
from services.kill_switch_service import KillSwitchService

if TYPE_CHECKING:
    from services.operator_alert_service import OperatorAlertService


class StrategyRiskDataProvider(Protocol):
    def is_holding(self, strategy_name: str, code: str) -> bool:
        ...

    def get_holds_by_strategy(self, strategy_name: str) -> list[dict]:
        ...

    def get_strategy_return_history(self, strategy_name: str) -> list[dict]:
        ...


@dataclass
class RiskGateDecision:
    rule: str
    reason: str
    severity: str = "block"
    gate: str = "risk_gate"
    error_code: ErrorCode = ErrorCode.RISK_GATE_BLOCKED
    context: dict[str, Any] = field(default_factory=dict)

    def to_response(self) -> ResCommonResponse:
        data = {
            "gate": self.gate,
            "rule": self.rule,
            "severity": self.severity,
            "reason": self.reason,
        }
        data.update(self.context)
        return ResCommonResponse(
            rt_cd=self.error_code.value,
            msg1=f"Risk Gate 차단: {self.reason}",
            data=data,
        )


class RiskGateService:
    """Common hard-block checks that must pass before a broker order submit."""

    def __init__(
        self,
        config: RiskGateConfig,
        kill_switch_service: Optional[KillSwitchService],
        account_snapshot_cache: Optional[AccountSnapshotCache],
        strategy_risk_provider: Optional[StrategyRiskDataProvider] = None,
        logger: Optional[logging.Logger] = None,
        env: Optional[Any] = None,
        operator_alert_service: Optional["OperatorAlertService"] = None,
        market_buy_reference_price_provider: Optional[
            Callable[[str, Exchange], Union[Optional[int], Awaitable[Optional[int]]]]
        ] = None,
    ):
        self._cfg = config
        self._kill_switch = kill_switch_service
        self._account_snapshot_cache = account_snapshot_cache
        self._strategy_risk_provider = strategy_risk_provider
        self._logger = logger or logging.getLogger(__name__)
        self._env = env
        self._operator_alert: Optional["OperatorAlertService"] = operator_alert_service
        self._market_buy_reference_price_provider = market_buy_reference_price_provider
        self._daily_total: dict[date, int] = defaultdict(int)
        self._strategy_resolver = STRATEGY_IDENTITY_RESOLVER

    async def validate_order(
        self,
        stock_code: str,
        price: int,
        qty: int,
        side: OrderSide,
        exchange: Exchange,
        active_order_count: int,
        source: str = "",
        strategy_name: Optional[str] = None,
        invalidation_price: Optional[float] = None,
        stop_loss_price: Optional[float] = None,
    ) -> Optional[ResCommonResponse]:
        """Return None when allowed, otherwise a blocking response."""
        if not self._cfg.enabled:
            return None

        strategy_name = strategy_name or self._strategy_name_from_source(source)
        # downstream consumer (kill_switch, virtual_trade_provider, config lookup)
        # 전체에서 strategy_id 로 일관되게 사용하도록 진입 시 한 번 정규화.
        if strategy_name:
            strategy_name = self._strategy_resolver.to_id(strategy_name)

        env_blocked = self._check_env_consistency(stock_code=stock_code, side=side)
        if env_blocked is not None:
            return env_blocked

        is_force_exit_sell = self._is_force_exit_sell(side=side, source=source)

        blocked = await self._check_kill_switch(skip=is_force_exit_sell)
        if blocked is not None:
            return blocked

        if strategy_name and not is_force_exit_sell:
            strategy_ks_blocked = self._check_strategy_kill_switch(strategy_name, side)
            if strategy_ks_blocked is not None:
                return strategy_ks_blocked

        if price < 0:
            return self._blocked(
                "negative_price",
                "주문 가격은 음수일 수 없습니다.",
                stock_code=stock_code,
                price=price,
                side=side.value,
            )

        effective_max_pending = self._effective_max_pending_orders()
        if active_order_count >= effective_max_pending:
            return self._blocked(
                "max_pending_orders",
                "진행 중 주문 수 초과",
                active_order_count=active_order_count,
                max_pending_orders=effective_max_pending,
            )

        effective_price = price
        if (
            price == 0
            and side == OrderSide.BUY
            and not is_force_exit_sell
            and self._is_real_mode()
        ):
            reference_price = await self._resolve_market_buy_reference_price(
                stock_code, exchange
            )
            if reference_price is None or reference_price <= 0:
                return self._blocked(
                    "market_buy_no_reference_price",
                    "실전 모드 시장가 매수: 기준가격 산정 실패로 차단",
                    stock_code=stock_code,
                    side=side.value,
                    qty=qty,
                )
            effective_price = reference_price

        signal_policy_blocked = self._check_signal_price_policy(
            stock_code=stock_code,
            side=side,
            source=source,
            strategy_name=strategy_name,
            effective_price=effective_price,
            invalidation_price=invalidation_price,
            stop_loss_price=stop_loss_price,
        )
        if signal_policy_blocked is not None:
            return signal_policy_blocked

        if effective_price > 0:
            order_amount = effective_price * qty
            if not is_force_exit_sell:
                # 1회 주문 금액 한도는 신규 노출을 늘리는 BUY에만 적용한다.
                # SELL은 손절/익절 청산 경로라 금액 한도로 막으면 리스크가 더 커질 수 있다.
                if side == OrderSide.BUY and order_amount > self._cfg.max_order_amount_won:
                    return self._blocked(
                        "max_order_amount",
                        "주문 금액 초과",
                        stock_code=stock_code,
                        side=side.value,
                        source=source,
                        strategy_name=strategy_name,
                        order_amount=order_amount,
                        max_order_amount_won=self._cfg.max_order_amount_won,
                    )

                daily_blocked = self._check_daily_cap(stock_code=stock_code, order_amount=order_amount, side=side)
                if daily_blocked is not None:
                    return daily_blocked

            if side == OrderSide.BUY:
                strategy_blocked = await self._check_strategy_risk(
                    stock_code=stock_code,
                    order_amount=order_amount,
                    strategy_name=strategy_name,
                    source=source,
                    exchange=exchange,
                )
                if strategy_blocked is not None:
                    return strategy_blocked

                exposure_blocked = await self._check_buy_exposure(stock_code, order_amount, exchange)
                if exposure_blocked is not None:
                    return exposure_blocked

            if not is_force_exit_sell:
                self._record_daily_amount(order_amount)

        return None

    def _check_signal_price_policy(
        self,
        *,
        stock_code: str,
        side: OrderSide,
        source: str,
        strategy_name: Optional[str],
        effective_price: int,
        invalidation_price: Optional[float],
        stop_loss_price: Optional[float],
    ) -> Optional[ResCommonResponse]:
        if effective_price <= 0:
            return None

        if invalidation_price is not None:
            invalidation = float(invalidation_price)
            if side == OrderSide.BUY and float(effective_price) <= invalidation:
                return self._blocked(
                    "signal_invalidated",
                    "매수 신호 무효화 가격 이하라 주문을 차단했습니다.",
                    stock_code=stock_code,
                    side=side.value,
                    source=source,
                    strategy_name=strategy_name,
                    effective_price=effective_price,
                    invalidation_price=invalidation,
                )
            if side == OrderSide.SELL and float(effective_price) >= invalidation:
                return self._blocked(
                    "signal_invalidated",
                    "매도 신호 무효화 가격 이상이라 주문을 차단했습니다.",
                    stock_code=stock_code,
                    side=side.value,
                    source=source,
                    strategy_name=strategy_name,
                    effective_price=effective_price,
                    invalidation_price=invalidation,
                )

        if stop_loss_price is not None and side == OrderSide.BUY:
            stop_price = float(stop_loss_price)
            if stop_price <= 0 or stop_price >= float(effective_price):
                return self._blocked(
                    "invalid_stop_loss_price",
                    "매수 주문의 손절가는 주문 기준가보다 낮은 양수여야 합니다.",
                    stock_code=stock_code,
                    side=side.value,
                    source=source,
                    strategy_name=strategy_name,
                    effective_price=effective_price,
                    stop_loss_price=stop_price,
                )

        return None

    def _today(self) -> date:
        return datetime.now().date()

    def _check_daily_cap(
        self,
        stock_code: str,
        order_amount: int,
        side: OrderSide,
    ) -> Optional[ResCommonResponse]:
        cap = getattr(self._cfg, "max_daily_order_amount_won", 0) or 0
        if cap <= 0:
            return None

        today = self._today()
        current = self._daily_total.get(today, 0)
        next_total = current + order_amount
        if next_total > cap:
            return self._blocked(
                "max_daily_order_amount",
                "1일 누적 주문 금액 한도 초과",
                stock_code=stock_code,
                side=side.value,
                order_amount=order_amount,
                daily_total_before=current,
                daily_total_after=next_total,
                max_daily_order_amount_won=cap,
            )
        return None

    def _record_daily_amount(self, order_amount: int) -> None:
        cap = getattr(self._cfg, "max_daily_order_amount_won", 0) or 0
        if cap <= 0:
            return
        today = self._today()
        self._daily_total[today] += order_amount
        # 7일 이상 된 키 정리 (메모리 leak 방지)
        stale = [d for d in self._daily_total.keys() if (today - d).days > 7]
        for d in stale:
            self._daily_total.pop(d, None)

    def _check_env_consistency(
        self,
        stock_code: str,
        side: OrderSide,
    ) -> Optional[ResCommonResponse]:
        """실전/모의 모드와 실제 사용중인 URL/계좌가 일치하는지 cross-check.

        주문 직전 hard block. env 미주입 시 fail-open(skip).
        - paper 모드: base_url에 'vts' 포함 + 활성 계좌가 paper_stock_account_number와 일치
        - real 모드: base_url에 'vts' 미포함 + 활성 계좌가 stock_account_number와 일치
        """
        env = self._env
        if env is None:
            return None

        is_paper = bool(getattr(env, "is_paper_trading", None))
        base_url = getattr(env, "_base_url", None) or ""
        active_cfg = getattr(env, "active_config", None) or {}
        active_account = active_cfg.get("stock_account_number") if isinstance(active_cfg, dict) else None
        real_account = getattr(env, "stock_account_number", None)
        paper_account = getattr(env, "paper_stock_account_number", None)

        url_is_paper = "vts" in base_url.lower()

        if is_paper != url_is_paper:
            return self._blocked(
                "env_mismatch_url",
                "실전/모의 모드와 base_url 호스트가 일치하지 않습니다.",
                stock_code=stock_code,
                side=side.value,
                is_paper_trading=is_paper,
                base_url_paper=url_is_paper,
            )

        if active_account is not None:
            expected_account = paper_account if is_paper else real_account
            if expected_account is not None and active_account != expected_account:
                return self._blocked(
                    "env_mismatch_account",
                    "실전/모의 모드와 활성 계좌번호가 일치하지 않습니다.",
                    stock_code=stock_code,
                    side=side.value,
                    is_paper_trading=is_paper,
                    active_account_prefix=str(active_account)[:4],
                    expected_account_prefix=str(expected_account)[:4],
                )

        return None

    def _is_real_mode(self) -> bool:
        env = self._env
        if env is None:
            return False
        return not bool(getattr(env, "is_paper_trading", True))

    def _should_fail_close(self) -> bool:
        """현재 모드에서 fail-open 경로가 허용되는지 판단.

        반환값 True 면 해당 fail-open 경로를 차단(fail-close)으로 전환해야 한다.
        BUY 경로에서만 호출되도록 호출부에서 보장한다 (SELL/force-exit 보존).
        """
        fail_open_cfg = getattr(self._cfg, "fail_open_allowed", None)
        if self._is_real_mode():
            return not bool(getattr(fail_open_cfg, "real", False))
        return not bool(getattr(fail_open_cfg, "paper", True))

    def _effective_max_total_exposure_pct(self) -> float:
        if self._is_real_mode():
            return self._cfg.real_mode_overrides.max_total_exposure_pct
        return self._cfg.max_total_exposure_pct

    def _effective_max_pending_orders(self) -> int:
        if self._is_real_mode():
            return self._cfg.real_mode_overrides.max_pending_orders
        return self._cfg.max_pending_orders

    async def _resolve_market_buy_reference_price(
        self,
        stock_code: str,
        exchange: Exchange,
    ) -> Optional[int]:
        provider = self._market_buy_reference_price_provider
        if provider is None:
            return None
        try:
            result = provider(stock_code, exchange)
            if inspect.isawaitable(result):
                result = await result
            if result is None:
                return None
            return int(result)
        except Exception as exc:
            self._logger.warning(
                "[RiskGate] market buy reference price provider failed: %s", exc
            )
            return None

    def _check_strategy_kill_switch(
        self, strategy_name: str, side: OrderSide
    ) -> Optional[ResCommonResponse]:
        """전략별 Kill Switch 활성 상태 확인. 트립 시 차단.

        trip metadata 의 block_side 가 "buy" 면 SELL 은 통과시켜 graceful 청산을 허용한다.
        ("all" 또는 미지정인 기존 트립은 backward-compat 으로 BUY/SELL 모두 차단.)
        """
        if self._kill_switch is None:
            return None
        trip_info = self._kill_switch.is_strategy_tripped(strategy_name)
        if trip_info is None:
            return None
        block_side = str(trip_info.get("block_side", "all"))
        if block_side == "buy" and side == OrderSide.SELL:
            return None
        reason = trip_info.get("trip_reason", "전략 Kill Switch 활성")
        return self._blocked(
            "strategy_kill_switch",
            f"전략 Kill Switch 차단: {reason}",
            error_code=ErrorCode.KILL_SWITCH_BLOCKED,
            strategy_name=strategy_name,
            trip_reason=reason,
            block_side=block_side,
        )

    async def _check_kill_switch(self, *, skip: bool = False) -> Optional[ResCommonResponse]:
        if skip or self._kill_switch is None:
            return None

        try:
            allowed, reason = await self._kill_switch.check_orders_allowed()
        except Exception as exc:
            return self._blocked(
                "kill_switch_check_failed",
                f"Kill Switch 확인 실패 ({exc})",
            )

        if allowed:
            return None

        return self._blocked(
            "kill_switch_active",
            f"Kill Switch 활성: {reason}",
            error_code=ErrorCode.KILL_SWITCH_BLOCKED,
            kill_switch_reason=reason,
        )

    async def _check_buy_exposure(
        self,
        stock_code: str,
        order_amount: int,
        exchange: Exchange,
    ) -> Optional[ResCommonResponse]:
        if self._account_snapshot_cache is None:
            if self._should_fail_close():
                return self._blocked(
                    "fail_close_no_snapshot_cache",
                    "실전 모드: account snapshot cache 부재로 노출 검증 불가",
                    stock_code=stock_code,
                    order_amount=order_amount,
                )
            self._logger.warning("[RiskGate] account snapshot cache 없음: 노출 검증 skip")
            return None

        snapshot = await self._account_snapshot_cache.get(exchange)
        if snapshot.total_equity <= 0:
            if self._should_fail_close():
                return self._blocked(
                    "fail_close_zero_equity",
                    "실전 모드: total_equity<=0 으로 노출 검증 불가",
                    stock_code=stock_code,
                    total_equity=snapshot.total_equity,
                )
            self._logger.warning(
                f"[RiskGate] total_equity<=0: 노출 검증 fail-open. "
                f"code={stock_code} equity={snapshot.total_equity}"
            )
            return None

        current_exposure = sum(max(value, 0) for value in snapshot.positions.values())
        next_exposure_pct = (current_exposure + order_amount) / snapshot.total_equity * 100
        effective_max_exposure_pct = self._effective_max_total_exposure_pct()
        if next_exposure_pct > effective_max_exposure_pct:
            return self._blocked(
                "max_total_exposure",
                "계좌 노출 한도 초과",
                stock_code=stock_code,
                current_exposure=current_exposure,
                order_amount=order_amount,
                total_equity=snapshot.total_equity,
                next_exposure_pct=round(next_exposure_pct, 2),
                max_total_exposure_pct=effective_max_exposure_pct,
            )

        return None

    async def _check_strategy_risk(
        self,
        stock_code: str,
        order_amount: int,
        strategy_name: Optional[str],
        source: str,
        exchange: Exchange,
    ) -> Optional[ResCommonResponse]:
        if not strategy_name:
            return None

        duplicate_blocked = await self._check_duplicate_strategy_position(
            strategy_name, stock_code, source
        )
        if duplicate_blocked is not None:
            return duplicate_blocked

        limit = self._get_strategy_limit(strategy_name)
        if limit is None:
            return None

        loss_blocked = await self._check_strategy_loss_limit(strategy_name, limit)
        if loss_blocked is not None:
            return loss_blocked

        cap_blocked = await self._check_strategy_capital_cap(
            strategy_name=strategy_name,
            stock_code=stock_code,
            order_amount=order_amount,
            limit=limit,
            exchange=exchange,
        )
        if cap_blocked is not None:
            return cap_blocked

        return await self._check_strategy_exposure_limit(
            strategy_name=strategy_name,
            stock_code=stock_code,
            order_amount=order_amount,
            limit=limit,
            exchange=exchange,
        )

    async def _check_duplicate_strategy_position(
        self,
        strategy_name: str,
        stock_code: str,
        source: str,
    ) -> Optional[ResCommonResponse]:
        limit = self._get_strategy_limit(strategy_name)
        block_duplicate = self._cfg.block_duplicate_strategy_position
        if limit is not None:
            block_duplicate = block_duplicate and limit.block_duplicate_position
        if not block_duplicate or self._strategy_risk_provider is None:
            return None

        try:
            holding = self._strategy_risk_provider.is_holding(strategy_name, stock_code)
            if inspect.isawaitable(holding):
                holding = await holding
        except Exception as exc:
            self._logger.warning(
                f"[RiskGate][CHECK_ERROR] rule=duplicate_strategy_position "
                f"strategy={strategy_name} code={stock_code} error={exc}"
            )
            if self._should_fail_close():
                return self._blocked(
                    "fail_close_duplicate_strategy_provider_error",
                    "실전 모드: 중복 보유 검증 provider 오류",
                    strategy_name=strategy_name,
                    stock_code=stock_code,
                    error=str(exc),
                )
            return None

        if not holding:
            return None

        return self._blocked(
            "duplicate_strategy_position",
            "동일 전략에서 이미 보유 중인 종목입니다.",
            strategy_name=strategy_name,
            stock_code=stock_code,
            source=source,
        )

    def _get_strategy_limit(self, strategy_name: str):
        """전략별 한도를 dual-key (한국어/strategy_id) 로 조회."""
        sid = self._strategy_resolver.to_id(strategy_name)
        display = self._strategy_resolver.to_display(sid)
        specific = self._cfg.strategy_limits.get(sid) or self._cfg.strategy_limits.get(display)
        return specific or self._cfg.default_strategy_limit

    async def _check_strategy_loss_limit(
        self,
        strategy_name: str,
        limit,
    ) -> Optional[ResCommonResponse]:
        if limit.max_loss_pct is None or self._strategy_risk_provider is None:
            return None

        try:
            history = self._strategy_risk_provider.get_strategy_return_history(strategy_name)
            if inspect.isawaitable(history):
                history = await history
        except Exception as exc:
            self._logger.warning(
                f"[RiskGate][CHECK_ERROR] rule=strategy_loss_limit "
                f"strategy={strategy_name} error={exc}"
            )
            if self._should_fail_close():
                return self._blocked(
                    "fail_close_strategy_loss_provider_error",
                    "실전 모드: 전략 손실 한도 검증 provider 오류",
                    strategy_name=strategy_name,
                    error=str(exc),
                )
            return None

        if not history:
            self._logger.debug(
                f"[RiskGate][SKIP] rule=strategy_loss_limit strategy={strategy_name} reason=no_history"
            )
            return None

        latest = history[-1] or {}
        latest_return = float(latest.get("return_rate", 0) or 0)
        max_loss_pct = abs(float(limit.max_loss_pct))
        if latest_return <= -max_loss_pct:
            return self._blocked(
                "strategy_loss_limit",
                "전략 손실 한도 초과",
                strategy_name=strategy_name,
                latest_return_pct=latest_return,
                max_loss_pct=max_loss_pct,
                reference_date=latest.get("date"),
            )

        return None

    async def _check_strategy_capital_cap(
        self,
        strategy_name: str,
        stock_code: str,
        order_amount: int,
        limit,
        exchange: Exchange,
    ) -> Optional[ResCommonResponse]:
        """capital_allocation_pct 기반 전략별 자본 할당 한도 검증.

        현재 보유 평가금액 + 주문 금액이 total_equity × cap_pct 를 초과하면 차단.
        """
        cap_pct = getattr(limit, "capital_allocation_pct", None)
        if cap_pct is None or self._strategy_risk_provider is None:
            return None
        if self._account_snapshot_cache is None:
            if self._should_fail_close():
                return self._blocked(
                    "fail_close_strategy_capital_cap_no_snapshot_cache",
                    "실전 모드: account snapshot cache 부재로 전략 자본 캡 검증 불가",
                    strategy_name=strategy_name,
                    stock_code=stock_code,
                )
            self._logger.warning("[RiskGate] account snapshot cache 없음: 전략 자본 캡 검증 skip")
            return None

        snapshot = await self._account_snapshot_cache.get(exchange)
        if snapshot.total_equity <= 0:
            if self._should_fail_close():
                return self._blocked(
                    "fail_close_strategy_capital_cap_zero_equity",
                    "실전 모드: total_equity<=0 으로 전략 자본 캡 검증 불가",
                    strategy_name=strategy_name,
                    stock_code=stock_code,
                    total_equity=snapshot.total_equity,
                )
            return None

        try:
            holds = self._strategy_risk_provider.get_holds_by_strategy(strategy_name) or []
            if inspect.isawaitable(holds):
                holds = await holds
        except Exception as exc:
            self._logger.warning(
                f"[RiskGate][CHECK_ERROR] rule=capital_allocation_cap "
                f"strategy={strategy_name} error={exc}"
            )
            if self._should_fail_close():
                return self._blocked(
                    "fail_close_strategy_capital_cap_provider_error",
                    "실전 모드: 전략 자본 캡 검증 provider 오류",
                    strategy_name=strategy_name,
                    stock_code=stock_code,
                    error=str(exc),
                )
            return None

        current_exposure = sum(self._position_value(hold) for hold in holds)
        budget = snapshot.total_equity * cap_pct / 100
        if current_exposure + order_amount > budget:
            return self._blocked(
                "capital_allocation_cap",
                "전략 자본 할당 한도 초과",
                strategy_name=strategy_name,
                stock_code=stock_code,
                current_exposure=current_exposure,
                order_amount=order_amount,
                total_equity=snapshot.total_equity,
                budget=int(budget),
                capital_allocation_pct=cap_pct,
            )
        return None

    async def _check_strategy_exposure_limit(
        self,
        strategy_name: str,
        stock_code: str,
        order_amount: int,
        limit,
        exchange: Exchange,
    ) -> Optional[ResCommonResponse]:
        if limit.max_exposure_pct is None or self._strategy_risk_provider is None:
            return None
        if self._account_snapshot_cache is None:
            if self._should_fail_close():
                return self._blocked(
                    "fail_close_strategy_exposure_no_snapshot_cache",
                    "실전 모드: account snapshot cache 부재로 전략 노출 검증 불가",
                    strategy_name=strategy_name,
                    stock_code=stock_code,
                )
            self._logger.warning("[RiskGate] account snapshot cache 없음: 전략 노출 검증 skip")
            return None

        snapshot = await self._account_snapshot_cache.get(exchange)
        if snapshot.total_equity <= 0:
            if self._should_fail_close():
                return self._blocked(
                    "fail_close_strategy_exposure_zero_equity",
                    "실전 모드: total_equity<=0 으로 전략 노출 검증 불가",
                    strategy_name=strategy_name,
                    stock_code=stock_code,
                    total_equity=snapshot.total_equity,
                )
            self._logger.warning(
                f"[RiskGate] total_equity<=0: 전략 노출 검증 fail-open. "
                f"strategy={strategy_name} code={stock_code} equity={snapshot.total_equity}"
            )
            return None

        try:
            holds = self._strategy_risk_provider.get_holds_by_strategy(strategy_name) or []
            if inspect.isawaitable(holds):
                holds = await holds
        except Exception as exc:
            self._logger.warning(
                f"[RiskGate][CHECK_ERROR] rule=strategy_exposure_limit "
                f"strategy={strategy_name} error={exc}"
            )
            if self._should_fail_close():
                return self._blocked(
                    "fail_close_strategy_exposure_provider_error",
                    "실전 모드: 전략 노출 검증 provider 오류",
                    strategy_name=strategy_name,
                    stock_code=stock_code,
                    error=str(exc),
                )
            return None

        current_strategy_exposure = sum(self._position_value(hold) for hold in holds)
        next_exposure_pct = (
            (current_strategy_exposure + order_amount) / snapshot.total_equity * 100
        )
        if next_exposure_pct > limit.max_exposure_pct:
            return self._blocked(
                "strategy_exposure_limit",
                "전략 자본 할당 한도 초과",
                strategy_name=strategy_name,
                stock_code=stock_code,
                current_strategy_exposure=current_strategy_exposure,
                order_amount=order_amount,
                total_equity=snapshot.total_equity,
                next_exposure_pct=round(next_exposure_pct, 2),
                max_exposure_pct=limit.max_exposure_pct,
            )

        return None

    @staticmethod
    def _position_value(hold: dict) -> int:
        for key in ("current_value", "eval_amount", "evlu_amt", "market_value"):
            if key in hold and hold.get(key) not in (None, ""):
                return int(float(str(hold.get(key)).replace(",", "")))
        price = float(str(hold.get("buy_price") or 0).replace(",", ""))
        qty = int(float(str(hold.get("qty") or 0).replace(",", "")))
        return int(price * qty)

    @staticmethod
    def _strategy_name_from_source(source: str) -> Optional[str]:
        source = source or ""
        if source.startswith("strategy:"):
            return source.split(":", 1)[1] or None
        if source.startswith("strategy_force_exit:"):
            return source.split(":", 1)[1] or None
        return None

    @staticmethod
    def _is_force_exit_sell(*, side: OrderSide, source: str) -> bool:
        return side == OrderSide.SELL and (source or "").startswith("strategy_force_exit:")

    def _blocked(
        self,
        rule: str,
        reason: str,
        *,
        error_code: ErrorCode = ErrorCode.RISK_GATE_BLOCKED,
        **context,
    ) -> ResCommonResponse:
        decision = RiskGateDecision(
            rule=rule,
            reason=reason,
            error_code=error_code,
            context=context,
        )
        self._logger.warning(
            f"[RiskGate][BLOCK] rule={rule} reason={reason} context={context}"
        )
        if self._operator_alert:
            strategy = str(context.get("strategy_name") or "")
            code = str(context.get("stock_code") or "")
            dedup_key = f"risk_gate:{rule}:{strategy}:{code}"
            asyncio.create_task(
                self._operator_alert.report(
                    AlertSource.RISK_GATE,
                    dedup_key,
                    "block",
                    f"RiskGate 차단: {rule}",
                    reason,
                    {"rule": rule, **context},
                )
            )
        return decision.to_response()
