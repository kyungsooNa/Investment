from unittest.mock import AsyncMock, MagicMock

import pytest

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


async def test_analyze_returns_structured_result_and_passes_document_text():
    ai_client = MagicMock()
    ai_client.complete = AsyncMock(
        return_value=(
            '```json\n{"summary":"하이브리드 본더 공장과 미국 법인을 추진합니다.",'
            '"score":75,"reasons":["신제품 및 생산능력 확대"],'
            '"event_key":"공장투자|하이브리드본더|2027상반기"}\n```'
        )
    )
    analyzer = AiDisclosureAnalyzer(ai_client, logger=MagicMock())

    result = await analyzer.analyze(
        _disclosure(),
        _importance(),
        "2027년 상반기 하이브리드 본더 전용 공장 가동 예정",
    )

    assert result.summary == "하이브리드 본더 공장과 미국 법인을 추진합니다."
    assert result.importance.score == 75
    assert result.importance.level == "HIGH"
    assert result.importance.reasons == ["신제품 및 생산능력 확대"]
    assert result.event_key == "공장투자|하이브리드본더|2027상반기"
    user_prompt = ai_client.complete.await_args.kwargs["user"]
    assert "삼성전자" in user_prompt
    assert "전환사채권발행결정" in user_prompt
    assert "005930" in user_prompt
    assert "하이브리드 본더 전용 공장" in user_prompt
    assert ai_client.complete.await_args.kwargs["usage_type"] == "disclosure"
    assert '"event_key"' in user_prompt


async def test_periodic_report_prompt_requires_balanced_half_year_review():
    ai_client = MagicMock()
    ai_client.complete = AsyncMock(
        return_value=(
            '{"summary":"무디스 등급 상향, 자사주 소각, HBM4E 샘플 공급이 긍정적입니다.",'
            '"score":75,"reasons":["신용등급 및 신제품 긍정 재료"],'
            '"event_key":"정기보고서|2026반기"}'
        )
    )
    analyzer = AiDisclosureAnalyzer(ai_client, logger=MagicMock())
    disclosure = DartDisclosure(
        corp_class="Y",
        corp_name="SK하이닉스",
        corp_code="00164779",
        stock_code="000660",
        report_name="반기보고서 (2026.06)",
        receipt_no="20260814000001",
        filer_name="SK하이닉스",
        receipt_date="20260814",
        remarks="유",
    )

    result = await analyzer.analyze(
        disclosure,
        DisclosureImportance(30, "NORMAL", ["정기보고서"]),
        (
            "무디스 A3 상향, 자기주식 소각, HBM4E 샘플 공급. "
            "상반기 연결재무제표와 우발채무 내용은 별도 표에 기재."
        ),
    )

    assert result.importance.score == 60
    assert result.importance.level == "MEDIUM"
    assert "정기보고서 핵심 점검 항목 누락으로 HIGH 제한" in result.importance.reasons
    user_prompt = ai_client.complete.await_args.kwargs["user"]
    assert "정기보고서 점검 기준" in user_prompt
    assert "상반기 실적" in user_prompt
    assert "신주 발행·자기주식" in user_prompt
    assert "우발채무·소송" in user_prompt


async def test_analyze_returns_none_when_ai_fails():
    ai_client = MagicMock()
    ai_client.complete = AsyncMock(side_effect=AiClientError("NETWORK", "timeout"))
    logger = MagicMock()
    analyzer = AiDisclosureAnalyzer(ai_client, logger=logger)

    result = await analyzer.analyze(_disclosure(), _importance(), "공시 본문")

    assert result is None
    logger.warning.assert_called_once()


async def test_analyze_returns_none_when_ai_returns_invalid_json():
    ai_client = MagicMock()
    ai_client.complete = AsyncMock(return_value="중요한 공시입니다.")
    analyzer = AiDisclosureAnalyzer(ai_client, logger=MagicMock())

    result = await analyzer.analyze(_disclosure(), _importance(), "공시 본문")

    assert result is None


def test_parse_analysis_strips_a_json_code_fence():
    analysis = AiDisclosureAnalyzer._parse_analysis(
        '```json\n{"summary": "요약", "score": 80, "reasons": ["근거"], "event_key": "k"}\n```'
    )

    assert analysis.summary == "요약"
    assert analysis.importance.score == 80
    assert analysis.event_key == "k"


def test_parse_analysis_rejects_blank_summary():
    with pytest.raises(ValueError, match="summary가 비어"):
        AiDisclosureAnalyzer._parse_analysis('{"summary": "   ", "score": 50, "reasons": []}')


def test_parse_analysis_rejects_non_list_reasons():
    with pytest.raises(ValueError, match="reasons가 배열이 아닙니다"):
        AiDisclosureAnalyzer._parse_analysis(
            '{"summary": "요약", "score": 50, "reasons": "근거 문자열"}'
        )


def test_parse_analysis_fills_a_default_reason_when_all_entries_are_blank():
    analysis = AiDisclosureAnalyzer._parse_analysis(
        '{"summary": "요약", "score": 50, "reasons": ["", "  "]}'
    )

    assert analysis.importance.reasons == ["공시 본문 기반 AI 판정"]


def test_parse_analysis_clamps_score_into_range():
    high = AiDisclosureAnalyzer._parse_analysis('{"summary": "s", "score": 400, "reasons": ["r"]}')
    low = AiDisclosureAnalyzer._parse_analysis('{"summary": "s", "score": -10, "reasons": ["r"]}')

    assert high.importance.score == 100
    assert low.importance.score == 0


@pytest.mark.parametrize(
    "score, level",
    [(100, "CRITICAL"), (90, "CRITICAL"), (89, "HIGH"), (70, "HIGH"),
     (69, "MEDIUM"), (50, "MEDIUM"), (49, "NORMAL"), (30, "NORMAL"), (29, "LOW"), (0, "LOW")],
)
def test_level_thresholds(score, level):
    assert AiDisclosureAnalyzer._level(score) == level
