"""해외 리스크 기반 사이징 테스트 (P1-4).

기존 고정 USD 슬롯은 손절 폭과 무관하게 같은 수량을 산다 — 손절이 -2% 든 -10% 든
1주당 리스크가 달라진다. 총자산과 손절 거리로 수량을 산출하도록 확장하되,
equity/stop 을 모르는 기존 호출부(dry-run)는 고정 슬롯으로 폴백한다.
"""
from unittest.mock import MagicMock

import pytest

from config.config_loader import PositionSizingConfig
from services.overseas_position_sizing_service import OverseasPositionSizingService


def _svc(*, slot_usd=1000.0, profile="canary", is_real=True, cfg=None, max_qty=None):
    return OverseasPositionSizingService(
        slot_usd=slot_usd,
        max_qty=max_qty,
        sizing_config=cfg or PositionSizingConfig(),
        operating_profile=profile,
        is_real_mode_provider=lambda: is_real,
        logger=MagicMock(),
    )


# ── 리스크 기반 산출 ────────────────────────────────────────────────────

def test_qty_is_derived_from_risk_amount_and_stop_distance():
    """canary per_trade_risk_pct=0.25% — equity $100,000 → 리스크 예산 $250.

    진입 $100 / 손절 $80 → 손절거리 $20 → 250/20 = 12주.
    (손절이 가까우면 비중 상한 1.5% 가 먼저 걸린다 — 아래 별도 케이스 참고.)
    """
    r = _svc().size(limit_price_usd=100.0, stop_price_usd=80.0, account_equity_usd=100_000.0)

    assert r["qty"] == 12
    assert r["reason"] == "risk_based"
    assert r["risk_amount_usd"] == pytest.approx(250.0)


def test_wider_stop_buys_fewer_shares():
    """같은 리스크 예산이면 손절이 멀수록 적게 산다 — 고정 슬롯은 이걸 못 한다."""
    tight = _svc().size(limit_price_usd=100.0, stop_price_usd=80.0, account_equity_usd=100_000.0)
    wide = _svc().size(limit_price_usd=100.0, stop_price_usd=75.0, account_equity_usd=100_000.0)

    assert tight["reason"] == wide["reason"] == "risk_based"
    assert tight["qty"] == 12   # 250 / 20
    assert wide["qty"] == 10    # 250 / 25
    assert tight["qty"] > wide["qty"]


def test_position_weight_cap_applies():
    """단일 종목 비중 상한(canary 1.5%) — equity $100,000 → $1,500 → $100 에 15주."""
    # 손절 거리가 아주 좁으면 리스크 기준으로는 많이 사게 되지만 비중 상한이 막는다
    r = _svc().size(limit_price_usd=100.0, stop_price_usd=99.9, account_equity_usd=100_000.0)

    assert r["qty"] == 15
    assert r["reason"] == "capped_by_position_weight"


def test_canary_and_real_full_profiles_differ():
    """canary 0.25%/1.5% vs base 1.5%/5.0% — canary 가 더 보수적이어야 한다."""
    canary = _svc(profile="canary").size(
        limit_price_usd=100.0, stop_price_usd=80.0, account_equity_usd=100_000.0)
    full = _svc(profile="real_full").size(
        limit_price_usd=100.0, stop_price_usd=80.0, account_equity_usd=100_000.0)

    assert canary["qty"] == 12   # 리스크 250 / 20
    assert full["qty"] == 50     # 리스크 1500/20 = 75 → 비중 상한 5% = 50 주
    assert full["qty"] > canary["qty"]


def test_paper_mode_uses_base_config_not_canary_overlay():
    """모의 모드는 canary overlay 를 적용하지 않는다(국내와 동일)."""
    paper = _svc(profile="canary", is_real=False).size(
        limit_price_usd=100.0, stop_price_usd=80.0, account_equity_usd=100_000.0)
    real_full = _svc(profile="real_full").size(
        limit_price_usd=100.0, stop_price_usd=80.0, account_equity_usd=100_000.0)
    assert paper["qty"] == real_full["qty"]


def test_position_weight_cap_binds_before_risk_budget_on_tight_stops():
    """canary 에서는 손절이 가까울수록 비중 상한(1.5%)이 리스크 예산보다 먼저 걸린다.

    price/거리 > 6 이면 항상 상한이 지배한다 — 운영 시 알아야 할 성질이라 고정한다.
    """
    capped = _svc().size(limit_price_usd=100.0, stop_price_usd=90.0,
                         account_equity_usd=100_000.0)
    assert capped["reason"] == "capped_by_position_weight"
    assert capped["qty"] == 15


def test_minimum_stop_distance_protects_denominator():
    """손절가가 진입가에 붙으면 분모가 0에 수렴해 수량이 발산한다."""
    r = _svc().size(limit_price_usd=100.0, stop_price_usd=99.999, account_equity_usd=1_000_000.0)
    # min_stop_distance_pct=1.0 → 최소 $1 거리로 계산 → 2500주, 비중상한 1.5% = $15,000 → 150주
    assert r["qty"] == 150
    assert r["reason"] == "capped_by_position_weight"


def test_stop_above_entry_falls_back_to_slot():
    """손절이 진입가 위면 리스크 계산이 성립하지 않는다."""
    r = _svc().size(limit_price_usd=100.0, stop_price_usd=105.0, account_equity_usd=100_000.0)
    assert r["reason"] == "slot"
    assert r["qty"] == 10


# ── 하위 호환 폴백 ──────────────────────────────────────────────────────

def test_missing_equity_falls_back_to_fixed_slot():
    """dry-run 경로는 총자산을 모른다 — 폴백이 없으면 관측 신호가 통째로 사라진다."""
    r = _svc().size(limit_price_usd=100.0, stop_price_usd=95.0)
    assert r["qty"] == 10
    assert r["reason"] == "slot"


def test_missing_stop_falls_back_to_fixed_slot():
    r = _svc().size(limit_price_usd=100.0, account_equity_usd=100_000.0)
    assert r["qty"] == 10
    assert r["reason"] == "slot"


def test_legacy_call_signature_still_works():
    """기존 호출부(dry-run 6종)는 limit_price_usd 만 넘긴다."""
    r = _svc().size(limit_price_usd=250.0)
    assert r["qty"] == 4
    assert r["reason"] == "slot"


def test_risk_based_respects_max_qty_cap():
    r = _svc(max_qty=5).size(
        limit_price_usd=100.0, stop_price_usd=80.0, account_equity_usd=100_000.0)
    assert r["qty"] == 5
    assert r["reason"] == "capped_by_max_qty"


def test_risk_based_respects_available_usd():
    r = _svc().size(limit_price_usd=100.0, stop_price_usd=80.0,
                    account_equity_usd=100_000.0, available_usd=500.0)
    assert r["qty"] == 5
    assert r["reason"] == "capped_by_available_usd"


def test_zero_risk_budget_yields_no_position():
    cfg = PositionSizingConfig(canary_overrides={"per_trade_risk_pct": 0.0,
                                                 "max_per_position_pct": 1.5})
    r = _svc(cfg=cfg).size(limit_price_usd=100.0, stop_price_usd=80.0,
                           account_equity_usd=100_000.0)
    assert r["qty"] == 0


def test_krw_exposure_still_reported():
    r = _svc().size(limit_price_usd=100.0, stop_price_usd=80.0,
                    account_equity_usd=100_000.0, fx_krw_per_usd=1300.0)
    assert r["krw_exposure"] == pytest.approx(12 * 100.0 * 1300.0)
