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
        """전략 표시명 (현재 VirtualTradeRepository strategy 컬럼 값, UI/로그용).

        한국어/표시 친화적인 문자열이 들어간다. 저장·식별 목적으로는
        `strategy_id` 를 사용하라. 두 값이 분리되기 전 단계라 기본
        구현은 `strategy_id` 가 `name` 으로 fallback 한다 (Phase 1).
        """
        ...

    @property
    def strategy_id(self) -> str:
        """안정적인 영문 식별자 (storage / config key / risk limit 용).

        외부 표면(저널 CSV, 설정 key, 알림 소스, risk_gate limit 키)에서
        `name` 을 식별자로 쓰던 코드를 점진적으로 이 값으로 옮긴다.
        오버라이드하지 않은 전략은 `name` 으로 fallback 한다 (Phase 1
        backward-compat). 활성 전략은 모두 명시적으로 override 해야 한다.
        """
        return self.name

    @property
    def display_name(self) -> str:
        """UI / log 에 노출할 표시명.

        기존 전략은 `name` 이 표시명 역할을 해 왔으므로 기본값은 `name` 이다.
        저장·정책 key 는 `strategy_id` 를 사용한다.
        """
        return self.name

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

    async def evaluate_exit_single(self, code: str, snapshot: dict, holding: dict) -> Optional[TradeSignal]:
        """이벤트 기반 단일 보유 종목 청산 fast-path 평가 (P2 2-4 exit shadow).

        StrategyEventRouter 가 보유 종목의 실시간 tick 도착 시 호출한다. 기본 구현은
        None (= 이벤트 청산 평가 미지원). 손절 등 latency 민감 청산을 snapshot 기반으로
        먼저 측정하려는 전략에서만 오버라이드한다.

        Args:
            code: 종목코드.
            snapshot: 라우터가 전달한 실시간 가격 snapshot (evaluate_single 과 동일 형식).
            holding: 해당 종목 보유 정보 (buy_price, qty, name 등; check_exits holdings 항목과 동일).
        """
        return None

    def current_candidate_codes(self) -> List[str]:
        """이벤트 라우터 구독 대상 종목 목록 (P2 2-4).

        StrategyScheduler 가 scan() 직후 호출하여 라우터 구독을 갱신한다.
        기본 구현은 빈 리스트 (= 라우터 구독 비활성). 적용 전략에서만 오버라이드한다.
        """
        return []

    async def load_state(self) -> None:
        """전략 state 명시 로드 hook.

        state 파일이 없는 전략은 기본 no-op 이다. state 를 가진 전략은 오버라이드해
        scheduler 시작 전 호출자가 await 할 수 있게 한다.
        """
        return None

    async def save_state(self) -> None:
        """전략 state 명시 저장 hook (load_state 와 대칭).

        state 파일이 없는 전략은 기본 no-op 이다. state 를 가진 전략은 오버라이드해
        scheduler 종료/스냅샷 시 호출자가 await 할 수 있게 한다. 백그라운드 schedule_save
        는 `StrategyStateIO.schedule_save()` 로 별도 처리되며, 본 hook 은 호출자가
        명시적으로 await 가능한 표면을 제공한다.
        """
        return None
