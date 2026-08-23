from unittest.mock import AsyncMock, MagicMock

import pytest

from services.premium_watchlist_ai_analysis_service import PremiumWatchlistAiAnalysisService


@pytest.mark.asyncio
async def test_analyze_merges_premium_and_favorites_once_and_preserves_source():
    favorites = MagicMock()
    favorites.get_all = AsyncMock(return_value=["005930", "000660"])
    analyzer = MagicMock()
    analyzer.analyze = AsyncMock(return_value="신호: 상\n신호 근거: 수급이 개선되었습니다.\n상세")
    service = PremiumWatchlistAiAnalysisService(
        ai_stock_analyzer=analyzer,
        favorite_service=favorites,
        stock_code_repository=MagicMock(get_name_by_code=lambda code: {"005930": "삼성전자", "000660": "SK하이닉스"}[code]),
    )

    result = await service.analyze(
        kospi=[{"code": "005930", "name": "삼성전자"}],
        kosdaq=[],
        report_date="20260320",
    )

    assert list(result) == ["005930", "000660"]
    assert result["005930"]["source"] == "premium"
    assert result["000660"]["source"] == "favorite"
    assert result["005930"]["signal"] == "상"
    assert result["005930"]["signal_reason"] == "수급이 개선되었습니다."
    assert analyzer.analyze.await_count == 2
    assert analyzer.analyze.await_args_list[0].args[0]["report_date"] == "20260320"


@pytest.mark.asyncio
async def test_analyze_keeps_favorite_in_report_when_ai_request_is_rate_limited():
    favorites = MagicMock()
    favorites.get_all = AsyncMock(return_value=["000660"])
    analyzer = MagicMock()
    analyzer.analyze = AsyncMock(side_effect=RuntimeError("429 Too Many Requests"))
    service = PremiumWatchlistAiAnalysisService(
        ai_stock_analyzer=analyzer,
        favorite_service=favorites,
        stock_code_repository=MagicMock(get_name_by_code=lambda _code: "SK하이닉스"),
    )

    result = await service.analyze(kospi=[], kosdaq=[], report_date="20260807")

    assert result == {
        "000660": {
            "name": "SK하이닉스",
            "source": "favorite",
            "signal": "-",
            "signal_reason": "AI 분석 요청 제한으로 다음 리포트에서 재시도합니다.",
        }
    }


