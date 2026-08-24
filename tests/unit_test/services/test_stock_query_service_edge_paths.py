"""StockQueryService 의 구독 동기화·prefetch·해외/지수 위임 방어 경로 테스트.

기존 테스트가 현재가 조회 본류를 다루므로, 여기서는 구독 서비스 미배선/실패,
batch prefetch 서킷과 응답 결손, broker 미설정 위임, 지수 조회 입력 검증처럼
정상 경로에서는 지나가지 않는 분기를 채운다.
"""
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, Exchange, ResCommonResponse
from services.data_quality_service import DataQualityService
from services.stock_query_service import StockQueryService


def _build(**overrides):
    defaults = dict(
        market_data_service=AsyncMock(),
        logger=MagicMock(),
        market_clock=MagicMock(),
        indicator_service=AsyncMock(),
        broker_api_wrapper=AsyncMock(),
    )
    defaults.update(overrides)
    return StockQueryService(**defaults)


def _ok(data):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=data)


# --- 구독 동기화 -------------------------------------------------------------

@pytest.mark.asyncio
async def test_subscription_sync_is_a_no_op_without_the_service():
    service = _build()

    assert await service.sync_price_subscriptions(["005930"], "strategy") is False


@pytest.mark.asyncio
async def test_subscription_sync_skips_blank_and_duplicate_codes():
    sub_svc = MagicMock(sync_subscriptions=AsyncMock())
    service = _build(price_subscription_service=sub_svc)

    assert await service.sync_price_subscriptions(["005930", "005930", "", None],
                                                  "strategy") is True
    assert sub_svc.sync_subscriptions.await_args.args[0] == ["005930"]


@pytest.mark.asyncio
async def test_subscription_sync_reports_failure_when_every_code_is_blank():
    sub_svc = MagicMock(sync_subscriptions=AsyncMock())
    service = _build(price_subscription_service=sub_svc)

    assert await service.sync_price_subscriptions(["", None], "strategy") is False
    sub_svc.sync_subscriptions.assert_not_awaited()


@pytest.mark.asyncio
async def test_subscription_sync_reports_failure_when_the_service_lacks_the_method():
    service = _build(price_subscription_service=SimpleNamespace(sync_subscriptions=None))

    assert await service.sync_price_subscriptions(["005930"], "strategy") is False


@pytest.mark.asyncio
async def test_subscription_sync_failure_is_logged_and_does_not_raise():
    sub_svc = MagicMock(sync_subscriptions=AsyncMock(side_effect=RuntimeError("구독 오류")))
    service = _build(price_subscription_service=sub_svc)

    assert await service.sync_price_subscriptions(["005930"], "strategy") is False
    service.logger.warning.assert_called_once()


# --- batch prefetch -----------------------------------------------------------

def _prefetch_service(**overrides):
    price_stream = MagicMock()
    price_stream.get_cached_price.return_value = None
    price_stream.cache_price_snapshot = MagicMock()
    defaults = dict(price_stream_service=price_stream)
    defaults.update(overrides)
    service = _build(**defaults)
    service.get_multi_price = AsyncMock(return_value=_ok([]))
    return service, price_stream


@pytest.mark.asyncio
async def test_prefetch_is_a_no_op_without_a_price_stream_service():
    service = _build()

    assert await service.prefetch_prices(["005930"]) == 0


@pytest.mark.asyncio
async def test_prefetch_skips_blank_and_duplicate_codes():
    service, _ = _prefetch_service()

    assert await service.prefetch_prices(["", None]) == 0
    service.get_multi_price.assert_not_awaited()


@pytest.mark.asyncio
async def test_prefetch_stops_once_the_circuit_opens():
    service, _ = _prefetch_service()
    service._multi_price_prefetch_disabled_until = time.time() + 60

    assert await service.prefetch_prices(["005930"]) == 0
    service.get_multi_price.assert_not_awaited()
    assert service._price_lookup_stats["batch_prefetch_circuit_open"] == 1


