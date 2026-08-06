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
