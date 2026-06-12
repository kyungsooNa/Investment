import json

from scripts import report_survivorship_bias_impact as mod


def _universe_payload():
    return {
        "survivorship_gap": {
            "delisted_only": [
                {"symbol": "230980", "name": "비유테크놀러지"},
            ],
            "current_only": [
                {"symbol": "123456", "name": "미래상장"},
            ],
        },
        "records": [
            {"symbol": "005930", "name": "삼성전자", "source": "current"},
            {"symbol": "230980", "name": "비유테크놀러지", "source": "delisted"},
        ],
    }


def _backtest_payload():
    return {
        "journal_records": [
            {"code": "005930", "status": "SOLD", "net_pnl": 10000},
            {"code": "230980", "status": "SOLD", "net_pnl": -50000},
            {"code": "123456", "status": "SOLD", "net_pnl": 30000},
            {"code": "230980", "status": "HOLD", "net_pnl": 999999},
        ]
    }


def test_compute_impact_summarizes_delisted_and_current_only_trade_pnl():
    summary = mod.compute_impact_summary(_universe_payload(), _backtest_payload())

    assert summary["totals"]["sold_trade_count"] == 3
    assert summary["totals"]["sold_net_pnl"] == -10000
    assert summary["delisted_only"]["trade_count"] == 1
    assert summary["delisted_only"]["net_pnl"] == -50000
    assert summary["current_only"]["trade_count"] == 1
    assert summary["current_only"]["net_pnl"] == 30000
    assert summary["delisted_only"]["by_code"][0]["code"] == "230980"


def test_main_writes_json_and_markdown(tmp_path, capsys):
    universe_path = tmp_path / "universe.json"
    backtest_path = tmp_path / "backtest.json"
    json_out = tmp_path / "impact.json"
    md_out = tmp_path / "impact.md"
    universe_path.write_text(json.dumps(_universe_payload(), ensure_ascii=False), encoding="utf-8")
    backtest_path.write_text(json.dumps(_backtest_payload(), ensure_ascii=False), encoding="utf-8")

    exit_code = mod.main([
        "--universe-json", str(universe_path),
        "--backtest-json", str(backtest_path),
        "--output-json", str(json_out),
        "--output-markdown", str(md_out),
    ])

    assert exit_code == 0
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["delisted_only"]["net_pnl"] == -50000
    assert "Survivorship Bias Impact" in md_out.read_text(encoding="utf-8")
    assert str(json_out) in capsys.readouterr().out
