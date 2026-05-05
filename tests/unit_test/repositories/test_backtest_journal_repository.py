from repositories.backtest_journal_repository import BacktestJournalRepository


def _record(**overrides):
    record = {
        "source": "backtest",
        "strategy": "VolumeBreakout",
        "code": "005930",
        "signal_time": "2026-05-05 09:01:00",
        "net_return": 1.2,
    }
    record.update(overrides)
    return record


def test_save_run_and_load_records(tmp_path):
    repo = BacktestJournalRepository(base_dir=tmp_path)

    saved = repo.save_run(
        [_record()],
        run_id="VolumeBreakout_005930_20260505",
        strategy="VolumeBreakout",
        target_date="20260505",
        metadata={"stock_code": "005930"},
    )

    assert saved["run_id"] == "VolumeBreakout_005930_20260505"
    assert saved["record_count"] == 1
    assert saved["strategy"] == "VolumeBreakout"
    assert saved["target_date"] == "20260505"
    assert repo.load_records("VolumeBreakout_005930_20260505") == [_record()]


def test_list_runs_returns_latest_first(tmp_path):
    repo = BacktestJournalRepository(base_dir=tmp_path)

    repo.save_run([_record(code="005930")], run_id="old", strategy="A", target_date="20260504")
    repo.save_run([_record(code="000660")], run_id="new", strategy="B", target_date="20260505")

    runs = repo.list_runs()

    assert [run["run_id"] for run in runs] == ["new", "old"]
    assert runs[0]["record_count"] == 1
    assert runs[0]["strategy"] == "B"


def test_load_records_missing_run_returns_empty_list(tmp_path):
    repo = BacktestJournalRepository(base_dir=tmp_path)

    assert repo.load_records("missing") == []


def test_load_records_for_date_combines_matching_runs(tmp_path):
    repo = BacktestJournalRepository(base_dir=tmp_path)
    repo.save_run([_record(code="005930")], run_id="a", strategy="VolumeBreakout", target_date="20260505")
    repo.save_run([_record(code="000660")], run_id="b", strategy="Momentum", target_date="20260505")
    repo.save_run([_record(code="035420")], run_id="c", strategy="VolumeBreakout", target_date="20260504")

    records = repo.load_records_for_date("20260505")

    assert [record["code"] for record in records] == ["000660", "005930"]


def test_load_records_for_date_can_filter_strategy(tmp_path):
    repo = BacktestJournalRepository(base_dir=tmp_path)
    repo.save_run([_record(code="005930")], run_id="a", strategy="VolumeBreakout", target_date="20260505")
    repo.save_run([_record(code="000660")], run_id="b", strategy="Momentum", target_date="20260505")

    records = repo.load_records_for_date("20260505", strategy="VolumeBreakout")

    assert [record["code"] for record in records] == ["005930"]


def test_run_id_is_sanitized(tmp_path):
    repo = BacktestJournalRepository(base_dir=tmp_path)

    saved = repo.save_run([_record()], run_id="../bad id", strategy="A", target_date="20260505")

    assert saved["run_id"] == "bad_id"
    assert (tmp_path / "bad_id.json").exists()
