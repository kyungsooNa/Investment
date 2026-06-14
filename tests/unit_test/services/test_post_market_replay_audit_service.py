import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import TradeSignal
from core.market_clock import MarketClock
from services.post_market_replay_audit_service import PostMarketReplayAuditService


def _write_strategy_log(log_dir: str, strategy: str = "OneilPocketPivot") -> None:
    path = os.path.join(log_dir, f"20260505_090300_{strategy}.log.json")
    rows = [
        {
            "timestamp": "2026-05-05 09:03:00,000",
            "level": "INFO",
            "data": {"event": "scan_with_watchlist", "count": 2},
        },
        {
            "timestamp": "2026-05-05 09:03:10,000",
            "level": "DEBUG",
            "data": {"event": "entry_rejected", "code": "005930", "name": "삼성전자", "reason": "near_signal"},
        },
        {
            "timestamp": "2026-05-05 09:03:11,000",
            "level": "DEBUG",
            "data": {"event": "entry_rejected", "code": "000660", "name": "SK하이닉스", "reason": "near_signal"},
        },
    ]
    with open(path, "w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")


def _strategy_factory(**_kwargs):
    universe = MagicMock()
    universe.get_watchlist = AsyncMock(return_value={"005930": object()})

    class FakeStrategy:
        name = "OneilPocketPivot"
        _universe = universe

        async def scan(self):
            watchlist = await self._universe.get_watchlist()
            if "005930" not in watchlist:
                return []
            return [
                TradeSignal(
                    code="005930",
                    name="삼성전자",
                    action="BUY",
                    price=70_000,
                    qty=1,
                    reason="replay_signal",
                    strategy_name="OneilPocketPivot",
                )
            ]

    return FakeStrategy()


@pytest.mark.asyncio
async def test_audit_service_marks_late_signal_and_missing_from_universe(tmp_path):
    _write_strategy_log(str(tmp_path))
    repo = MagicMock()
    store = MagicMock()
    store.load_signal_history_for_date.return_value = [
        {
            "strategy_name": "OneilPocketPivot",
            "code": "005930",
            "action": "BUY",
            "timestamp": "2026-05-05 09:05:00",
            "api_success": True,
        }
    ]
    service = PostMarketReplayAuditService(
        stock_query_service=AsyncMock(),
        universe_service=MagicMock(),
        indicator_service=MagicMock(),
        market_clock=MarketClock(),
        backtest_journal_repository=repo,
        scheduler_store=store,
        log_dir=str(tmp_path),
        strategy_factory=_strategy_factory,
        env=SimpleNamespace(is_paper_trading=False),
        logger=MagicMock(),
    )

    result = await service.run("20260505")

    assert result.strategy_count == 1
    assert result.late_count == 1
    assert result.missing_from_universe_count == 1
    records = repo.save_run.call_args.args[0]
    by_code = {record["code"]: record for record in records}
    assert by_code["005930"]["metadata"]["audit_status"] == "late_signal"
    assert by_code["005930"]["metadata"]["live_signal_time"] == "2026-05-05 09:05:00"
    assert by_code["000660"]["rejected_reason"] == "missing_from_universe"
    assert by_code["000660"]["metadata"]["audit_status"] == "missing_from_universe"
    assert repo.save_run.call_args.kwargs["run_id"] == "audit_OneilPocketPivot_20260505"
    assert repo.save_run.call_args.kwargs["metadata"]["audit_type"] == "missed_signal"


@pytest.mark.asyncio
async def test_audit_service_marks_replay_signal_missing_when_live_has_no_buy(tmp_path):
    _write_strategy_log(str(tmp_path))
    repo = MagicMock()
    store = MagicMock()
    store.load_signal_history_for_date.return_value = []
    service = PostMarketReplayAuditService(
        stock_query_service=AsyncMock(),
        universe_service=MagicMock(),
        indicator_service=MagicMock(),
        market_clock=MarketClock(),
        backtest_journal_repository=repo,
        scheduler_store=store,
        log_dir=str(tmp_path),
        strategy_factory=_strategy_factory,
        env=SimpleNamespace(is_paper_trading=False),
        logger=MagicMock(),
    )

    result = await service.run("20260505")

    assert result.missed_count == 1
    records = repo.save_run.call_args.args[0]
    missed = [record for record in records if record["code"] == "005930"][0]
    assert missed["metadata"]["audit_status"] == "missed_by_scheduler"


