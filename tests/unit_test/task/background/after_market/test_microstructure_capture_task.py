from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, ResCommonResponse
from services.notification_service import NotificationCategory, NotificationLevel
from task.background.after_market.microstructure_capture_task import MicrostructureCaptureTask


def _payload(date="20260702"):
    return {
        "metadata": {
            "trade_date": date,
            "row_counts": {"intraday_minutes": 3, "execution_strength": 1, "program_trades": 1},
        },
        "intraday_minutes": {},
        "execution_strength": {},
        "program_trades": {},
    }


def _quality_payload(
    *,
    date="20260702",
    codes=None,
    intraday_minutes=None,
    program_trades=None,
    quality=None,
):
    codes = codes or ["005930", "000660"]
    intraday_minutes = intraday_minutes or {code: [{}] for code in codes}
    program_trades = program_trades or {
        code: {"program_net_buy_qty": 1} for code in codes
    }
    return {
        "metadata": {
            "trade_date": date,
            "codes": codes,
            "program_source": "program_db",
            "program_fallback_codes": [],
            "quality": quality or {
                "empty_minute_codes": [],
                "stale_minute_rows_dropped": {},
            },
            "row_counts": {
                "intraday_minutes": sum(len(rows) for rows in intraday_minutes.values()),
                "execution_strength": len(codes),
                "program_trades": sum(1 for value in program_trades.values() if value is not None),
            },
        },
        "intraday_minutes": intraday_minutes,
        "execution_strength": {code: 120.0 for code in codes},
        "program_trades": program_trades,
    }


@pytest.fixture
def capture_service():
    svc = MagicMock()
    svc.capture = AsyncMock(return_value=_payload())
    svc.write_overlay_files = MagicMock(return_value={})
    return svc


@pytest.fixture
def universe_service():
    svc = MagicMock()
    svc.get_watchlist = AsyncMock(return_value={"005930": MagicMock(), "000660": MagicMock()})
    return svc


@pytest.fixture
def virtual_trade_service():
    svc = MagicMock()
    svc.get_holds = MagicMock(return_value=[{"code": "035420"}])
    return svc


class _FakeStore:
    """save_keyed/load_keyed 만 흉내내는 인메모리 스토어 (재시작 영속화 검증용)."""

    def __init__(self):
        self._data = {}

    def save_keyed(self, key, value):
        self._data[key] = value

    def load_keyed(self, key):
        return self._data.get(key)


def _make_task(capture_service, tmp_path, **kwargs):
    defaults = dict(
        capture_service=capture_service,
        output_dir=tmp_path / "out",
        program_db_path=tmp_path / "program_trading.db",
        execution_strength_db_path=tmp_path / "execution_strength.db",
        orderbook_db_path=tmp_path / "orderbook_snapshots.db",
        logger=MagicMock(),
        quality_retry_attempts=0,
    )
    defaults.update(kwargs)
    return MicrostructureCaptureTask(**defaults)


@pytest.mark.asyncio
async def test_on_market_closed_captures_once_per_date(capture_service, universe_service, tmp_path):
    task = _make_task(capture_service, tmp_path, universe_service=universe_service)

    await task._on_market_closed("20260702")
    await task._on_market_closed("20260702")

    capture_service.capture.assert_awaited_once()
    capture_service.write_overlay_files.assert_called_once()
    assert task.get_progress()["last_captured_date"] == "20260702"


@pytest.mark.asyncio
async def test_last_captured_date_persists_across_restart(capture_service, universe_service, tmp_path):
    store = _FakeStore()
    task = _make_task(capture_service, tmp_path, universe_service=universe_service, scheduler_store=store)
    await task._on_market_closed("20260702")

    restarted = _make_task(capture_service, tmp_path, universe_service=universe_service, scheduler_store=store)
    await restarted._on_market_closed("20260702")

    capture_service.capture.assert_awaited_once()


@pytest.mark.asyncio
async def test_codes_union_holdings_first_dedup_and_capped(
    capture_service, universe_service, virtual_trade_service, tmp_path
):
    # 보유 종목이 watchlist 에도 있으면 중복 제거하고 보유 우선 순서를 유지한다.
    virtual_trade_service.get_holds.return_value = [{"code": "005930"}, {"code": "035420"}]

    task = _make_task(
        capture_service, tmp_path,
        universe_service=universe_service,
        virtual_trade_service=virtual_trade_service,
    )
    await task._on_market_closed("20260702")

    codes = capture_service.capture.await_args.kwargs["codes"]
    assert codes == ["005930", "035420", "000660"]

    # max_codes cap 검증
    capture_service.capture.reset_mock()
    capped = _make_task(
        capture_service, tmp_path,
        universe_service=universe_service,
        virtual_trade_service=virtual_trade_service,
        max_codes=1,
    )
    await capped._on_market_closed("20260703")
    assert capture_service.capture.await_args.kwargs["codes"] == ["005930"]


