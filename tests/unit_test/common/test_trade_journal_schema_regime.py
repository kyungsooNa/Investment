"""trade_journal_schema 의 market_regime 라벨 부착 테스트.

요구사항:
- SCHEMA_VERSION >= 2
- STANDARD_TRADE_JOURNAL_FIELDS 에 'market_regime' 포함
- normalize_* 가 service 를 호출하지 않는다 (의존성 없이 호출 가능)
- market_regime 인자 또는 input record 의 market_regime / metadata.market_regime 를 보존
"""
from common.trade_journal_schema import (
    SCHEMA_VERSION,
    STANDARD_TRADE_JOURNAL_FIELDS,
    normalize_backtest_decision,
    normalize_backtest_trade,
    normalize_virtual_trade,
)


REGIME = {"kospi": "bull", "kosdaq": "bear", "stock_market": "KOSDAQ"}


def test_schema_version_bumped():
    assert SCHEMA_VERSION >= 2


def test_market_regime_field_in_standard_schema():
    assert "market_regime" in STANDARD_TRADE_JOURNAL_FIELDS


def test_normalize_virtual_trade_accepts_market_regime_param():
    trade = {
        "code": "005930", "strategy": "PP", "buy_date": "20260514",
        "status": "SOLD", "qty": 1, "buy_price": 100.0, "sell_price": 110.0,
        "return_rate": 10.0, "reason": "PP",
    }
    row = normalize_virtual_trade(trade, market_regime=REGIME)
    assert row["market_regime"] == REGIME


def test_normalize_virtual_trade_falls_back_to_record_field():
    trade = {
        "code": "005930", "strategy": "PP", "buy_date": "20260514",
        "status": "HOLD", "qty": 1, "buy_price": 100.0, "reason": "PP",
        "market_regime": REGIME,
    }
    row = normalize_virtual_trade(trade)
    assert row["market_regime"] == REGIME


def test_normalize_virtual_trade_without_regime_returns_none():
    trade = {
        "code": "005930", "strategy": "PP", "buy_date": "20260514",
        "status": "HOLD", "qty": 1, "buy_price": 100.0, "reason": "PP",
    }
    row = normalize_virtual_trade(trade)
    assert row["market_regime"] is None


def test_normalize_backtest_trade_accepts_market_regime_param():
    trade = {"entry_px": 100.0, "exit_px": 110.0, "entry_time": "20260514"}
    row = normalize_backtest_trade(trade, stock_code="005930", strategy="PP", market_regime=REGIME)
    assert row["market_regime"] == REGIME


def test_normalize_backtest_decision_accepts_market_regime_param():
    decision = {"current": 100.0, "signal_time": "20260514", "reason": "signal"}
    row = normalize_backtest_decision(
        decision, stock_code="005930", strategy="PP", accepted=True, market_regime=REGIME
    )
    assert row["market_regime"] == REGIME


def test_normalizer_does_not_call_any_service():
    """정규화는 외부 서비스 의존성이 없어야 한다 — service 미주입 환경에서도 동작."""
    trade = {
        "code": "005930", "strategy": "PP", "buy_date": "20260514",
        "status": "HOLD", "qty": 1, "buy_price": 100.0, "reason": "PP",
    }
    # 인자 없이 호출해도 예외 없음
    row = normalize_virtual_trade(trade)
    assert row["code"] == "005930"


def test_metadata_market_regime_overflow_path():
    """metadata.market_regime 위치도 보존된다 (input record 후방 호환)."""
    trade = {
        "code": "005930", "strategy": "PP", "buy_date": "20260514",
        "status": "HOLD", "qty": 1, "buy_price": 100.0, "reason": "PP",
        "metadata": {"market_regime": REGIME},
    }
    row = normalize_virtual_trade(trade)
    assert row["market_regime"] == REGIME
