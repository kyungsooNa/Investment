"""PositionSizingService 단위 테스트."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from services.position_sizing_service import PositionSizingService
from config.config_loader import OrderPolicyConfig, PositionSizingConfig, RiskGateConfig
from common.types import TradeSignal, ResCommonResponse, ErrorCode
from core.account_snapshot import AccountSnapshot


def _make_config(**kwargs) -> PositionSizingConfig:
    defaults = dict(
        enabled=True,
        per_trade_risk_pct=1.0,
        max_per_position_pct=10.0,
        default_stop_loss_pct=-5.0,
        atr_period=14,
        atr_multiplier=2.0,
        min_stop_distance_pct=1.0,
        snapshot_ttl_sec=60,
    )
    defaults.update(kwargs)
    return PositionSizingConfig(**defaults)


def _make_snapshot(total_equity=10_000_000, available_cash=5_000_000, positions=None) -> AccountSnapshot:
    return AccountSnapshot(
        total_equity=total_equity,
        available_cash=available_cash,
        positions=positions or {},
    )


def _quote(ask_qty=100):
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data={
            "askp1": "10000",
            "bidp1": "9990",
            "askp_rsqn1": str(ask_qty),
            "bidp_rsqn1": "100",
        },
    )


def _make_service(
    snapshot,
    atr_value=0.0,
    cfg=None,
    risk_gate_config=None,
    quote_provider=None,
    order_policy_config=None,
    enable_intracycle_reservations=False,
):
    cache = AsyncMock()
    cache.get.return_value = snapshot

    indicator = AsyncMock()
    if atr_value > 0:
        indicator.calculate_atr.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="OK",
            data=[{"atr": atr_value, "date": "20250101"}],
        )
    else:
        indicator.calculate_atr.return_value = None

    svc = PositionSizingService(
        account_snapshot_cache=cache,
        indicator_service=indicator,
        config=cfg or _make_config(),
        risk_gate_config=risk_gate_config,
        quote_provider=quote_provider,
        order_policy_config=order_policy_config,
        enable_intracycle_reservations=enable_intracycle_reservations,
    )
    return svc, cache, indicator


def _buy_signal(price=10_000, qty=100, stop_loss_pct=None, atr_multiplier=None):
    return TradeSignal(
        code="005930", name="삼성전자", action="BUY",
        price=price, qty=qty, reason="test", strategy_name="test",
        stop_loss_pct=stop_loss_pct, atr_multiplier=atr_multiplier,
    )


# ── 기본 산식 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sizing_disabled_returns_signal_qty():
    svc, _, _ = _make_service(_make_snapshot(), cfg=_make_config(enabled=False))
    signal = _buy_signal(qty=50)
    qty, reason = await svc.adjust_buy_qty(signal)
    assert qty == 50
    assert reason == "sizing_disabled"


@pytest.mark.asyncio
async def test_sell_signal_bypass():
    svc, _, _ = _make_service(_make_snapshot())
    signal = TradeSignal(code="A", name="X", action="SELL", price=10_000, qty=10,
                         reason="r", strategy_name="s")
    qty, reason = await svc.adjust_buy_qty(signal)
    assert qty == 10
    assert reason == "bypass"


@pytest.mark.asyncio
async def test_zero_qty_bypass():
    svc, _, _ = _make_service(_make_snapshot())
    signal = _buy_signal(qty=0)
    qty, reason = await svc.adjust_buy_qty(signal)
    assert qty == 0
    assert reason == "bypass"


@pytest.mark.asyncio
async def test_zero_price_bypass():
    svc, _, _ = _make_service(_make_snapshot())
    signal = _buy_signal(price=0, qty=10)
    qty, reason = await svc.adjust_buy_qty(signal)
    assert qty == 10
    assert reason == "bypass"


@pytest.mark.asyncio
async def test_zero_total_equity_returns_risk_zero():
    snap = _make_snapshot(total_equity=0, available_cash=1_000_000)
    svc, _, _ = _make_service(snap)
    qty, reason = await svc.adjust_buy_qty(_buy_signal())
    assert qty == 0
    assert reason == "risk_zero"


# ── risk_qty 제한 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_risk_qty_limits_signal():
    """총자산 10M, risk 1%, 손절 5% → per_share_risk=500, risk_qty=200."""
    snap = _make_snapshot(total_equity=10_000_000, available_cash=10_000_000)
    cfg = _make_config(per_trade_risk_pct=1.0, default_stop_loss_pct=-5.0,
                       min_stop_distance_pct=0.0, max_per_position_pct=100.0)
    svc, _, _ = _make_service(snap, cfg=cfg)
    # per_share_risk = 10000 * 5% = 500
    # total_risk = 10M * 1% = 100,000 → risk_qty = 200
    # signal.qty=500 > 200 → limited to 200
    qty, reason = await svc.adjust_buy_qty(_buy_signal(price=10_000, qty=500))
    assert qty == 200
    assert reason == "risk_limited"


# ── cap_qty 제한 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cap_qty_limits_when_position_near_cap():
    """총자산 10M, cap 10% = 1M, 이미 800K 보유 → cap 잔여 200K/10000 = 20주."""
    snap = _make_snapshot(total_equity=10_000_000, available_cash=10_000_000,
                          positions={"005930": 800_000})
    cfg = _make_config(max_per_position_pct=10.0, per_trade_risk_pct=100.0,
                       default_stop_loss_pct=-5.0, min_stop_distance_pct=0.0)
    svc, _, _ = _make_service(snap, cfg=cfg)
    qty, reason = await svc.adjust_buy_qty(_buy_signal(price=10_000, qty=100))
    assert qty == 20
    assert reason == "cap_limited"


@pytest.mark.asyncio
async def test_cap_exhausted_returns_zero():
    """이미 캡 초과 보유 → cap_qty=0 → 주문 skip."""
    snap = _make_snapshot(total_equity=10_000_000, available_cash=10_000_000,
                          positions={"005930": 1_500_000})
    cfg = _make_config(max_per_position_pct=10.0, per_trade_risk_pct=100.0,
                       default_stop_loss_pct=-5.0, min_stop_distance_pct=0.0)
    svc, _, _ = _make_service(snap, cfg=cfg)
    qty, reason = await svc.adjust_buy_qty(_buy_signal(price=10_000, qty=10))
    assert qty == 0
    assert reason == "cap_exhausted"


# ── cash_qty 제한 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cash_limits_qty():
    """현금 50K, 주가 10K → cash_qty=5."""
    snap = _make_snapshot(total_equity=10_000_000, available_cash=50_000)
    cfg = _make_config(per_trade_risk_pct=100.0, max_per_position_pct=100.0,
                       default_stop_loss_pct=-5.0, min_stop_distance_pct=0.0)
    svc, _, _ = _make_service(snap, cfg=cfg)
    qty, reason = await svc.adjust_buy_qty(_buy_signal(price=10_000, qty=100))
    assert qty == 5
    assert reason == "cash_limited"


@pytest.mark.asyncio
async def test_cash_short_returns_zero():
    snap = _make_snapshot(total_equity=10_000_000, available_cash=5_000)
    cfg = _make_config(per_trade_risk_pct=100.0, max_per_position_pct=100.0,
                       default_stop_loss_pct=-5.0, min_stop_distance_pct=0.0)
    svc, _, _ = _make_service(snap, cfg=cfg)
    qty, reason = await svc.adjust_buy_qty(_buy_signal(price=10_000, qty=100))
    assert qty == 0
    assert reason == "cash_short"


# ── ATR ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_atr_based_per_share_risk():
    """ATR=300, multiplier=2 → stop_from_atr=600 > stop_from_pct=500 → 600이 분모."""
    snap = _make_snapshot(total_equity=10_000_000, available_cash=10_000_000)
    cfg = _make_config(per_trade_risk_pct=1.0, atr_multiplier=2.0,
                       default_stop_loss_pct=-5.0, min_stop_distance_pct=0.0,
                       max_per_position_pct=100.0)
    svc, _, _ = _make_service(snap, atr_value=300.0, cfg=cfg)
    # per_share_risk = max(500, 300*2=600) = 600
    # risk_qty = floor(100_000 / 600) = 166
    qty, reason = await svc.adjust_buy_qty(_buy_signal(price=10_000, qty=500))
    assert qty == 166
    assert reason == "risk_limited"


@pytest.mark.asyncio
async def test_atr_failure_falls_back_to_stop_loss_pct():
    """ATR 조회 실패 시 stop_loss_pct 단독으로 산정."""
    snap = _make_snapshot(total_equity=10_000_000, available_cash=10_000_000)
    cfg = _make_config(per_trade_risk_pct=1.0, default_stop_loss_pct=-5.0,
                       min_stop_distance_pct=0.0, max_per_position_pct=100.0)
    svc, _, _ = _make_service(snap, atr_value=0.0, cfg=cfg)
    # per_share_risk = 10000 * 5% = 500 → risk_qty = 200
    qty, reason = await svc.adjust_buy_qty(_buy_signal(price=10_000, qty=500))
    assert qty == 200


# ── ok 케이스 ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ok_when_signal_qty_smallest():
    snap = _make_snapshot(total_equity=100_000_000, available_cash=100_000_000)
    cfg = _make_config(per_trade_risk_pct=100.0, max_per_position_pct=100.0,
                       default_stop_loss_pct=-5.0, min_stop_distance_pct=0.0)
    svc, _, _ = _make_service(snap, cfg=cfg)
    qty, reason = await svc.adjust_buy_qty(_buy_signal(price=10_000, qty=1))
    assert qty == 1
    assert reason == "ok"


# ── 스냅샷 캐시 API 비호출 검증 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_snapshot_cache_called_once_for_multiple_signals():
    """연속 시그널에서 AccountSnapshotCache.get()이 시그널 수만큼만 호출(내부 단일 cache hit)."""
    snap = _make_snapshot(total_equity=100_000_000, available_cash=100_000_000)
    svc, cache, _ = _make_service(snap)
    for _ in range(5):
        await svc.adjust_buy_qty(_buy_signal(qty=1))
    # 각 adjust_buy_qty 호출마다 cache.get() 1회씩(캐시 hit 여부는 캐시 자체가 제어)
    assert cache.get.call_count == 5


# ── signal.stop_loss_pct 우선 적용 ────────────────────────────────────

@pytest.mark.asyncio
async def test_signal_stop_loss_pct_overrides_default():
    """signal.stop_loss_pct=-2.0 → per_share_risk=200 → risk_qty=500."""
    snap = _make_snapshot(total_equity=10_000_000, available_cash=10_000_000)
    cfg = _make_config(per_trade_risk_pct=1.0, default_stop_loss_pct=-5.0,
                       min_stop_distance_pct=0.0, max_per_position_pct=100.0)
    svc, _, _ = _make_service(snap, cfg=cfg)
    signal = _buy_signal(price=10_000, qty=1000, stop_loss_pct=-2.0)
    qty, reason = await svc.adjust_buy_qty(signal)
    # per_share_risk = 10000 * 2% = 200 → risk_qty = 500
    assert qty == 500
    assert reason == "risk_limited"


@pytest.mark.asyncio
async def test_signal_stop_loss_price_overrides_stop_loss_pct_for_per_share_risk():
    """stop_loss_price 명시 시 절대가 기준 1주 리스크를 우선 사용한다."""
    snap = _make_snapshot(total_equity=10_000_000, available_cash=10_000_000)
    cfg = _make_config(
        per_trade_risk_pct=1.0,
        default_stop_loss_pct=-5.0,
        min_stop_distance_pct=0.0,
        max_per_position_pct=100.0,
    )
    svc, _, _ = _make_service(snap, cfg=cfg)
    signal = _buy_signal(price=10_000, qty=1000, stop_loss_pct=-2.0)
    signal.stop_loss_price = 9_500

    qty, reason = await svc.adjust_buy_qty(signal)

    # per_share_risk = 10000 - 9500 = 500 → risk_qty = 200
    assert qty == 200
    assert reason == "risk_limited"


# ── qty=None: PositionSizingService 단독 결정 ─────────────────────────────

@pytest.mark.asyncio
async def test_qty_none_sizing_determines_result():
    """qty=None: signal cap 없이 risk/cap/cash 4-way min으로 qty 결정."""
    snap = _make_snapshot(total_equity=10_000_000, available_cash=10_000_000)
    cfg = _make_config(per_trade_risk_pct=1.0, default_stop_loss_pct=-5.0,
                       min_stop_distance_pct=0.0, max_per_position_pct=100.0)
    svc, _, _ = _make_service(snap, cfg=cfg)
    signal = TradeSignal(
        code="005930", name="삼성전자", action="BUY",
        price=10_000, qty=None, reason="test", strategy_name="test",
    )
    # risk_qty=200, cap_qty/cash_qty >> 200 → sizing 단독 결정
    qty, reason = await svc.adjust_buy_qty(signal)
    assert qty == 200
    assert reason == "risk_limited"


@pytest.mark.asyncio
async def test_qty_none_sizing_disabled_returns_zero():
    """qty=None + sizing 비활성화 → (0, 'sizing_disabled') — 주문 skip."""
    svc, _, _ = _make_service(_make_snapshot(), cfg=_make_config(enabled=False))
    signal = TradeSignal(
        code="005930", name="삼성전자", action="BUY",
        price=10_000, qty=None, reason="test", strategy_name="test",
    )
    qty, reason = await svc.adjust_buy_qty(signal)
    assert qty == 0
    assert reason == "sizing_disabled"


@pytest.mark.asyncio
async def test_qty_int_acts_as_voluntary_cap():
    """qty=int: sizing 결과(200)보다 작은 signal.qty(50) → signal.qty가 자발적 상한."""
    snap = _make_snapshot(total_equity=10_000_000, available_cash=10_000_000)
    cfg = _make_config(per_trade_risk_pct=1.0, default_stop_loss_pct=-5.0,
                       min_stop_distance_pct=0.0, max_per_position_pct=100.0)
    svc, _, _ = _make_service(snap, cfg=cfg)
    # risk_qty=200, signal.qty=50 → signal cap 적용 → 50
    qty, reason = await svc.adjust_buy_qty(_buy_signal(price=10_000, qty=50))
    assert qty == 50
    assert reason == "ok"


@pytest.mark.asyncio
async def test_max_order_amount_limits_qty_before_risk_gate():
    """단일 주문 한도를 넘는 수량은 RiskGate에서 실패하기 전에 줄인다."""
    snap = _make_snapshot(total_equity=1_000_000_000, available_cash=1_000_000_000)
    cfg = _make_config(
        per_trade_risk_pct=100.0,
        max_per_position_pct=100.0,
        default_stop_loss_pct=-5.0,
        min_stop_distance_pct=0.0,
    )
    risk_cfg = RiskGateConfig(max_order_amount_won=50_000_000)
    svc, _, _ = _make_service(snap, cfg=cfg, risk_gate_config=risk_cfg)

    qty, reason = await svc.adjust_buy_qty(_buy_signal(price=135_900, qty=375))

    assert qty == 367
    assert 135_900 * qty <= 50_000_000
    assert reason == "max_order_amount_limited"


@pytest.mark.asyncio
async def test_top_of_book_qty_limits_buy_qty_before_order_policy():
    """최우선 매도호가 잔량보다 큰 매수 수량은 주문 전 줄인다."""
    snap = _make_snapshot(total_equity=1_000_000_000, available_cash=1_000_000_000)
    cfg = _make_config(
        per_trade_risk_pct=100.0,
        max_per_position_pct=100.0,
        default_stop_loss_pct=-5.0,
        min_stop_distance_pct=0.0,
    )
    provider = AsyncMock()
    provider.get_asking_price.return_value = _quote(ask_qty=200)
    svc, _, _ = _make_service(
        snap,
        cfg=cfg,
        quote_provider=provider,
        order_policy_config=OrderPolicyConfig(order_book_checks_enabled=True),
    )

    qty, reason = await svc.adjust_buy_qty(_buy_signal(price=51_800, qty=921))

    assert qty == 200
    assert reason == "top_of_book_limited"


@pytest.mark.asyncio
async def test_top_of_book_participation_limits_buy_qty():
    """호가 잔량 참여율 제한도 포지션 사이징 단계에서 반영한다."""
    snap = _make_snapshot(total_equity=1_000_000_000, available_cash=1_000_000_000)
    cfg = _make_config(
        per_trade_risk_pct=100.0,
        max_per_position_pct=100.0,
        default_stop_loss_pct=-5.0,
        min_stop_distance_pct=0.0,
    )
    provider = AsyncMock()
    provider.get_asking_price.return_value = _quote(ask_qty=100)
    svc, _, _ = _make_service(
        snap,
        cfg=cfg,
        quote_provider=provider,
        order_policy_config=OrderPolicyConfig(
            order_book_checks_enabled=True,
            max_top_of_book_participation_pct=50.0,
        ),
    )

    qty, reason = await svc.adjust_buy_qty(_buy_signal(price=10_000, qty=80))

    assert qty == 50
    assert reason == "top_of_book_limited"


@pytest.mark.asyncio
async def test_order_amount_cap_when_single_share_exceeds_limit():
    """1주 가격이 max_order_amount_won 초과 → final_qty=0, reason='order_amount_cap'."""
    snap = _make_snapshot(total_equity=1_000_000_000, available_cash=1_000_000_000)
    cfg = _make_config(
        per_trade_risk_pct=100.0,
        max_per_position_pct=100.0,
        default_stop_loss_pct=-5.0,
        min_stop_distance_pct=0.0,
    )
    risk_cfg = RiskGateConfig(max_order_amount_won=10_000_000)
    svc, _, _ = _make_service(snap, cfg=cfg, risk_gate_config=risk_cfg)

    qty, reason = await svc.adjust_buy_qty(_buy_signal(price=15_000_000, qty=1))

    assert qty == 0
    assert reason == "order_amount_cap"


@pytest.mark.asyncio
async def test_no_order_amount_cap_when_risk_gate_not_set():
    """risk_gate_config=None → max_order_amount_won 제약 없음, 기존 동작 유지."""
    snap = _make_snapshot(total_equity=1_000_000_000, available_cash=1_000_000_000)
    cfg = _make_config(
        per_trade_risk_pct=100.0,
        max_per_position_pct=100.0,
        default_stop_loss_pct=-5.0,
        min_stop_distance_pct=0.0,
    )
    svc, _, _ = _make_service(snap, cfg=cfg, risk_gate_config=None)

    qty, reason = await svc.adjust_buy_qty(_buy_signal(price=10_000, qty=5))

    assert qty == 5
    assert reason == "ok"


# ── P0 0-2: real_mode_overrides 분기 ──────────────────────────────────

def _env(is_paper_trading: bool):
    env = MagicMock()
    env.is_paper_trading = is_paper_trading
    return env


def _make_service_with_env(env, cfg=None):
    # NOTE: P0 0-7 — 기존 real_mode_overrides 동작 검증 테스트가 의존하므로
    # helper default 는 "real_limited" overlay 를 사용. canary 분기 테스트는
    # 별도 `_make_service_with_profile` 또는 명시적 operating_profile 인자를 사용한다.
    cache = AsyncMock()
    cache.get.return_value = _make_snapshot(
        total_equity=10_000_000,
        available_cash=10_000_000,
    )
    indicator = AsyncMock()
    indicator.calculate_atr.return_value = None
    svc = PositionSizingService(
        account_snapshot_cache=cache,
        indicator_service=indicator,
        config=cfg or _make_config(),
        env=env,
        operating_profile="real_limited",
    )
    return svc


def test_position_sizing_is_real_mode_default_false_when_env_missing():
    svc = _make_service_with_env(env=None)
    assert svc._is_real_mode() is False


def test_position_sizing_is_real_mode_false_for_paper_env():
    svc = _make_service_with_env(env=_env(is_paper_trading=True))
    assert svc._is_real_mode() is False


def test_position_sizing_is_real_mode_true_for_real_env():
    svc = _make_service_with_env(env=_env(is_paper_trading=False))
    assert svc._is_real_mode() is True


def test_position_sizing_paper_uses_top_level_values():
    cfg = _make_config(per_trade_risk_pct=1.5, max_per_position_pct=5.0)
    svc = _make_service_with_env(env=_env(is_paper_trading=True), cfg=cfg)
    assert svc._effective_per_trade_risk_pct() == 1.5
    assert svc._effective_max_per_position_pct() == 5.0


def test_position_sizing_real_uses_overrides():
    cfg = _make_config(per_trade_risk_pct=1.5, max_per_position_pct=5.0)
    svc = _make_service_with_env(env=_env(is_paper_trading=False), cfg=cfg)
    # overrides default canary 값 적용
    assert svc._effective_per_trade_risk_pct() == 0.5
    assert svc._effective_max_per_position_pct() == 3.0


def test_position_sizing_real_uses_overrides_user_yaml():
    cfg = PositionSizingConfig(
        per_trade_risk_pct=1.5,
        max_per_position_pct=5.0,
        real_mode_overrides={"per_trade_risk_pct": 0.25, "max_per_position_pct": 2.0},
    )
    svc = _make_service_with_env(env=_env(is_paper_trading=False), cfg=cfg)
    assert svc._effective_per_trade_risk_pct() == 0.25
    assert svc._effective_max_per_position_pct() == 2.0


@pytest.mark.asyncio
async def test_position_sizing_real_mode_shrinks_risk_qty():
    """동일 paper/real 설정에서 real 은 canary overrides 로 인해 qty 가 더 작아야 한다."""
    snap = _make_snapshot(total_equity=10_000_000, available_cash=10_000_000)
    cfg = _make_config(per_trade_risk_pct=1.0, default_stop_loss_pct=-5.0, min_stop_distance_pct=0.0)
    cache = AsyncMock()
    cache.get.return_value = snap
    indicator = AsyncMock()
    indicator.calculate_atr.return_value = None

    paper_svc = PositionSizingService(
        account_snapshot_cache=cache, indicator_service=indicator,
        config=cfg, env=_env(is_paper_trading=True),
    )
    real_svc = PositionSizingService(
        account_snapshot_cache=cache, indicator_service=indicator,
        config=cfg, env=_env(is_paper_trading=False),
    )
    sig = _buy_signal(price=10_000, qty=10_000, stop_loss_pct=-5.0)

    paper_qty, _ = await paper_svc.adjust_buy_qty(sig)
    real_qty, _ = await real_svc.adjust_buy_qty(sig)

    # paper: per_trade_risk_pct=1.0 / max_per_position_pct=10.0
    # real overlay: per_trade_risk_pct=0.5 / max_per_position_pct=3.0 → 둘 다 빠듯해진다
    assert paper_qty > real_qty > 0


# ── P0 0-7: operating_profile 분기 ────────────────────────────────────


def _make_service_with_profile(env, cfg=None, operating_profile="canary"):
    cache = AsyncMock()
    cache.get.return_value = _make_snapshot(
        total_equity=10_000_000,
        available_cash=10_000_000,
    )
    indicator = AsyncMock()
    indicator.calculate_atr.return_value = None
    svc = PositionSizingService(
        account_snapshot_cache=cache,
        indicator_service=indicator,
        config=cfg or _make_config(),
        env=env,
        operating_profile=operating_profile,
    )
    return svc


def test_position_sizing_canary_profile_uses_canary_overrides():
    """real 모드 + profile=canary → canary_overrides 적용 (0.25%, 1.5%)."""
    cfg = _make_config(per_trade_risk_pct=1.5, max_per_position_pct=5.0)
    svc = _make_service_with_profile(
        env=_env(is_paper_trading=False), cfg=cfg, operating_profile="canary"
    )
    assert svc._effective_per_trade_risk_pct() == 0.25
    assert svc._effective_max_per_position_pct() == 1.5


def test_position_sizing_real_limited_profile_uses_real_mode_overrides():
    """real 모드 + profile=real_limited → real_mode_overrides 적용 (0.5%, 3.0%)."""
    cfg = _make_config(per_trade_risk_pct=1.5, max_per_position_pct=5.0)
    svc = _make_service_with_profile(
        env=_env(is_paper_trading=False), cfg=cfg, operating_profile="real_limited"
    )
    assert svc._effective_per_trade_risk_pct() == 0.5
    assert svc._effective_max_per_position_pct() == 3.0


def test_position_sizing_real_full_profile_uses_base_values():
    """real 모드 + profile=real_full → overlay 미적용, base 사용."""
    cfg = _make_config(per_trade_risk_pct=1.5, max_per_position_pct=5.0)
    svc = _make_service_with_profile(
        env=_env(is_paper_trading=False), cfg=cfg, operating_profile="real_full"
    )
    assert svc._effective_per_trade_risk_pct() == 1.5
    assert svc._effective_max_per_position_pct() == 5.0


def test_position_sizing_paper_mode_ignores_profile():
    """paper 모드: profile 무시, base 사용."""
    cfg = _make_config(per_trade_risk_pct=1.5, max_per_position_pct=5.0)
    svc = _make_service_with_profile(
        env=_env(is_paper_trading=True), cfg=cfg, operating_profile="canary"
    )
    assert svc._effective_per_trade_risk_pct() == 1.5
    assert svc._effective_max_per_position_pct() == 5.0


def test_position_sizing_default_profile_is_canary():
    """operating_profile 미지정 시 canary 기본값."""
    cfg = _make_config(per_trade_risk_pct=1.5, max_per_position_pct=5.0)
    svc = _make_service_with_profile(env=_env(is_paper_trading=False), cfg=cfg)
    assert svc._effective_per_trade_risk_pct() == 0.25
    assert svc._effective_max_per_position_pct() == 1.5


def test_position_sizing_canary_overrides_user_yaml():
    """yaml 에서 canary_overrides 명시 시 default 보다 우선."""
    cfg = PositionSizingConfig(
        per_trade_risk_pct=1.5,
        max_per_position_pct=5.0,
        canary_overrides={"per_trade_risk_pct": 0.1, "max_per_position_pct": 0.5},
    )
    svc = _make_service_with_profile(
        env=_env(is_paper_trading=False), cfg=cfg, operating_profile="canary"
    )
    assert svc._effective_per_trade_risk_pct() == 0.1
    assert svc._effective_max_per_position_pct() == 0.5


# ─────────────────────────────────────────────────────────────────────
# P0 0-10: intra-cycle reservation overlay (reserved cash + same-symbol)
# ─────────────────────────────────────────────────────────────────────

def _no_constraint_cfg():
    """risk/cap 이 binding 되지 않게 큰 값 — cash/cap overlay 만 격리 검증."""
    return _make_config(per_trade_risk_pct=100.0, max_per_position_pct=100.0)


@pytest.mark.asyncio
async def test_reservation_reduces_cash_for_second_buy_different_code():
    """같은 사이클: 첫 BUY 가 예약한 현금이 두 번째 BUY 의 cash_qty 에서 차감된다."""
    snap = _make_snapshot(total_equity=100_000_000, available_cash=500_000)
    svc, _, _ = _make_service(
        snap, cfg=_no_constraint_cfg(), enable_intracycle_reservations=True
    )
    # 첫 매수: 코드 A, price 100k → cash 500k / 100k = 5주, 500k 예약
    qty_a, _ = await svc.adjust_buy_qty(
        TradeSignal(code="AAAAAA", name="A", action="BUY", price=100_000, qty=None,
                    reason="r", strategy_name="s1")
    )
    assert qty_a == 5
    # 두 번째 매수: 코드 B (다른 종목) → 가용현금 500k-500k=0 → 0주
    qty_b, reason_b = await svc.adjust_buy_qty(
        TradeSignal(code="BBBBBB", name="B", action="BUY", price=100_000, qty=None,
                    reason="r", strategy_name="s2")
    )
    assert qty_b == 0
    assert reason_b == "cash_short"


@pytest.mark.asyncio
async def test_reservation_same_symbol_reduces_position_cap():
    """같은 사이클: 같은 종목을 여러 전략이 사면 종목별 비중 cap 이 누적 반영된다."""
    # total_equity=10M, max_per_position_pct=10% → 종목당 1M cap. cash 는 충분.
    snap = _make_snapshot(total_equity=10_000_000, available_cash=100_000_000)
    cfg = _make_config(per_trade_risk_pct=100.0, max_per_position_pct=10.0)
    svc, _, _ = _make_service(snap, cfg=cfg, enable_intracycle_reservations=True)
    # 첫 매수: 코드 X, price 100k → cap 1M / 100k = 10주, 1M 예약
    qty1, _ = await svc.adjust_buy_qty(
        TradeSignal(code="XXXXXX", name="X", action="BUY", price=100_000, qty=None,
                    reason="r", strategy_name="s1")
    )
    assert qty1 == 10
    # 두 번째 매수: 같은 코드 X (다른 전략) → 종목 cap 소진 → 0주
    qty2, reason2 = await svc.adjust_buy_qty(
        TradeSignal(code="XXXXXX", name="X", action="BUY", price=100_000, qty=None,
                    reason="r", strategy_name="s2")
    )
    assert qty2 == 0
    assert reason2 == "cap_exhausted"


@pytest.mark.asyncio
async def test_reservation_resets_on_new_snapshot():
    """새 snapshot(fetched_at 변경) 도착 시 예약이 초기화된다 (fill→invalidate 모방)."""
    from datetime import datetime, timedelta
    snap1 = _make_snapshot(total_equity=100_000_000, available_cash=500_000)
    svc, cache, _ = _make_service(
        snap1, cfg=_no_constraint_cfg(), enable_intracycle_reservations=True
    )
    qty1, _ = await svc.adjust_buy_qty(
        TradeSignal(code="AAAAAA", name="A", action="BUY", price=100_000, qty=None,
                    reason="r", strategy_name="s1")
    )
    assert qty1 == 5  # 500k 예약됨

    # 체결 → 캐시 invalidate → 다음 사이클 새 snapshot (fetched_at 다름)
    snap2 = AccountSnapshot(
        total_equity=100_000_000, available_cash=500_000, positions={},
        fetched_at=datetime.now() + timedelta(seconds=120),
    )
    cache.get.return_value = snap2
    qty2, _ = await svc.adjust_buy_qty(
        TradeSignal(code="BBBBBB", name="B", action="BUY", price=100_000, qty=None,
                    reason="r", strategy_name="s2")
    )
    # 예약 초기화되어 새 snapshot 의 500k 전부 사용 가능 → 다시 5주
    assert qty2 == 5


@pytest.mark.asyncio
async def test_no_reservation_overlay_when_flag_disabled():
    """flag=False(기본, backtest) 면 overlay 미적용 — 두 번째 BUY 도 동일 수량."""
    snap = _make_snapshot(total_equity=100_000_000, available_cash=500_000)
    svc, _, _ = _make_service(
        snap, cfg=_no_constraint_cfg(), enable_intracycle_reservations=False
    )
    qty_a, _ = await svc.adjust_buy_qty(
        TradeSignal(code="AAAAAA", name="A", action="BUY", price=100_000, qty=None,
                    reason="r", strategy_name="s1")
    )
    qty_b, _ = await svc.adjust_buy_qty(
        TradeSignal(code="BBBBBB", name="B", action="BUY", price=100_000, qty=None,
                    reason="r", strategy_name="s2")
    )
    # overlay 없으므로 같은 stale snapshot 으로 둘 다 5주 (= 기존 over-allocation 동작)
    assert qty_a == 5
    assert qty_b == 5


@pytest.mark.asyncio
async def test_reservation_default_is_disabled():
    """enable_intracycle_reservations 미지정 시 기본 False (backtest 안전)."""
    snap = _make_snapshot(total_equity=100_000_000, available_cash=500_000)
    svc, _, _ = _make_service(snap, cfg=_no_constraint_cfg())
    qty_a, _ = await svc.adjust_buy_qty(
        TradeSignal(code="AAAAAA", name="A", action="BUY", price=100_000, qty=None,
                    reason="r", strategy_name="s1")
    )
    qty_b, _ = await svc.adjust_buy_qty(
        TradeSignal(code="BBBBBB", name="B", action="BUY", price=100_000, qty=None,
                    reason="r", strategy_name="s2")
    )
    assert qty_a == 5 and qty_b == 5
