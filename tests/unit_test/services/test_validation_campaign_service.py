from services.validation_campaign_service import ValidationCampaignService


def _quality_report(valid_dates):
    return {
        "by_date": {
            date: {"quality_gate_passed": True, "issues": []}
            for date in valid_dates
        },
        "totals": {"capture_files": len(valid_dates)},
    }


def test_plan_selects_valid_dates_and_defers_walk_forward_when_sample_is_short(tmp_path):
    dates = [f"202609{day:02d}" for day in range(1, 14)]
    service = ValidationCampaignService(
        replay_dir=tmp_path,
        strategies=("oneil_pocket_pivot", "larry_williams_vbo"),
        quality_report_provider=lambda _: _quality_report(dates),
    )

    plan = service.build_plan()

    assert plan["valid_dates"] == dates
    assert plan["strategy_count"] == 2
    assert plan["walk_forward"] == {
        "ready": False,
        "required_dates": 30,
        "available_dates": 13,
        "status": "waiting_for_more_valid_dates",
    }


def test_campaign_aggregates_results_and_continues_after_strategy_failure(tmp_path):
    dates = ["20260928", "20260929", "20260930"]

    def runner(strategy, selected_dates, replay_dir):
        assert selected_dates == dates
        if strategy == "broken":
            raise RuntimeError("boom")
        return {
            "execution_reports": [
                {"side": "BUY", "filled_qty": 2},
                {"side": "SELL", "filled_qty": 2},
            ],
            "journal_records": [{"status": "REJECTED", "reason": "market_timing_off"}],
            "portfolio": {"realized_net_pnl": 1234, "positions": {}},
        }

    service = ValidationCampaignService(
        replay_dir=tmp_path,
        strategies=("ok", "broken"),
        quality_report_provider=lambda _: _quality_report(dates),
        strategy_runner=runner,
    )

    result = service.run()

    assert result["summary"] == {"completed": 1, "failed": 1, "zero_trade": 0}
    assert result["strategies"][0]["buy_fills"] == 1
    assert result["strategies"][0]["realized_net_pnl"] == 1234
    assert result["strategies"][0]["rejection_reasons"] == {"market_timing_off": 1}
    assert result["strategies"][1]["status"] == "failed"


def test_campaign_labels_zero_trade_without_claiming_unknown_root_cause(tmp_path):
    service = ValidationCampaignService(
        replay_dir=tmp_path,
        strategies=("empty",),
        quality_report_provider=lambda _: _quality_report(["20260929"]),
        strategy_runner=lambda *_: {
            "execution_reports": [],
            "journal_records": [],
            "portfolio": {},
        },
    )

    result = service.run()

    strategy = result["strategies"][0]
    assert strategy["zero_trade"] is True
    assert strategy["zero_trade_reason"] == "no_signal_or_candidate_observed"
