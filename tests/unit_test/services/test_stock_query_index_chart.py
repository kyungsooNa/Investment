"""홈 지수 패널용 국내 지수 조회 서비스 단위 테스트."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from services.stock_query_service import StockQueryService
from common.types import ResCommonResponse, ErrorCode


def _service(broker):
    return StockQueryService(
        market_data_service=MagicMock(),
        logger=MagicMock(),
        market_clock=MagicMock(),
        broker_api_wrapper=broker,
    )


def _broker_response(summary=None, candles=None):
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data={
            "summary": summary if summary is not None else {},
            "candles": candles if candles is not None else [],
        },
    )


@pytest.mark.asyncio
async def test_index_chart_normalizes_summary_and_points():
    broker = MagicMock()
    broker.inquire_daily_indexchartprice = AsyncMock(return_value=_broker_response(
        summary={
            "hts_kor_isnm": "코스피",
            "bstp_nmix_prpr": "2650.15",
            "bstp_nmix_prdy_vrss": "12.30",
            "bstp_nmix_prdy_ctrt": "0.47",
        },
        candles=[
            {"stck_bsop_date": "20260727", "bstp_nmix_prpr": "2650.15"},
            {"stck_bsop_date": "20260724", "bstp_nmix_prpr": "2637.85"},
        ],
    ))

    result = await _service(broker).get_index_chart("0001", period="1M")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data["code"] == "0001"
    assert result.data["name"] == "코스피"
    assert result.data["current"] == pytest.approx(2650.15)
    assert result.data["change"] == pytest.approx(12.30)
    assert result.data["change_rate"] == pytest.approx(0.47)
    # KIS 는 최신순으로 주므로 차트용으로 과거→현재 순서여야 한다.
    assert [p["date"] for p in result.data["points"]] == ["20260724", "20260727"]
    assert result.data["points"][-1]["close"] == pytest.approx(2650.15)


@pytest.mark.asyncio
async def test_index_chart_1d_uses_minute_api_and_keeps_time():
    broker = MagicMock()
    broker.inquire_daily_indexchartprice = AsyncMock()
    broker.inquire_time_indexchartprice = AsyncMock(return_value=_broker_response(
        summary={"hts_kor_isnm": "코스피", "bstp_nmix_prpr": "2650.15"},
        candles=[
            {"stck_bsop_date": "20260729", "stck_cntg_hour": "090000", "bstp_nmix_prpr": "2640.10"},
            {"stck_bsop_date": "20260729", "stck_cntg_hour": "091000", "bstp_nmix_prpr": "2650.15"},
        ],
    ))

    result = await _service(broker).get_index_chart("0001", period="1D")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data["period"] == "1D"
    broker.inquire_daily_indexchartprice.assert_not_called()
    # 10분봉이면 정규장 전 구간이 한 번에 담긴다.
    assert broker.inquire_time_indexchartprice.await_args.kwargs["interval_seconds"] == 600
    assert [p["time"] for p in result.data["points"]] == ["090000", "091000"]
    assert result.data["points"][-1]["close"] == pytest.approx(2650.15)


@pytest.mark.asyncio
async def test_index_chart_1y_uses_weekly_candles():
    broker = MagicMock()
    broker.inquire_daily_indexchartprice = AsyncMock(return_value=_broker_response(
        summary={"bstp_nmix_prpr": "2650.15"},
        candles=[{"stck_bsop_date": "20260727", "bstp_nmix_prpr": "2650.15"}],
    ))

    result = await _service(broker).get_index_chart("0001", period="1Y")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert broker.inquire_daily_indexchartprice.await_args.kwargs["period"] == "W"
    # 분봉이 아니므로 시각은 붙지 않는다.
    assert "time" not in result.data["points"][0]


@pytest.mark.asyncio
async def test_index_chart_1m_trims_to_period_length():
    candles = [
        {"stck_bsop_date": f"202607{day:02d}", "bstp_nmix_prpr": str(2600 + day)}
        for day in range(1, 31)
    ]
    broker = MagicMock()
    broker.inquire_daily_indexchartprice = AsyncMock(return_value=_broker_response(
        summary={"bstp_nmix_prpr": "2630.0"}, candles=candles,
    ))

    result = await _service(broker).get_index_chart("0001", period="1M")

    assert broker.inquire_daily_indexchartprice.await_args.kwargs["period"] == "D"
    assert len(result.data["points"]) == 22
    assert result.data["points"][-1]["date"] == "20260730"


@pytest.mark.asyncio
async def test_index_chart_rejects_unknown_period():
    broker = MagicMock()
    broker.inquire_daily_indexchartprice = AsyncMock()
    broker.inquire_time_indexchartprice = AsyncMock()

    result = await _service(broker).get_index_chart("0001", period="10Y")

    assert result.rt_cd == ErrorCode.INVALID_INPUT.value
    broker.inquire_daily_indexchartprice.assert_not_called()
    broker.inquire_time_indexchartprice.assert_not_called()


@pytest.mark.asyncio
async def test_index_chart_rejects_unknown_index_code():
    broker = MagicMock()
    broker.inquire_daily_indexchartprice = AsyncMock()

    result = await _service(broker).get_index_chart("9999")

    assert result.rt_cd == ErrorCode.INVALID_INPUT.value
    broker.inquire_daily_indexchartprice.assert_not_called()


@pytest.mark.asyncio
async def test_index_chart_propagates_broker_failure():
    broker = MagicMock()
    broker.inquire_daily_indexchartprice = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.API_ERROR.value, msg1="조회 실패", data=None,
    ))

    result = await _service(broker).get_index_chart("0001", period="1M")

    assert result.rt_cd == ErrorCode.API_ERROR.value


@pytest.mark.asyncio
async def test_index_chart_skips_unparsable_candles():
    broker = MagicMock()
    broker.inquire_daily_indexchartprice = AsyncMock(return_value=_broker_response(
        summary={"bstp_nmix_prpr": "2650.15"},
        candles=[
            {"stck_bsop_date": "20260727", "bstp_nmix_prpr": "2650.15"},
            {"stck_bsop_date": "", "bstp_nmix_prpr": ""},
            {"stck_bsop_date": "20260724", "bstp_nmix_prpr": "invalid"},
        ],
    ))

    result = await _service(broker).get_index_chart("0001", period="1M")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert [p["date"] for p in result.data["points"]] == ["20260727"]
