import logging
from typing import Optional

from common.types import ErrorCode, Exchange, OrderSide, ResCommonResponse
from config.config_loader import RiskGateConfig
from core.account_snapshot import AccountSnapshotCache
from services.kill_switch_service import KillSwitchService


class RiskGateService:
    """Common hard-block checks that must pass before a broker order submit."""

    def __init__(
        self,
        config: RiskGateConfig,
        kill_switch_service: Optional[KillSwitchService],
        account_snapshot_cache: Optional[AccountSnapshotCache],
        logger: Optional[logging.Logger] = None,
    ):
        self._cfg = config
        self._kill_switch = kill_switch_service
        self._account_snapshot_cache = account_snapshot_cache
        self._logger = logger or logging.getLogger(__name__)

    async def validate_order(
        self,
        stock_code: str,
        price: int,
        qty: int,
        side: OrderSide,
        exchange: Exchange,
        active_order_count: int,
    ) -> Optional[ResCommonResponse]:
        """Return None when allowed, otherwise a blocking response."""
        if not self._cfg.enabled:
            return None

        blocked = await self._check_kill_switch()
        if blocked is not None:
            return blocked

        if side == OrderSide.BUY and price <= 0:
            return self._blocked(
                f"Risk Gate 차단: BUY 주문은 0 이하 가격을 허용하지 않습니다. 종목={stock_code}, 가격={price}"
            )

        if active_order_count >= self._cfg.max_pending_orders:
            return self._blocked(
                f"Risk Gate 차단: 진행 중 주문 수 초과 "
                f"({active_order_count}/{self._cfg.max_pending_orders})"
            )

        if price > 0:
            order_amount = price * qty
            if order_amount > self._cfg.max_order_amount_won:
                return self._blocked(
                    f"Risk Gate 차단: 주문 금액 초과 "
                    f"({order_amount:,}원 > {self._cfg.max_order_amount_won:,}원)"
                )

            if side == OrderSide.BUY:
                exposure_blocked = await self._check_buy_exposure(stock_code, order_amount, exchange)
                if exposure_blocked is not None:
                    return exposure_blocked

        return None

    async def _check_kill_switch(self) -> Optional[ResCommonResponse]:
        if self._kill_switch is None:
            return None

        try:
            allowed, reason = await self._kill_switch.check_orders_allowed()
        except Exception as exc:
            msg = f"Risk Gate 차단: Kill Switch 확인 실패 ({exc})"
            self._logger.warning(msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.RISK_GATE_BLOCKED.value,
                msg1=msg,
                data=None,
            )

        if allowed:
            return None

        msg = f"Kill Switch 활성: {reason}"
        self._logger.warning(msg)
        return ResCommonResponse(
            rt_cd=ErrorCode.KILL_SWITCH_BLOCKED.value,
            msg1=msg,
            data={"reason": reason},
        )

    async def _check_buy_exposure(
        self,
        stock_code: str,
        order_amount: int,
        exchange: Exchange,
    ) -> Optional[ResCommonResponse]:
        if self._account_snapshot_cache is None:
            self._logger.warning("[RiskGate] account snapshot cache 없음: 노출 검증 skip")
            return None

        snapshot = await self._account_snapshot_cache.get(exchange)
        if snapshot.total_equity <= 0:
            self._logger.warning(
                f"[RiskGate] total_equity<=0: 노출 검증 fail-open. "
                f"code={stock_code} equity={snapshot.total_equity}"
            )
            return None

        current_exposure = sum(max(value, 0) for value in snapshot.positions.values())
        next_exposure_pct = (current_exposure + order_amount) / snapshot.total_equity * 100
        if next_exposure_pct > self._cfg.max_total_exposure_pct:
            return self._blocked(
                f"Risk Gate 차단: 계좌 노출 한도 초과 "
                f"({next_exposure_pct:.2f}% > {self._cfg.max_total_exposure_pct:.2f}%)"
            )

        return None

    def _blocked(self, msg: str) -> ResCommonResponse:
        self._logger.warning(msg)
        return ResCommonResponse(
            rt_cd=ErrorCode.RISK_GATE_BLOCKED.value,
            msg1=msg,
            data=None,
        )
