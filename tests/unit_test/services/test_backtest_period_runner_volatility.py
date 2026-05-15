"""BacktestPeriodRunner 가 signal.volatility_20d_annualized 를 journal record 로 전파하는지 검증.

대상 경로:
  - 매수 체결 → _execution_record(BUY)
  - 매도 체결 → _execution_record(SELL)
  - position sizing skip → _rejected_signal_record
  - risk gate 차단 → _rejected_signal_record
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from common.types import ErrorCode, ResCommonResponse, TradeSignal
from services.backtest_execution_simulator import BacktestBar, BacktestPortfolioLedger
from services.backtest_period_runner import BacktestPeriodRunner


VOL = 0.2734


class _Strategy:
    name = "OSB"

    def __init__(self) -> None:
        self.current_date = ""

    def set_backtest_date(self, date_ymd: str) -> None:
        self.current_date = date_ymd

    async def scan(self):
        if self.current_date == "20260501":
            return [
                TradeSignal(
                    code="005930",
                    name="삼성전자",
                    action="BUY",
                    price=70_000,
                    qty=2,
                    reason="squeeze_breakout",
                    strategy_name=self.name,
                    volatility_20d_annualized=VOL,
                )
            ]
        return []

    async def check_exits(self, holdings):
        if self.current_date == "20260502" and holdings:
            return [
                TradeSignal(
                    code="005930",
                    name="삼성전자",
                    action="SELL",
                    price=77_000,
                    qty=2,
                    reason="target_hit",
                    strategy_name=self.name,
                    volatility_20d_annualized=VOL,
                )
            ]
        return []


@dataclass
class _Bars:
    bars: dict[tuple[str, str, str], BacktestBar]

    async def get_bar(self, *, signal: TradeSignal, date_ymd: str, side: str,
                      execution_policy: str = "current_bar") -> BacktestBar:
        return self.bars[(date_ymd, signal.code, side)]


@pytest.mark.asyncio
async def test_volatility_propagates_to_buy_and_sell_execution_records():
    strategy = _Strategy()
    provider = _Bars({
        ("20260501", "005930", "BUY"): BacktestBar("20260501 091000", 70_000, 70_500, 69_500, 70_200, 1_000),
        ("20260502", "005930", "SELL"): BacktestBar("20260502 100000", 77_000, 77_500, 76_500, 77_100, 1_000),
    })
    runner = BacktestPeriodRunner(
        strategy=strategy,
        bar_provider=provider,
        ledger=BacktestPortfolioLedger(initial_cash=1_000_000),
    )

    result = await runner.run(["20260501", "20260502"])

    buy_record = result.journal_records[0]
    sell_record = result.journal_records[1]
    assert buy_record["side"] == "BUY"
    assert sell_record["side"] == "SELL"
    assert buy_record["volatility_20d_annualized"] == pytest.approx(VOL)
    assert sell_record["volatility_20d_annualized"] == pytest.approx(VOL)


@pytest.mark.asyncio
async def test_volatility_propagates_to_risk_gate_rejected_record():
    strategy = _Strategy()
    provider = _Bars({
        ("20260501", "005930", "BUY"): BacktestBar("20260501 091000", 70_000, 70_500, 69_500, 70_200, 1_000),
    })
    risk_gate = AsyncMock()
    risk_gate.validate_order = AsyncMock(return_value=ResCommonResponse(
        rt_cd=str(ErrorCode.RISK_GATE_BLOCKED.value),
        msg1="blocked",
        data={"rule": "max_position"},
    ))

    runner = BacktestPeriodRunner(
        strategy=strategy,
        bar_provider=provider,
        ledger=BacktestPortfolioLedger(initial_cash=1_000_000),
        risk_gate_service=risk_gate,
    )

    result = await runner.run(["20260501"])

    assert any(
        rec["status"] == "REJECTED" and rec["volatility_20d_annualized"] == pytest.approx(VOL)
        for rec in result.journal_records
    )


@pytest.mark.asyncio
async def test_volatility_propagates_to_position_sizing_skip_record():
    strategy = _Strategy()
    provider = _Bars({
        ("20260501", "005930", "BUY"): BacktestBar("20260501 091000", 70_000, 70_500, 69_500, 70_200, 1_000),
    })
    sizing = AsyncMock()
    sizing.adjust_buy_qty = AsyncMock(return_value=(0, "cash_short"))

    runner = BacktestPeriodRunner(
        strategy=strategy,
        bar_provider=provider,
        ledger=BacktestPortfolioLedger(initial_cash=1_000_000),
        position_sizing_service=sizing,
    )

    result = await runner.run(["20260501"])

    assert any(
        rec["status"] == "REJECTED"
        and "sizing_skip" in str(rec.get("rejected_reason") or "")
        and rec["volatility_20d_annualized"] == pytest.approx(VOL)
        for rec in result.journal_records
    )
