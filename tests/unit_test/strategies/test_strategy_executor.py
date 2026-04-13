from strategies.strategy_executor import StrategyExecutor
from strategies.momentum_strategy import MomentumStrategy
import pytest
import importlib
from unittest.mock import AsyncMock
from common.types import ResCommonResponse


@pytest.mark.asyncio
async def test_strategy_executor_with_mocked_quotations():
    # ✅ 오염된 클래스/함수 패치를 모두 걷어내고 깨끗하게 다시 로드
    # ── 0) 오염 제거: 모듈 리로드로 깨끗한 클래스 확보 ──
    import importlib
    import strategies.momentum_strategy as mm
    import strategies.strategy_executor as se
    mm = importlib.reload(mm)
    se = importlib.reload(se)
    MomentumStrategy = mm.MomentumStrategy
    StrategyExecutor = se.StrategyExecutor

    broker = AsyncMock()
    # ... (broker 나머지 설정은 동일) ...
    broker.get_price_summary.side_effect = [
        ResCommonResponse(rt_cd="0", msg1="정상",
                          data={"symbol": "0001", "open": 10000, "current": 11000, "change_rate": 10.0}),
        ResCommonResponse(rt_cd="0", msg1="정상",
                          data={"symbol": "0002", "open": 20000, "current": 24000, "change_rate": 20.0}),
        ResCommonResponse(rt_cd="0", msg1="정상",
                          data={"symbol": "0003", "open": 15000, "current": 16000, "change_rate": 6.7}),
    ]

    async def mock_get_current_price(code, **kwargs):
        data_map = {
            "0001": {"stck_prpr": 11500},
            "0002": {"stck_prpr": 25000},
            "0003": {"stck_prpr": 16500},
        }
        return ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data=data_map.get(code, {"stck_prpr": 0})
        )

    broker.get_current_price.side_effect = mock_get_current_price
    broker.get_name_by_code = AsyncMock(side_effect=lambda code: f"종목{code}")

    strategy = MomentumStrategy(
        broker=broker,
        min_change_rate=10.0,
        min_follow_through=3.0,
        min_follow_through_time=10,
        mode="live",
    )
    executor = StrategyExecutor(strategy=strategy)

    result = await executor.execute(["0001", "0002", "0003"])

    assert isinstance(result, dict)
    assert "follow_through" in result


@pytest.mark.asyncio
async def test_strategy_executor_in_backtest_mode():
    # ── 0) 오염 제거: 모듈 리로드로 깨끗한 클래스 확보 ──
    import importlib
    import strategies.momentum_strategy as mm
    import strategies.strategy_executor as se
    mm = importlib.reload(mm)
    se = importlib.reload(se)
    MomentumStrategy = mm.MomentumStrategy
    StrategyExecutor = se.StrategyExecutor

    # ── 1) 입력 의존 side_effect로 안정적인 mock 구성 ──
    broker = AsyncMock()

    async def price_summary_effect(code):
        data_map = {
            "005930": {"symbol": "005930", "open": 70000, "current": 77000, "change_rate": 10.0},
            "000660": {"symbol": "000660", "open": 100000, "current": 105000, "change_rate": 5.0},
        }
        return ResCommonResponse(rt_cd="0", msg1="정상", data=data_map.get(code, {}))

    broker.get_price_summary.side_effect = price_summary_effect
    broker.get_name_by_code = AsyncMock(side_effect=lambda code: f"종목{code}")

    # ── 2) backtest price lookup (async) ──
    async def mock_backtest_lookup(code, summary, minutes):
        return {"005930": 80000, "000660": 106000}[code]

    # ── 3) 전략 생성 (backtest 모드) ──
    strategy = MomentumStrategy(
        broker=broker,
        min_change_rate=10.0,
        min_follow_through=3.0,
        min_follow_through_time=10,
        mode="backtest",
        backtest_lookup=mock_backtest_lookup,
    )
    executor = StrategyExecutor(strategy=strategy)

    # ── 4) 실행 ──
    result = await executor.execute(["005930", "000660"])

    # ── 5) 검증 ──
    assert isinstance(result, dict)
    assert result["follow_through"] == [{"code": "005930", "name": "종목005930"}]


@pytest.mark.asyncio
async def test_strategy_executor_backtest_mode_without_lookup_raises():
    # ── 0) 오염 제거: 모듈 리로드로 깨끗한 클래스 확보 ──
    import importlib
    import strategies.momentum_strategy as mm
    import strategies.strategy_executor as se
    mm = importlib.reload(mm)
    se = importlib.reload(se)
    MomentumStrategy = mm.MomentumStrategy
    StrategyExecutor = se.StrategyExecutor

    broker = AsyncMock()
    broker.get_price_summary.return_value = {
        "symbol": "005930",
        "open": 70000,
        "current": 77000,
        "change_rate": 10.0
    }

    # backtest_lookup 없이 생성
    strategy = MomentumStrategy(
        broker=broker,
        mode="backtest"  # backtest 모드 설정
        # backtest_lookup intentionally omitted
    )

    executor = StrategyExecutor(strategy=strategy)

    # 예외 발생 검증
    with pytest.raises(ValueError, match="Backtest 모드에서는 backtest_lookup 함수가 필요합니다."):
        await executor.execute(["005930"])

