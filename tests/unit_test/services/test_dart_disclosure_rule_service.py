import pytest

from services.dart_disclosure_client import DartDisclosure
from services.dart_disclosure_rule_service import DartDisclosureRuleService


def _disclosure(report_name: str, remarks: str = "") -> DartDisclosure:
    return DartDisclosure(
        corp_class="Y",
        corp_name="테스트",
        corp_code="00123456",
        stock_code="123456",
        report_name=report_name,
        receipt_no="20260714000001",
        filer_name="테스트",
        receipt_date="20260714",
        remarks=remarks,
    )


@pytest.mark.parametrize(
    ("report_name", "minimum_score", "expected_level"),
    [
        ("상장폐지결정", 100, "CRITICAL"),
        ("횡령ㆍ배임혐의발생", 95, "CRITICAL"),
        ("전환사채권발행결정", 85, "HIGH"),
        ("최대주주변경", 80, "HIGH"),
        ("최대주주등소유주식변동신고서", 70, "HIGH"),
        ("단일판매ㆍ공급계약체결", 70, "HIGH"),
        ("현금ㆍ현물배당결정", 60, "MEDIUM"),
        ("분기보고서 (2026.03)", 30, "NORMAL"),
        ("기업설명회(IR)개최", 20, "LOW"),
    ],
)
def test_rule_engine_assigns_deterministic_scores(report_name, minimum_score, expected_level):
    result = DartDisclosureRuleService().evaluate(_disclosure(report_name))

    assert result.score >= minimum_score
    assert result.level == expected_level
    assert result.reasons


def test_withdrawal_and_correction_are_explained():
    result = DartDisclosureRuleService().evaluate(
        _disclosure("[기재정정] 단일판매ㆍ공급계약체결", remarks="철")
    )

    assert result.score >= 80
    assert any("정정" in reason for reason in result.reasons)
    assert any("철회" in reason for reason in result.reasons)


def test_unknown_report_remains_low_and_explainable():
    result = DartDisclosureRuleService().evaluate(_disclosure("기타경영사항(자율공시)"))

    assert result.score == 10
    assert result.level == "LOW"
    assert result.reasons == ["일반 공시"]