@pytest.mark.asyncio
async def test_program_source_prefers_program_db_when_file_exists(
    capture_service, universe_service, tmp_path
):
    db_path = tmp_path / "program_trading.db"
    task = _make_task(
        capture_service, tmp_path,
        universe_service=universe_service,
        program_db_path=db_path,
    )

    await task._on_market_closed("20260702")
    assert capture_service.capture.await_args.kwargs["program_source"] == "daily_rest"

    capture_service.capture.reset_mock()
    db_path.write_bytes(b"")
    task2 = _make_task(
        capture_service, tmp_path,
        universe_service=universe_service,
        program_db_path=db_path,
    )
    await task2._on_market_closed("20260703")
    assert capture_service.capture.await_args.kwargs["program_source"] == "program_db"


@pytest.mark.asyncio
async def test_no_codes_skips_capture_and_keeps_date_unmarked(capture_service, tmp_path):
    empty_universe = MagicMock()
    empty_universe.get_watchlist = AsyncMock(return_value={})
    task = _make_task(capture_service, tmp_path, universe_service=empty_universe)

    await task._on_market_closed("20260702")
    capture_service.capture.assert_not_awaited()
    assert task.get_progress()["last_captured_date"] is None

    # 이후 후보가 생기면 같은 날짜라도 캡처된다 (날짜 미저장 확인)
    empty_universe.get_watchlist = AsyncMock(return_value={"005930": MagicMock()})
    await task._on_market_closed("20260702")
    capture_service.capture.assert_awaited_once()


@pytest.mark.asyncio
async def test_watchlist_error_degrades_to_holdings_only(
    capture_service, virtual_trade_service, tmp_path
):
    broken_universe = MagicMock()
    broken_universe.get_watchlist = AsyncMock(side_effect=RuntimeError("universe down"))
    task = _make_task(
        capture_service, tmp_path,
        universe_service=broken_universe,
        virtual_trade_service=virtual_trade_service,
    )

    await task._on_market_closed("20260702")

    assert capture_service.capture.await_args.kwargs["codes"] == ["035420"]


@pytest.mark.asyncio
async def test_capture_error_logged_and_date_not_marked(capture_service, universe_service, tmp_path):
    capture_service.capture = AsyncMock(side_effect=RuntimeError("KIS down"))
    task = _make_task(capture_service, tmp_path, universe_service=universe_service)

    await task._on_market_closed("20260702")

    assert task.get_progress()["last_captured_date"] is None
    assert task.get_progress()["last_result"] == {"error": "KIS down"}


@pytest.mark.asyncio
async def test_quality_and_fallback_exposed_in_last_result(
    capture_service, universe_service, tmp_path
):
    payload = _payload()
    payload["metadata"]["program_fallback_codes"] = ["000660"]
    payload["metadata"]["quality"] = {
        "empty_minute_codes": ["005930"],
        "stale_minute_rows_dropped": {},
    }
    capture_service.capture = AsyncMock(return_value=payload)
    task = _make_task(capture_service, tmp_path, universe_service=universe_service)

    await task._on_market_closed("20260702")

    last_result = task.get_progress()["last_result"]
    assert last_result["program_fallback_codes"] == ["000660"]
    assert last_result["quality"]["empty_minute_codes"] == ["005930"]


