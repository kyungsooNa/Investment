"""포지션 사이징 서비스 — Fixed Fractional (Penbold 식).

계좌 노출 제어:
  1. risk_qty  : (총자산 × risk_pct) / 1주당 리스크(KRW)
  2. cap_qty   : 단일 종목 비중 상한 잔여분
  3. cash_qty  : 매수 가능 현금 한도
  4. alloc_qty : 전략별 자본 할당 캡 (설정된 경우)
  5. signal.qty: 전략이 설정한 자발적 상한 (Optional — None 이면 제약 없음)
  final_qty = max(0, min([risk_qty, cap_qty, cash_qty, (alloc_qty), (signal.qty)]))
"""

import asyncio
import math
import inspect
import logging
from datetime import datetime
from typing import Any, Dict, Optional, Protocol, Tuple, TYPE_CHECKING

from common.types import ErrorCode, Exchange, ResCommonResponse, TradeSignal
from core.account_snapshot import AccountSnapshotCache

if TYPE_CHECKING:
    from services.indicator_service import IndicatorService
    from config.config_loader import OrderPolicyConfig, PositionSizingConfig, RiskGateConfig


class QuoteProvider(Protocol):
    async def get_asking_price(
        self,
        stock_code: str,
        exchange: Exchange = Exchange.KRX,
    ) -> ResCommonResponse:
        ...


class PendingBuyExposureProvider(Protocol):
    def __call__(self, stock_code: str, exchange: Exchange = Exchange.KRX) -> Any:
        ...


