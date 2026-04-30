"""Unit tests for StrategyDebugRunner and _UniverseFilterProxy."""
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import TradeSignal
from strategies.debug.strategy_debug_runner import (
    DebugReport,
    StrategyDebugRunner,
    _UniverseFilterProxy,
)
from strategies.oneil_common_types import OSBWatchlistItem


# ── 헬퍼 ─────────────────────────────────────────────────────────────

def _make_watchlist_item(code: str) -> OSBWatchlistItem:
    return OSBWatchlistItem(
        code=code, name=f"종목{code}", market="KOSPI",
        high_20d=10000, ma_20d=9500.0, ma_50d=9000.0,
        avg_vol_20d=500000.0, bb_width_min_20d=0.05, prev_bb_width=0.06,
        w52_hgpr=12000, avg_trading_value_5d=5_000_000_000.0,
    )


def _make_debug_logger() -> logging.Logger:
    logger = logging.getLogger(f"strategy_debug_test_{id(object())}")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger


def _make_strategy(watchlist: dict, signals: list | None = None):
    """mock LiveStrategy."""
    universe = MagicMock()
    universe.get_watchlist = AsyncMock(return_value=watchlist)
    universe.is_market_timing_ok = AsyncMock(return_value=True)

    strategy = MagicMock()
    strategy.name = "테스트전략"
    strategy._universe = universe
    strategy._logger = _make_debug_logger()
    strategy.scan = AsyncMock(return_value=signals or [])
    return strategy


# ── _UniverseFilterProxy ─────────────────────────────────────────────

class TestUniverseFilterProxy:
    async def test_filters_watchlist_by_allowed_codes(self):
        inner = MagicMock()
        full = {
            "005930": _make_watchlist_item("005930"),
            "000660": _make_watchlist_item("000660"),
            "035720": _make_watchlist_item("035720"),
        }
        inner.get_watchlist = AsyncMock(return_value=full)
        proxy = _UniverseFilterProxy(inner, allowed={"005930", "035720"})

        result = await proxy.get_watchlist()
        assert set(result.keys()) == {"005930", "035720"}
        assert proxy._last_full_set == {"005930", "000660", "035720"}

    async def test_delegates_other_methods_to_inner(self):
        inner = MagicMock()
        inner.is_market_timing_ok = AsyncMock(return_value=True)
        proxy = _UniverseFilterProxy(inner, allowed=set())

        result = await proxy.is_market_timing_ok("KOSPI")
        inner.is_market_timing_ok.assert_called_once_with("KOSPI")
        assert result is True

    async def test_returns_empty_when_no_codes_in_watchlist(self):
        inner = MagicMock()
        inner.get_watchlist = AsyncMock(return_value={"999999": _make_watchlist_item("999999")})
        proxy = _UniverseFilterProxy(inner, allowed={"005930"})

        result = await proxy.get_watchlist()
        assert result == {}
        assert proxy._last_full_set == {"999999"}


# ── StrategyDebugRunner ──────────────────────────────────────────────