@pytest.mark.asyncio
async def test_audit_service_skips_paper_mode_without_failing(tmp_path):
    _write_strategy_log(str(tmp_path))
    repo = MagicMock()
    service = PostMarketReplayAuditService(
        stock_query_service=AsyncMock(),
        universe_service=MagicMock(),
        indicator_service=MagicMock(),
        market_clock=MarketClock(),
        backtest_journal_repository=repo,
        scheduler_store=MagicMock(),
        log_dir=str(tmp_path),
        strategy_factory=_strategy_factory,
        env=SimpleNamespace(is_paper_trading=True),
        logger=MagicMock(),
    )

    result = await service.run("20260505")

    assert result.skipped is True
    assert result.skip_reason == "historical_intraday_unavailable_in_paper"
    repo.save_run.assert_not_called()


# ---------------------------------------------------------------------------
# 모듈 레벨 헬퍼
# ---------------------------------------------------------------------------
from datetime import datetime  # noqa: E402
from services.post_market_replay_audit_service import (  # noqa: E402
    _canonical_signal_time,
    _parse_signal_time,
    _strategy_name_from_path,
)


def test_strategy_name_from_path():
    assert _strategy_name_from_path("20260505_090300_OneilPocketPivot.log.json") == "OneilPocketPivot"
    assert _strategy_name_from_path("20260505_OneilSqueezeBreakout.log.json.gz") == "OneilSqueezeBreakout"
    assert _strategy_name_from_path("not-a-strategy-file.txt") is None


def test_canonical_signal_time_variants():
    assert _canonical_signal_time("2026-05-05 09:03:00,123") == "2026-05-05 09:03:00"
    assert _canonical_signal_time("20260505090300") == "2026-05-05 09:03:00"
    assert _canonical_signal_time("20260505") == "2026-05-05 00:00:00"
    assert _canonical_signal_time("xyz") == "xyz"


def test_parse_signal_time_valid_and_invalid():
    assert _parse_signal_time("2026-05-05 09:03:00") == datetime(2026, 5, 5, 9, 3, 0)
    assert _parse_signal_time("garbage") == datetime.min


# ---------------------------------------------------------------------------
# 입력 수집 / 보조 분기
# ---------------------------------------------------------------------------
def _make_service(tmp_path, **overrides):
    kwargs = dict(
        stock_query_service=AsyncMock(),
        universe_service=MagicMock(),
        indicator_service=MagicMock(),
        market_clock=MarketClock(),
        backtest_journal_repository=MagicMock(),
        scheduler_store=MagicMock(),
        log_dir=str(tmp_path),
        strategy_factory=_strategy_factory,
        env=SimpleNamespace(is_paper_trading=False),
        logger=MagicMock(),
    )
    kwargs.update(overrides)
    return PostMarketReplayAuditService(**kwargs)


@pytest.mark.asyncio
async def test_run_skips_when_no_live_candidates(tmp_path):
    store = MagicMock()
    store.load_signal_history_for_date.return_value = []
    service = _make_service(tmp_path, scheduler_store=store)  # 로그 디렉터리 비어 있음

    result = await service.run("20260505")

    assert result.skipped is True
    assert result.skip_reason == "no_live_candidates"


def test_collect_inputs_from_logs_reads_gzip_and_buy_signal(tmp_path):
    import gzip

    path = os.path.join(str(tmp_path), "20260505_090300_OneilPocketPivot.log.json.gz")
    rows = [
        json.dumps({"timestamp": "2026-05-05 09:03:00", "data": {"event": "scan_with_watchlist"}}),
        json.dumps({"timestamp": "2026-05-05 09:03:05", "data": {"event": "buy_signal_generated", "code": "005930"}}),
        "this-is-not-json",  # decode/JSON 오류 → 건너뜀
        json.dumps({"timestamp": "2026-05-05 09:03:06", "data": "not-a-dict"}),  # data 비-dict → 건너뜀
        json.dumps({"timestamp": "2025-01-01 00:00:00", "data": {"event": "scan_with_watchlist"}}),  # 날짜 불일치
    ]
    with gzip.open(path, "wb") as fp:
        for row in rows:
            fp.write((row + "\n").encode("utf-8"))

    service = _make_service(tmp_path)
    inputs = service._collect_inputs_from_logs("20260505")

    audit = inputs["OneilPocketPivot"]
    assert "005930" in audit.candidates
    assert audit.scan_times == {"2026-05-05 09:03:00"}
    assert audit.live_buy_times["005930"] == "2026-05-05 09:03:05"


