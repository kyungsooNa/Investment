"""StockQueryService 의 구독 동기화·prefetch·지수 조회 방어 경로 테스트.

기존 테스트가 정상 시세 조회 흐름을 다루므로, 여기서는 구독 서비스 미배선,
batch prefetch 서킷 오픈, broker 미설정 위임, 지수 코드/기간 검증처럼 조회가
성립하지 않을 때의 반환값을 채운다.
"""
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, Exchange, ResCommonResponse
from services.stock_query_service import StockQueryService


@pytest.fixture
def service():
    return StockQueryService(
        market_data_service=AsyncMock(),
        logger=MagicMock(),
        market_clock=MagicMock(),
        indicator_service=AsyncMock(),
        broker_api_wrapper=AsyncMock(),
    )


# --- 구독 동기화 -------------------------------------------------------------

@pytest.mark.asyncio
async def test_subscription_sync_is_a_no_op_without_the_service(service):
    assert await service.sync_price_subscriptions(["005930"], "strategy") is False


@pytest.mark.asyncio
@pytest.mark.parametrize("codes", [[], [None, "", "   "]])
async def test_subscription_sync_skips_when_no_usable_code_remains(service, codes):
    service.price_subscription_service = MagicMock(sync_subscriptions=AsyncMock())

    assert await service.sync_price_subscriptions(codes, "strategy") is False
    service.price_subscription_service.sync_subscriptions.assert_not_awaited()


@pytest.mark.asyncio
async def test_subscription_sync_skips_when_the_service_lacks_the_method(service):
    service.price_subscription_service = MagicMock(sync_subscriptions=None)

    assert await service.sync_price_subscriptions(["005930"], "strategy") is False


@pytest.mark.asyncio
async def test_subscription_sync_failure_is_logged_and_reported(service):
    service.price_subscription_service = MagicMock(
        sync_subscriptions=AsyncMock(side_effect=RuntimeError("구독 실패"))
    )

    assert await service.sync_price_subscriptions(["005930"], "strategy") is False
    service.logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_subscription_sync_deduplicates_codes_and_defaults_the_priority(service):
    from services.price_subscription_service import SubscriptionPriority

    sync = AsyncMock()
    service.price_subscription_service = MagicMock(sync_subscriptions=sync)

    assert await service.sync_price_subscriptions(
        ["005930", "005930", "000660"], "strategy"
    ) is True
    sync.assert_awaited_once_with(
        ["005930", "000660"], "strategy", SubscriptionPriority.MEDIUM
    )


# --- batch prefetch ----------------------------------------------------------

@pytest.mark.asyncio
async def test_prefetch_is_skipped_without_a_price_stream(service):
    assert await service.prefetch_prices(["005930"]) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("codes", [[], [None, "", "  "]])
async def test_prefetch_skips_when_no_usable_code_remains(service, codes):
    service.price_stream_service = MagicMock()

    assert await service.prefetch_prices(codes) == 0


@pytest.mark.asyncio
async def test_prefetch_stops_while_the_circuit_is_open(service):
    service.price_stream_service = MagicMock(get_cached_price=MagicMock(return_value=None))
    service._multi_price_prefetch_disabled_until = time.time() + 60

    assert await service.prefetch_prices(["005930"]) == 0
    assert service._price_lookup_stats["batch_prefetch_circuit_open"] == 1


@pytest.mark.asyncio
async def test_prefetch_call_failure_is_counted_and_the_loop_continues(service):
    service.price_stream_service = MagicMock(get_cached_price=MagicMock(return_value=None))
    service.get_multi_price = AsyncMock(side_effect=RuntimeError("API 오류"))

    assert await service.prefetch_prices(["005930"]) == 0
    assert service._price_lookup_stats["batch_prefetch_failure"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        None,
        ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="fail", data=None),
        ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=None),
    ],
)
async def test_prefetch_treats_an_unusable_response_as_a_failure(service, response):
    service.price_stream_service = MagicMock(get_cached_price=MagicMock(return_value=None))
    service.get_multi_price = AsyncMock(return_value=response)

    assert await service.prefetch_prices(["005930"]) == 0
    assert service._price_lookup_stats["batch_prefetch_failure"] == 1


@pytest.mark.asyncio
async def test_prefetch_skips_rows_without_a_code_or_a_price(service):
    service.price_stream_service = MagicMock(get_cached_price=MagicMock(return_value=None))
    service.get_multi_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="ok",
        data=["딕셔너리 아님",
              {"stck_shrn_iscd": "", "stck_prpr": "70000"},
              {"stck_shrn_iscd": "005930", "stck_prpr": "0"}],
    ))

    assert await service.prefetch_prices(["005930"]) == 0
    service.price_stream_service.cache_price_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_prefetch_backfill_failure_is_logged_but_not_fatal(service):
    service.price_stream_service = MagicMock(
        get_cached_price=MagicMock(return_value=None),
        cache_price_snapshot=MagicMock(side_effect=RuntimeError("캐시 오류")),
    )
    service.get_multi_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="ok",
        data=[{"stck_shrn_iscd": "005930", "stck_prpr": "70000"}],
    ))

    assert await service.prefetch_prices(["005930"]) == 0
    service.logger.debug.assert_called()


