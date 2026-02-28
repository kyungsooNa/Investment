import pytest
from typing import List
from interfaces.live_strategy import LiveStrategy
from common.types import TradeSignal

def test_live_strategy_cannot_instantiate_incomplete_subclass():
    """
    TC: 추상 메서드(name, scan, check_exits)를 구현하지 않은 하위 클래스는
    인스턴스화할 수 없어야 함을 검증 (TypeError 발생).
    """
    class IncompleteStrategy(LiveStrategy):
        pass

    # 추상 메서드를 구현하지 않고 인스턴스화 시도 시 TypeError 발생 예상
    with pytest.raises(TypeError) as exc_info:
        IncompleteStrategy()
    
    error_msg = str(exc_info.value)
    # 에러 메시지에 구현되지 않은 추상 메서드들이 언급되는지 확인
    assert "Can't instantiate abstract class" in error_msg
    assert "name" in error_msg
    assert "scan" in error_msg
    assert "check_exits" in error_msg

@pytest.mark.asyncio
async def test_live_strategy_can_instantiate_complete_subclass():
    """
    TC: 모든 추상 메서드를 구현한 하위 클래스는 정상적으로 인스턴스화되어야 함을 검증.
    """
    class ConcreteStrategy(LiveStrategy):
        @property
        def name(self) -> str:
            return "ConcreteTestStrategy"

        async def scan(self) -> List[TradeSignal]:
            return []

        async def check_exits(self, holdings: List[dict]) -> List[TradeSignal]:
            return []

    strategy = ConcreteStrategy()
    assert isinstance(strategy, LiveStrategy)
    assert strategy.name == "ConcreteTestStrategy"
    assert await strategy.scan() == []
    assert await strategy.check_exits([]) == []