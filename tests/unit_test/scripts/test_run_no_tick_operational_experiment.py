"""No-tick 운영 실험 runner 테스트."""
from __future__ import annotations

import json

import pytest

from scripts.run_no_tick_operational_experiment import (
    build_dry_run_result,
    format_markdown_result,
    main,
    run_experiment,
    select_experiment,
)


def _plan() -> dict:
    return {
        "summary": {"verdict": "a1_kis_no_send"},
        "experiments": [
            {
                "id": "A_common_stock_only",
                "goal": "common only",
                "codes": ["OK001", "NO001"],
                "expected_signal": "OK should tick and NO may stay silent.",
                "rows": [
                    {"code": "OK001", "name": "정상", "instrument_type": "common_or_other"},
                    {"code": "NO001", "name": "무틱", "instrument_type": "common_or_other"},
                ],
            },
            {
                "id": "B_non_common_only",
                "goal": "non common only",
                "codes": ["ETF01"],
                "expected_signal": "ETF behavior.",
                "rows": [{"code": "ETF01", "name": "ETF", "instrument_type": "ETF"}],
            },
        ],
    }


class FakeStreamingService:
    def __init__(self):
        self.calls = []

    async def connect_websocket(self, callback=None):
        self.calls.append(("connect", callback is not None))
        return True

    async def subscribe_unified_price(self, code):
        self.calls.append(("subscribe", code))
        return True

    async def wait_unified_price_ack(self, code, timeout=None):
        self.calls.append(("ack", code, timeout))
        return True

    async def unsubscribe_unified_price(self, code):
        self.calls.append(("unsubscribe", code))
        return True

    async def disconnect_websocket(self):
        self.calls.append(("disconnect",))
        return True

    def dispatch_realtime_message(self, message):
        self.calls.append(("dispatch", message))


class FakePriceStreamService:
    def __init__(self):
        self.stats = {
            "OK001": {"received": 0, "quality_reject": 0, "dispatched": 0, "malformed": 0},
            "NO001": {"received": 0, "quality_reject": 0, "dispatched": 0, "malformed": 0},
        }

    def tick_ingest_stats_snapshot(self, codes):
        return {code: dict(self.stats.get(code, {})) for code in codes}


def test_select_experiment_by_id():
    experiment = select_experiment(_plan(), "B_non_common_only")

    assert experiment["codes"] == ["ETF01"]


def test_select_experiment_raises_for_unknown_id():
    with pytest.raises(ValueError, match="UNKNOWN"):
        select_experiment(_plan(), "UNKNOWN")


@pytest.mark.asyncio
async def test_run_experiment_subscribes_waits_and_reports_deltas():
    streaming = FakeStreamingService()
    price_stream = FakePriceStreamService()

    async def sleeper(_duration):
        price_stream.stats["OK001"]["received"] = 3
        price_stream.stats["OK001"]["dispatched"] = 3

    result = await run_experiment(
        experiment=select_experiment(_plan(), "A_common_stock_only"),
        streaming_service=streaming,
        price_stream_service=price_stream,
        duration_sec=7,
        ack_timeout_sec=1.5,
        sleeper=sleeper,
    )

    assert ("connect", True) in streaming.calls
    assert ("subscribe", "OK001") in streaming.calls
    assert ("ack", "OK001", 1.5) in streaming.calls
    assert ("unsubscribe", "NO001") in streaming.calls
    assert ("disconnect",) in streaming.calls
    assert result["summary"]["received_codes"] == 1
    assert result["summary"]["no_tick_codes"] == 1
    assert result["per_code"]["OK001"]["received_delta"] == 3
    assert result["per_code"]["NO001"]["received_delta"] == 0


def test_dry_run_result_and_markdown_include_selected_cohort():
    result = build_dry_run_result(
        experiment=select_experiment(_plan(), "A_common_stock_only"),
        duration_sec=30,
    )
    md = format_markdown_result(result)

    assert result["status"] == "dry_run"
    assert result["summary"]["total_codes"] == 2
    assert result["summary"]["no_tick_codes"] == 0
    assert "A_common_stock_only" in md
    assert "OK001" in md


def test_main_dry_run_writes_json_and_markdown(tmp_path):
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan(), ensure_ascii=False), encoding="utf-8")
    out_json = tmp_path / "result.json"
    out_md = tmp_path / "result.md"

    rc = main([
        "--plan", str(plan_path),
        "--experiment-id", "A_common_stock_only",
        "--duration-sec", "5",
        "--output-json", str(out_json),
        "--output-markdown", str(out_md),
    ])

    assert rc == 0
    assert json.loads(out_json.read_text(encoding="utf-8"))["status"] == "dry_run"
    assert "A_common_stock_only" in out_md.read_text(encoding="utf-8")
