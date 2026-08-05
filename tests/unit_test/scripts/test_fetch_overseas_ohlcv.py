"""해외 일봉 수집 CLI 테스트 (스윕 입력 생성용).

`scripts/run_overseas_vbo_sweep.py` 가 읽는 per-code JSONL 을 만든다.
읽기 전용 조회 API만 사용하며 주문 경로는 조립하지 않는다.
"""
import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from scripts.fetch_overseas_ohlcv import build_parser, fetch_to_dir, resolve_symbols
from common.types import ErrorCode, ResCommonResponse
from common.overseas_types import OverseasExchange


def _bar(d):
    return {"date": d, "open": 100, "high": 110, "low": 99, "close": 105, "volume": 1000}


def _ok(bars):
    return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="ok", data=bars)


def _fail():
    return ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="err", data=None)


@pytest.mark.asyncio
async def test_fetch_writes_one_jsonl_per_symbol(tmp_path):
    sqs = MagicMock()
    sqs.get_ohlcv_range = AsyncMock(return_value=_ok([_bar("20260511"), _bar("20260512")]))

    result = await fetch_to_dir(
        stock_query_service=sqs, symbols=["AAA", "BBB"],
        exchange=OverseasExchange.NASD, start_date="20260501", end_date="20260512",
        out_dir=tmp_path / "ohlcv", logger=MagicMock(),
    )

    assert result["symbols"] == 2
    assert result["bars"] == 4
    lines = (tmp_path / "ohlcv" / "AAA.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["date"] == "20260511"


@pytest.mark.asyncio
async def test_fetch_reports_actual_covered_range(tmp_path):
    """KIS 해외 일봉은 start_date 를 무시하고 마지막 N봉만 준다 — 실제 구간을 보고한다."""
    sqs = MagicMock()
    sqs.get_ohlcv_range = AsyncMock(return_value=_ok([_bar("20260312"), _bar("20260804")]))

    result = await fetch_to_dir(
        stock_query_service=sqs, symbols=["AAA"],
        exchange=OverseasExchange.NASD, start_date="20260101", end_date="20260804",
        out_dir=tmp_path / "ohlcv", logger=MagicMock(),
    )

    assert result["first_date"] == "20260312"
    assert result["last_date"] == "20260804"


@pytest.mark.asyncio
async def test_fetch_skips_failed_symbol_without_aborting(tmp_path):
    sqs = MagicMock()
    sqs.get_ohlcv_range = AsyncMock(side_effect=[_fail(), _ok([_bar("20260511")])])

    result = await fetch_to_dir(
        stock_query_service=sqs, symbols=["BAD", "GOOD"],
        exchange=OverseasExchange.NASD, start_date="20260501", end_date="20260512",
        out_dir=tmp_path / "ohlcv", logger=MagicMock(),
    )

    assert result["symbols"] == 1
    assert result["failed"] == ["BAD"]
    assert not (tmp_path / "ohlcv" / "BAD.jsonl").exists()


@pytest.mark.asyncio
async def test_fetch_absorbs_exception_per_symbol(tmp_path):
    sqs = MagicMock()
    sqs.get_ohlcv_range = AsyncMock(side_effect=RuntimeError("boom"))

    result = await fetch_to_dir(
        stock_query_service=sqs, symbols=["AAA"],
        exchange=OverseasExchange.NASD, start_date="20260501", end_date="20260512",
        out_dir=tmp_path / "ohlcv", logger=MagicMock(),
    )

    assert result["symbols"] == 0
    assert result["failed"] == ["AAA"]


@pytest.mark.asyncio
async def test_resolve_symbols_prefers_explicit_list():
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(return_value=[{"code": "ZZZ"}])

    symbols = await resolve_symbols(
        candidate_service=candidate_service, symbols_arg="aaa, bbb",
        exchange=OverseasExchange.NASD, top_n=10,
    )

    assert symbols == ["AAA", "BBB"]
    candidate_service.get_candidates.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_symbols_falls_back_to_candidates():
    candidate_service = MagicMock()
    candidate_service.get_candidates = AsyncMock(
        return_value=[{"code": "AAA"}, {"code": "BBB"}, {"code": ""}]
    )

    symbols = await resolve_symbols(
        candidate_service=candidate_service, symbols_arg=None,
        exchange=OverseasExchange.NASD, top_n=10,
    )

    assert symbols == ["AAA", "BBB"]


def test_parser_defaults():
    args = build_parser().parse_args(["--start-date", "20260101", "--end-date", "20260131"])

    assert args.out_dir == "data/overseas_ohlcv"
    assert args.exchange == "NASD"
    assert args.top_n == 50
