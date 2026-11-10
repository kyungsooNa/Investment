from unittest.mock import MagicMock


def test_get_backtest_results_summarizes_saved_runs(web_client, mock_web_ctx):
    mock_web_ctx.backtest_journal_repository = MagicMock()
    mock_web_ctx.backtest_journal_repository.list_runs.return_value = [
        {
            "run_id": "run-1",
            "strategy": "VBO",
            "target_date": "20260101_20261231",
            "record_count": 1,
            "saved_at": "2026-12-31T08:00:00+00:00",
            "metadata": {
                "dates": ["20260101", "20261231"],
                "date_count": 2,
                "initial_cash": 1_000_000,
                "portfolio": {
                    "initial_cash": 1_000_000,
                    "realized_net_pnl": 10_000,
                    "positions": {},
                },
            },
        }
    ]
    mock_web_ctx.backtest_journal_repository.load_records.return_value = [
        {"status": "SOLD", "net_pnl": 10_000, "net_return": 3.0}
    ]

    response = web_client.get("/api/backtest/results?limit=20")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["results"][0]["strategy"] == "VBO"
    assert body["results"][0]["realized_return_pct"] == 1.0
    assert body["results"][0]["validity"]["status"] == "INSUFFICIENT_SAMPLE"
    mock_web_ctx.backtest_journal_repository.list_runs.assert_called_once_with(limit=20)