@pytest.mark.asyncio
async def test_prefetch_records_a_failure_when_the_batch_call_raises():
    service, _ = _prefetch_service()
    service.get_multi_price = AsyncMock(side_effect=RuntimeError("API 오류"))

    assert await service.prefetch_prices(["005930"]) == 0
    assert service._multi_price_prefetch_consecutive_failures == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [None, ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="fail", data=None),
     ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=None)],
)
async def test_prefetch_records_a_failure_for_an_unusable_batch_response(response):
    service, _ = _prefetch_service()
    service.get_multi_price = AsyncMock(return_value=response)

    assert await service.prefetch_prices(["005930"]) == 0
    assert service._multi_price_prefetch_consecutive_failures == 1


@pytest.mark.asyncio
async def test_repeated_prefetch_failures_open_the_circuit():
    service, _ = _prefetch_service()
    service.get_multi_price = AsyncMock(return_value=None)

    for _ in range(service._multi_price_prefetch_failure_threshold):
        await service.prefetch_prices(["005930"])

    assert service._is_multi_price_prefetch_circuit_open() is True


@pytest.mark.asyncio
async def test_prefetch_skips_rows_without_a_code_or_price():
    service, price_stream = _prefetch_service()
    service.get_multi_price = AsyncMock(return_value=_ok([
        "딕셔너리 아님",
        {"stck_shrn_iscd": "", "stck_prpr": "70000"},
        {"stck_shrn_iscd": "005930", "stck_prpr": "0"},
    ]))

    assert await service.prefetch_prices(["005930"]) == 0
    price_stream.cache_price_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_prefetch_backfill_failure_is_logged_and_skipped():
    service, price_stream = _prefetch_service()
    price_stream.cache_price_snapshot.side_effect = RuntimeError("캐시 오류")
    service.get_multi_price = AsyncMock(return_value=_ok([
        {"stck_shrn_iscd": "005930", "stck_prpr": "70000"}
    ]))

    assert await service.prefetch_prices(["005930"]) == 0
    service.logger.debug.assert_called()


@pytest.mark.asyncio
async def test_successful_prefetch_backfills_the_snapshot_cache_and_resets_the_circuit():
    service, price_stream = _prefetch_service()
    service._multi_price_prefetch_consecutive_failures = 2
    service.get_multi_price = AsyncMock(return_value=_ok([
        {"stck_shrn_iscd": "005930", "stck_prpr": "70000"}
    ]))

    assert await service.prefetch_prices(["005930"]) == 1
    assert service._multi_price_prefetch_consecutive_failures == 0
    price_stream.cache_price_snapshot.assert_called_once()


# --- 체결강도 snapshot --------------------------------------------------------

@pytest.mark.asyncio
async def test_conclusion_snapshot_is_missing_without_a_price_stream_service():
    service = _build()

    snapshot, reason = await service.get_conclusion_snapshot("005930")

    assert snapshot is None
    assert reason == DataQualityService.REASON_CONCLUSION_MISSING


@pytest.mark.asyncio
async def test_conclusion_snapshot_is_missing_when_the_rest_call_fails():
    price_stream = MagicMock()
    price_stream.get_conclusion_snapshot.return_value = None
    service = _build(price_stream_service=price_stream)
    service.market_data_service.get_stock_conclusion = AsyncMock(return_value=None)

    snapshot, reason = await service.get_conclusion_snapshot("005930")

    assert snapshot is None
    assert reason == DataQualityService.REASON_CONCLUSION_MISSING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    [
        [{"tday_rltv": "숫자아님"}],
        {"tday_rltv": "N/A"},
        SimpleNamespace(tday_rltv=None, cgld=None),
    ],
)
async def test_unparsable_execution_strength_falls_back_to_zero(output):
    price_stream = MagicMock()
    price_stream.get_conclusion_snapshot.return_value = None
    service = _build(price_stream_service=price_stream)
    service.market_data_service.get_stock_conclusion = AsyncMock(
        return_value=_ok({"output": output})
    )

    await service.get_conclusion_snapshot("005930")

    price_stream.cache_conclusion_snapshot.assert_called_once_with("005930", 0.0)


