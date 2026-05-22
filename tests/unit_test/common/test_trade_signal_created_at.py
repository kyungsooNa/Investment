"""TradeSignal.created_at 필드 단위 테스트 (P2 2-2 후속)."""
from __future__ import annotations

from common.types import TradeSignal


def _make(**overrides) -> TradeSignal:
    base = dict(
        code="005930",
        name="삼성전자",
        action="BUY",
        price=70000,
        qty=1,
        reason="t",
        strategy_name="VBO",
    )
    base.update(overrides)
    return TradeSignal(**base)


def test_default_created_at_is_none():
    sig = _make()
    assert sig.created_at is None


def test_created_at_can_be_set():
    sig = _make(created_at=12345.678)
    assert sig.created_at == 12345.678


def test_to_dict_includes_created_at():
    sig = _make(created_at=12345.0)
    payload = sig.to_dict()
    assert "created_at" in payload
    assert payload["created_at"] == 12345.0


def test_to_dict_includes_none_created_at_by_default():
    sig = _make()
    payload = sig.to_dict()
    assert "created_at" in payload
    assert payload["created_at"] is None
