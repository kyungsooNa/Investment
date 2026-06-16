"""run_backtest 의 point-in-time universe(R-1 생존편향) 배선 회귀 테스트.

연결층 4단계: --pit-universe / --delisted-ohlcv-dir 플래그 → replay 에 상폐 OHLCV
fallback 주입 + universe 를 PointInTimeAugmentedUniverse 로 래핑.
PIT provider / wrapper 내부 동작 자체는 별도 테스트(test_point_in_time_universe_*)로
잠겨 있으므로 여기서는 run_backtest 의 배선만 검증한다.
"""
from __future__ import annotations

import json

import pytest

from common.types import ErrorCode, ResCommonResponse
from scripts.run_backtest import (
    _load_delisted_ohlcv_store,
    _load_pit_provider,
    _make_osb_pit_item_factory,
    _parse_args,
    _wrap_pit_universe,
)


def test_parse_args_includes_pit_flags(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_backtest.py",
            "--strategy", "larry_williams_vbo",
            "--dates", "20260101",
            "--pit-universe", "snap.json",
            "--delisted-ohlcv-dir", "delisted/",
            "--pit-min-trading-value", "5000000000",
        ],
    )
    args = _parse_args()
    assert args.pit_universe == "snap.json"
    assert args.delisted_ohlcv_dir == "delisted/"
    assert args.pit_min_trading_value == 5_000_000_000


def test_pit_flags_default_to_none(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["run_backtest.py", "--strategy", "larry_williams_vbo", "--dates", "20260101"],
    )
    args = _parse_args()
    assert args.pit_universe is None
    assert args.delisted_ohlcv_dir is None
    assert args.pit_min_trading_value == 0


def test_load_pit_provider_none_passthrough():
    assert _load_pit_provider(None) is None


def test_load_pit_provider_from_full_records_snapshot(tmp_path):
    snapshot = {
        "config": {"full_records": True, "as_of_date": None},
        "records": [
            {
                "symbol": "008110",
                "name": "대동전자",
                "market": "KOSPI",
                "listing_date": "1990-06-05",
                "delisting_date": "2026-03-30",
                "source": "delisted",
            }
        ],
    }
    path = tmp_path / "full.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")

    provider = _load_pit_provider(str(path))

    assert "008110" in provider.delisted_codes_as_of("20260301")
    assert "008110" not in provider.delisted_codes_as_of("20260401")


def test_load_delisted_ohlcv_store_none_passthrough():
    assert _load_delisted_ohlcv_store(None) is None


def test_load_delisted_ohlcv_store_from_dir(tmp_path):
    # 빈 디렉터리도 안전하게 store 인스턴스를 반환한다.
    store = _load_delisted_ohlcv_store(str(tmp_path))
    assert store is not None
    assert hasattr(store, "get_daily_rows")


def test_osb_pit_item_factory_exempts_market_cap():
    factory = _make_osb_pit_item_factory()
    item = factory(code="008110", name="대동전자", market="KOSPI", avg_trading_value_5d=8_000_000_000)

    assert item.code == "008110"
    assert item.market_cap == 0  # 상폐 종목은 시총 면제
    assert item.avg_trading_value_5d == 8_000_000_000


def test_osb_pit_item_passes_vbo_validity_filter():
    """상폐 후보 item(market_cap=0)이 VBO 규모 필터를 통과한다(시총 면제, 거래대금 게이트만)."""
    from strategies.larry_williams_vbo_strategy import LarryWilliamsVBOStrategy

    factory = _make_osb_pit_item_factory()
    # 거래대금은 VBO min_5d_trading_value 위로 둬서 시총(=0) 면제만 검증한다.
    item = factory(code="008110", name="대동전자", market="KOSPI", avg_trading_value_5d=12_000_000_000)
    strategy = LarryWilliamsVBOStrategy(
        stock_query_service=object(),
        market_clock=object(),
        universe_service=None,
    )
    stock = {
        "code": item.code,
        "name": item.name,
        "market_cap": item.market_cap,  # 0 — market_cap>0 게이트에 안 걸려야 함
        "avg_5d_tv": item.avg_trading_value_5d,
    }
    assert strategy._passes_validity_filter(stock, {"code": item.code}) is True


class _FakeBaseUniverse:
    def __init__(self):
        self.sentinel = "base-attr"

    async def get_watchlist(self, logger=None):
        return {}


class _FakeSqs:
    async def get_recent_daily_ohlcv(self, code, limit=None, end_date=None):
        rows = [{"close": 1000, "volume": 9_000_000} for _ in range(5)]
        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=rows)


class _FakeClock:
    def get_current_kst_time(self):
        from datetime import datetime

        return datetime(2026, 3, 1)


@pytest.mark.asyncio
async def test_wrap_pit_universe_augments_delisted_candidate(tmp_path):
    snapshot = {
        "records": [
            {
                "symbol": "008110",
                "name": "대동전자",
                "market": "KOSPI",
                "listing_date": "1990-06-05",
                "delisting_date": "2026-03-30",
                "source": "delisted",
            }
        ]
    }
    path = tmp_path / "full.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    provider = _load_pit_provider(str(path))

    base = _FakeBaseUniverse()
    wrapped = _wrap_pit_universe(
        base,
        pit_provider=provider,
        replay_sqs=_FakeSqs(),
        backtest_clock=_FakeClock(),
        min_trading_value=5_000_000_000,
    )

    # __getattr__ 위임 확인
    assert wrapped.sentinel == "base-attr"

    watchlist = await wrapped.get_watchlist()
    assert "008110" in watchlist
    item = watchlist["008110"]
    assert item.market_cap == 0
    # close(1000)×volume(9M)=9e9 ≥ min 5e9 → 합류
    assert item.avg_trading_value_5d == pytest.approx(9_000_000_000)
