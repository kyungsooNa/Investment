# interfaces/live_strategy.py
from abc import ABC, abstractmethod
from typing import List, Optional
from common.types import TradeSignal


class LiveStrategy(ABC):
    """라이브 모드 전략의 추상 인터페이스.

    모든 라이브 전략은 scan()과 check_exits()를 구현해야 한다.
    StrategyScheduler가 장중 주기적으로 이 메서드들을 호출한다.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """전략 고유 이름 (VirtualTradeRepository strategy 컬럼 값)."""
        ...

    @abstractmethod
    async def scan(self) -> List[TradeSignal]:
        """시장을 스캔하여 매수 후보를 찾고 BUY TradeSignal 리스트를 반환한다."""
        ...

    @abstractmethod
    async def check_exits(self, holdings: List[dict]) -> List[TradeSignal]:
        """보유 종목의 청산 조건을 확인하고 SELL TradeSignal 리스트를 반환한다.

        Args:
            holdings: VirtualTradeRepository.get_holds_by_strategy()의 반환값.
                      각 dict는 strategy, code, buy_date, buy_price, status 키를 포함.
        """
        ...

    async def evaluate_single(self, code: str, snapshot: dict) -> Optional[TradeSignal]:
        """이벤트 기반 단일 종목 fast-path 평가 (P2 2-4).

        StrategyEventRouter 가 실시간 체결 tick 도착 시 호출한다. 기본 구현은 None
        (= 이벤트 평가 미지원). 적용 전략에서만 오버라이드한다.

        호출자(라우터)는 None 결과는 무시한다. 결과로 TradeSignal 을 돌려주면
        호출자 정책(shadow 기록 / 실 주문)에 따라 처리된다.
        """
        return None

    def current_candidate_codes(self) -> List[str]:
        """이벤트 라우터 구독 대상 종목 목록 (P2 2-4).

        StrategyScheduler 가 scan() 직후 호출하여 라우터 구독을 갱신한다.
        기본 구현은 빈 리스트 (= 라우터 구독 비활성). 적용 전략에서만 오버라이드한다.
        """
        return []
