# tests/unit_test/test_strategy_executor.py
import pytest
from typing import List
from strategies.strategy_executor import StrategyExecutor
from interfaces.live_strategy import LiveStrategy
from common.types import TradeSignal, ResStockFullInfoApiOutput
from config.config_loader import RiskGateConfig, RiskGateStrategyLimitConfig


class _MockLiveStrategy(LiveStrategy):
    """테스트용 LiveStrategy 스텁."""

    def __init__(self, strategy_name: str):
        self._name = strategy_name
        self.last_run_codes: List[str] = []

    @property
    def name(self) -> str:
        return self._name

    async def scan(self) -> List[TradeSignal]:
        return []

    async def check_exits(self, holdings) -> List[TradeSignal]:
        return []

    async def run(self, stock_codes: List[str]):
        self.last_run_codes = list(stock_codes)
        return {"signals": []}


def _make_price_info(acml_tr_pbmn: str = "0", acml_vol: str = "0") -> ResStockFullInfoApiOutput:
    return ResStockFullInfoApiOutput(
        acml_tr_pbmn=acml_tr_pbmn,
        acml_vol=acml_vol,
        stck_prpr="10000",
        stck_hgpr="10500",
        stck_lwpr="9800",
        stck_oprc="10200",
        stck_sdpr="10000",
    )


# ── 거래대금 필터 테스트 ───────────────────────────────────────────────────────


async def test_low_trading_value_codes_are_filtered_out():
    """min_trading_value_won 미달 종목은 strategy.run() 이전에 제거된다."""
    strategy = _MockLiveStrategy("test_strategy")

    async def _get_price(code: str) -> ResStockFullInfoApiOutput:
        amounts = {
            "000001": "500000000",    # 5억 — 기준 미달
            "000002": "15000000000",  # 150억 — 기준 충족
        }
        return _make_price_info(acml_tr_pbmn=amounts.get(code, "0"))

    risk_cfg = RiskGateConfig(
        strategy_limits={
            "test_strategy": RiskGateStrategyLimitConfig(min_trading_value_won=10_000_000_000)
        }
    )

    executor = StrategyExecutor(
        strategy=strategy,
        risk_gate_config=risk_cfg,
        get_current_price_fn=_get_price,
    )

    await executor.execute(["000001", "000002"])

    assert "000001" not in strategy.last_run_codes
    assert "000002" in strategy.last_run_codes


async def test_low_volume_codes_are_filtered_out():
    """min_avg_volume 미달 종목은 필터링된다."""
    strategy = _MockLiveStrategy("vol_strategy")

    async def _get_price(code: str) -> ResStockFullInfoApiOutput:
        volumes = {
            "111111": "50000",   # 5만주 — 기준 미달
            "222222": "500000",  # 50만주 — 기준 충족
        }
        return _make_price_info(acml_vol=volumes.get(code, "0"))

    risk_cfg = RiskGateConfig(
        strategy_limits={
            "vol_strategy": RiskGateStrategyLimitConfig(min_avg_volume=100_000)
        }
    )

    executor = StrategyExecutor(
        strategy=strategy,
        risk_gate_config=risk_cfg,
        get_current_price_fn=_get_price,
    )

    await executor.execute(["111111", "222222"])

    assert "111111" not in strategy.last_run_codes
    assert "222222" in strategy.last_run_codes


async def test_no_liquidity_filter_when_threshold_not_set():
    """strategy_limits 설정이 없으면 모든 종목이 그대로 통과한다."""
    strategy = _MockLiveStrategy("test_strategy")
    risk_cfg = RiskGateConfig()

    executor = StrategyExecutor(strategy=strategy, risk_gate_config=risk_cfg)

    await executor.execute(["000001", "000002"])

    assert strategy.last_run_codes == ["000001", "000002"]


async def test_no_liquidity_filter_when_risk_gate_config_absent():
    """risk_gate_config 자체가 주입되지 않으면 필터가 동작하지 않는다."""
    strategy = _MockLiveStrategy("test_strategy")
    executor = StrategyExecutor(strategy=strategy)

    await executor.execute(["000001", "000002"])

    assert strategy.last_run_codes == ["000001", "000002"]


async def test_price_fetch_error_blocks_code():
    """개별 종목 시세 조회 실패 시 해당 종목은 필터링된다 (Fail-Close)."""
    strategy = _MockLiveStrategy("test_strategy")

    async def _get_price(code: str) -> ResStockFullInfoApiOutput:
        if code == "ERR001":
            raise RuntimeError("API 오류")
        return _make_price_info(acml_tr_pbmn="20000000000")  # 200억

    risk_cfg = RiskGateConfig(
        strategy_limits={
            "test_strategy": RiskGateStrategyLimitConfig(min_trading_value_won=10_000_000_000)
        }
    )

    executor = StrategyExecutor(
        strategy=strategy,
        risk_gate_config=risk_cfg,
        get_current_price_fn=_get_price,
    )

    await executor.execute(["ERR001", "GOOD01"])

    assert "ERR001" not in strategy.last_run_codes
    assert "GOOD01" in strategy.last_run_codes


async def test_both_trading_value_and_volume_must_pass():
    """min_trading_value_won과 min_avg_volume 둘 다 설정된 경우 모두 충족해야 통과."""
    strategy = _MockLiveStrategy("strict_strategy")

    async def _get_price(code: str) -> ResStockFullInfoApiOutput:
        data = {
            "PASS00": ("20000000000", "500000"),  # 둘 다 통과
            "FAIL01": ("500000000", "500000"),    # 거래대금 미달
            "FAIL02": ("20000000000", "10000"),   # 거래량 미달
            "FAIL03": ("500000000", "10000"),     # 둘 다 미달
        }
        pbmn, vol = data.get(code, ("0", "0"))
        return _make_price_info(acml_tr_pbmn=pbmn, acml_vol=vol)

    risk_cfg = RiskGateConfig(
        strategy_limits={
            "strict_strategy": RiskGateStrategyLimitConfig(
                min_trading_value_won=10_000_000_000,
                min_avg_volume=100_000,
            )
        }
    )

    executor = StrategyExecutor(
        strategy=strategy,
        risk_gate_config=risk_cfg,
        get_current_price_fn=_get_price,
    )

    await executor.execute(["PASS00", "FAIL01", "FAIL02", "FAIL03"])

    assert strategy.last_run_codes == ["PASS00"]