@pytest.mark.asyncio
async def test_prefetch_backfills_the_snapshot_cache(service):
    service.price_stream_service = MagicMock(get_cached_price=MagicMock(return_value=None))
    service.get_multi_price = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value, msg1="ok",
        data=[{"stck_shrn_iscd": "005930", "stck_prpr": "70000"}],
    ))

    assert await service.prefetch_prices(["005930"]) == 1
    service.price_stream_service.cache_price_snapshot.assert_called_once()


# --- 체결강도 snapshot --------------------------------------------------------

@pytest.mark.asyncio
async def test_conclusion_snapshot_is_missing_without_a_price_stream(service):
    from services.data_quality_service import DataQualityService

    snapshot, reason = await service.get_conclusion_snapshot("005930")

    assert snapshot is None
    assert reason == DataQualityService.REASON_CONCLUSION_MISSING
    assert service._price_lookup_stats["conclusion_missing_fallback"] == 1


@pytest.mark.asyncio
async def test_conclusion_snapshot_reports_a_failed_rest_lookup(service):
    from services.data_quality_service import DataQualityService

    service.price_stream_service = MagicMock(
        get_conclusion_snapshot=MagicMock(return_value=None)
    )
    service.market_data_service.get_stock_conclusion = AsyncMock(return_value=None)

    snapshot, reason = await service.get_conclusion_snapshot("005930")

    assert snapshot is None
    assert reason == DataQualityService.REASON_CONCLUSION_MISSING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output", [{"tday_rltv": "숫자아님"}, {"tday_rltv": "N/A"}, "딕셔너리도 리스트도 아님"]
)
async def test_unparsable_execution_strength_falls_back_to_zero(service, output):
    service.price_stream_service = MagicMock(
        get_conclusion_snapshot=MagicMock(return_value=None)
    )
    service.market_data_service.get_stock_conclusion = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok",
                                       data={"output": output})
    )

    await service.get_conclusion_snapshot("005930")

    service.price_stream_service.cache_conclusion_snapshot.assert_called_once_with(
        "005930", 0.0
    )


# --- broker 미설정 위임 -------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, args, kwargs, expected_data",
    [
        ("get_overseas_price", ("AAPL",), {}, None),
        ("get_overseas_dailyprice", ("AAPL",), {}, []),
        ("get_overseas_balance", (), {}, None),
        ("get_overseas_order_history", (), {"start_date": "20260501",
                                            "end_date": "20260531"}, None),
        ("get_investor_trade_daily_multi", ("005930", "20260501", 5), {}, []),
    ],
)
async def test_overseas_and_investor_queries_report_a_missing_broker(
    service, method, args, kwargs, expected_data
):
    service.broker = None

    resp = await getattr(service, method)(*args, **kwargs)

    assert resp.rt_cd == ErrorCode.UNKNOWN_ERROR.value
    assert resp.msg1 == "broker 미설정"
    assert resp.data == expected_data


# --- 지수 일봉 ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_index_ohlcv_rejects_an_unsupported_index_code(service):
    resp = await service.get_recent_daily_index_ohlcv("9999", 20)

    assert resp.rt_cd == ErrorCode.INVALID_INPUT.value
    assert resp.data == []
    service.logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_index_ohlcv_rejects_a_non_positive_limit(service):
    resp = await service.get_recent_daily_index_ohlcv("0001", 0)

    assert resp.rt_cd == ErrorCode.INVALID_INPUT.value


@pytest.mark.asyncio
async def test_index_ohlcv_rejects_a_malformed_end_date(service):
    resp = await service.get_recent_daily_index_ohlcv("0001", 20, end_date="날짜아님")

    assert resp.rt_cd == ErrorCode.INVALID_INPUT.value


@pytest.mark.asyncio
async def test_index_ohlcv_reports_a_failed_broker_lookup(service):
    service.broker.inquire_daily_indexchartprice = AsyncMock(return_value=None)

    resp = await service.get_recent_daily_index_ohlcv("0001", 20, end_date="20260501")

    assert resp.rt_cd == ErrorCode.API_ERROR.value


