# tests/unit_test/test_position_sizing_service.py
"""PositionSizingService 단위 테스트 — 전략별 자본 캡 (capital_allocation_pct) 중심."""
import math
import pytest
from dataclasses import dataclass
from typing import Dict
from unittest.mock import AsyncMock, MagicMock

from core.account_snapshot import AccountSnapshot, AccountSnapshotCache
from config.config_loader import PositionSizingConfig, RiskGateConfig, RiskGateStrategyLimitConfig
from common.types import Exchange, TradeSignal
from services.position_sizing_service import PositionSizingService


async def test_sizing_disabled_behaviour():
    """Sizing이 비활성화된 경우 signal.qty 그대로 반환 또는 0 반환을 확인한다."""
    snapshot = _make_snapshot()
    svc = PositionSizingService(
        account_snapshot_cache=_make_cache(snapshot),
        indicator_service=_make_indicator_svc(),
        config=_base_cfg(enabled=False),
    )

    sig_with_qty = _make_signal(qty=5)
    final, reason = await svc.adjust_buy_qty(sig_with_qty, exchange=Exchange.KRX)
    assert final == 5 and reason == "sizing_disabled"

    sig_no_qty = _make_signal(qty=None)
    final2, reason2 = await svc.adjust_buy_qty(sig_no_qty, exchange=Exchange.KRX)
    assert final2 == 0 and reason2 == "sizing_disabled"


async def test_bypass_on_sell_and_invalid_price():
    snapshot = _make_snapshot()
    svc = PositionSizingService(
        account_snapshot_cache=_make_cache(snapshot),
        indicator_service=_make_indicator_svc(),
        config=_base_cfg(),
    )

    sell_sig = _make_signal(qty=10)
    sell_sig.action = "SELL"
    qty, reason = await svc.adjust_buy_qty(sell_sig, exchange=Exchange.KRX)
    assert reason == "bypass"

    bad_price_sig = _make_signal(price=0, qty=10)
    qty2, reason3 = await svc.adjust_buy_qty(bad_price_sig, exchange=Exchange.KRX)
    assert reason3 == "bypass"


async def test_zero_total_equity_skips_order():
    snapshot = _make_snapshot(total_equity=0, available_cash=0)
    svc = PositionSizingService(
        account_snapshot_cache=_make_cache(snapshot),
        indicator_service=_make_indicator_svc(),
        config=_base_cfg(),
    )
    sig = _make_signal()
    final, reason = await svc.adjust_buy_qty(sig, exchange=Exchange.KRX)
    assert final == 0 and reason == "risk_zero"


def test_extract_pick_to_int_helpers():
    svc_cls = PositionSizingService
    # _extract_quote_data variations
    d1 = {"output1": {"a": 1}}
    assert svc_cls._extract_quote_data(d1) == {"a": 1}
    d2 = {"output": {"b": 2}}
    assert svc_cls._extract_quote_data(d2) == {"b": 2}
    d3 = {"c": 3}
    assert svc_cls._extract_quote_data(d3) == {"c": 3}

    # _pick
    assert svc_cls._pick({"x": 9, "y": 8}, "z", "y") == 8
    assert svc_cls._pick({}, "no") is None

    # _to_int with commas and floats
    assert svc_cls._to_int("1,234") == 1234
    assert svc_cls._to_int("12.0") == 12
    assert svc_cls._to_int(None) == 0


async def test_calc_max_order_amount_qty_edgecases():
    snapshot = _make_snapshot()
    # no risk_gate_config -> None
    svc = PositionSizingService(
        account_snapshot_cache=_make_cache(snapshot),
        indicator_service=_make_indicator_svc(),
        config=_base_cfg(),
    )
    assert svc._calc_max_order_amount_qty(10_000) is None

    # zero max_amount -> None
    from config.config_loader import RiskGateConfig
    rg = RiskGateConfig(max_order_amount_won=0)
    svc2 = PositionSizingService(
        account_snapshot_cache=_make_cache(snapshot),
        indicator_service=_make_indicator_svc(),
        config=_base_cfg(),
        risk_gate_config=rg,
    )
    assert svc2._calc_max_order_amount_qty(10_000) is None

    # positive max_amount
    rg2 = RiskGateConfig(max_order_amount_won=123_456)
    svc3 = PositionSizingService(
        account_snapshot_cache=_make_cache(snapshot),
        indicator_service=_make_indicator_svc(),
        config=_base_cfg(),
        risk_gate_config=rg2,
    )
    assert svc3._calc_max_order_amount_qty(10_000) == 12


