"""
tests/unit_test/services/test_minervini_stage_service.py

MinerviniStageService 단위 테스트.
- classify_stage(): Stage 1~4 분류 로직
- check_vcp_pattern(): VCP 수축 감지
- _is_high_volatility(): ATR-proxy
- get_stage_for_code(): 비동기 통합 흐름
- get_stage2_list(), _fetch_rs_rating(), 헬퍼 메서드 분기
"""

import asyncio

import numpy as np
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from common.types import ResCommonResponse, ResRSRating
from services.minervini_stage_service import MinerviniStageService


def _make_svc(**kwargs) -> MinerviniStageService:
    return MinerviniStageService(
        stock_query_service=AsyncMock(),
        rs_rating_service=None,
        **kwargs,
    )


def _flat_closes(price: float, n: int) -> list[float]:
    return [float(price)] * n


def _trending_closes(start: float, end: float, n: int) -> list[float]:
    step = (end - start) / (n - 1)
    return [start + step * i for i in range(n)]


class TestClassifyStage:

    def test_insufficient_data_returns_unknown(self):
        svc = _make_svc()
        closes = _flat_closes(10000, 150)
        result = svc.classify_stage(closes, closes)
        assert result == MinerviniStageService.STAGE_UNKNOWN

    def test_stage4_when_price_below_ma200(self):
        svc = _make_svc()
        closes = _trending_closes(10000, 5000, 260)
        result = svc.classify_stage(closes, closes)
        assert result == MinerviniStageService.STAGE_4_DECLINING

    def test_stage4_when_ma200_slope_negative(self):
        svc = _make_svc()
        closes = [10000] * 240 + _trending_closes(10000, 7000, 20)
        result = svc.classify_stage(closes, closes)
        assert result == MinerviniStageService.STAGE_4_DECLINING

    def test_stage2_trend_template_all_conditions(self):
        svc = _make_svc()
        closes = _trending_closes(5000, 15000, 260)
        lows = [c * 0.95 for c in closes]
        result = svc.classify_stage(closes, lows, rs_rating=80)
        assert result == MinerviniStageService.STAGE_2_ADVANCING

    def test_stage2_rs_zero_skips_rs_condition(self):
        svc = _make_svc()
        closes = _trending_closes(5000, 15000, 260)
        lows = [c * 0.95 for c in closes]
        result = svc.classify_stage(closes, lows, rs_rating=0)
        assert result == MinerviniStageService.STAGE_2_ADVANCING

    def test_stage1_flat_above_ma200(self):
        svc = _make_svc()
        closes = _trending_closes(9900, 10000, 260)
        result = svc.classify_stage(closes, closes, rs_rating=50)
        assert result == MinerviniStageService.STAGE_1_NEGLECT

    def test_stage3_price_below_ma50_high_volatility(self):
        svc = _make_svc(volatility_threshold=0.001)
        base = _trending_closes(5000, 12000, 240)
        volatile_20 = [8500 + i * 73 + (300 if i % 2 == 0 else -300) for i in range(20)]
        closes = base + volatile_20
        result = svc.classify_stage(closes, closes)
        assert result == MinerviniStageService.STAGE_3_TOPPING

    def test_return_reason_tuple(self):
        svc = _make_svc()
        closes = _trending_closes(5000, 15000, 260)
        lows = [c * 0.95 for c in closes]
        stage, reason = svc.classify_stage(closes, lows, rs_rating=80, return_reason=True)
        assert isinstance(stage, int)
        assert isinstance(reason, str)
        assert reason


class TestCheckVcpPattern:

    def test_contracting_weekly_ranges_returns_true(self):
        svc = _make_svc()
        closes = []
        for week_range in [500, 400, 300, 200, 100]:
            mid = 10000.0
            for i in range(5):
                closes.append(mid + (week_range / 2 if i < 3 else -week_range / 2))
        assert svc.check_vcp_pattern(closes) is True

    def test_expanding_weekly_ranges_returns_false(self):
        svc = _make_svc()
        closes = []
        for week_range in [100, 200, 300, 400, 500]:
            mid = 10000.0
            for i in range(5):
                closes.append(mid + (week_range / 2 if i < 3 else -week_range / 2))
        assert svc.check_vcp_pattern(closes) is False

    def test_insufficient_data_returns_false(self):
        svc = _make_svc()
        closes = [10000.0] * 10
        assert svc.check_vcp_pattern(closes, weeks=5) is False

    def test_with_highs_parameter(self):
        svc = _make_svc()
        closes = [10000.0] * 25
        highs = []
        for week_range in [500, 400, 300, 200, 100]:
            for _ in range(5):
                highs.append(10000.0 + week_range)
        assert svc.check_vcp_pattern(closes, highs=highs, weeks=5) is True

    def test_weeks_zero_returns_false(self):
        svc = _make_svc()
        assert svc.check_vcp_pattern([10000.0] * 5, weeks=0) is False