def _service(**kwargs):
    analyzer = kwargs.pop("analyzer", None) or MagicMock(
        analyze=AsyncMock(return_value="신호: 상\n신호 근거: 근거\n상세")
    )
    favorites = kwargs.pop("favorites", "__default__")
    if favorites == "__default__":
        favorites = MagicMock(get_all=AsyncMock(return_value=[]))
    return PremiumWatchlistAiAnalysisService(
        ai_stock_analyzer=analyzer,
        favorite_service=favorites,
        stock_code_repository=kwargs.pop("stock_code_repository", MagicMock()),
        logger=kwargs.pop("logger", MagicMock()),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_analyze_analyzes_each_code_only_once():
    analyzer = MagicMock(analyze=AsyncMock(return_value="신호: 상\n신호 근거: 근거"))
    service = _service(analyzer=analyzer)

    result = await service.analyze(
        kospi=[{"code": "005930"}, {"code": " 005930 "}],
        kosdaq=[{"code": "005930", "name": "중복"}, {"code": "5930"}],
        report_date="20260320",
    )

    # 공백 제거 + 6자리 zero-fill 후 중복 판정한다.
    assert list(result) == ["005930"]
    assert analyzer.analyze.await_count == 1


@pytest.mark.asyncio
async def test_analyze_caps_candidate_count_at_max_stocks():
    analyzer = MagicMock(analyze=AsyncMock(return_value="신호: 상\n신호 근거: 근거"))
    service = _service(analyzer=analyzer, max_stocks=2)

    result = await service.analyze(
        kospi=[{"code": f"00000{i}"} for i in range(5)],
        kosdaq=[],
        report_date="20260320",
    )

    assert len(result) == 2


@pytest.mark.asyncio
async def test_analyze_works_without_favorite_service_or_code_repository():
    service = _service(favorites=None, stock_code_repository=None)

    result = await service.analyze(
        kospi=[{"code": "005930"}], kosdaq=[], report_date="20260320"
    )

    assert list(result) == ["005930"]
    assert result["005930"]["name"] == "005930"


@pytest.mark.asyncio
async def test_analyze_zero_fills_short_favorite_codes():
    favorites = MagicMock(get_all=AsyncMock(return_value=["660", " 005930 "]))
    service = _service(favorites=favorites, stock_code_repository=None)

    result = await service.analyze(kospi=[], kosdaq=[], report_date="20260320")

    assert list(result) == ["000660", "005930"]


@pytest.mark.asyncio
async def test_max_stocks_is_at_least_one():
    service = _service(max_stocks=0)

    assert service._max_stocks == 1


@pytest.mark.asyncio
async def test_current_price_lookup_is_optional_and_failure_safe():
    service = _service()
    assert await service._optional_current_price("005930") is None

    service._stock_query = MagicMock(spec=[])  # 조회 메서드 없음
    assert await service._optional_current_price("005930") is None

    service._stock_query = MagicMock()
    service._stock_query.handle_get_current_stock_price = AsyncMock(
        side_effect=RuntimeError("조회 실패")
    )
    assert await service._optional_current_price("005930") is None

    service._stock_query.handle_get_current_stock_price = AsyncMock(return_value={"price": 1})
    assert await service._optional_current_price("005930") == {"price": 1}


@pytest.mark.asyncio
async def test_optional_call_absorbs_missing_target_method_and_errors():
    assert await PremiumWatchlistAiAnalysisService._optional_call(None, "any") is None
    assert await PremiumWatchlistAiAnalysisService._optional_call(MagicMock(spec=[]), "any") is None

    failing = MagicMock()
    failing.get_rating = AsyncMock(side_effect=RuntimeError("boom"))
    assert await PremiumWatchlistAiAnalysisService._optional_call(failing, "get_rating", "005930") is None

    working = MagicMock()
    working.get_rating = AsyncMock(return_value=88)
    assert await PremiumWatchlistAiAnalysisService._optional_call(working, "get_rating", "005930") == 88


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, None),
        ({"a": 1}, {"a": 1}),
        ([1, 2], [1, 2]),
        ((1, 2), (1, 2)),
    ],
)
def test_response_data_passes_through_plain_containers(value, expected):
    assert PremiumWatchlistAiAnalysisService._response_data(value) == expected


def test_response_data_unwraps_successful_api_response():
    ok = MagicMock(rt_cd="0", data={"price": 70000})
    assert PremiumWatchlistAiAnalysisService._response_data(ok) == {"price": 70000}

    failed = MagicMock(rt_cd="1", data={"price": 70000})
    assert PremiumWatchlistAiAnalysisService._response_data(failed) is failed


@pytest.mark.asyncio
async def test_context_carries_source_label_and_optional_sections():
    stock_query = MagicMock()
    stock_query.handle_get_current_stock_price = AsyncMock(
        return_value=MagicMock(rt_cd="0", data={"price": 70000})
    )
    stock_query.get_financial_ratio = AsyncMock(return_value=MagicMock(rt_cd="0", data={"per": 12}))
    stock_query.get_investor_trade_daily_multi = AsyncMock(return_value=None)
    service = _service(stock_query_service=stock_query)

    context = await service._build_context("005930", "삼성전자", "favorite", {}, "20260320")

    assert context["candidate_source"] == "즐겨찾기"
    assert context["premium_metrics"] is None
    assert context["current"] == {"price": 70000}
    assert context["financial"] == {"per": 12}
    assert context["stage"] is None
    assert context["news"] is None
