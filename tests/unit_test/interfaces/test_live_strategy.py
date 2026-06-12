import pytest
from typing import List
from unittest.mock import MagicMock
from interfaces.live_strategy import LiveStrategy
from common.types import TradeSignal


def _make_concrete(name="테스트전략"):
    class ConcreteStrategy(LiveStrategy):
        @property
        def name(self) -> str:
            return name

        async def scan(self) -> List[TradeSignal]:
            return []

        async def check_exits(self, holdings: List[dict]) -> List[TradeSignal]:
            return []

    return ConcreteStrategy()

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
    assert strategy.display_name == "ConcreteTestStrategy"
    assert await strategy.load_state() is None
    assert await strategy.scan() == []
    assert await strategy.check_exits([]) == []


@pytest.mark.asyncio
async def test_live_strategy_display_name_can_be_overridden():
    """
    TC: storage/config 용 strategy_id 와 UI/log 용 display_name 을 분리할 수 있어야 한다.
    """
    class ConcreteStrategy(LiveStrategy):
        @property
        def name(self) -> str:
            return "legacy-display"

        @property
        def strategy_id(self) -> str:
            return "stable_id"

        @property
        def display_name(self) -> str:
            return "표시명"

        async def scan(self) -> List[TradeSignal]:
            return []

        async def check_exits(self, holdings: List[dict]) -> List[TradeSignal]:
            return []

    strategy = ConcreteStrategy()
    assert strategy.strategy_id == "stable_id"
    assert strategy.display_name == "표시명"

@pytest.mark.asyncio
async def test_live_strategy_abstract_methods_execution():
    """
    TC: LiveStrategy 추상 클래스의 메서드 본문(...) 라인을 커버리지에 포함시키기 위한 테스트.
    일반적으로 추상 메서드는 호출되지 않지만, 커버리지 100% 달성을 위해 직접 호출합니다.
    """
    # 1. name 프로퍼티의 getter 실행
    # property 객체의 fget을 사용하여 self=None으로 호출 (본문 '...' 실행)
    assert LiveStrategy.name.fget(None) is None

    # 2. scan 비동기 메서드 실행
    await LiveStrategy.scan(None)

    # 3. check_exits 비동기 메서드 실행
    await LiveStrategy.check_exits(None, [])

# ── scheduler 연동 표면 (S-9 후속: private 속성 침투를 인터페이스로 승격) ──

def test_state_surface_defaults_are_safe_without_internal_attrs():
    """내부 관례 속성이 없는 전략: 빈 dict / no-op / False / None 기본값."""
    strategy = _make_concrete()

    assert strategy.position_state == {}
    strategy.persist_state()  # no-op, 예외 없음
    strategy.discard_bought_today("005930")  # no-op, 예외 없음
    assert strategy.exclude_code_for_today("005930", reason="r") is False
    assert strategy.strategy_logger is None


def test_state_surface_delegates_to_internal_convention_attrs():
    """기존 전략들의 내부 관례 속성(_position_state 등)으로 위임한다 (전략 무변경 호환)."""
    strategy = _make_concrete()

    strategy._position_state = {"005930": object()}
    assert strategy.position_state is strategy._position_state  # 동일 객체 (mutation 반영)

    strategy._save_state = MagicMock()
    strategy.persist_state()
    strategy._save_state.assert_called_once()

    strategy._bought_today = {"005930", "000020"}
    strategy.discard_bought_today("005930")
    assert strategy._bought_today == {"000020"}

    universe = MagicMock()
    strategy._universe = universe
    meta = {"order_msg": "차단"}
    assert strategy.exclude_code_for_today("000020", reason="rule", metadata=meta) is True
    universe.exclude_code_for_today.assert_called_once_with("000020", reason="rule", metadata=meta)

    strategy._logger = MagicMock()
    assert strategy.strategy_logger is strategy._logger


def test_position_state_returns_empty_dict_for_non_dict_attr():
    """_position_state 가 dict 가 아니면 빈 dict (스케줄러 방어 계약과 동일)."""
    strategy = _make_concrete()
    strategy._position_state = "broken"
    assert strategy.position_state == {}

    strategy._bought_today = ["not", "a", "set"]
    strategy.discard_bought_today("not")  # set 이 아니면 no-op, 예외 없음
    assert strategy._bought_today == ["not", "a", "set"]
