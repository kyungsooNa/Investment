from unittest.mock import AsyncMock, MagicMock

from services.ai_client import AiClientError
from services.ai_disclosure_analyzer import AiDisclosureAnalyzer
from services.dart_disclosure_client import DartDisclosure
from services.dart_disclosure_rule_service import DisclosureImportance


def _disclosure():
    return DartDisclosure(
        corp_class="Y",
        corp_name="삼성전자",
        corp_code="00126380",
        stock_code="005930",
        report_name="전환사채권발행결정",
        receipt_no="20260714000001",
        filer_name="삼성전자",
        receipt_date="20260714",
        remarks="유",
    )


def _importance():
    return DisclosureImportance(85, "HIGH", ["자금조달·주식 희석 관련 공시"])


async def test_summarize_returns_ai_text_and_passes_context():
    ai_client = MagicMock()
    ai_client.complete = AsyncMock(return_value="  전환사채 발행으로 주식 희석 우려가 있습니다.  ")
    analyzer = AiDisclosureAnalyzer(ai_client, logger=MagicMock())

    result = await analyzer.summarize(_disclosure(), _importance())

    assert result == "전환사채 발행으로 주식 희석 우려가 있습니다."
    user_prompt = ai_client.complete.await_args.kwargs["user"]
    assert "삼성전자" in user_prompt
    assert "전환사채권발행결정" in user_prompt
    assert "005930" in user_prompt
    assert ai_client.complete.await_args.kwargs["usage_type"] == "disclosure"


async def test_summarize_returns_none_when_ai_fails():
    ai_client = MagicMock()
    ai_client.complete = AsyncMock(side_effect=AiClientError("NETWORK", "timeout"))
    logger = MagicMock()
    analyzer = AiDisclosureAnalyzer(ai_client, logger=logger)

    result = await analyzer.summarize(_disclosure(), _importance())

    assert result is None
    logger.warning.assert_called_once()


async def test_summarize_returns_none_when_ai_returns_blank():
    ai_client = MagicMock()
    ai_client.complete = AsyncMock(return_value="   ")
    analyzer = AiDisclosureAnalyzer(ai_client, logger=MagicMock())

    result = await analyzer.summarize(_disclosure(), _importance())

    assert result is None
