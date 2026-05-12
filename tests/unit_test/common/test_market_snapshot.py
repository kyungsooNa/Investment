"""MarketSnapshot / ConclusionSnapshot dataclass invariant 테스트."""
import pytest
from common.market_snapshot import ConclusionSnapshot, MarketSnapshot


def _ws_dict() -> dict:
    return {
        "price": "75000",
        "change": "500",
        "rate": "0.67",
        "sign": "2",
        "acml_vol": 120000,
        "acml_tr_pbmn": 9000000000,
        "high": 75500.0,
        "low": 74200.0,
        "open": 74500.0,
        "received_at": 1700000000.0,
        "latency_sec": 0.05,
        "quality_status": "ok",
        "quality_reason": "websocket",
    }


def _rest_dict() -> dict:
    d = _ws_dict()
    d["high"] = None
    d["low"] = None
    d["open"] = None
    d["quality_reason"] = "rest_snapshot"
    return d


class TestMarketSnapshotFromLegacyDict:
    def test_websocket_dict_populates_high_low_open(self):
        snap = MarketSnapshot.from_legacy_dict("005930", _ws_dict(), source="websocket")
        assert snap.code == "005930"
        assert snap.price == 75000.0
        assert snap.high == 75500.0
        assert snap.low == 74200.0
        assert snap.open == 74500.0
        assert snap.acml_vol == 120000
        assert snap.acml_tr_pbmn == 9000000000
        assert snap.source == "websocket"

    def test_rest_dict_high_low_open_are_none(self):
        snap = MarketSnapshot.from_legacy_dict("005930", _rest_dict(), source="rest")
        assert snap.high is None
        assert snap.low is None
        assert snap.open is None
        assert snap.source == "rest"

    def test_invalid_numeric_values_default_to_zero(self):
        d = _ws_dict()
        d["price"] = "N/A"
        d["acml_vol"] = "bad"
        snap = MarketSnapshot.from_legacy_dict("005930", d)
        assert snap.price == 0.0
        assert snap.acml_vol == 0

    def test_missing_keys_default_gracefully(self):
        snap = MarketSnapshot.from_legacy_dict("005930", {})
        assert snap.price == 0.0
        assert snap.high is None
        assert snap.sign == "3"
        assert snap.quality_status == "ok"


class TestMarketSnapshotToLegacyDict:
    def test_roundtrip_websocket(self):
        original = _ws_dict()
        snap = MarketSnapshot.from_legacy_dict("005930", original, source="websocket")
        result = snap.to_legacy_dict()
        assert result["price"] == "75000.0"
        assert result["acml_vol"] == 120000
        assert result["high"] == 75500.0
        assert result["low"] == 74200.0
        assert result["quality_reason"] == "websocket"

    def test_roundtrip_rest_none_fields(self):
        snap = MarketSnapshot.from_legacy_dict("005930", _rest_dict(), source="rest")
        result = snap.to_legacy_dict()
        assert result["high"] is None
        assert result["low"] is None
        assert result["open"] is None


class TestMarketSnapshotFrozen:
    def test_is_frozen(self):
        snap = MarketSnapshot.from_legacy_dict("005930", _ws_dict())
        with pytest.raises((AttributeError, TypeError)):
            snap.price = 99999.0  # type: ignore[misc]


class TestConclusionSnapshot:
    def test_basic_fields(self):
        cs = ConclusionSnapshot(
            code="005930",
            execution_strength_pct=123.4,
            received_at=1700000000.0,
            source="rest",
        )
        assert cs.code == "005930"
        assert cs.execution_strength_pct == 123.4
        assert cs.source == "rest"

    def test_is_frozen(self):
        cs = ConclusionSnapshot(
            code="005930",
            execution_strength_pct=100.0,
            received_at=1700000000.0,
            source="rest",
        )
        with pytest.raises((AttributeError, TypeError)):
            cs.execution_strength_pct = 200.0  # type: ignore[misc]
