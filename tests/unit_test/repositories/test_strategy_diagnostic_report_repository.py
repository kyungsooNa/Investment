from repositories.strategy_diagnostic_report_repository import (
    StrategyDiagnosticReportRepository,
)


def test_save_list_and_get_report(tmp_path):
    repository = StrategyDiagnosticReportRepository(tmp_path)

    saved = repository.save("2026-07-14", "<b>상세 리포트</b>")
    reports = repository.list_reports()

    assert saved["report_date"] == "20260714"
    assert reports[0]["id"] == saved["id"]
    assert repository.get_report(saved["id"]) == {
        **saved,
        "content": "<b>상세 리포트</b>",
    }


def test_list_reports_is_latest_first_and_limited(tmp_path, monkeypatch):
    repository = StrategyDiagnosticReportRepository(tmp_path)
    stamps = iter(["090000_000001", "160000_000002"])
    monkeypatch.setattr(repository, "_current_stamp", lambda: next(stamps))

    first = repository.save("20260713", "first")
    second = repository.save("20260714", "second")

    reports = repository.list_reports(limit=1)

    assert [item["id"] for item in reports] == [second["id"]]
    assert first["id"] != second["id"]


def test_get_report_rejects_path_traversal_and_unknown_file(tmp_path):
    repository = StrategyDiagnosticReportRepository(tmp_path)

    assert repository.get_report("../config.yaml") is None
    assert repository.get_report("missing.html") is None
