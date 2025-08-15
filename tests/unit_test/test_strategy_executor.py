from strategies.strategy_executor import StrategyExecutor
from strategies.momentum_strategy import MomentumStrategy
import pytest
from unittest.mock import AsyncMock
from common.types import ResCommonResponse

@pytest.mark.asyncio
async def test_strategy_executor_with_mocked_quotations():
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

    async def mock_get_current_price(code):
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


    # ▼▼▼▼▼ 핵심 수정 부분 ▼▼▼▼▼
    # 'quotations=' 대신 MomentumStrategy의 __init__에 정의된 실제 파라미터 이름을 사용해야 합니다.
    # (여기서는 'api_client'라고 가정)
    strategy = MomentumStrategy(
        broker=broker,  # 'quotations=' -> 'api_client=' 로 변경
        min_change_rate=10.0,
        min_follow_through=3.0,
        min_follow_through_time=10,
        mode="live"
    )
    # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

    executor = StrategyExecutor(strategy=strategy)

    result = await executor.execute(["0001", "0002", "0003"])

    assert "follow_through" in result
    assert "not_follow_through" in result

    assert result["follow_through"] == [
        {"code": "0001", "name": "종목0001"},
        {"code": "0002", "name": "종목0002"}
    ]


@pytest.mark.asyncio
async def test_strategy_executor_in_backtest_mode():
    # 1. Mock Quotations 객체
    broker = AsyncMock()
    broker.get_price_summary.side_effect = [
        ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={"symbol": "005930", "open": 70000, "current": 77000, "change_rate": 10.0}
        ),
        ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={"symbol": "000660", "open": 100000, "current": 105000, "change_rate": 5.0}
        )
    ]
    broker.get_name_by_code = AsyncMock(side_effect=lambda code: f"종목{code}")

    # 2. Mock backtest price lookup
    async def mock_backtest_lookup(code, summary, minutes):
        return {
            "005930": 80000,   # 상승률 3.9%
            "000660": 106000   # 상승률 0.95%
        }[code]

    # 3. 전략 생성 (backtest 모드)
    strategy = MomentumStrategy(
        broker=broker,
        min_change_rate=10.0,
        min_follow_through=3.0,
        min_follow_through_time=10,  # 10분 후 상승률 기준으로 판단
        mode="backtest",
        backtest_lookup=mock_backtest_lookup
    )

    executor = StrategyExecutor(strategy=strategy)

    # 4. 실행
    result = await executor.execute(["005930", "000660"])

    # 5. 검증
    assert result["follow_through"] == [{"code": "005930", "name": "종목005930"}]
    assert result["not_follow_through"] == [{"code": "000660", "name": "종목000660"}]


@pytest.mark.asyncio
async def test_strategy_executor_backtest_mode_without_lookup_raises():
    # Mock Quotations 객체 생성
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
