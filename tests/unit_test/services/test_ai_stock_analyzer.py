from unittest.mock import AsyncMock, MagicMock

from services.ai_stock_analyzer import AiStockAnalyzer


async def test_analyze_passes_all_stock_context_to_ai_client():
    ai_client = MagicMock()
    ai_client.complete = AsyncMock(return_value="  상승 추세지만 밸류에이션 확인이 필요합니다.  ")
    analyzer = AiStockAnalyzer(ai_client, max_tokens=1536)
    context = {
        "code": "005930",
        "name": "삼성전자",
        "current": {"price": "70000", "per": "15.2"},
        "financial": [{"stac_yymm": "202512", "roe_val": "9.5"}],
        "stage": {"stage": 2, "reason": "상승 추세"},
        "rs_rating": {"rs_rating": 87},
        "investor_flow": [{"frgn_ntby_tr_pbmn": "12000"}],
        "disclosures": [{"report_name": "분기보고서", "receipt_date": "20260715"}],
    }

    result = await analyzer.analyze(context)

    assert result == "상승 추세지만 밸류에이션 확인이 필요합니다."
    call = ai_client.complete.await_args.kwargs
    assert call["max_tokens"] == 1536
    assert call["usage_type"] == "stock"
    assert "투자 권유" in call["system"]
    for expected in (
        "005930",
        "삼성전자",
        "70000",
        "202512",
        "상승 추세",
        "87",
        "12000",
        "분기보고서",
    ):
        assert expected in call["user"]


async def test_analyze_accepts_missing_optional_sources():
    ai_client = MagicMock()
    ai_client.complete = AsyncMock(return_value="데이터가 제한적입니다.")
    analyzer = AiStockAnalyzer(ai_client)

    result = await analyzer.analyze(
        {
            "code": "005930",
            "name": "삼성전자",
            "current": None,
            "financial": None,
            "stage": None,
            "rs_rating": None,
            "investor_flow": None,
            "disclosures": [],
        }
    )

    assert result == "데이터가 제한적입니다."
    assert "데이터 없음" in ai_client.complete.await_args.kwargs["user"]


async def test_system_prompt_requires_first_line_signal():
    ai_client = MagicMock()
    ai_client.complete = AsyncMock(return_value="신호: 중\n한줄 요약")
    analyzer = AiStockAnalyzer(ai_client)

    await analyzer.analyze({"code": "005930", "name": "삼성전자"})

    system = ai_client.complete.await_args.kwargs["system"]
    assert "첫 줄" in system
    for expected in ("신호: 상", "신호: 중", "신호: 하"):
        assert expected in system
