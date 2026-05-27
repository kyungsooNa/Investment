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