class PositionSizingService:
    """장중 전략 시그널의 BUY qty 를 변동성/계좌 한도에 맞게 보정한다."""

    def __init__(
        self,
        account_snapshot_cache: AccountSnapshotCache,
        indicator_service: "IndicatorService",
        config: "PositionSizingConfig",
        logger: Optional[logging.Logger] = None,
        risk_gate_config: Optional["RiskGateConfig"] = None,
        quote_provider: Optional[QuoteProvider] = None,
        order_policy_config: Optional["OrderPolicyConfig"] = None,
        env: Optional[Any] = None,
        operating_profile: str = "canary",
        enable_intracycle_reservations: bool = False,
        pending_buy_exposure_provider: Optional[PendingBuyExposureProvider] = None,
    ):
        self._cache = account_snapshot_cache
        self._indicator = indicator_service
        self._cfg = config
        self._logger = logger or logging.getLogger(__name__)
        self._risk_gate_config = risk_gate_config
        self._quote_provider = quote_provider
        self._order_policy_config = order_policy_config
        self._env = env
        self._operating_profile = operating_profile
        self._pending_buy_exposure_provider = pending_buy_exposure_provider

        # P0 0-10: 같은 scheduler 사이클의 미체결 BUY 를 현금/종목 노출에 선반영하는 overlay.
        # live 에서만 활성화한다. backtest 는 BacktestPortfolioLedger 가 예약을 처리하므로
        # 기본 False (이중 차감 방지). 예약은 snapshot.fetched_at 이 바뀌면 자동 초기화한다
        # (체결 → 캐시 invalidate → 새 snapshot, 또는 TTL 만료). 주문 실패 시 별도 release 는
        # 하지 않으며, 다음 snapshot 까지 보수적으로 유지된다.
        self._enable_intracycle_reservations = enable_intracycle_reservations
        self._reservations: Dict[str, int] = {}      # {code: 예약 현금(KRW)}
        self._reservation_baseline_ts: Optional[datetime] = None
        self._reservation_lock = asyncio.Lock()

    def _is_real_mode(self) -> bool:
        """env 미주입이면 paper 로 간주 (보수적 default)."""
        env = self._env
        if env is None:
            return False
        return not bool(getattr(env, "is_paper_trading", True))

    def _effective_per_trade_risk_pct(self) -> float:
        if not self._is_real_mode():
            return self._cfg.per_trade_risk_pct
        if self._operating_profile == "canary":
            return self._cfg.canary_overrides.per_trade_risk_pct
        if self._operating_profile == "real_full":
            return self._cfg.per_trade_risk_pct
        # real_limited (default fallback)
        return self._cfg.real_mode_overrides.per_trade_risk_pct

    def _effective_max_per_position_pct(self) -> float:
        if not self._is_real_mode():
            return self._cfg.max_per_position_pct
        if self._operating_profile == "canary":
            return self._cfg.canary_overrides.max_per_position_pct
        if self._operating_profile == "real_full":
            return self._cfg.max_per_position_pct
        return self._cfg.real_mode_overrides.max_per_position_pct

    def _effective_max_portfolio_open_risk_pct(self) -> float:
        """R-3: 전 포지션 합산 open-risk(heat) 한도 (%). 0 이면 비활성."""
        base = getattr(self._cfg, "max_portfolio_open_risk_pct", 0.0)
        if not self._is_real_mode():
            return base
        if self._operating_profile == "canary":
            return getattr(self._cfg.canary_overrides, "max_portfolio_open_risk_pct", base)
        if self._operating_profile == "real_full":
            return base
        return getattr(self._cfg.real_mode_overrides, "max_portfolio_open_risk_pct", base)

    # ── 공개 API ──────────────────────────────────────────────────

    async def adjust_buy_qty(
        self,
        signal: TradeSignal,
        exchange: "Exchange | None" = None,
    ) -> Tuple[int, str]:
        """BUY 시그널 수량을 포지션 사이징 규칙에 맞게 조정한다.

        Returns:
            (final_qty, reason)  — final_qty == 0 이면 주문 skip.
        """
        if not self._cfg.enabled:
            if signal.qty is None:
                return 0, "sizing_disabled"
            return signal.qty, "sizing_disabled"

        if signal.action != "BUY" or signal.price <= 0:
            return signal.qty, "bypass"
        if signal.qty is not None and signal.qty <= 0:
            return signal.qty, "bypass"

        # P0 0-10: 예약 overlay 활성 시, snapshot 읽기~예약 기록을 직렬화해
        # 병렬 BUY 간 cash/종목 cap 이중 사용을 막는다.
        if self._enable_intracycle_reservations:
            async with self._reservation_lock:
                return await self._compute_buy_qty(signal, exchange)
        return await self._compute_buy_qty(signal, exchange)

    async def _compute_buy_qty(
        self,
        signal: TradeSignal,
        exchange: "Exchange | None",
    ) -> Tuple[int, str]:
        price = signal.price

        # 1. 잔고 스냅샷 (API 비호출, 캐시에서 읽기)
        snapshot = await self._cache.get(exchange)
        total_equity = snapshot.total_equity
        available_cash = snapshot.available_cash
        current_position_value = snapshot.positions.get(signal.code, 0)

        # P0 0-10: 같은 사이클 미체결 BUY 예약을 현금/종목 노출에 선반영.
        # snapshot.fetched_at 이 바뀌면(체결 invalidate/TTL) 예약 초기화 → 새 baseline.
        if self._enable_intracycle_reservations:
            if self._reservation_baseline_ts != snapshot.fetched_at:
                self._reservations.clear()
                self._reservation_baseline_ts = snapshot.fetched_at
            reserved_total = sum(self._reservations.values())
            available_cash = max(available_cash - reserved_total, 0)
            current_position_value = current_position_value + self._reservations.get(signal.code, 0)

        pending_buy_exposure = await self._get_pending_buy_exposure_won(signal, exchange)
        current_position_value += pending_buy_exposure

        if total_equity <= 0:
            self._logger.warning(
                f"[PositionSizing] {signal.code}: total_equity=0, 주문 skip"
            )
            return 0, "risk_zero"

        # 2. 1주당 리스크(KRW) 산정
        per_share_risk = await self._get_per_share_risk_krw(signal, price)

        # 3. risk_qty (Penbold Fixed Fractional)
        total_risk_krw = total_equity * self._effective_per_trade_risk_pct() / 100
        risk_qty = math.floor(total_risk_krw / per_share_risk) if per_share_risk > 0 else 0

        # 4. cap_qty (단일 종목 비중 상한 — 기존 보유분 차감)
        weight_budget = max(
            total_equity * self._effective_max_per_position_pct() / 100 - current_position_value,
            0,
        )
        cap_qty = math.floor(weight_budget / price) if price > 0 else 0

        # 5. cash_qty (매수 가능 현금)
        cash_qty = math.floor(available_cash / price) if price > 0 else 0

        # 6. alloc_qty (전략별 자본 할당 캡, None 이면 제약 없음)
        alloc_qty = self._calc_strategy_alloc_qty(signal, price, total_equity)

        # 7. 주문 직전 hard-block 정책과 같은 한도도 미리 수량에 반영
        max_order_amount_qty = self._calc_max_order_amount_qty(price)
        top_of_book_qty = await self._calc_top_of_book_qty(signal, price, exchange)

        # 7-1. R-3: 포트폴리오 총위험(heat) 한도. 스냅샷에 종목별 stop 이 없으므로
        # 기존 보유 open-risk 를 Σ(평가금 × |default_stop|) proxy 로 추정하고(보수적),
        # 신규 후보는 정확한 per_share_risk×qty 로 잔여 budget 에 맞춰 축소한다.
        heat_qty = self._calc_portfolio_heat_qty(
            snapshot=snapshot, total_equity=total_equity, per_share_risk=per_share_risk
        )

        # 8. 후보 목록 구성 — signal.qty / alloc_qty 는 None 이면 제외 (제약 없음)
        candidates = {
            "risk_limited": risk_qty,
            "cap_limited": cap_qty,
            "cash_limited": cash_qty,
        }
        if heat_qty is not None:
            candidates["heat_limited"] = heat_qty
        if alloc_qty is not None:
            candidates["strategy_capital_cap"] = alloc_qty
        if max_order_amount_qty is not None:
            candidates["max_order_amount_limited"] = max_order_amount_qty
        if top_of_book_qty is not None:
            candidates["top_of_book_limited"] = top_of_book_qty
        if signal.qty is not None:
            candidates["signal_cap"] = signal.qty
        final_qty = max(0, min(candidates.values()))

        # 9. 사유 결정
        if final_qty == 0:
            if risk_qty == 0:
                reason = "risk_zero"
            elif cap_qty == 0:
                reason = "cap_exhausted"
            elif alloc_qty is not None and alloc_qty == 0:
                reason = "strategy_capital_cap"
            elif max_order_amount_qty is not None and max_order_amount_qty == 0:
                reason = "order_amount_cap"
            elif top_of_book_qty is not None and top_of_book_qty == 0:
                reason = "top_of_book_limited"
            elif heat_qty is not None and heat_qty == 0:
                reason = "portfolio_heat_exhausted"
            else:
                reason = "cash_short"
        elif signal.qty is not None and final_qty == signal.qty:
            reason = "ok"
        else:
            # signal.qty 상한보다 작게 됐거나, qty=None 에서 sizing 이 단독 결정
            reason_priority = (
                "strategy_capital_cap",
                "max_order_amount_limited",
                "top_of_book_limited",
                "heat_limited",
                "cash_limited",
                "cap_limited",
                "risk_limited",
            )
            reason = next(
                key for key in reason_priority
                if candidates.get(key) == final_qty
            )

        # P0 0-10: 매수 확정분을 예약에 누적 → 같은 사이클 후속 BUY 가 차감된 현금/cap 을 본다.
        if self._enable_intracycle_reservations and final_qty > 0:
            self._reservations[signal.code] = (
                self._reservations.get(signal.code, 0) + final_qty * price
            )

        self._logger.info(
            f"[PositionSizing] {signal.code} price={price:,} "
            f"equity={total_equity:,} risk/share={per_share_risk:.0f} "
            f"risk_qty={risk_qty} cap_qty={cap_qty} cash_qty={cash_qty} alloc_qty={alloc_qty} "
            f"pending_buy_exposure={pending_buy_exposure:,} heat_qty={heat_qty} "
            f"max_order_amount_qty={max_order_amount_qty} top_of_book_qty={top_of_book_qty} "
            f"signal_qty={signal.qty} → final={final_qty} ({reason})"
        )
        return final_qty, reason

    # ── 내부 ──────────────────────────────────────────────────────

    def _calc_strategy_alloc_qty(self, signal: TradeSignal, price: int, total_equity: int) -> Optional[int]:
        """전략별 자본 할당 캡 (capital_allocation_pct) 기반 최대 수량.

        캡 미설정 시 None 반환 (제약 없음).
        """
        if not self._risk_gate_config or not signal.strategy_name or price <= 0:
            return None

        cfg = self._risk_gate_config
        limit = cfg.strategy_limits.get(signal.strategy_name) or cfg.default_strategy_limit
        cap_pct = limit.capital_allocation_pct if limit else None
        if cap_pct is None:
            return None

        alloc_budget = int(total_equity * cap_pct / 100)
        return math.floor(alloc_budget / price)

    def _calc_max_order_amount_qty(self, price: int) -> Optional[int]:
        if not self._risk_gate_config or price <= 0:
            return None
        max_amount = int(getattr(self._risk_gate_config, "max_order_amount_won", 0) or 0)
        if max_amount <= 0:
            return None
        return math.floor(max_amount / price)

    def _calc_portfolio_heat_qty(
        self,
        snapshot: Any,
        total_equity: int,
        per_share_risk: float,
    ) -> Optional[int]:
        """R-3: 전 포지션 합산 open-risk(heat) budget 잔여분에 맞춘 신규 BUY 최대 수량.

        한도 미설정(<=0)이거나 per_share_risk<=0 이면 None(제약 없음).
        기존 보유 open-risk 는 종목별 stop 부재로 Σ(평가금 × |default_stop|) proxy 로 추정한다.
        reservation overlay 활성 시 같은 사이클 accepted BUY 예약도 합산한다.
        """
        max_heat_pct = self._effective_max_portfolio_open_risk_pct()
        if max_heat_pct <= 0 or per_share_risk <= 0 or total_equity <= 0:
            return None

        assumed_stop = abs(self._cfg.default_stop_loss_pct) / 100
        existing_notional = sum(max(value, 0) for value in snapshot.positions.values())
        if self._enable_intracycle_reservations:
            existing_notional += sum(max(value, 0) for value in self._reservations.values())
        existing_open_risk = existing_notional * assumed_stop

        heat_budget = total_equity * max_heat_pct / 100
        remaining_heat = max(heat_budget - existing_open_risk, 0)
        return math.floor(remaining_heat / per_share_risk)

    async def _get_pending_buy_exposure_won(
        self,
        signal: TradeSignal,
        exchange: "Exchange | None",
    ) -> int:
        provider = self._pending_buy_exposure_provider
        if provider is None:
            return 0

        try:
            exposure = provider(signal.code, exchange=exchange or Exchange.KRX)
        except TypeError:
            try:
                exposure = provider(signal.code)
            except Exception as exc:
                self._logger.debug(f"[PositionSizing] 미체결 BUY 노출 조회 실패 ({signal.code}): {exc}")
                return 0
        except Exception as exc:
            self._logger.debug(f"[PositionSizing] 미체결 BUY 노출 조회 실패 ({signal.code}): {exc}")
            return 0

        try:
            if inspect.isawaitable(exposure):
                exposure = await exposure
            return max(self._to_int(exposure), 0)
        except Exception as exc:
            self._logger.debug(f"[PositionSizing] 미체결 BUY 노출 변환 실패 ({signal.code}): {exc}")
            return 0

    async def _calc_top_of_book_qty(
        self,
        signal: TradeSignal,
        price: int,
        exchange: "Exchange | None",
    ) -> Optional[int]:
        if self._quote_provider is None or price <= 0:
            return None
        if self._order_policy_config is not None and not getattr(
            self._order_policy_config, "order_book_checks_enabled", True
        ):
            return None

        try:
            resp = self._quote_provider.get_asking_price(
                signal.code,
                exchange=exchange or Exchange.KRX,
            )
            if inspect.isawaitable(resp):
                resp = await resp
        except TypeError:
            try:
                resp = self._quote_provider.get_asking_price(signal.code)
                if inspect.isawaitable(resp):
                    resp = await resp
            except Exception as exc:
                self._logger.debug(f"[PositionSizing] 호가 조회 실패 ({signal.code}): {exc}")
                return None
        except Exception as exc:
            self._logger.debug(f"[PositionSizing] 호가 조회 실패 ({signal.code}): {exc}")
            return None

        if not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
            return None

        quote = self._extract_quote_data(resp.data)
        ask_qty = self._to_int(self._pick(quote, "askp_rsqn1", "매도호가잔량1"))
        if ask_qty <= 0:
            return None

        max_pct = 100.0
        if self._order_policy_config is not None:
            max_pct = float(getattr(
                self._order_policy_config,
                "max_top_of_book_participation_pct",
                100.0,
            ) or 0.0)
        if max_pct > 0:
            return math.floor(ask_qty * max_pct / 100)
        return ask_qty

    async def _get_per_share_risk_krw(self, signal: TradeSignal, price: int) -> float:
        """1주당 리스크 금액(KRW) — 절대가 stop, ATR, stop_loss_pct 기반."""
        stop_pct = abs(signal.stop_loss_pct or self._cfg.default_stop_loss_pct)
        stop_from_pct = price * stop_pct / 100  # KRW

        stop_from_price = 0.0
        if signal.stop_loss_price is not None:
            stop_from_price = max(float(price) - float(signal.stop_loss_price), 0.0)

        atr_krw = await self._get_atr_krw(signal)
        atr_mult = signal.atr_multiplier or self._cfg.atr_multiplier
        stop_from_atr = atr_krw * atr_mult if atr_krw > 0 else 0.0

        min_stop = price * self._cfg.min_stop_distance_pct / 100  # 하한 보호

        return max(stop_from_price, stop_from_pct, stop_from_atr, min_stop)

    async def _get_atr_krw(self, signal: TradeSignal) -> float:
        """ATR 마지막 값을 KRW 로 반환. 실패 시 0.0.

        P0 0-8: 사이징은 당일 미확정 봉의 high/low 흔들림을 ATR 에 반영하지 않도록 exclude_today=True.
        """
        try:
            resp = await self._indicator.calculate_atr(
                signal.code, period=self._cfg.atr_period, exclude_today=True,
            )
            from common.types import ErrorCode
            if resp is None or resp.rt_cd != ErrorCode.SUCCESS.value or not resp.data:
                return 0.0
            last = resp.data[-1] if isinstance(resp.data, list) else resp.data
            atr_val = last.get("atr") if isinstance(last, dict) else getattr(last, "atr", None)
            return float(atr_val) if atr_val is not None else 0.0
        except Exception as e:
            self._logger.debug(f"[PositionSizing] ATR 조회 실패 ({signal.code}): {e}")
            return 0.0

    @staticmethod
    def _extract_quote_data(data: Any) -> dict:
        if isinstance(data, dict):
            if isinstance(data.get("output1"), dict):
                return data["output1"]
            if isinstance(data.get("output"), dict):
                return data["output"]
            return data
        return {}

    @staticmethod
    def _pick(data: dict, *keys: str):
        for key in keys:
            if key in data:
                return data.get(key)
        return None

    @staticmethod
    def _to_int(value) -> int:
        try:
            return int(float(str(value or "0").replace(",", "")))
        except (TypeError, ValueError):
            return 0
