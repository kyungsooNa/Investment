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


class _StubPriceStream:
    """PriceStreamService 스텁 — get_market_snapshot / cache_price_snapshot 만 노출."""

    def __init__(self, snapshots=None):
        self._snapshots = dict(snapshots or {})
        self.cache_calls = []

    def get_market_snapshot(self, code):
        from common.market_snapshot import MarketSnapshot
        d = self._snapshots.get(code)
        if d is None:
            return None
        return MarketSnapshot.from_legacy_dict(code, d)

    def cache_price_snapshot(self, code, price, volume='0', acml_tr_pbmn=None, **kwargs):
        self.cache_calls.append({
            "code": code, "price": price, "volume": volume,
            "acml_tr_pbmn": acml_tr_pbmn,
        })


async def test_snapshot_hit_skips_rest_call():
    """fresh snapshot 이 있으면 get_current_price_fn 을 호출하지 않는다."""
    import time as _time

    strategy = _MockLiveStrategy("test_strategy")

    rest_calls = []

    async def _get_price(code):
        rest_calls.append(code)
        return _make_price_info(acml_tr_pbmn="20000000000")

    stub = _StubPriceStream(snapshots={
        "000001": {"acml_vol": 500_000, "acml_tr_pbmn": 20_000_000_000,
                   "received_at": _time.time()},
    })

    risk_cfg = RiskGateConfig(
        strategy_limits={
            "test_strategy": RiskGateStrategyLimitConfig(min_trading_value_won=10_000_000_000)
        }
    )

    executor = StrategyExecutor(
        strategy=strategy,
        risk_gate_config=risk_cfg,
        get_current_price_fn=_get_price,
        price_stream_service=stub,
        snapshot_max_age_sec=5.0,
    )

    await executor.execute(["000001"])

    assert rest_calls == [], "snapshot hit 인 경우 REST 가 호출되어선 안 된다"
    assert "000001" in strategy.last_run_codes


async def test_stale_snapshot_falls_back_to_rest_and_caches():
    """오래된 snapshot 은 REST fallback + cache_price_snapshot 으로 보강된다."""
    import time as _time

    strategy = _MockLiveStrategy("test_strategy")

    rest_calls = []

    async def _get_price(code):
        rest_calls.append(code)
        return _make_price_info(acml_tr_pbmn="20000000000", acml_vol="500000")

    # 100초 전 — snapshot_max_age_sec=5 보다 훨씬 오래됨
    stale_ts = _time.time() - 100.0
    stub = _StubPriceStream(snapshots={
        "000001": {"acml_vol": 1, "acml_tr_pbmn": 1, "received_at": stale_ts},
    })

    risk_cfg = RiskGateConfig(
        strategy_limits={
            "test_strategy": RiskGateStrategyLimitConfig(min_trading_value_won=10_000_000_000)
        }
    )

    executor = StrategyExecutor(
        strategy=strategy,
        risk_gate_config=risk_cfg,
        get_current_price_fn=_get_price,
        price_stream_service=stub,
        snapshot_max_age_sec=5.0,
    )

    await executor.execute(["000001"])

    assert rest_calls == ["000001"], "stale snapshot 은 REST fallback 을 트리거해야 한다"
    assert len(stub.cache_calls) == 1
    assert stub.cache_calls[0]["code"] == "000001"
    assert "000001" in strategy.last_run_codes


async def test_liquidity_filter_respects_concurrency_limit():
    """max_liquidity_concurrency 가 동시 실행되는 _get_price 호출 수의 상한이다."""
    import asyncio

    strategy = _MockLiveStrategy("test_strategy")

    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    async def _get_price(code: str) -> ResStockFullInfoApiOutput:
        nonlocal in_flight, peak
        async with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        try:
            await asyncio.sleep(0.02)  # 동시 실행 윈도우 확보
            return _make_price_info(acml_tr_pbmn="20000000000")
        finally:
            async with lock:
                in_flight -= 1

    risk_cfg = RiskGateConfig(
        strategy_limits={
            "test_strategy": RiskGateStrategyLimitConfig(min_trading_value_won=10_000_000_000)
        }
    )

    executor = StrategyExecutor(
        strategy=strategy,
        risk_gate_config=risk_cfg,
        get_current_price_fn=_get_price,
        max_liquidity_concurrency=2,
    )

    codes = [f"{i:06d}" for i in range(1, 11)]  # 10 종목
    await executor.execute(codes)

    assert peak <= 2, f"동시 실행 피크 {peak} 가 한도 2 를 초과했다"
    assert strategy.last_run_codes == codes  # 모두 통과해야 한다


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