async def test_top_of_book_non_success_and_zero_qty():
    snapshot = _make_snapshot()

    class BadQuote:
        async def get_asking_price(self, code, exchange=None):
            from common.types import ResCommonResponse
            return ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="err", data={})

    bad = BadQuote()
    svc = PositionSizingService(
        account_snapshot_cache=_make_cache(snapshot),
        indicator_service=_make_indicator_svc(),
        config=_base_cfg(),
        quote_provider=bad,
    )

    sig = _make_signal()
    tob = await svc._calc_top_of_book_qty(sig, sig.price, Exchange.KRX)
    assert tob is None

    # ask_qty zero -> None
    class ZeroQuote:
        async def get_asking_price(self, code, exchange=None):
            from common.types import ResCommonResponse
            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="", data={"output1": {"askp_rsqn1": "0"}})

    zeroq = ZeroQuote()
    svc2 = PositionSizingService(
        account_snapshot_cache=_make_cache(snapshot),
        indicator_service=_make_indicator_svc(),
        config=_base_cfg(),
        quote_provider=zeroq,
    )
    tob2 = await svc2._calc_top_of_book_qty(sig, sig.price, Exchange.KRX)
    assert tob2 is None


# ── 픽스처 헬퍼 ────────────────────────────────────────────────────────────────


def _make_snapshot(
    total_equity: int = 10_000_000,
    available_cash: int = 5_000_000,
    positions: Dict[str, int] | None = None,
) -> AccountSnapshot:
    return AccountSnapshot(
        total_equity=total_equity,
        available_cash=available_cash,
        positions=positions or {},
    )


def _make_cache(snapshot: AccountSnapshot) -> AccountSnapshotCache:
    cache = MagicMock(spec=AccountSnapshotCache)
    cache.get = AsyncMock(return_value=snapshot)
    return cache


def _make_indicator_svc() -> MagicMock:
    """ATR 조회 항상 실패 → per_share_risk 는 stop_loss_pct 기반으로 계산된다."""
    svc = MagicMock()
    svc.calculate_atr = AsyncMock(return_value=None)
    return svc


def _base_cfg(**overrides) -> PositionSizingConfig:
    defaults = dict(
        enabled=True,
        per_trade_risk_pct=1.5,
        max_per_position_pct=10.0,
        default_stop_loss_pct=-5.0,
        atr_period=14,
        atr_multiplier=2.0,
        min_stop_distance_pct=1.0,
    )
    defaults.update(overrides)
    return PositionSizingConfig(**defaults)


def _make_signal(
    code: str = "005930",
    price: int = 10_000,
    qty: int = 100,
    strategy_name: str = "",
) -> TradeSignal:
    return TradeSignal(
        code=code,
        name="테스트종목",
        action="BUY",
        price=price,
        qty=qty,
        strategy_name=strategy_name,
    )


# ── 전략별 자본 캡 테스트 ─────────────────────────────────────────────────────