class TestIsHighVolatility:

    def test_low_volatility_returns_false(self):
        svc = _make_svc(volatility_threshold=0.02)
        closes = [10000.0] * 25
        assert svc._is_high_volatility(closes) is False

    def test_high_volatility_returns_true(self):
        svc = _make_svc(volatility_threshold=0.001)
        closes = [10000 + (500 if i % 2 == 0 else -500) for i in range(25)]
        assert svc._is_high_volatility(closes) is True

    def test_insufficient_data_returns_false(self):
        svc = _make_svc()
        assert svc._is_high_volatility([10000.0] * 5, period=20) is False


class TestHelpers:

    def test_calculate_slope_short_list_returns_zero(self):
        svc = _make_svc()
        assert svc._calculate_slope([1.0]) == 0.0

    def test_calculate_slope_handles_polyfit_error(self):
        svc = _make_svc()
        with patch("services.minervini_stage_service.np.polyfit", side_effect=np.linalg.LinAlgError):
            assert svc._calculate_slope([1.0, 2.0, 3.0]) == 0.0

    def test_extract_price_series_skips_invalid_rows(self):
        svc = _make_svc()
        rows = [
            {"stck_clpr": "100", "stck_lwpr": "90"},
            {"stck_clpr": None, "stck_lwpr": "bad"},
            {"close": "200", "low": "190"},
            {"close": "300"},
            {"close": "notnum"},
        ]
        closes, lows = svc._extract_price_series(rows)
        assert closes == [100.0, 200.0, 300.0]
        assert lows == [90.0, 190.0, 300.0]

    def test_describe_stage_mapping(self):
        svc = _make_svc()
        assert "Stage 2" in svc.describe_stage(svc.STAGE_2_ADVANCING)
        assert "미계산" in svc.describe_stage(svc.STAGE_UNKNOWN)
        assert svc.describe_stage(9) == "Stage 9"


class TestGetStage2List:

    @pytest.mark.asyncio
    async def test_returns_db_data_when_available(self):
        stock_repo = AsyncMock()
        stock_repo.get_latest_trade_date.return_value = "20260101"
        stock_repo.get_minervini_stage2_stocks.return_value = [
            {
                "code": "005930",
                "name": "삼성전자",
                "current_price": 70000,
                "change_rate": 1.0,
                "minervini_stage": 2,
                "rs_rating": 85,
                "market_cap": 1000000,
            }
        ]
        svc = MinerviniStageService(stock_query_service=AsyncMock(), stock_repository=stock_repo)

        resp = await svc.get_stage2_list()

        assert resp.rt_cd == "0"
        assert resp.msg1 == "성공"
        assert resp.data[0]["code"] == "005930"
        assert resp.data[0]["stck_prpr"] == "70000"

    @pytest.mark.asyncio
    async def test_falls_back_to_cache_when_db_empty(self):
        stock_repo = AsyncMock()
        stock_repo.get_latest_trade_date.return_value = "20260101"
        stock_repo.get_minervini_stage2_stocks.return_value = []
        update_task = AsyncMock()
        update_task.get_minervini_stage2_cache.return_value = [{"code": "000660", "name": "SK하이닉스"}]

        svc = MinerviniStageService(stock_query_service=AsyncMock(), stock_repository=stock_repo)
        svc._minervini_update_task = update_task

        resp = await svc.get_stage2_list()

        assert resp.rt_cd == "0"
        assert resp.data[0]["code"] == "000660"

    @pytest.mark.asyncio
    async def test_triggers_refresh_when_no_data(self):
        stock_repo = AsyncMock()
        stock_repo.get_latest_trade_date.return_value = None
        update_task = AsyncMock()
        update_task.get_minervini_stage2_cache.return_value = []
        update_task.get_progress = MagicMock(return_value={"running": False})

        svc = MinerviniStageService(stock_query_service=AsyncMock(), stock_repository=stock_repo)
        svc._minervini_update_task = update_task

        with patch("services.minervini_stage_service.asyncio.create_task") as create_task:
            resp = await svc.get_stage2_list()

        assert resp.rt_cd == "0"
        assert resp.msg1 == "수집 중"
        assert resp.data == []
        create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_collecting_without_refresh_when_already_running(self):
        stock_repo = AsyncMock()
        stock_repo.get_latest_trade_date.return_value = None
        update_task = AsyncMock()
        update_task.get_minervini_stage2_cache.return_value = []
        update_task.get_progress = MagicMock(return_value={"running": True})

        svc = MinerviniStageService(stock_query_service=AsyncMock(), stock_repository=stock_repo)
        svc._minervini_update_task = update_task

        with patch("services.minervini_stage_service.asyncio.create_task") as create_task:
            resp = await svc.get_stage2_list()

        assert resp.rt_cd == "0"
        assert resp.msg1 == "수집 중"
        create_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_task_returns_error(self):
        svc = MinerviniStageService(stock_query_service=AsyncMock(), stock_repository=None)
        svc._minervini_update_task = None

        resp = await svc.get_stage2_list()

        assert resp.rt_cd == "1"
        assert resp.data is None

    @pytest.mark.asyncio
    async def test_db_exception_falls_back_to_task(self):
        stock_repo = AsyncMock()
        stock_repo.get_latest_trade_date.side_effect = RuntimeError("DB 오류")
        update_task = AsyncMock()
        update_task.get_minervini_stage2_cache.return_value = [{"code": "035720"}]

        svc = MinerviniStageService(stock_query_service=AsyncMock(), stock_repository=stock_repo)
        svc._minervini_update_task = update_task

        resp = await svc.get_stage2_list()

        assert resp.rt_cd == "0"
        assert resp.data[0]["code"] == "035720"