@pytest.mark.asyncio
async def test_quality_gate_metrics_exposed_and_warned_when_capture_quality_fails(
    capture_service, universe_service, tmp_path
):
    payload = {
        "metadata": {
            "trade_date": "20260702",
            "codes": ["005930", "000660"],
            "program_source": "program_db",
            "program_fallback_codes": [],
            "quality": {
                "empty_minute_codes": ["000660"],
                "stale_minute_rows_dropped": {"005930": 2},
            },
            "row_counts": {"intraday_minutes": 1, "execution_strength": 2, "program_trades": 0},
        },
        "intraday_minutes": {"005930": [{"stck_cntg_hour": "090000"}], "000660": []},
        "execution_strength": {"005930": 120.0, "000660": 130.0},
        "program_trades": {"005930": None, "000660": None},
    }
    capture_service.capture = AsyncMock(return_value=payload)
    logger = MagicMock()
    db_path = tmp_path / "program_trading.db"
    db_path.write_bytes(b"")
    task = _make_task(
        capture_service,
        tmp_path,
        universe_service=universe_service,
        program_db_path=db_path,
        logger=logger,
    )

    await task._on_market_closed("20260702")

    last_result = task.get_progress()["last_result"]
    assert last_result["quality_gate_passed"] is False
    assert last_result["quality_issues"] == [
        "intraday_coverage_below_threshold",
        "program_overlay_coverage_below_threshold",
        "program_db_coverage_below_threshold",
    ]
    assert last_result["intraday_coverage_pct"] == 50.0
    assert last_result["program_overlay_coverage_pct"] == 0.0
    assert last_result["program_db_coverage_pct"] == 0.0
    written_payload = capture_service.write_overlay_files.call_args.args[0]
    assert written_payload["metadata"]["quality_gate"] == {
        "valid_for_backtest": False,
        "passed": False,
        "issues": [
            "intraday_coverage_below_threshold",
            "program_overlay_coverage_below_threshold",
            "program_db_coverage_below_threshold",
        ],
        "warnings": [],
        "intraday_coverage_pct": 50.0,
        "program_overlay_coverage_pct": 0.0,
        "program_db_coverage_pct": 0.0,
        "execution_strength_db_coverage_pct": None,
        "orderbook_db_coverage_pct": None,
        "orderbook_sparse_codes": [],
        "orderbook_min_rows_per_code": 30,
    }
    logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_quality_gate_failure_emits_background_warning_notification(
    capture_service, universe_service, tmp_path
):
    payload = {
        "metadata": {
            "trade_date": "20260702",
            "codes": ["005930", "000660"],
            "program_source": "program_db",
            "program_fallback_codes": [],
            "quality": {
                "empty_minute_codes": ["000660"],
                "stale_minute_rows_dropped": {"005930": 2},
            },
            "row_counts": {"intraday_minutes": 1, "execution_strength": 2, "program_trades": 0},
        },
        "intraday_minutes": {"005930": [{}], "000660": []},
        "execution_strength": {"005930": 120.0, "000660": 130.0},
        "program_trades": {"005930": None, "000660": None},
    }
    capture_service.capture = AsyncMock(return_value=payload)
    notification_service = MagicMock()
    notification_service.emit = AsyncMock()
    db_path = tmp_path / "program_trading.db"
    db_path.write_bytes(b"")
    task = _make_task(
        capture_service,
        tmp_path,
        universe_service=universe_service,
        program_db_path=db_path,
        notification_service=notification_service,
    )

    await task._on_market_closed("20260702")

    notification_service.emit.assert_awaited_once()
    args = notification_service.emit.await_args.args
    kwargs = notification_service.emit.await_args.kwargs
    assert args[:4] == (
        NotificationCategory.BACKGROUND,
        NotificationLevel.WARNING,
        "Microstructure 캡처 품질 게이트 실패",
        "20260702: intraday=50.0%, program=0.0%, program_db=0.0%, "
        "orderbook_db=-, "
        "empty_minutes=1 [000660], stale_dropped=2 [005930:2]",
    )
    assert kwargs["metadata"]["issues"] == [
        "intraday_coverage_below_threshold",
        "program_overlay_coverage_below_threshold",
        "program_db_coverage_below_threshold",
    ]
    assert kwargs["metadata"]["codes"] == 2
    assert kwargs["metadata"]["empty_minute_codes"] == ["000660"]
    assert kwargs["metadata"]["stale_minute_rows_dropped"] == 2
    assert kwargs["metadata"]["stale_minute_rows_dropped_by_code"] == {"005930": 2}
    assert kwargs["metadata"]["program_fallback_codes"] == []
    assert kwargs["metadata"]["orderbook_db_coverage_pct"] is None
    assert kwargs["metadata"]["orderbook_sparse_codes"] == []