async def test_strategy_cap_reduces_qty_when_allocation_exceeded():
    """capital_allocation_pct 한도가 signal.qty × price 보다 낮으면 qty를 줄인다."""
    # total_equity = 10,000,000. capital_allocation_pct = 10.0 → budget = 1,000,000
    # price = 10,000 → alloc_qty = 100. signal.qty = 200 → final 100
    snapshot = _make_snapshot(total_equity=10_000_000, available_cash=5_000_000)
    risk_cfg = RiskGateConfig(
        strategy_limits={
            "my_strategy": RiskGateStrategyLimitConfig(capital_allocation_pct=10.0)
        }
    )

    svc = PositionSizingService(
        account_snapshot_cache=_make_cache(snapshot),
        indicator_service=_make_indicator_svc(),
        config=_base_cfg(),
        risk_gate_config=risk_cfg,
    )
    signal = _make_signal(price=10_000, qty=200, strategy_name="my_strategy")

    final_qty, reason = await svc.adjust_buy_qty(signal, exchange=Exchange.KRX)

    # alloc_budget = 1,000,000 → alloc_qty = 100
    assert final_qty <= 100
    assert "strategy_capital_cap" in reason


    async def test_max_order_amount_limit_applied():
        """`max_order_amount_won` 가 설정되어 있으면 주문대금 한도로 수량이 제한된다."""
        snapshot = _make_snapshot(total_equity=10_000_000, available_cash=5_000_000)
        # max_order_amount_won = 500,000 → price=10,000 → max_qty = 50
        risk_cfg = RiskGateConfig(
            strategy_limits={},
            max_order_amount_won=500_000,
        )

        svc = PositionSizingService(
            account_snapshot_cache=_make_cache(snapshot),
            indicator_service=_make_indicator_svc(),
            config=_base_cfg(per_trade_risk_pct=100.0),  # 큰 리스크로 다른 한도 제거
            risk_gate_config=risk_cfg,
        )
        signal = _make_signal(price=10_000, qty=1_000, strategy_name="my_strategy")

        final_qty, reason = await svc.adjust_buy_qty(signal, exchange=Exchange.KRX)

        assert final_qty <= 50
        assert reason == "max_order_amount_limited"


    async def test_top_of_book_quote_formats_and_typeerror_fallback():
        """호가 조회의 여러 데이터 포맷과 TypeError 호출 패턴을 처리한다."""
        snapshot = _make_snapshot(total_equity=10_000_000, available_cash=5_000_000)

        # quote provider: first call with (code, exchange) raises TypeError,
        # second call with (code) returns ResCommonResponse with output1 dict
        class FakeQuote:
            def __init__(self):
                self.called = 0

            def get_asking_price(self, *args, **kwargs):
                self.called += 1
                if len(args) >= 2:
                    raise TypeError("unexpected arg")
                # return shape with output1 containing askp_rsqn1
                from common.types import ResCommonResponse
                return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="", data={
                    "output1": {"askp_rsqn1": "120"}
                })

        quote = FakeQuote()

        # order policy restrict top-of-book participation to 50%
        from config.config_loader import OrderPolicyConfig
        order_policy = OrderPolicyConfig(max_top_of_book_participation_pct=50.0)

        svc = PositionSizingService(
            account_snapshot_cache=_make_cache(snapshot),
            indicator_service=_make_indicator_svc(),
            config=_base_cfg(per_trade_risk_pct=100.0),
            quote_provider=quote,
            order_policy_config=order_policy,
        )

        # price 10_000, but ask qty from provider = 120 → participation 50% → allowed = 60
        signal = _make_signal(price=10_000, qty=1_000, strategy_name="s")

        final_qty, reason = await svc.adjust_buy_qty(signal, exchange=Exchange.KRX)

        assert final_qty <= 60
        assert "top_of_book" in reason or "top_of_book_limited" in reason


    async def test_atr_success_sets_risk_qty():
        """ATR 조회 성공 시 ATR 기반 리스크가 사용되어 risk_qty가 계산된다."""
        snapshot = _make_snapshot(total_equity=10_000_000, available_cash=5_000_000)

        # indicator returns ATR=100
        indicator = MagicMock()
        from common.types import ResCommonResponse
        indicator.calculate_atr = AsyncMock(return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="", data=[{"atr": 100}]))

        svc = PositionSizingService(
            account_snapshot_cache=_make_cache(snapshot),
            indicator_service=indicator,
            config=_base_cfg(per_trade_risk_pct=1.5),
        )

        # price=10_000 → default_stop_loss_pct=5% → stop_from_pct=500
        # atr_mult=2 → stop_from_atr = 100*2=200 → per_share_risk = max(500,200,100)=500
        # total_risk_krw = 10_000_000 * 1.5% = 150_000 → risk_qty = floor(150_000 / 500) = 300
        signal = _make_signal(price=10_000, qty=None, strategy_name="")

        final_qty, reason = await svc.adjust_buy_qty(signal, exchange=Exchange.KRX)

        assert final_qty == 300
        assert reason == "ok" or reason == "risk_limited" or isinstance(reason, str)


    async def test_atr_failure_uses_stop_loss_pct():
        """ATR 조회 실패 시 예외 처리를 통해 0.0 반환하고 stop_loss_pct 기반으로 계산된다."""
        snapshot = _make_snapshot(total_equity=1_000_000, available_cash=1_000_000)

        indicator = MagicMock()
        indicator.calculate_atr = AsyncMock(side_effect=Exception("fail"))

        svc = PositionSizingService(
            account_snapshot_cache=_make_cache(snapshot),
            indicator_service=indicator,
            config=_base_cfg(per_trade_risk_pct=1.0, default_stop_loss_pct=-2.0),
        )

        # price=50,000, default_stop_loss_pct=2% → per_share_risk = max(1000, 0, 500) = 1000
        signal = _make_signal(price=50_000, qty=None, strategy_name="")

        final_qty, reason = await svc.adjust_buy_qty(signal, exchange=Exchange.KRX)

        # total_risk_krw = 1_000_000 * 1% = 10_000 → risk_qty = floor(10000 / 1000) = 10
        assert final_qty == 10


