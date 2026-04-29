# strategies/larry_williams_cb_types.py
from dataclasses import dataclass
from strategies.base_strategy_config import BaseStrategyConfig


@dataclass
class LarryWilliamsCBConfig(BaseStrategyConfig):
    """래리 윌리엄스 / 펜볼드 돈천 채널 돌파 전략 설정."""
    # Phase 1: Setup
    rs_rating_min: int = 80                 # RS Rating 최소값 (Pool A 내 추가 필터)
    adx_period: int = 14                    # ADX 계산 기간
    adx_threshold: float = 25.0            # ADX 최소값 (25 이상 = 유효 추세)
    adx_slope_lookback: int = 3            # ADX 우상향 판단 기간 (봉 수)

    # Phase 2: Trigger
    channel_high_period: int = 20          # 채널 상단 기간 (20일 고가)
    volume_multiplier: float = 1.5         # 당일 거래량 / 20일 평균 거래량 최소 배수
    entry_cutoff_hour: int = 15            # 종가 베팅 진입 시작 시각 (시)
    entry_cutoff_minute: int = 10          # 종가 베팅 진입 시작 시각 (분)

    # Phase 3: Exit
    channel_low_period: int = 10           # trailing stop 기준 채널 하단 기간 (10일 저가)
    hard_stop_pct: float = -7.0            # 진입가 대비 칼손절 하한 (%)

    # 자금 관리 (Fixed Fractional)
    cooldown_days: int = 2                 # 청산 후 동일 종목 재진입 차단 일수


@dataclass
class LarryWilliamsCBPositionState:
    """돈천 채널 돌파 포지션 추적 상태.

    channel_low_10d: 보유 기간 중 매 장마감 후 재계산하여 상향 갱신.
                     재시작 후 trailing stop 복원을 위해 JSON 파일에 영속.
    hard_stop_price: 진입 직후 확정, 이후 변경 없음.
    """
    entry_price: int                       # 진입가 (원)
    entry_date: str                        # 진입일 (YYYYMMDD)
    hard_stop_price: int                   # 칼손절가 = max(20일 채널 하단, 진입가 × 0.93)
    channel_low_10d: int                   # 현재 trailing stop 기준 (10일 채널 하단)
    entry_adx: float = 0.0                 # 진입 시점 ADX 값 (참고용)