class TestGetStageForCode:

    @pytest.mark.asyncio
    async def test_returns_unknown_when_ohlcv_missing(self):
        svc = _make_svc()
        svc._stock_query_svc.get_recent_daily_ohlcv = AsyncMock(return_value=None)

        stage, reason = await svc.get_stage_for_code("000000")

        assert stage == MinerviniStageService.STAGE_UNKNOWN
        assert "OHLCV" in reason

    @pytest.mark.asyncio
    async def test_returns_unknown_when_data_insufficient(self):
        svc = _make_svc()
        rows = [{"close": 10000.0, "low": 9500.0}] * 150
        svc._stock_query_svc.get_recent_daily_ohlcv = AsyncMock(
            return_value=ResCommonResponse(rt_cd="0", msg1="ok", data=rows)
        )

        stage, reason = await svc.get_stage_for_code("000000")

        assert stage == MinerviniStageService.STAGE_UNKNOWN
        assert "데이터 부족" in reason

    @pytest.mark.asyncio
    async def test_returns_stage2_for_uptrending_stock(self):
        svc = _make_svc()
        closes = _trending_closes(5000, 15000, 260)
        rows = [{"close": c, "low": c * 0.95} for c in closes]
        svc._stock_query_svc.get_recent_daily_ohlcv = AsyncMock(
            return_value=ResCommonResponse(rt_cd="0", msg1="ok", data=rows)
        )

        stage, _ = await svc.get_stage_for_code("005930")

        assert stage == MinerviniStageService.STAGE_2_ADVANCING

    @pytest.mark.asyncio
    async def test_error_in_ohlcv_returns_unknown(self):
        svc = _make_svc()
        svc._stock_query_svc.get_recent_daily_ohlcv = AsyncMock(side_effect=RuntimeError("API 오류"))

        stage, reason = await svc.get_stage_for_code("000000")

        assert stage == MinerviniStageService.STAGE_UNKNOWN
        assert "오류" in reason

    @pytest.mark.asyncio
    async def test_uses_db_snapshot_when_available(self):
        mq = MagicMock()
        stock_repo = AsyncMock()
        stock_repo.get_latest_daily_snapshot.return_value = {
            "minervini_stage": 2,
            "minervini_reason": "(DB)",
            "trade_date": "20260101",
        }
        market_data_service = MagicMock()
        market_data_service._stock_repo = stock_repo
        mcs = AsyncMock()
        mcs.get_latest_trading_date.return_value = "20260101"
        market_data_service._mcs = mcs
        mq.market_data_service = market_data_service

        svc = MinerviniStageService(stock_query_service=mq, rs_rating_service=None)
        stage, reason = await svc.get_stage_for_code("005930")

        assert stage == 2
        assert isinstance(reason, str)

    @pytest.mark.asyncio
    async def test_db_snapshot_invalid_stage_falls_back_to_unknown_stage_value(self):
        mq = MagicMock()
        stock_repo = AsyncMock()
        stock_repo.get_latest_daily_snapshot.return_value = {
            "minervini_stage": "bad",
            "minervini_reason": None,
            "trade_date": "20260101",
        }
        market_data_service = MagicMock()
        market_data_service._stock_repo = stock_repo
        market_data_service._mcs = None
        mq.market_data_service = market_data_service

        svc = MinerviniStageService(stock_query_service=mq, rs_rating_service=None)
        stage, reason = await svc.get_stage_for_code("005930")

        assert stage == MinerviniStageService.STAGE_UNKNOWN
        assert reason == "(DB)"

    @pytest.mark.asyncio
    async def test_stale_db_snapshot_falls_through_to_realtime_calculation(self):
        mq = MagicMock()
        stock_repo = AsyncMock()
        stock_repo.get_latest_daily_snapshot.return_value = {
            "minervini_stage": 2,
            "minervini_reason": "(DB)",
            "trade_date": "20251231",
        }
        market_data_service = MagicMock()
        market_data_service._stock_repo = stock_repo
        mcs = AsyncMock()
        mcs.get_latest_trading_date.return_value = "20260101"
        market_data_service._mcs = mcs
        mq.market_data_service = market_data_service
        closes = _trending_closes(5000, 15000, 260)
        mq.get_recent_daily_ohlcv = AsyncMock(
            return_value=ResCommonResponse(
                rt_cd="0",
                msg1="ok",
                data=[{"close": c, "low": c * 0.95} for c in closes],
            )
        )

        svc = MinerviniStageService(stock_query_service=mq, rs_rating_service=None)
        stage, reason = await svc.get_stage_for_code("005930")

        assert stage == MinerviniStageService.STAGE_2_ADVANCING
        assert "트렌드 템플릿" in reason

    @pytest.mark.asyncio
    async def test_db_access_exception_falls_through_to_realtime_calculation(self):
        mq = MagicMock()
        stock_repo = AsyncMock()
        stock_repo.get_latest_daily_snapshot.side_effect = RuntimeError("db boom")
        market_data_service = MagicMock()
        market_data_service._stock_repo = stock_repo
        market_data_service._mcs = None
        mq.market_data_service = market_data_service
        closes = _trending_closes(5000, 15000, 260)
        mq.get_recent_daily_ohlcv = AsyncMock(
            return_value=ResCommonResponse(
                rt_cd="0",
                msg1="ok",
                data=[{"close": c, "low": c * 0.95} for c in closes],
            )
        )

        svc = MinerviniStageService(stock_query_service=mq, rs_rating_service=None)
        stage, _ = await svc.get_stage_for_code("005930")

        assert stage == MinerviniStageService.STAGE_2_ADVANCING

    @pytest.mark.asyncio
    async def test_ohlcv_cancelled_returns_unknown(self):
        svc = _make_svc()
        svc._stock_query_svc.get_recent_daily_ohlcv = AsyncMock(side_effect=asyncio.CancelledError())

        stage, reason = await svc.get_stage_for_code("000000")

        assert stage == MinerviniStageService.STAGE_UNKNOWN
        assert reason == "작업 취소"