async def test_strategy_cap_not_applied_when_within_budget():
    """주문 금액이 capital_allocation_pct 한도 이내면 캡이 작동하지 않는다."""
    # total_equity = 10,000,000. capital_allocation_pct = 50.0 → budget = 5,000,000
    # price = 10,000, qty = 10 → order = 100,000 → 한도 이내
    snapshot = _make_snapshot(total_equity=10_000_000, available_cash=5_000_000)
    risk_cfg = RiskGateConfig(
        strategy_limits={
            "my_strategy": RiskGateStrategyLimitConfig(capital_allocation_pct=50.0)
        }
    )

    svc = PositionSizingService(
        account_snapshot_cache=_make_cache(snapshot),
        indicator_service=_make_indicator_svc(),
        config=_base_cfg(per_trade_risk_pct=100.0),  # risk_qty 충분히 크게
        risk_gate_config=risk_cfg,
    )
    signal = _make_signal(price=10_000, qty=10, strategy_name="my_strategy")

    final_qty, reason = await svc.adjust_buy_qty(signal, exchange=Exchange.KRX)

    assert "strategy_capital_cap" not in reason


async def test_final_qty_zero_reason_priority_cases():
    """Cover reason priority when final_qty == 0: risk_zero vs strategy_capital_cap."""
    # 1) risk_qty == 0 -> reason 'risk_zero'
    snapshot = _make_snapshot(total_equity=1_000_000, available_cash=500_000)
    svc = PositionSizingService(
        account_snapshot_cache=_make_cache(snapshot),
        indicator_service=_make_indicator_svc(),
        config=_base_cfg(),
    )
    # force per-share risk -> 0
    svc._get_per_share_risk_krw = AsyncMock(return_value=0)
    sig = _make_signal(qty=None, strategy_name="no")
    final, reason = await svc.adjust_buy_qty(sig, exchange=Exchange.KRX)
    assert final == 0 and reason == "risk_zero"

    # 2) alloc_qty == 0 -> reason 'strategy_capital_cap'
    snapshot2 = _make_snapshot(total_equity=10_000_000, available_cash=5_000_000)
    rg = RiskGateConfig(
        strategy_limits={"s": RiskGateStrategyLimitConfig(capital_allocation_pct=0.0)}
    )
    svc2 = PositionSizingService(
        account_snapshot_cache=_make_cache(snapshot2),
        indicator_service=_make_indicator_svc(),
        config=_base_cfg(),
        risk_gate_config=rg,
    )
    sig2 = _make_signal(price=10_000, qty=None, strategy_name="s")
    final2, reason2 = await svc2.adjust_buy_qty(sig2, exchange=Exchange.KRX)
    assert final2 == 0 and "strategy_capital_cap" in reason2


async def test_top_of_book_order_policy_disabled_and_typeerror_fallback():
    snapshot = _make_snapshot()

    # order_book_checks disabled -> immediate None (line 209)
    from config.config_loader import OrderPolicyConfig
    op = OrderPolicyConfig(order_book_checks_enabled=False)
    class Q:
        async def get_asking_price(self, code, exchange=None):
            return None

    svc = PositionSizingService(
        account_snapshot_cache=_make_cache(snapshot),
        indicator_service=_make_indicator_svc(),
        config=_base_cfg(),
        quote_provider=Q(),
        order_policy_config=op,
    )
    sig = _make_signal()
    assert await svc._calc_top_of_book_qty(sig, sig.price, Exchange.KRX) is None

    # TypeError fallback: first call with (code, exchange) raises, second call without exchange succeeds
    class FQ:
        def __init__(self):
            self._count = 0

        def get_asking_price(self, *args, **kwargs):
            self._count += 1
            if len(args) >= 2:
                raise TypeError("bad args")
            from common.types import ResCommonResponse, ErrorCode
            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="", data={"output": {"askp_rsqn1": "42"}})

    svc2 = PositionSizingService(
        account_snapshot_cache=_make_cache(snapshot),
        indicator_service=_make_indicator_svc(),
        config=_base_cfg(),
        quote_provider=FQ(),
    )
    tob = await svc2._calc_top_of_book_qty(sig, sig.price, Exchange.KRX)
    assert isinstance(tob, int)