def test_merge_scheduler_signals_handles_non_buy_and_errors(tmp_path):
    from services.post_market_replay_audit_service import _AuditInput

    # 1) loader 가 없는 store → 조용히 반환
    service = _make_service(tmp_path, scheduler_store=SimpleNamespace())
    inputs = {}
    service._merge_scheduler_signals(inputs, "20260505")
    assert inputs == {}

    # 2) loader 가 예외 → 조용히 반환
    store = MagicMock()
    store.load_signal_history_for_date.side_effect = RuntimeError("db down")
    service = _make_service(tmp_path, scheduler_store=store)
    service._merge_scheduler_signals(inputs, "20260505")
    assert inputs == {}

    # 3) SELL 액션 → candidate 만 추가, live_buy_times 없음
    store = MagicMock()
    store.load_signal_history_for_date.return_value = [
        {"strategy_name": "OneilPocketPivot", "code": "005930", "action": "SELL", "timestamp": "2026-05-05 09:05:00"},
        {"strategy_name": "", "code": "x"},  # strategy 누락 → skip
    ]
    service = _make_service(tmp_path, scheduler_store=store)
    inputs = {}
    service._merge_scheduler_signals(inputs, "20260505")
    assert inputs["OneilPocketPivot"].candidates == {"005930"}
    assert inputs["OneilPocketPivot"].live_buy_times == {}


def test_merge_virtual_trades_variants(tmp_path):
    # 1) virtual_trade_service None → no-op
    service = _make_service(tmp_path, virtual_trade_service=None)
    inputs = {}
    service._merge_virtual_trades(inputs, "20260505")
    assert inputs == {}

    # 2) get_all_trades 없음 → no-op
    service = _make_service(tmp_path, virtual_trade_service=SimpleNamespace())
    service._merge_virtual_trades(inputs, "20260505")
    assert inputs == {}

    # 3) get_all_trades 예외 → no-op
    vts = MagicMock()
    vts.get_all_trades.side_effect = RuntimeError("csv down")
    service = _make_service(tmp_path, virtual_trade_service=vts)
    service._merge_virtual_trades(inputs, "20260505")
    assert inputs == {}

    # 4) 정상 trade + 날짜 불일치/필드 누락 trade
    vts = MagicMock()
    vts.get_all_trades.return_value = [
        {"buy_date": "2026-05-05 09:00:00", "strategy": "OneilPocketPivot", "code": "005930"},
        {"buy_date": "2025-01-01 09:00:00", "strategy": "OneilPocketPivot", "code": "000660"},  # 날짜 불일치
        {"buy_date": "2026-05-05 09:00:00", "strategy": "", "code": "000999"},  # strategy 누락
    ]
    service = _make_service(tmp_path, virtual_trade_service=vts)
    inputs = {}
    service._merge_virtual_trades(inputs, "20260505")
    assert inputs["OneilPocketPivot"].candidates == {"005930"}
    assert inputs["OneilPocketPivot"].live_buy_times["005930"] == "2026-05-05 09:00:00"


@pytest.mark.asyncio
async def test_run_strategy_audit_marks_data_unavailable_on_factory_error(tmp_path):
    _write_strategy_log(str(tmp_path))
    repo = MagicMock()
    store = MagicMock()
    store.load_signal_history_for_date.return_value = []

    def _raising_factory(**_kwargs):
        raise RuntimeError("replay data missing")

    service = _make_service(
        tmp_path, backtest_journal_repository=repo, scheduler_store=store,
        strategy_factory=_raising_factory,
    )

    result = await service.run("20260505")

    assert result.data_unavailable_count >= 1
    records = repo.save_run.call_args.args[0]
    assert any(r["metadata"]["audit_status"] == "data_unavailable" for r in records)


def test_default_strategy_factory_rejects_unknown_strategy(tmp_path):
    service = _make_service(tmp_path, strategy_factory=None)
    with pytest.raises(ValueError, match="unsupported audit strategy"):
        service._default_strategy_factory(
            strategy_name="UnknownStrategy",
            replay_sqs=MagicMock(),
            universe_service=MagicMock(),
            indicator_service=MagicMock(),
            backtest_clock=MagicMock(),
            state_dir="/tmp",
            logger=MagicMock(),
        )