@pytest.mark.asyncio
async def test_index_ohlcv_sorts_and_trims_to_the_requested_limit(service):
    service.broker.inquire_daily_indexchartprice = AsyncMock(
        return_value=ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data={
            "candles": [
                {"stck_bsop_date": "20260503", "bstp_nmix_prpr": "2600"},
                {"stck_bsop_date": "20260501", "bstp_nmix_prpr": "2500"},
                {"stck_bsop_date": "", "bstp_nmix_prpr": "2400"},
                {"stck_bsop_date": "20260502", "bstp_nmix_prpr": "숫자아님"},
            ]
        })
    )

    resp = await service.get_recent_daily_index_ohlcv("0001", 2, end_date="20260503")

    assert [row["date"] for row in resp.data] == ["20260501", "20260503"]


# --- 지수 수급 ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_investor_netbuy_is_none_when_the_lookup_raises(service):
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
async def test_investor_netbuy_is_none_for_an_unusable_response(service, response):
    service.broker.inquire_investor_daily_by_market = AsyncMock(return_value=response)

    assert await service._index_investor_netbuy("0001", "20260501") is None


# --- MarketSnapshot 캐시 ------------------------------------------------------

def test_market_snapshot_is_missing_without_a_price_stream(service):
    from services.data_quality_service import DataQualityService

    assert service.get_market_snapshot("005930") == (
        None, DataQualityService.REASON_SNAPSHOT_MISSING
    )


def test_market_snapshot_is_forced_stale_on_demand(service):
    from services.data_quality_service import DataQualityService

    service.price_stream_service = MagicMock()

    assert service.get_market_snapshot("005930", force_fresh=True) == (
        None, DataQualityService.REASON_SNAPSHOT_STALE
    )


def test_market_snapshot_is_missing_when_nothing_is_cached(service):
    from services.data_quality_service import DataQualityService

    service.price_stream_service = MagicMock(
        get_market_snapshot=MagicMock(return_value=None)
    )

    assert service.get_market_snapshot("005930") == (
        None, DataQualityService.REASON_SNAPSHOT_MISSING
    )


def test_an_aged_market_snapshot_is_returned_but_flagged_stale(service):
    from services.data_quality_service import DataQualityService

    snap = MagicMock(received_at=time.time() - 600)
    service.price_stream_service = MagicMock(
        get_market_snapshot=MagicMock(return_value=snap)
    )

    assert service.get_market_snapshot("005930", max_age_sec=1.0) == (
        snap, DataQualityService.REASON_SNAPSHOT_STALE
    )


def test_a_fresh_market_snapshot_carries_no_reason(service):
    snap = MagicMock(received_at=time.time())
    service.price_stream_service = MagicMock(
        get_market_snapshot=MagicMock(return_value=snap)
    )

    assert service.get_market_snapshot("005930") == (snap, None)


# --- 랭킹 카테고리 디스패치 ----------------------------------------------------

@pytest.mark.asyncio
async def test_unsupported_ranking_category_is_rejected(service):
    resp = await service.handle_get_top_stocks("없는카테고리")

    assert resp.rt_cd == ErrorCode.INVALID_INPUT.value
    service.logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_ranking_task_categories_are_dispatched(service):
    ok = ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=[{"code": "005930"}])
    service.ranking_task = MagicMock(get_foreign_net_buy_ranking=AsyncMock(return_value=ok))
    service._notification_service = MagicMock(emit=AsyncMock())

    assert await service.handle_get_top_stocks("foreign_buy") is ok
    service._notification_service.emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_failed_ranking_lookup_emits_a_warning_notification(service):
    service.market_data_service.get_top_volume_stocks = AsyncMock(return_value=None)
    service._notification_service = MagicMock(emit=AsyncMock())

    assert await service.handle_get_top_stocks("volume") is None
    args = service._notification_service.emit.await_args.args
    assert args[3] == "응답 없음"


# --- broker 배선 시 해외 위임 --------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, target, args, kwargs",
    [
        ("get_overseas_price", "get_overseas_price", ("AAPL",), {}),
        ("get_overseas_dailyprice", "get_overseas_dailyprice", ("AAPL",), {}),
        ("get_overseas_balance", "get_overseas_balance", (), {}),
        ("get_overseas_order_history", "inquire_overseas_ccnl", (),
         {"start_date": "20260501", "end_date": "20260531"}),
        ("get_investor_trade_daily_multi", "get_investor_trade_by_stock_daily_multi",
         ("005930", "20260501", 5), {}),
    ],
)
async def test_broker_backed_queries_delegate_to_the_wrapper(
    service, method, target, args, kwargs
):
    sentinel = object()
    setattr(service.broker, target, AsyncMock(return_value=sentinel))

    assert await getattr(service, method)(*args, **kwargs) is sentinel
    getattr(service.broker, target).assert_awaited_once()
