import json

from services.strategy_profitability_gate_service import StrategyProfitabilityGateConfig
from services.strategy_validation_readiness_service import build_strategy_validation_readiness


def _record(strategy, *, status="SOLD", net_return=1.0, net_pnl=1000, config_hash=None):
    return {
        "strategy": strategy,
        "status": status,
        "net_return": net_return,
        "net_pnl": net_pnl,
        "config_hash": config_hash,
        "signal_time": "2026-09-30T09:01:00+09:00",
    }


def test_build_readiness_combines_gate_progress_config_cohorts_and_capture_quality(tmp_path):
    replay_dir = tmp_path / "backtest_microstructure"
    replay_dir.mkdir()
    (replay_dir / "replay_quality_20260929.json").write_text(
        json.dumps({"valid_for_backtest": True, "passed": True}),
        encoding="utf-8",
    )
    (replay_dir / "replay_quality_20260930.json").write_text(
        json.dumps({"valid_for_backtest": False, "passed": False}),
        encoding="utf-8",
    )

    records = [
        _record("alpha", config_hash="aaa111"),
        _record("alpha", config_hash="bbb222"),
        _record("beta", status="HOLD", config_hash="ccc333"),
    ]
    result = build_strategy_validation_readiness(
        records,
        StrategyProfitabilityGateConfig(min_trades=3),
        replay_dir=replay_dir,
    )

    assert result["summary"] == {
        "strategy_count": 2,
        "pass_count": 0,
        "fail_count": 0,
        "insufficient_sample_count": 2,
        "valid_capture_days": 1,
        "latest_capture_date": "20260930",
        "latest_valid_capture_date": "20260929",
        "quality_file_error_count": 0,
    }
    alpha = result["strategies"][0]
    assert alpha["strategy"] == "alpha"
    assert alpha["status"] == "insufficient_sample"
    assert alpha["sold_trades"] == 2
    assert alpha["min_trades"] == 3
    assert alpha["progress_pct"] == 66.7
    assert alpha["blocking_reasons"] == ["insufficient_trades"]
    assert alpha["config_cohorts"] == [
        {"config_hash": "aaa111", "record_count": 1, "sold_count": 1},
        {"config_hash": "bbb222", "record_count": 1, "sold_count": 1},
    ]
    assert alpha["mixed_config"] is True

    beta = result["strategies"][1]
    assert beta["status"] == "insufficient_sample"
    assert beta["sold_trades"] == 0
    assert beta["config_cohorts"][0]["config_hash"] == "ccc333"


def test_build_readiness_reports_malformed_quality_files_without_failing(tmp_path):
    (tmp_path / "replay_quality_20260930.json").write_text("not-json", encoding="utf-8")

    result = build_strategy_validation_readiness([], replay_dir=tmp_path)

    assert result["summary"]["strategy_count"] == 0
    assert result["summary"]["quality_file_error_count"] == 1
    assert result["summary"]["valid_capture_days"] == 0

