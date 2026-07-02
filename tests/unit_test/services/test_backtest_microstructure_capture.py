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


def _service(sqs=None, provider=None, db_path="data/program_subscribe/program_trading.db"):
    return BacktestMicrostructureCaptureService(
        stock_query_service=sqs or AsyncMock(),
        program_provider=provider,
        program_db_path=db_path,
    )


@pytest.mark.asyncio
async def test_capture_program_source_none_returns_all_none():
    payload = await _service().capture(
        codes=["000001"],
        date_ymd="20260512",
        include_intraday=False,
        include_execution_strength=False,
        program_source="none",
    )
    assert payload["program_trades"] == {"000001": None}
    assert payload["metadata"]["row_counts"]["program_trades"] == 0


@pytest.mark.asyncio
async def test_capture_unsupported_program_source_raises():
    with pytest.raises(ValueError, match="unsupported program_source"):
        await _service().capture(
            codes=["000001"],
            date_ymd="20260512",
            include_intraday=False,
            include_execution_strength=False,
            program_source="bogus",
        )


@pytest.mark.asyncio
async def test_capture_daily_rest_without_provider_method_returns_none():
    # provider 가 None → getter 가 callable 이 아님
    payload = await _service(provider=None).capture(
        codes=["000001"],
        date_ymd="20260512",
        include_intraday=False,
        include_execution_strength=False,
        program_source="daily_rest",
    )
    assert payload["program_trades"] == {"000001": None}


@pytest.mark.asyncio
async def test_capture_daily_rest_getter_exception_and_missing_qty():
    provider = AsyncMock()
    provider.get_program_trade_by_stock_daily.side_effect = [
        Exception("boom"),
        _response({"unrelated": "1"}),  # qty 추출 불가 → None
    ]
    payload = await _service(provider=provider).capture(
        codes=["A", "B"],
        date_ymd="20260512",
        include_intraday=False,
        include_execution_strength=False,
        program_source="daily_rest",
    )
    assert payload["program_trades"] == {"A": None, "B": None}


@pytest.mark.asyncio
async def test_capture_execution_strength_exception_returns_none():
    sqs = AsyncMock()
    sqs.get_stock_conclusion.side_effect = Exception("conclusion down")
    payload = await _service(sqs=sqs).capture(
        codes=["000001"],
        date_ymd="20260512",
        include_intraday=False,
        include_execution_strength=True,
        program_source="none",
    )
    assert payload["execution_strength"] == {"000001": None}


@pytest.mark.asyncio
async def test_capture_program_db_missing_path_returns_none(tmp_path):
    payload = await _service(db_path=tmp_path / "nope.db").capture(
        codes=["000001"],
        date_ymd="20260512",
        include_intraday=False,
        include_execution_strength=False,
        program_source="program_db",
    )
    assert payload["program_trades"] == {"000001": None}


@pytest.mark.asyncio
async def test_capture_program_db_no_matching_row_returns_none(tmp_path):
    db_path = tmp_path / "program_trading.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE pt_history (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "code TEXT, trade_time TEXT, net_vol INTEGER, created_at REAL)"
        )
        # trade_time 이 조회 윈도우(0900~1000) 밖이라 매칭되지 않음
        conn.execute(
            "INSERT INTO pt_history (code, trade_time, net_vol, created_at) VALUES (?,?,?,?)",
            ("000001", "120000", 999, 1.0),
        )
    payload = await _service(db_path=db_path).capture(
        codes=["000001"],
        date_ymd="20260512",
        start_hhmmss="090000",
        end_hhmmss="100000",
        include_intraday=False,
        include_execution_strength=False,
        program_source="program_db",
    )
    assert payload["program_trades"] == {"000001": None}


# --- 모듈 레벨 헬퍼 단위 테스트 ---
from services.backtest_microstructure_capture import (  # noqa: E402
    _extract_execution_strength,
    _extract_program_net_buy_qty,
    _first,
    _first_output_row,
    _to_float,
    _to_int,
)


def _fail(data=None):
    return ResCommonResponse(rt_cd="1", msg1="FAIL", data=data)


def test_extract_execution_strength_variants():
    assert _extract_execution_strength(_fail()) is None  # 비성공
    assert _extract_execution_strength(_response({"output": []})) is None  # row 없음
    assert _extract_execution_strength(_response({"output": [{"tday_rltv": "10.5"}]})) == 10.5


def test_extract_program_net_buy_qty_variants():
    assert _extract_program_net_buy_qty(_fail()) is None
    assert _extract_program_net_buy_qty(_response({"pgtr_ntby_qty": "500"})) == 500
    assert _extract_program_net_buy_qty(_response({"nothing": "x"})) is None


def test_first_output_row_handles_non_dict_and_scalar_output():
    assert _first_output_row(_response("not-a-dict")) is None
    # output 이 list 가 아닌 dict → 그대로 반환
    assert _first_output_row(_response({"output": {"k": 1}})) == {"k": 1}


def test_first_handles_none_row_and_object_attr():
    from types import SimpleNamespace

    assert _first(None, "a") is None
    assert _first({"a": "", "b": "v"}, "a", "b") == "v"  # 빈 값은 건너뜀
    assert _first(SimpleNamespace(x=7), "x") == 7  # 객체 속성 경로


def test_to_float_and_to_int_edge_cases():
    assert _to_float(None) is None
    assert _to_float("abc") is None
    assert _to_float("3.5") == 3.5
    assert _to_int("") is None
    assert _to_int("xyz") is None
    assert _to_int("42") == 42


def test_write_overlay_files_creates_four_fixture_files(tmp_path):
    import json

    payload = {
        "metadata": {"trade_date": "20260702", "codes": ["000001"]},
        "intraday_minutes": {"000001": [{"stck_cntg_hour": "090000"}]},
        "execution_strength": {"000001": 145.5},
        "program_trades": {"000001": {"program_net_buy_qty": 30000}},
    }

    paths = BacktestMicrostructureCaptureService.write_overlay_files(payload, tmp_path / "out")

    assert json.loads(paths["capture"].read_text(encoding="utf-8")) == payload
    assert json.loads(paths["execution_strength"].read_text(encoding="utf-8")) == {"000001": 145.5}
    assert json.loads(paths["program_trades"].read_text(encoding="utf-8")) == {"000001": 30000}
    assert json.loads(paths["intraday_minutes"].read_text(encoding="utf-8")) == {
        "000001": [{"stck_cntg_hour": "090000"}],
    }
    assert paths["capture"].name == "replay_microstructure_20260702.json"

