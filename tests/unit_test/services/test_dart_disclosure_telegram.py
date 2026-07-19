from unittest.mock import AsyncMock

from repositories.dart_disclosure_repository import StoredDisclosure
from services.dart_disclosure_client import DartDisclosure
from services.dart_disclosure_rule_service import DisclosureImportance
from services.telegram_notifier import TelegramReporter


def _stored(report_name="분기보고서 (2026.03)", score=30):
    disclosure = DartDisclosure(
        corp_class="Y",
        corp_name="A&B <전자>",
        corp_code="00126380",
        stock_code="005930",
        report_name=report_name,
        receipt_no="20260714001234",
        filer_name="A&B",
        receipt_date="20260714",
        remarks="유",
    )
    importance = DisclosureImportance(score, "NORMAL", ["정기보고서"])
    return StoredDisclosure(disclosure, importance)


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