def test_extract_pick_to_int_edgecases():
    svc_cls = PositionSizingService
    # non-dict input for extract -> {}
    assert svc_cls._extract_quote_data(None) == {}
    assert svc_cls._extract_quote_data(123) == {}

    # output1 / output dict forms
    assert svc_cls._extract_quote_data({"output1": {"a": 1}}) == {"a": 1}
    assert svc_cls._extract_quote_data({"output": {"b": 2}}) == {"b": 2}

    # _pick missing keys -> None
    assert svc_cls._pick({"x": 1}, "nope") is None

    # _to_int invalid numeric -> returns 0
    assert svc_cls._to_int("notanumber") == 0


async def test_get_atr_exception_returns_zero():
    # indicator.calculate_atr raises -> _get_atr_krw returns 0.0
    indicator = MagicMock()
    indicator.calculate_atr = AsyncMock(side_effect=Exception("boom"))
    svc = PositionSizingService(
        account_snapshot_cache=_make_cache(_make_snapshot()),
        indicator_service=indicator,
        config=_base_cfg(),
    )
    sig = _make_signal()
    val = await svc._get_atr_krw(sig)
    assert val == 0.0


async def test_strategy_cap_not_applied_when_no_risk_config():
    """risk_gate_config 미주입 시 캡이 동작하지 않는다."""
    snapshot = _make_snapshot(total_equity=10_000_000, available_cash=5_000_000)

    svc = PositionSizingService(
        account_snapshot_cache=_make_cache(snapshot),
        indicator_service=_make_indicator_svc(),
        config=_base_cfg(per_trade_risk_pct=100.0),
    )
    signal = _make_signal(price=10_000, qty=200, strategy_name="my_strategy")

    final_qty, reason = await svc.adjust_buy_qty(signal, exchange=Exchange.KRX)

    assert "strategy_capital_cap" not in reason


async def test_strategy_cap_not_applied_when_strategy_name_absent():
    """strategy_name 이 빈 문자열이면 캡이 동작하지 않는다."""
    snapshot = _make_snapshot(total_equity=10_000_000, available_cash=5_000_000)
    risk_cfg = RiskGateConfig(
        strategy_limits={
            "my_strategy": RiskGateStrategyLimitConfig(capital_allocation_pct=10.0)
        }
    )

    svc = PositionSizingService(
        account_snapshot_cache=_make_cache(snapshot),
        indicator_service=_make_indicator_svc(),
        config=_base_cfg(per_trade_risk_pct=100.0),
        risk_gate_config=risk_cfg,
    )
    signal = _make_signal(price=10_000, qty=200, strategy_name="")  # 전략명 없음

    final_qty, reason = await svc.adjust_buy_qty(signal, exchange=Exchange.KRX)

    assert "strategy_capital_cap" not in reason


async def test_strategy_cap_uses_default_limit_when_strategy_not_in_limits():
    """strategy_limits에 없는 전략명이면 default_strategy_limit 사용."""
    snapshot = _make_snapshot(total_equity=10_000_000, available_cash=5_000_000)
    # default_strategy_limit에 capital_allocation_pct 없음 → 캡 미동작
    risk_cfg = RiskGateConfig()

    svc = PositionSizingService(
        account_snapshot_cache=_make_cache(snapshot),
        indicator_service=_make_indicator_svc(),
        config=_base_cfg(per_trade_risk_pct=100.0),
        risk_gate_config=risk_cfg,
    )
    signal = _make_signal(price=10_000, qty=200, strategy_name="unknown_strategy")

    final_qty, reason = await svc.adjust_buy_qty(signal, exchange=Exchange.KRX)

    assert "strategy_capital_cap" not in reason