@pytest.mark.asyncio
async def test_strategy_executor_live_mode_without_backtest_lookup():
    # ── 0) 오염 제거: 모듈 리로드로 깨끗한 클래스 확보 ──
    import importlib
    import strategies.momentum_strategy as mm
    import strategies.strategy_executor as se
    mm = importlib.reload(mm)
    se = importlib.reload(se)
    MomentumStrategy = mm.MomentumStrategy
    StrategyExecutor = se.StrategyExecutor

    broker = AsyncMock()
    broker.get_price_summary.return_value = ResCommonResponse(
        rt_cd="0",
        msg1="정상",
        data={
            "symbol": "005930",
            "open": 70000,
            "current": 77000,
            "change_rate": 10.0
        }
    )
    # 실제 API 응답 구조에 맞게 dict로 반환
    broker.get_current_price.return_value = ResCommonResponse(
        rt_cd="0",
        msg1="정상",
        data={
            "stck_prpr": 80000
        }
    )
    broker.get_name_by_code = AsyncMock(return_value="삼성전자")

    strategy = MomentumStrategy(
        broker=broker,
        mode="live"
    )

    executor = StrategyExecutor(strategy=strategy)

    result = await executor.execute(["005930"])

    assert "follow_through" in result
    assert result["follow_through"] == [{"code": "005930", "name": "삼성전자"}]


# ── Stage Guard 테스트 ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stage_guard_filters_non_stage2():
    """stage_guard=True 시 Stage 2·0 이외 종목은 strategy.run()에 전달되지 않는다."""
    import strategies.strategy_executor as se
    se = importlib.reload(se)
    StrategyExecutor = se.StrategyExecutor

    # Stage map: 0001=Stage2, 0002=Stage4(제거), 0003=Stage0(통과), 0004=Stage1(제거)
    stage_map = {"0001": (2, "ok"), "0002": (4, "declining"), "0003": (0, "unknown"), "0004": (1, "neglect")}

    mock_minervini = AsyncMock()
    mock_minervini.get_stage_for_code.side_effect = lambda code: stage_map[code]

    received_codes: list = []

    class CapturingStrategy:
        async def run(self, codes):
            received_codes.extend(codes)
            return {"follow_through": [], "not_follow_through": []}

    executor = StrategyExecutor(
        strategy=CapturingStrategy(),
        minervini_stage_service=mock_minervini,
        stage_guard=True,
        allowed_stages=(0, 2),
    )

    await executor.execute(["0001", "0002", "0003", "0004"])

    assert "0001" in received_codes   # Stage 2 → 통과
    assert "0003" in received_codes   # Stage 0 → 통과
    assert "0002" not in received_codes  # Stage 4 → 제거
    assert "0004" not in received_codes  # Stage 1 → 제거


@pytest.mark.asyncio
async def test_stage_guard_disabled_passes_all():
    """stage_guard=False(기본값)이면 Stage 조회 없이 모든 종목이 그대로 전달된다."""
    import strategies.strategy_executor as se
    se = importlib.reload(se)
    StrategyExecutor = se.StrategyExecutor

    mock_minervini = AsyncMock()
    received_codes: list = []

    class CapturingStrategy:
        async def run(self, codes):
            received_codes.extend(codes)
            return {}

    executor = StrategyExecutor(
        strategy=CapturingStrategy(),
        minervini_stage_service=mock_minervini,
        stage_guard=False,          # 비활성
    )

    await executor.execute(["A", "B", "C"])

    assert received_codes == ["A", "B", "C"]
    mock_minervini.get_stage_for_code.assert_not_called()


@pytest.mark.asyncio
async def test_stage_guard_timeout_passes_through():
    """Stage 조회 타임아웃 발생 시 해당 종목은 Stage 0으로 처리되어 통과한다."""
    import strategies.strategy_executor as se
    se = importlib.reload(se)
    StrategyExecutor = se.StrategyExecutor

    async def slow_stage(code):
        import asyncio
        await asyncio.sleep(99)  # 절대 완료 안 됨
        return (2, "ok")

    mock_minervini = AsyncMock()
    mock_minervini.get_stage_for_code.side_effect = slow_stage

    received_codes: list = []

    class CapturingStrategy:
        async def run(self, codes):
            received_codes.extend(codes)
            return {}

    executor = StrategyExecutor(
        strategy=CapturingStrategy(),
        minervini_stage_service=mock_minervini,
        stage_guard=True,
        allowed_stages=(0, 2),
        guard_timeout=0.01,         # 매우 짧은 타임아웃
    )

    await executor.execute(["X"])

    assert "X" in received_codes   # 타임아웃 → Stage 0 처리 → 통과
