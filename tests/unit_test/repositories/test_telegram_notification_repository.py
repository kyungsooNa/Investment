from datetime import datetime

from repositories.telegram_notification_repository import TelegramNotificationRepository


def test_records_and_returns_only_requested_day_latest_first(tmp_path):
    repository = TelegramNotificationRepository(tmp_path / "telegram_notifications.db")

    repository.record(
        sent_at="2026-07-12T23:59:59+09:00",
        source="report",
        title="어제 리포트",
        message="어제 내용",
    )
    repository.record(
        sent_at="2026-07-13T09:00:00+09:00",
        source="strategy",
        title="매수 시그널",
        message="삼성전자 매수",
        level="critical",
    )
    repository.record(
        sent_at="2026-07-13T15:40:00+09:00",
        source="report",
        title="장 마감 리포트",
        message="랭킹 결과",
    )

    items = repository.get_by_date(datetime(2026, 7, 13).date())

    assert [item["title"] for item in items] == ["장 마감 리포트", "매수 시그널"]
    assert items[1]["category"] == "TELEGRAM"
    assert items[1]["level"] == "critical"
    assert items[1]["metadata"] == {"source": "strategy"}


def test_get_by_date_applies_count_limit(tmp_path):
    repository = TelegramNotificationRepository(tmp_path / "telegram_notifications.db")
    for minute in range(3):
        repository.record(
            sent_at=f"2026-07-13T09:0{minute}:00+09:00",
            source="report",
            title=f"리포트 {minute}",
            message="내용",
        )

    items = repository.get_by_date(datetime(2026, 7, 13).date(), count=2)

    assert [item["title"] for item in items] == ["리포트 2", "리포트 1"]


def test_list_reports_and_get_report_for_archive(tmp_path):
    repository = TelegramNotificationRepository(tmp_path / "telegram_notifications.db")
    repository.record(
        sent_at="2026-07-16T16:34:39+09:00",
        source="report",
        title="장 마감 랭킹",
        message="상승률 및 수급 랭킹 내용",
    )

    reports = repository.list_reports(limit=20)

    assert reports == [{
        "id": "telegram-1",
        "report_date": "20260716",
        "created_at": "2026-07-16T16:34:39+09:00",
        "kind": "telegram",
        "title": "장 마감 랭킹",
        "source": "report",
        "summary": "상승률 및 수급 랭킹 내용",
    }]
    assert repository.get_report("telegram-1") == {
        **reports[0],
        "content": "상승률 및 수급 랭킹 내용",
    }
    assert repository.get_report("telegram-invalid") is None
