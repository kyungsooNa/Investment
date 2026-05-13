import json

from scripts.capture_backtest_microstructure import _write_output_files


def test_write_output_files_creates_overlay_and_intraday_files(tmp_path):
    payload = {
        "metadata": {"trade_date": "20260512", "codes": ["000001"]},
        "intraday_minutes": {"000001": [{"stck_cntg_hour": "090000"}]},
        "execution_strength": {"000001": 145.5},
        "program_trades": {"000001": {"program_net_buy_qty": 30000}},
    }

    paths = _write_output_files(payload, tmp_path)

    assert json.loads(paths["capture"].read_text(encoding="utf-8")) == payload
    assert json.loads(paths["execution_strength"].read_text(encoding="utf-8")) == {
        "000001": 145.5,
    }
    assert json.loads(paths["program_trades"].read_text(encoding="utf-8")) == {
        "000001": 30000,
    }
    assert json.loads(paths["intraday_minutes"].read_text(encoding="utf-8")) == {
        "000001": [{"stck_cntg_hour": "090000"}],
    }