# --- broker 미설정 위임 --------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, kwargs",
    [
        ("get_overseas_price", {}),
        ("get_overseas_dailyprice", {}),
        ("get_overseas_balance", {}),
        ("get_overseas_order_history", {"start_date": "20260501", "end_date": "20260501"}),
    ],
)
async def test_overseas_queries_report_a_missing_broker(method, kwargs):
    service = _build(broker_api_wrapper=None)
    args = () if method in ("get_overseas_balance", "get_overseas_order_history") else ("AAPL",)

    resp = await getattr(service, method)(*args, **kwargs)

    assert resp.rt_cd == ErrorCode.UNKNOWN_ERROR.value
    assert resp.msg1 == "broker 미설정"


@pytest.mark.asyncio
async def test_multi_day_investor_query_reports_a_missing_broker():
    service = _build(broker_api_wrapper=None)

    resp = await service.get_investor_trade_daily_multi("005930", "20260501", 5)

    assert resp.rt_cd == ErrorCode.UNKNOWN_ERROR.value
    assert resp.data == []


# --- 지수 조회 ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_index_ohlcv_rejects_an_unsupported_index_code():
    service = _build()

    resp = await service.get_recent_daily_index_ohlcv("9999", 5)

    assert resp.rt_cd == ErrorCode.INVALID_INPUT.value
    service.logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_index_ohlcv_rejects_a_non_positive_limit():
    service = _build()

    resp = await service.get_recent_daily_index_ohlcv("0001", 0)

    assert resp.rt_cd == ErrorCode.INVALID_INPUT.value


@pytest.mark.asyncio
async def test_index_ohlcv_rejects_a_malformed_end_date():
    service = _build()

    resp = await service.get_recent_daily_index_ohlcv("0001", 5, end_date="2026년5월")

    assert resp.rt_cd == ErrorCode.INVALID_INPUT.value


@pytest.mark.asyncio
async def test_index_ohlcv_returns_the_api_failure_response_as_is():
    service = _build()
    failure = ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="조회 실패", data=[])
    service.broker.inquire_daily_indexchartprice = AsyncMock(return_value=failure)

    assert await service.get_recent_daily_index_ohlcv("0001", 5, "20260501") is failure


@pytest.mark.asyncio
async def test_index_ohlcv_substitutes_a_failure_response_when_none_comes_back():
    service = _build()
    service.broker.inquire_daily_indexchartprice = AsyncMock(return_value=None)

    resp = await service.get_recent_daily_index_ohlcv("0001", 5, "20260501")

    assert resp.rt_cd == ErrorCode.API_ERROR.value


@pytest.mark.asyncio
async def test_index_ohlcv_drops_candles_without_a_date_or_close_and_sorts_ascending():
    service = _build()
    service.broker.inquire_daily_indexchartprice = AsyncMock(return_value=_ok({"candles": [
        {"stck_bsop_date": "20260504", "bstp_nmix_prpr": "2620.5"},
        {"stck_bsop_date": "20260501", "bstp_nmix_prpr": "2600.0"},
        {"stck_bsop_date": "", "bstp_nmix_prpr": "2610.0"},
        {"stck_bsop_date": "20260505", "bstp_nmix_prpr": "숫자아님"},
    ]}))

    resp = await service.get_recent_daily_index_ohlcv("0001", 5, "20260505")

    assert [row["date"] for row in resp.data] == ["20260501", "20260504"]


@pytest.mark.asyncio
async def test_index_investor_netbuy_is_none_when_the_query_raises():
    service = _build()
    service.broker.inquire_investor_daily_by_market = AsyncMock(
        side_effect=RuntimeError("API 오류")
    )

    assert await service._index_investor_netbuy("0001", "20260501") is None
    service.logger.warning.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        None,
        ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="fail", data=None),
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=[]),
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=["딕셔너리 아님"]),
    ],
)
async def test_index_investor_netbuy_is_none_for_an_unusable_response(response):
    service = _build()
    service.broker.inquire_investor_daily_by_market = AsyncMock(return_value=response)

    assert await service._index_investor_netbuy("0001", "20260501") is None