@pytest.mark.asyncio
async def test_intraday_quality_failure_retries_once_before_warning(
    capture_service, universe_service, tmp_path
):
    first_payload = _quality_payload(
        intraday_minutes={"005930": [{}], "000660": []},
        quality={
            "empty_minute_codes": ["000660"],
            "stale_minute_rows_dropped": {},
        },
    )
    retry_payload = _quality_payload()
    capture_service.capture = AsyncMock(side_effect=[first_payload, retry_payload])
    notification_service = MagicMock()
    notification_service.emit = AsyncMock()
    db_path = tmp_path / "program_trading.db"
    db_path.write_bytes(b"")
    task = _make_task(
        capture_service,
        tmp_path,
        universe_service=universe_service,
        program_db_path=db_path,
        notification_service=notification_service,
        quality_retry_attempts=1,
        quality_retry_delay_sec=0,
    )

    await task._on_market_closed("20260702")

    assert capture_service.capture.await_count == 2
    capture_service.write_overlay_files.assert_called_once_with(
        retry_payload,
        tmp_path / "out",
    )
    notification_service.emit.assert_not_awaited()
    assert task.get_progress()["last_result"]["quality_gate_passed"] is True


@pytest.mark.asyncio
async def test_quality_gate_passes_without_warning_when_capture_quality_is_good(
    capture_service, universe_service, tmp_path
):
    payload = {
        "metadata": {
            "trade_date": "20260702",
            "codes": ["005930", "000660"],
            "program_source": "program_db",
            "program_fallback_codes": [],
            "quality": {
                "empty_minute_codes": [],
                "stale_minute_rows_dropped": {},
            },
            "row_counts": {"intraday_minutes": 2, "execution_strength": 2, "program_trades": 2},
        },
        "intraday_minutes": {"005930": [{}], "000660": [{}]},
        "execution_strength": {"005930": 120.0, "000660": 130.0},
        "program_trades": {
            "005930": {"program_net_buy_qty": 1},
            "000660": {"program_net_buy_qty": -1},
        },
    }
    capture_service.capture = AsyncMock(return_value=payload)
    logger = MagicMock()
    db_path = tmp_path / "program_trading.db"
    db_path.write_bytes(b"")
    task = _make_task(
        capture_service,
        tmp_path,
        universe_service=universe_service,
        program_db_path=db_path,
        logger=logger,
    )

    await task._on_market_closed("20260702")

    last_result = task.get_progress()["last_result"]
    assert last_result["quality_gate_passed"] is True
    assert last_result["quality_issues"] == []
    assert last_result["intraday_coverage_pct"] == 100.0
    assert last_result["program_overlay_coverage_pct"] == 100.0
    assert last_result["program_db_coverage_pct"] == 100.0
    logger.warning.assert_not_called()


@pytest.mark.asyncio
async def test_quality_gate_success_does_not_emit_notification(
    capture_service, universe_service, tmp_path
):
    payload = {
        "metadata": {
            "trade_date": "20260702",
            "codes": ["005930"],
            "program_source": "daily_rest",
            "program_fallback_codes": [],
            "quality": {
                "empty_minute_codes": [],
                "stale_minute_rows_dropped": {},
            },
            "row_counts": {"intraday_minutes": 1, "execution_strength": 1, "program_trades": 1},
        },
        "intraday_minutes": {"005930": [{}]},
        "execution_strength": {"005930": 120.0},
        "program_trades": {"005930": {"program_net_buy_qty": 1}},
    }
    capture_service.capture = AsyncMock(return_value=payload)
    notification_service = MagicMock()
    notification_service.emit = AsyncMock()
    task = _make_task(
        capture_service,
        tmp_path,
        universe_service=universe_service,
        notification_service=notification_service,
    )

    await task._on_market_closed("20260702")

    notification_service.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_quality_target_warning_is_exposed_and_notified_without_gate_failure(
    capture_service, universe_service, tmp_path
):
    codes = ["005930", "000660"]
    orderbook_rows = [
        {"time": f"0900{i:02d}", "ask_price": 101, "bid_price": 100}
        for i in range(30)
    ]
    payload = _quality_payload(codes=codes)
    payload["metadata"].update({
        "execution_strength_source": "es_db",
        "orderbook_source": "orderbook_db",
    })
    payload["execution_strength_intraday"] = {
        "005930": [{"time": "090001", "strength": 120.0}],
        "000660": [],
    }
    payload["orderbook_intraday"] = {
        "005930": orderbook_rows,
        "000660": [],
    }
    capture_service.capture = AsyncMock(return_value=payload)
    notification_service = MagicMock()
    notification_service.emit = AsyncMock()
    task = _make_task(
        capture_service,
        tmp_path,
        universe_service=universe_service,
        notification_service=notification_service,
    )

    await task._on_market_closed("20260702")

    result = task.get_progress()["last_result"]
    assert result["quality_gate_passed"] is True
    assert result["quality_warnings"] == [
        "execution_strength_db_coverage_below_target",
        "orderbook_db_coverage_below_target",
    ]
    notification_service.emit.assert_awaited_once()
    assert notification_service.emit.await_args.args[2] == "Microstructure 캡처 품질 경고"


