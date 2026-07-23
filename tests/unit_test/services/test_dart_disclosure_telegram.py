from unittest.mock import AsyncMock

from repositories.dart_disclosure_repository import StoredDisclosure
from services.dart_disclosure_client import DartDisclosure
from services.dart_disclosure_rule_service import DisclosureImportance
from services.telegram_notifier import TelegramReporter


def _stored(
    report_name="분기보고서 (2026.03)",
    score=30,
    *,
    receipt_no="20260714001234",
    event_key="",
    summary="",
):
    disclosure = DartDisclosure(
        corp_class="Y",
        corp_name="A&B <전자>",
        corp_code="00126380",
        stock_code="005930",
        report_name=report_name,
        receipt_no=receipt_no,
        filer_name="A&B",
        receipt_date="20260714",
        remarks="유",
    )
    importance = DisclosureImportance(score, "NORMAL", ["정기보고서"])
    return StoredDisclosure(disclosure, importance, event_key, summary)


async def test_send_disclosure_alert_escapes_html_and_includes_reason_and_link():
    reporter = TelegramReporter("token", "chat")
    reporter._send_message = AsyncMock(return_value=True)
    stored = _stored("전환사채권발행결정", 85)

    sent = await reporter.send_disclosure_alert(stored.disclosure, stored.importance)

    assert sent is True
    message = reporter._send_message.await_args.args[0]
    assert "A&amp;B &lt;전자&gt;" in message
    assert "정기보고서" in message
    assert "rcpNo=20260714001234" in message


async def test_send_disclosure_alert_includes_ai_summary_block_when_provided():
    reporter = TelegramReporter("token", "chat")
    reporter._send_message = AsyncMock(return_value=True)
    stored = _stored("전환사채권발행결정", 85)

    sent = await reporter.send_disclosure_alert(
        stored.disclosure, stored.importance, ai_summary="전환사채 발행 <주의>"
    )

    assert sent is True
    message = reporter._send_message.await_args.args[0]
    assert "AI 요약" in message
    assert "전환사채 발행 &lt;주의&gt;" in message


async def test_send_disclosure_alert_emphasizes_key_labels():
    reporter = TelegramReporter("token", "chat")
    reporter._send_message = AsyncMock(return_value=True)
    stored = _stored("주식소각결정", 80)

    await reporter.send_disclosure_alert(
        stored.disclosure, stored.importance, ai_summary="자기주식 소각 결정"
    )

    message = reporter._send_message.await_args.args[0]
    assert "<b>공시:</b> 주식소각결정" in message
    assert "<b>중요도:</b> NORMAL (80점)" in message
    assert "🤖 <b>AI 요약</b>" in message
    assert "<b>판정 근거</b>" in message
    assert "<b>접수일:</b> 20260714" in message


async def test_send_disclosure_alert_truncates_oversized_ai_summary():
    reporter = TelegramReporter("token", "chat")
    reporter._send_message = AsyncMock(return_value=True)
    stored = _stored("전환사채권발행결정", 85)

    await reporter.send_disclosure_alert(
        stored.disclosure, stored.importance, ai_summary="가" * 5000
    )

    message = reporter._send_message.await_args.args[0]
    assert len(message) <= 4000
    assert "가" * 5000 not in message
    assert "…" in message


async def test_send_disclosure_alert_omits_ai_block_when_absent():
    reporter = TelegramReporter("token", "chat")
    reporter._send_message = AsyncMock(return_value=True)
    stored = _stored("전환사채권발행결정", 85)

    await reporter.send_disclosure_alert(stored.disclosure, stored.importance)

    message = reporter._send_message.await_args.args[0]
    assert "AI 요약" not in message


async def test_send_disclosure_digest_formats_multiple_rows():
    reporter = TelegramReporter("token", "chat")
    reporter._send_message = AsyncMock(return_value=True)
    rows = [_stored(), _stored("기업설명회(IR)개최", 20)]

    sent = await reporter.send_disclosure_digest(rows, "20260714")

    assert sent is True
    full = "".join(call.args[0] for call in reporter._send_message.await_args_list)
    assert "관심종목 공시 요약" in full
    assert "분기보고서" in full
    assert "기업설명회" in full


async def test_send_disclosure_digest_includes_one_line_summary_when_available():
    reporter = TelegramReporter("token", "chat")
    reporter._send_message = AsyncMock(return_value=True)
    rows = [
        _stored(
            "풍문또는보도에대한해명",
            10,
            summary="언론의 특정 공급계약 추진 보도는 사실이 아니라고 해명했습니다.",
        )
    ]

    sent = await reporter.send_disclosure_digest(rows, "20260722")

    assert sent is True
    message = reporter._send_message.await_args.args[0]
    assert "• 내용: 언론의 특정 공급계약 추진 보도는 사실이 아니라고 해명했습니다." in message


async def test_send_disclosure_digest_uses_group_summary_and_escapes_html():
    reporter = TelegramReporter("token", "chat")
    reporter._send_message = AsyncMock(return_value=True)
    rows = [
        _stored(
            "일괄신고추가서류(파생결합증권-주가연계증권)",
            10,
            receipt_no="20260720000120",
            event_key="ELS|37980,37981|20000000000",
            summary="ELS <37980회> 발행 조건을 추가 제출했습니다.",
        ),
        _stored(
            "투자설명서(일괄신고)",
            10,
            receipt_no="20260720000134",
            event_key="ELS|37980,37981|20000000000",
        ),
    ]

    await reporter.send_disclosure_digest(rows, "20260720")

    full = "".join(call.args[0] for call in reporter._send_message.await_args_list)
    assert "• 내용: ELS &lt;37980회&gt; 발행 조건을 추가 제출했습니다." in full


async def test_send_disclosure_digest_groups_only_same_non_empty_event_key():
    reporter = TelegramReporter("token", "chat")
    reporter._send_message = AsyncMock(return_value=True)
    rows = [
        _stored(
            "일괄신고추가서류(파생결합증권-주가연계증권)",
            10,
            receipt_no="20260720000120",
            event_key="ELS|37980,37981|20000000000",
        ),
        _stored(
            "투자설명서(일괄신고)",
            10,
            receipt_no="20260720000134",
            event_key="ELS|37980,37981|20000000000",
        ),
        _stored(
            "증권신고서(채무증권)",
            10,
            receipt_no="20260720000190",
            event_key="CP|2-1,2-2,2-3|1191161446589",
        ),
    ]

    await reporter.send_disclosure_digest(rows, "20260720")

    full = "".join(call.args[0] for call in reporter._send_message.await_args_list)
    assert "관련 공시 2건" in full
    assert full.count("<b>A&amp;B &lt;전자&gt; (005930)</b>") == 2
    assert "원문 1" in full
    assert "원문 2" in full
    assert "증권신고서(채무증권)" in full


async def test_send_disclosure_digest_collapses_mass_same_company_report_type():
    reporter = TelegramReporter("token", "chat")
    reporter._send_message = AsyncMock(return_value=True)
    rows = [
        _stored(
            "임원ㆍ주요주주특정증권등소유상황보고서",
            50,
            receipt_no=f"20260721{index:06d}",
            event_key=f"임원지분변동|제출자{index}",
        )
        for index in range(744)
    ]

    sent = await reporter.send_disclosure_digest(rows, "20260721")

    assert sent is True
    reporter._send_message.assert_awaited_once()
    message = reporter._send_message.await_args.args[0]
    assert "관련 공시 744건" in message
    assert message.count("<b>A&amp;B &lt;전자&gt; (005930)</b>") == 1
    assert "외 741건" in message
    assert len(message.encode("utf-8")) <= 4000
