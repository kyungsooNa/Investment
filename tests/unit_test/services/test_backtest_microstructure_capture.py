import sqlite3
from unittest.mock import AsyncMock

import pytest

from common.types import ErrorCode, ResCommonResponse
from services.backtest_microstructure_capture import BacktestMicrostructureCaptureService


def _response(data):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="OK", data=data)


@pytest.mark.asyncio
async def test_capture_collects_intraday_execution_strength_and_daily_program_overlay():
    sqs = AsyncMock()
    sqs.get_day_intraday_minutes_list.side_effect = [
        [{"stck_bsop_date": "20260512", "stck_cntg_hour": "090000", "stck_prpr": "10000"}],
        [{"stck_bsop_date": "20260512", "stck_cntg_hour": "090100", "stck_prpr": "20000"}],
    ]
    sqs.get_stock_conclusion.side_effect = [
        _response({"output": [{"tday_rltv": "145.5"}]}),
        _response({"output": [{"cgld": "132.0"}]}),
    ]
    program_provider = AsyncMock()
    program_provider.get_program_trade_by_stock_daily.side_effect = [
        _response({"whol_smtn_ntby_qty": "30000"}),
        _response({"program_net_buy_qty": "-1200"}),
    ]

    service = BacktestMicrostructureCaptureService(
        stock_query_service=sqs,
        program_provider=program_provider,
    )

    payload = await service.capture(
        codes=["000001", "000002"],
        date_ymd="20260512",
        start_hhmmss="090000",
        end_hhmmss="153000",
        program_source="daily_rest",
    )

    assert payload["metadata"]["codes"] == ["000001", "000002"]
    assert payload["metadata"]["row_counts"] == {
        "intraday_minutes": 2,
        "execution_strength": 2,
        "program_trades": 2,
    }
    assert payload["intraday_minutes"]["000001"][0]["stck_prpr"] == "10000"
    assert payload["execution_strength"] == {"000001": 145.5, "000002": 132.0}
    assert payload["program_trades"] == {
        "000001": {"program_net_buy_qty": 30000},
        "000002": {"program_net_buy_qty": -1200},
    }


@pytest.mark.asyncio
async def test_capture_can_read_latest_program_overlay_from_program_db(tmp_path):
    db_path = tmp_path / "program_trading.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE pt_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                trade_time TEXT,
                net_vol INTEGER,
                created_at REAL NOT NULL
            )
            """
        )
        conn.executemany(
            "INSERT INTO pt_history (code, trade_time, net_vol, created_at) VALUES (?, ?, ?, ?)",
            [
                ("000001", "100000", 100, 1.0),
                ("000001", "101500", 350, 2.0),
                ("000002", "102000", -50, 3.0),
            ],
        )

    service = BacktestMicrostructureCaptureService(
        stock_query_service=AsyncMock(),
        program_db_path=db_path,
    )

    payload = await service.capture(
        codes=["000001", "000002"],
        date_ymd="20260512",
        include_intraday=False,
        include_execution_strength=False,
        program_source="program_db",
    )

    assert payload["program_trades"] == {
        "000001": {"program_net_buy_qty": 350},
        "000002": {"program_net_buy_qty": -50},
    }

