"""P3-4 TradeSignal contract 확장 — Phase 1 잠금 테스트.

새 필드(`signal_id`, `strategy_id`, `entry_reason`, `invalidation_price`,
`stop_loss_price`, `target_price`, `trailing_rule`, `expected_holding_period_days`,
`confidence`, `required_data`) 는 모두 Optional · default None 이어야 한다.
기존 호출자/소비자가 손대지 않아도 동작 유지된다는 것을 회귀로 잠근다.

소비자 마이그레이션 (예: scheduler 가 signal_id 자동 발급, risk_gate 가
strategy_id 기반으로 limit 조회) 는 별도 PR 로 분리되어 있어 여기서는
contract 자체만 검증한다.
"""
from __future__ import annotations

import pytest

from common.types import TradeSignal


# 확장 필드 목록 — 이 표가 곧 contract. 신규 필드 추가/제거 시 함께 갱신.
_EXTENDED_FIELDS = [
    "signal_id",
    "strategy_id",
    "entry_reason",
    "invalidation_price",
    "stop_loss_price",
    "target_price",
    "trailing_rule",
    "expected_holding_period_days",
    "confidence",
    "required_data",
]


def _minimal_signal() -> TradeSignal:
    """기존 호출자가 사용하던 최소 필드 형태. 확장 후에도 그대로 통과해야 한다."""
    return TradeSignal(code="005930", name="삼성전자", action="BUY", price=70000)


# ── 기존 호환성 ──────────────────────────────────────────────────────


def test_minimal_signal_still_constructs() -> None:
    signal = _minimal_signal()
    assert signal.code == "005930"
    assert signal.action == "BUY"
    assert signal.price == 70000


def test_to_dict_round_trip_preserves_minimal_fields() -> None:
    signal = _minimal_signal()
    payload = signal.to_dict()
    restored = TradeSignal(**payload)
    assert restored.code == signal.code
    assert restored.action == signal.action
    assert restored.price == signal.price


# ── 확장 필드 default None 잠금 ──────────────────────────────────────


@pytest.mark.parametrize("field_name", _EXTENDED_FIELDS)
def test_extended_field_defaults_to_none(field_name: str) -> None:
    signal = _minimal_signal()
    assert hasattr(signal, field_name), f"TradeSignal 에 {field_name} 필드가 없다"
    assert getattr(signal, field_name) is None, (
        f"{field_name} 의 default 는 None 이어야 한다 (기존 호출자 영향 없음 보장)"
    )


def test_to_dict_includes_all_extended_fields() -> None:
    payload = _minimal_signal().to_dict()
    for field_name in _EXTENDED_FIELDS:
        assert field_name in payload, f"to_dict 결과에 {field_name} 가 누락"
        assert payload[field_name] is None


# ── 명시적 할당 ──────────────────────────────────────────────────────


def test_all_extended_fields_can_be_set_explicitly() -> None:
    signal = TradeSignal(
        code="005930",
        name="삼성전자",
        action="BUY",
        price=70000,
        signal_id="oneil_pocket_pivot:005930:1700000000000",
        strategy_id="oneil_pocket_pivot",
        entry_reason="pocket_pivot_breakout",
        invalidation_price=68000.0,
        stop_loss_price=66500.0,
        target_price=78000.0,
        trailing_rule='{"type":"atr","mult":2.5}',
        expected_holding_period_days=20,
        confidence=0.72,
        required_data=["daily_ohlcv:60", "volume_ma:50"],
    )
    assert signal.signal_id == "oneil_pocket_pivot:005930:1700000000000"
    assert signal.strategy_id == "oneil_pocket_pivot"
    assert signal.entry_reason == "pocket_pivot_breakout"
    assert signal.invalidation_price == 68000.0
    assert signal.stop_loss_price == 66500.0
    assert signal.target_price == 78000.0
    assert signal.trailing_rule == '{"type":"atr","mult":2.5}'
    assert signal.expected_holding_period_days == 20
    assert signal.confidence == 0.72
    assert signal.required_data == ["daily_ohlcv:60", "volume_ma:50"]


def test_to_dict_preserves_explicit_extended_values() -> None:
    signal = TradeSignal(
        code="005930", name="삼성전자", action="BUY", price=70000,
        signal_id="abc-123", strategy_id="oneil_pocket_pivot",
        confidence=0.5, required_data=["a", "b"],
    )
    payload = signal.to_dict()
    assert payload["signal_id"] == "abc-123"
    assert payload["strategy_id"] == "oneil_pocket_pivot"
    assert payload["confidence"] == 0.5
    assert payload["required_data"] == ["a", "b"]


# ── 기존 손절 관련 필드와 신규 필드 공존 ─────────────────────────────


def test_legacy_stop_loss_pct_and_new_stop_loss_price_coexist() -> None:
    """기존 stop_loss_pct (비율) 와 새 stop_loss_price (절대가) 는 별도 필드.
    한쪽만 set 해도 다른 쪽에 영향이 없어야 한다."""
    signal = TradeSignal(
        code="005930", name="삼성전자", action="BUY", price=70000,
        stop_loss_pct=-5.0,
    )
    assert signal.stop_loss_pct == -5.0
    assert signal.stop_loss_price is None

    signal2 = TradeSignal(
        code="005930", name="삼성전자", action="BUY", price=70000,
        stop_loss_price=66500.0,
    )
    assert signal2.stop_loss_price == 66500.0
    assert signal2.stop_loss_pct is None
