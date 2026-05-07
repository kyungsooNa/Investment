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
