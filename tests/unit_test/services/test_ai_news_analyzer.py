from unittest.mock import AsyncMock, MagicMock

from services.ai_news_analyzer import AiNewsAnalyzer


def _context(news=None):
    return {
        "code": "005930",
        "name": "삼성전자",
        "news": news if news is not None else [
            {
                "title": "삼성전자, HBM4 양산 계약 체결",
                "press": "연합뉴스",
                "published_at": "2026.07.20 09:10",
                "url": "https://finance.naver.com/item/news_read.naver?article_id=1",
            },
            {
                "title": "코스피 이번 주 운명의 한 주",
                "press": "주간조선",
                "published_at": "2026.07.19 18:00",
                "url": "https://finance.naver.com/item/news_read.naver?article_id=2",
            },
        ],
    }


async def test_analyze_passes_news_context_to_ai_client():
    ai_client = MagicMock()
    ai_client.complete = AsyncMock(return_value="  한줄 요약: 수주 모멘텀이 있습니다.  ")
    analyzer = AiNewsAnalyzer(ai_client, max_tokens=1536)

    result = await analyzer.analyze(_context())

    assert result == "한줄 요약: 수주 모멘텀이 있습니다."
    call = ai_client.complete.await_args.kwargs
    assert call["max_tokens"] == 1536
    assert call["usage_type"] == "news"
    for expected in ("005930", "삼성전자", "HBM4 양산 계약", "연합뉴스", "2026.07.20 09:10"):
        assert expected in call["user"]


async def test_system_prompt_forbids_recommendation_and_body_guessing():
    ai_client = MagicMock()
    ai_client.complete = AsyncMock(return_value="요약")
    analyzer = AiNewsAnalyzer(ai_client)

    await analyzer.analyze(_context())

    system = ai_client.complete.await_args.kwargs["system"]
    assert "투자 권유" in system
    assert "제목" in system  # 제목만 제공된다는 사실을 프롬프트가 명시해야 한다


async def test_analyze_handles_empty_news_and_blank_response():
    ai_client = MagicMock()
    ai_client.complete = AsyncMock(return_value="")
    analyzer = AiNewsAnalyzer(ai_client)

    result = await analyzer.analyze(_context(news=[]))

    assert result == ""
    assert "뉴스 없음" in ai_client.complete.await_args.kwargs["user"]