class TestStrategyDebugRunner:
    async def test_run_returns_debug_report(self):
        strategy = _make_strategy(watchlist={"005930": _make_watchlist_item("005930")})
        runner = StrategyDebugRunner(strategy, _make_debug_logger())
        report = await runner.run()

        assert isinstance(report, DebugReport)
        assert report.strategy_name == "테스트전략"
        assert isinstance(report.limitations, list)
        assert len(report.limitations) > 0

    async def test_scanned_and_missing_codes_with_candidate_codes(self):
        watchlist = {
            "005930": _make_watchlist_item("005930"),
            "000660": _make_watchlist_item("000660"),
        }
        strategy = _make_strategy(watchlist=watchlist)
        runner = StrategyDebugRunner(strategy, _make_debug_logger())

        # 005930은 watchlist에 있고, 035720은 없음
        report = await runner.run(candidate_codes=["005930", "035720"])

        assert "005930" in report.scanned_codes
        assert "035720" in report.missing_codes
        assert "035720" not in report.scanned_codes

    async def test_universe_restored_after_run(self):
        """정상 실행 후 strategy._universe가 원본으로 복원되어야 한다."""
        watchlist = {"005930": _make_watchlist_item("005930")}
        strategy = _make_strategy(watchlist=watchlist)
        original_universe = strategy._universe
        runner = StrategyDebugRunner(strategy, _make_debug_logger())

        await runner.run(candidate_codes=["005930"])

        assert strategy._universe is original_universe

    async def test_universe_restored_on_exception(self):
        """예외 발생 시에도 strategy._universe가 원본으로 복원되어야 한다."""
        watchlist = {"005930": _make_watchlist_item("005930")}
        strategy = _make_strategy(watchlist=watchlist)
        strategy.scan = AsyncMock(side_effect=RuntimeError("scan failed"))
        original_universe = strategy._universe
        runner = StrategyDebugRunner(strategy, _make_debug_logger())

        with pytest.raises(RuntimeError):
            await runner.run(candidate_codes=["005930"])

        assert strategy._universe is original_universe

    async def test_captures_rejection_events_from_strategy_logger(self):
        """전략 실행 중 debug_logger에 emit된 dict 로그가 events에 포함되어야 한다."""
        watchlist = {"005930": _make_watchlist_item("005930")}
        debug_logger = _make_debug_logger()

        async def fake_scan():
            debug_logger.info({"event": "pp_rejected", "code": "005930", "reason": "no_ma_proximity"})
            return []

        strategy = _make_strategy(watchlist=watchlist)
        strategy.scan = fake_scan
        runner = StrategyDebugRunner(strategy, debug_logger)
        report = await runner.run(candidate_codes=["005930"])

        assert len(report.events) == 1
        assert report.events[0].event == "pp_rejected"

    async def test_missing_codes_reported_when_all_codes_absent(self):
        """요청 코드가 모두 watchlist에 없으면 events는 비고 missing_codes만 채워진다."""
        strategy = _make_strategy(watchlist={"999999": _make_watchlist_item("999999")})
        runner = StrategyDebugRunner(strategy, _make_debug_logger())
        report = await runner.run(candidate_codes=["005930", "000660"])

        assert report.scanned_codes == []
        assert set(report.missing_codes) == {"005930", "000660"}
        assert report.events == []

    async def test_signals_included_in_report(self):
        signal = TradeSignal(code="005930", name="삼성전자", action="BUY", price=70000, qty=10, reason="test", strategy_name="테스트전략")
        strategy = _make_strategy(
            watchlist={"005930": _make_watchlist_item("005930")},
            signals=[signal],
        )
        runner = StrategyDebugRunner(strategy, _make_debug_logger())
        report = await runner.run(candidate_codes=["005930"])

        assert len(report.signals) == 1
        assert report.signals[0].code == "005930"

    async def test_run_without_candidate_codes_uses_full_universe(self):
        """candidate_codes=None 이면 전체 watchlist 스캔 수를 리포트한다."""
        watchlist = {
            "005930": _make_watchlist_item("005930"),
            "000660": _make_watchlist_item("000660"),
        }
        strategy = _make_strategy(watchlist=watchlist)

        async def fake_scan():
            await strategy._universe.get_watchlist()
            return []

        strategy.scan = fake_scan
        runner = StrategyDebugRunner(strategy, _make_debug_logger())
        report = await runner.run(candidate_codes=None)

        assert report.requested_codes is None
        assert report.scanned_codes == ["005930", "000660"]

    async def test_stage_guard_filters_codes_and_captures_blocked_events(self):
        watchlist = {
            "005930": _make_watchlist_item("005930"),
            "000660": _make_watchlist_item("000660"),
            "035720": _make_watchlist_item("035720"),
        }
        debug_logger = _make_debug_logger()
        strategy = _make_strategy(watchlist=watchlist)
        scanned_inside_strategy = []

        async def fake_scan():
            scanned_inside_strategy.extend((await strategy._universe.get_watchlist()).keys())
            return []

        strategy.scan = fake_scan
        stage_service = MagicMock()
        stage_service.get_stage_for_code = AsyncMock(side_effect=[(2, "Stage 2"), 3, RuntimeError("stage down")])
        runner = StrategyDebugRunner(strategy, debug_logger, stage_service=stage_service)

        report = await runner.run(candidate_codes=["005930", "000660", "035720"])

        assert report.scanned_codes == ["005930", "000660", "035720"]
        assert report.events == []
        assert scanned_inside_strategy == ["005930"]

    async def test_run_without_universe_uses_candidate_codes(self):
        strategy = SimpleNamespace(
            name="무유니버스전략",
            scan=AsyncMock(return_value=[]),
        )
        debug_logger = _make_debug_logger()
        runner = StrategyDebugRunner(strategy, debug_logger)

        report = await runner.run(candidate_codes=["005930"])

        assert report.scanned_codes == ["005930"]
        assert report.missing_codes == []