class TestFetchRsRating:

    @pytest.mark.asyncio
    async def test_no_service_returns_zero(self):
        svc = MinerviniStageService(stock_query_service=AsyncMock(), rs_rating_service=None)
        assert await svc._fetch_rs_rating("005930") == 0

    @pytest.mark.asyncio
    async def test_handles_success_and_errors(self):
        rs_svc = AsyncMock()
        rs_svc.get_rating.return_value = ResCommonResponse(
            rt_cd="0",
            msg1="",
            data=ResRSRating(code="005930", trade_date="20260101", rs_rating=88, weighted_rs=10.1),
        )
        svc = MinerviniStageService(stock_query_service=AsyncMock(), rs_rating_service=rs_svc)

        assert await svc._fetch_rs_rating("005930") == 88

        async def raise_exc(code):
            raise Exception("boom")

        rs_svc.get_rating.side_effect = raise_exc
        assert await svc._fetch_rs_rating("005930") == 0

    @pytest.mark.asyncio
    async def test_cancelled_returns_zero(self):
        rs_svc = AsyncMock()
        rs_svc.get_rating.side_effect = asyncio.CancelledError()
        svc = MinerviniStageService(stock_query_service=AsyncMock(), rs_rating_service=rs_svc)

        assert await svc._fetch_rs_rating("005930") == 0

    @pytest.mark.asyncio
    async def test_invalid_payload_returns_zero(self):
        rs_svc = AsyncMock()
        rs_svc.get_rating.side_effect = [
            ResCommonResponse(rt_cd="1", msg1="", data=None),
            ResCommonResponse(rt_cd="0", msg1="", data=MagicMock(rs_rating="bad")),
        ]
        svc = MinerviniStageService(stock_query_service=AsyncMock(), rs_rating_service=rs_svc)

        assert await svc._fetch_rs_rating("005930") == 0
        assert await svc._fetch_rs_rating("005930") == 0
