"""지수 투자자 수급 시간차트용 표본 수집/조회 서비스 단위 테스트."""
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode
from services.index_flow_history_service import IndexFlowHistoryService


def _clock(now):
    clock = MagicMock()
    clock.get_current_kst_time.return_value = now
    return clock


def _query(netbuy_by_code):
    query = MagicMock()
    query.get_index_investor_netbuy = AsyncMock(side_effect=lambda code: netbuy_by_code.get(code))
    return query


_KOSPI = {"individual": 598, "foreign": -3888, "institution": 3504}
_KOSDAQ = {"individual": 219, "foreign": 60, "institution": -253}


@pytest.mark.asyncio
async def test_record_sample_appends_time_point_per_index_and_persists(tmp_path):
    service = IndexFlowHistoryService(
        stock_query_service=_query({"0001": _KOSPI, "1001": _KOSDAQ}),
        market_clock=_clock(datetime(2026, 10, 23, 9, 35, 12)),
        data_dir=tmp_path,
        logger=MagicMock(),
    )

    await service.record_sample()

    result = service.get_history("0001")
    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data == {
        "code": "0001",
        "date": "20261023",
        "points": [{"time": "0935", **_KOSPI}],
    }
    saved = json.loads((tmp_path / "20261023.json").read_text(encoding="utf-8"))
    assert saved["1001"] == [{"time": "0935", **_KOSDAQ}]


@pytest.mark.asyncio
async def test_same_minute_sample_replaces_previous_point(tmp_path):
    clock = _clock(datetime(2026, 10, 23, 9, 35))
    query = _query({"0001": _KOSPI})
    service = IndexFlowHistoryService(
        stock_query_service=query, market_clock=clock, data_dir=tmp_path, logger=MagicMock(),
    )

    await service.record_sample()
    updated = {"individual": 1, "foreign": 2, "institution": 3}
    query.get_index_investor_netbuy.side_effect = lambda code: updated if code == "0001" else None
    await service.record_sample()

    assert service.get_history("0001").data["points"] == [{"time": "0935", **updated}]


@pytest.mark.asyncio
async def test_failed_fetch_skips_index_without_breaking_others(tmp_path):
    service = IndexFlowHistoryService(
        stock_query_service=_query({"1001": _KOSDAQ}),
        market_clock=_clock(datetime(2026, 10, 23, 10, 0)),
        data_dir=tmp_path,
        logger=MagicMock(),
    )

    await service.record_sample()

    assert service.get_history("0001").data["points"] == []
    assert service.get_history("1001").data["points"] == [{"time": "1000", **_KOSDAQ}]


@pytest.mark.asyncio
async def test_samples_survive_restart(tmp_path):
    clock = _clock(datetime(2026, 10, 23, 9, 35))
    first = IndexFlowHistoryService(
        stock_query_service=_query({"0001": _KOSPI}), market_clock=clock,
        data_dir=tmp_path, logger=MagicMock(),
    )
    await first.record_sample()

    clock.get_current_kst_time.return_value = datetime(2026, 10, 23, 9, 40)
    restarted = IndexFlowHistoryService(
        stock_query_service=_query({"0001": _KOSPI}), market_clock=clock,
        data_dir=tmp_path, logger=MagicMock(),
    )
    await restarted.record_sample()

    times = [p["time"] for p in restarted.get_history("0001").data["points"]]
    assert times == ["0935", "0940"]


def test_history_falls_back_to_latest_saved_day_before_today(tmp_path):
    (tmp_path / "20261022.json").write_text(
        json.dumps({"0001": [{"time": "1530", **_KOSPI}]}), encoding="utf-8",
    )
    (tmp_path / "20261021.json").write_text(json.dumps({"0001": []}), encoding="utf-8")
    service = IndexFlowHistoryService(
        stock_query_service=_query({}),
        market_clock=_clock(datetime(2026, 10, 24, 11, 0)),  # 토요일
        data_dir=tmp_path,
        logger=MagicMock(),
    )

    result = service.get_history("0001")

    assert result.data["date"] == "20261022"
    assert result.data["points"] == [{"time": "1530", **_KOSPI}]


def test_history_rejects_unknown_index(tmp_path):
    service = IndexFlowHistoryService(
        stock_query_service=_query({}),
        market_clock=_clock(datetime(2026, 10, 23, 10, 0)),
        data_dir=tmp_path,
        logger=MagicMock(),
    )

    assert service.get_history("9999").rt_cd == ErrorCode.INVALID_INPUT.value
