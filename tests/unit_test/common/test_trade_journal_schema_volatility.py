"""trade_journal_schema 변동성 필드 정규화 단위 테스트.

`market_regime` 와 동일하게 명시 인자 > record 직접 필드 > metadata fallback > None 우선순위.
"""
import pytest

from common.trade_journal_schema import (
    STANDARD_TRADE_JOURNAL_FIELDS,
    SCHEMA_VERSION,
    normalize_backtest_decision,
    normalize_backtest_trade,
    normalize_virtual_trade,
)


def test_volatility_field_is_in_standard_schema():
    assert "volatility_20d_annualized" in STANDARD_TRADE_JOURNAL_FIELDS
    assert SCHEMA_VERSION >= 3


def test_explicit_volatility_argument_wins():
    trade = {"buy_price": 1000, "sell_price": 1100, "qty": 1, "status": "SOLD",
             "volatility_20d_annualized": 0.10}
    record = normalize_virtual_trade(trade, volatility_20d_annualized=0.35)
    assert record["volatility_20d_annualized"] == pytest.approx(0.35)


def test_record_field_used_when_no_explicit():
    trade = {"buy_price": 1000, "sell_price": 1100, "qty": 1, "status": "SOLD",
             "volatility_20d_annualized": 0.22}
    record = normalize_virtual_trade(trade)
    assert record["volatility_20d_annualized"] == pytest.approx(0.22)


def test_metadata_fallback_in_decision():
    decision = {"current": 1000, "metadata": {"volatility_20d_annualized": 0.18}}
    record = normalize_backtest_decision(decision, stock_code="005930", accepted=True)
    assert record["volatility_20d_annualized"] == pytest.approx(0.18)


def test_none_when_missing_everywhere():
    trade = {"entry_px": 1000, "exit_px": 1050}
    record = normalize_backtest_trade(trade, stock_code="005930", qty=1)
    assert record["volatility_20d_annualized"] is None


def test_invalid_value_becomes_none():
    trade = {"buy_price": 1000, "sell_price": 1100, "qty": 1, "status": "SOLD",
             "volatility_20d_annualized": "not-a-number"}
    record = normalize_virtual_trade(trade)
    assert record["volatility_20d_annualized"] is None
