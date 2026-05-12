"""PriceStreamService / StockQueryService snapshot contract 회귀 테스트."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.market_snapshot import ConclusionSnapshot, MarketSnapshot
from common.types import ErrorCode, ResCommonResponse
from services.data_quality_service import DataQualityService
from services.price_stream_service import PriceStreamService
from services.stock_query_service import StockQueryService


# ─── PriceStreamService 신규 메서드 ───────────────────────────────────────────

class TestPriceStreamServiceMarketSnapshot:
    def _make_svc(self) -> PriceStreamService:
        return PriceStreamService(stock_repo=MagicMock(), logger=MagicMock())

    def test_get_market_snapshot_none_when_no_cache(self):
        svc = self._make_svc()
        assert svc.get_market_snapshot("005930") is None

    def test_get_market_snapshot_websocket_source(self):
        svc = self._make_svc()
        svc.on_price_tick({
            "유가증권단축종목코드": "005930",
            "주식현재가": "75000",
            "전일대비": "500",
            "전일대비율": "0.67",
            "전일대비부호": "2",
            "누적거래량": "120000",
            "누적거래대금": "9000000000",
            "주식최고가": "75500",
            "주식최저가": "74200",
            "주식시가": "74500",
        })
        snap = svc.get_market_snapshot("005930")
        assert isinstance(snap, MarketSnapshot)
        assert snap.code == "005930"
        assert snap.price == 75000.0
        assert snap.high == 75500.0
        assert snap.low == 74200.0
        assert snap.open == 74500.0
        assert snap.source == "websocket"

    def test_get_market_snapshot_rest_source(self):
        svc = self._make_svc()
        svc.cache_price_snapshot("005930", price="75000", volume="100000")
        snap = svc.get_market_snapshot("005930")
        assert snap is not None
        assert snap.source == "rest"
        assert snap.high is None  # REST backfill 시 high 없음

    def test_cache_and_get_conclusion_snapshot(self):
        svc = self._make_svc()
        assert svc.get_conclusion_snapshot("005930") is None

        svc.cache_conclusion_snapshot("005930", 123.4)
        cs = svc.get_conclusion_snapshot("005930")
        assert isinstance(cs, ConclusionSnapshot)
        assert cs.code == "005930"
        assert cs.execution_strength_pct == 123.4
        assert cs.source == "rest"

    def test_clear_subscription_state_removes_conclusion(self):
        svc = self._make_svc()
        svc.cache_conclusion_snapshot("005930", 100.0)
        svc.clear_subscription_state("005930")
        assert svc.get_conclusion_snapshot("005930") is None


# ─── StockQueryService.get_market_snapshot ────────────────────────────────────

class TestStockQueryServiceGetMarketSnapshot:
    def _make_sqs(self, snap_dict=None):
        pss = MagicMock()
        if snap_dict is None:
            pss.get_market_snapshot.return_value = None
        else:
            code = snap_dict.get("code", "005930")
            pss.get_market_snapshot.return_value = MarketSnapshot.from_legacy_dict(code, snap_dict)
        mds = MagicMock()
        sqs = StockQueryService(
            market_data_service=mds,
            logger=MagicMock(),
            market_clock=MagicMock(),
            price_stream_service=pss,
            snapshot_max_age_sec=5.0,
        )
        return sqs, pss

    def _fresh_dict(self, age=1.0):
        return {
            "price": "75000", "change": "0", "rate": "0.00", "sign": "3",
            "acml_vol": 100000, "acml_tr_pbmn": 5000000000,
            "received_at": time.time() - age,
            "latency_sec": 0.0, "quality_status": "ok", "quality_reason": "websocket",
        }

    def test_returns_snapshot_and_none_reason_when_fresh(self):
        sqs, _ = self._make_sqs(self._fresh_dict(age=1.0))
        snap, reason = sqs.get_market_snapshot("005930")
        assert isinstance(snap, MarketSnapshot)
        assert reason is None

    def test_returns_snapshot_and_stale_reason_when_old(self):
        sqs, _ = self._make_sqs(self._fresh_dict(age=10.0))
        snap, reason = sqs.get_market_snapshot("005930")
        assert snap is not None
        assert reason == DataQualityService.REASON_SNAPSHOT_STALE

    def test_returns_none_and_missing_reason_when_no_cache(self):
        sqs, _ = self._make_sqs(snap_dict=None)
        snap, reason = sqs.get_market_snapshot("005930")
        assert snap is None
        assert reason == DataQualityService.REASON_SNAPSHOT_MISSING

    def test_force_fresh_returns_none_and_stale_reason(self):
        sqs, _ = self._make_sqs(self._fresh_dict(age=0.0))
        snap, reason = sqs.get_market_snapshot("005930", force_fresh=True)
        assert snap is None
        assert reason == DataQualityService.REASON_SNAPSHOT_STALE

    def test_no_price_stream_service_returns_missing(self):
        mds = MagicMock()
        sqs = StockQueryService(
            market_data_service=mds, logger=MagicMock(), market_clock=MagicMock(),
            price_stream_service=None,
        )
        snap, reason = sqs.get_market_snapshot("005930")
        assert snap is None
        assert reason == DataQualityService.REASON_SNAPSHOT_MISSING


# ─── StockQueryService.get_conclusion_snapshot ────────────────────────────────

class TestStockQueryServiceGetConclusionSnapshot:
    def _make_sqs_with_pss(self, cs=None):
        pss = MagicMock()
        pss.get_conclusion_snapshot.return_value = cs
        mds = AsyncMock()
        sqs = StockQueryService(
            market_data_service=mds,
            logger=MagicMock(),
            market_clock=MagicMock(),
            price_stream_service=pss,
        )
        return sqs, pss, mds

    def _make_cs(self, age=1.0) -> ConclusionSnapshot:
        return ConclusionSnapshot(
            code="005930",
            execution_strength_pct=115.0,
            received_at=time.time() - age,
            source="rest",
        )

    @pytest.mark.asyncio
    async def test_returns_cached_when_fresh(self):
        cs = self._make_cs(age=1.0)
        sqs, pss, _ = self._make_sqs_with_pss(cs=cs)
        result, reason = await sqs.get_conclusion_snapshot("005930", max_age_sec=10.0)
        assert result is cs
        assert reason is None
        assert sqs._price_lookup_stats["conclusion_hit"] == 1

    @pytest.mark.asyncio
    async def test_rest_fallback_when_stale(self):
        cs = self._make_cs(age=20.0)
        sqs, pss, mds = self._make_sqs_with_pss(cs=cs)
        output = {"tday_rltv": "123.4"}
        mds.get_stock_conclusion.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="ok",
            data={"output": [output]},
        )
        fresh_cs = ConclusionSnapshot(code="005930", execution_strength_pct=123.4,
                                      received_at=time.time(), source="rest")
        pss.get_conclusion_snapshot.side_effect = [cs, fresh_cs]

        result, reason = await sqs.get_conclusion_snapshot("005930", max_age_sec=10.0)
        assert result is fresh_cs
        assert reason is None
        pss.cache_conclusion_snapshot.assert_called_once_with("005930", 123.4)
        assert sqs._price_lookup_stats["conclusion_stale_fallback"] == 1

    @pytest.mark.asyncio
    async def test_rest_fallback_when_missing(self):
        sqs, pss, mds = self._make_sqs_with_pss(cs=None)
        output = {"tday_rltv": "99.5"}
        mds.get_stock_conclusion.return_value = ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value, msg1="ok",
            data={"output": [output]},
        )
        fresh_cs = ConclusionSnapshot(code="005930", execution_strength_pct=99.5,
                                      received_at=time.time(), source="rest")
        pss.get_conclusion_snapshot.side_effect = [None, fresh_cs]

        result, reason = await sqs.get_conclusion_snapshot("005930")
        assert result is fresh_cs
        assert sqs._price_lookup_stats["conclusion_missing_fallback"] == 1

    @pytest.mark.asyncio
    async def test_returns_none_when_rest_fails(self):
        sqs, _, mds = self._make_sqs_with_pss(cs=None)
        mds.get_stock_conclusion.return_value = ResCommonResponse(
            rt_cd="1", msg1="error", data={}
        )
        result, reason = await sqs.get_conclusion_snapshot("005930")
        assert result is None
        assert reason == DataQualityService.REASON_CONCLUSION_MISSING