def test_write_output_dir_passed_through(capture_service, tmp_path):
    task = _make_task(capture_service, tmp_path)
    assert task.task_name == "microstructure_capture"
    assert task.get_progress()["running"] is False


@pytest.mark.asyncio
async def test_execution_strength_source_auto_selects_es_db_when_db_exists(
    capture_service, universe_service, tmp_path
):
    es_db = tmp_path / "execution_strength.db"
    es_db.write_bytes(b"")
    task = _make_task(
        capture_service, tmp_path,
        universe_service=universe_service,
        execution_strength_db_path=es_db,
    )

    await task._on_market_closed("20260702")

    kwargs = capture_service.capture.await_args.kwargs
    assert kwargs["execution_strength_source"] == "es_db"
    assert task.get_progress()["last_result"]["execution_strength_source"] == "es_db"


@pytest.mark.asyncio
async def test_execution_strength_source_falls_back_to_rest_scalar_without_db(
    capture_service, universe_service, tmp_path
):
    task = _make_task(capture_service, tmp_path, universe_service=universe_service)

    await task._on_market_closed("20260702")

    kwargs = capture_service.capture.await_args.kwargs
    assert kwargs["execution_strength_source"] == "rest_scalar"
    assert (
        task.get_progress()["last_result"]["execution_strength_source"]
        == "rest_scalar"
    )


@pytest.mark.asyncio
async def test_orderbook_source_auto_selects_db_when_file_exists(
    capture_service, universe_service, tmp_path
):
    orderbook_db = tmp_path / "orderbook_snapshots.db"
    orderbook_db.write_bytes(b"")
    task = _make_task(
        capture_service,
        tmp_path,
        universe_service=universe_service,
        orderbook_db_path=orderbook_db,
    )

    await task._on_market_closed("20260702")

    kwargs = capture_service.capture.await_args.kwargs
    assert kwargs["orderbook_source"] == "orderbook_db"
    assert task.get_progress()["last_result"]["orderbook_source"] == "orderbook_db"


@pytest.mark.asyncio
async def test_execution_strength_metrics_exposed_in_last_result(
    capture_service, universe_service, tmp_path
):
    payload = _payload()
    payload["metadata"]["codes"] = ["005930", "000660"]
    payload["metadata"]["execution_strength_source"] = "es_db"
    payload["metadata"]["execution_strength_fallback_codes"] = ["000660"]
    payload["execution_strength_intraday"] = {
        "005930": [{"time": "090001", "strength": 110.0}],
        "000660": [],
    }
    capture_service.capture = AsyncMock(return_value=payload)
    es_db = tmp_path / "execution_strength.db"
    es_db.write_bytes(b"")
    task = _make_task(
        capture_service, tmp_path,
        universe_service=universe_service,
        execution_strength_db_path=es_db,
    )

    await task._on_market_closed("20260702")

    last_result = task.get_progress()["last_result"]
    assert last_result["execution_strength_fallback_codes"] == ["000660"]
    assert last_result["execution_strength_db_coverage_pct"] == 50.0


@pytest.mark.asyncio
async def test_candidate_sources_passed_to_capture_and_counts_exposed(
    capture_service, universe_service, tmp_path
):
    sqs = MagicMock()
    sqs.get_top_trading_value_stocks = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="ok",
            data=[{"mksc_shrn_iscd": "100001", "hts_kor_isnm": "랭킹A"}],
        )
    )
    task = _make_task(
        capture_service, tmp_path,
        universe_service=universe_service,
        stock_query_service=sqs,
    )

    await task._on_market_closed("20260702")

    kwargs = capture_service.capture.await_args.kwargs
    assert kwargs["candidate_sources"] == {
        "base": ["005930", "000660"],
        "ranking_supplement": ["100001"],
    }
    last_result = task.get_progress()["last_result"]
    assert last_result["candidate_source_counts"] == {"base": 2, "ranking_supplement": 1}
