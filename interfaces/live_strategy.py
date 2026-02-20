# interfaces/live_strategy.py
from abc import ABC, abstractmethod
from typing import List
from common.types import TradeSignal


class LiveStrategy(ABC):
    """라이브 모드 전략의 추상 인터페이스.

    모든 라이브 전략은 scan()과 check_exits()를 구현해야 한다.
    StrategyScheduler가 장중 주기적으로 이 메서드들을 호출한다.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """전략 고유 이름 (VirtualTradeManager의 strategy 컬럼 값)."""
        ...

    @abstractmethod
    async def scan(self) -> List[TradeSignal]:
        """시장을 스캔하여 매수 후보를 찾고 BUY TradeSignal 리스트를 반환한다."""
        ...

    @abstractmethod
    async def check_exits(self, holdings: List[dict]) -> List[TradeSignal]:
        """보유 종목의 청산 조건을 확인하고 SELL TradeSignal 리스트를 반환한다.

        Args:
            holdings: VirtualTradeManager.get_holds_by_strategy()의 반환값.
                      각 dict는 strategy, code, buy_date, buy_price, status 키를 포함.
        """
        ...
