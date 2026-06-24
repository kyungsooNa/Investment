# strategies/first_pullback_types.py
from dataclasses import dataclass
from typing import Optional
from strategies.base_strategy_config import BaseStrategyConfig


@dataclass
class FirstPullbackConfig(BaseStrategyConfig):
    """첫 눌림목(Holy Grail) 전략 설정."""
    # Phase 1: Setup (로켓 발사)
    surge_lookback_days: int = 20           # 급등 이력 조회 기간
    upper_limit_pct: float = 29.0           # 상한가 기준 (종가 +29%)
    rapid_surge_pct: float = 30.0           # 단기 급등 기준 (+30%)
    rapid_surge_min_days: int = 5           # 급등 최소 기간
    rapid_surge_max_days: int = 10          # 급등 최대 기간
    ma_period: int = 20                     # 이동평균선 기간
    ma_rising_days: int = 5                 # 20MA 우상향 연속 일수
    ma_rising_min_count: int = 4            # 20MA 우상향 최소 일수 (5일 중 4일 이상)
    
    # Phase 2: Pullback (건전한 숨 고르기)
    pullback_lower_pct: float = -1.0        # 20MA 대비 하한 (-1%)
    pullback_upper_pct: float = 3.0         # 20MA 대비 상한 (+3%)
    volume_dryup_ratio: float = 0.5         # 급등일 거래량 대비 50%
    volume_dryup_days: int = 3              # 거래량 고갈 비교 기간

    # Phase 3: Trigger (매수 방아쇠)
    execution_strength_min: float = 100.0   # 체결강도 >= 100%
    reversal_prev_close_floor_pct: float = -2.0   # 전일종가 대비 최소 위치 (-2%)
    reversal_min_relative_pos: float = 0.5         # 캔들 내 현재가 상대위치 최소값 (0.5=중간)

    # Phase 4: Exit (기계적 청산)
    hard_stop_loss_pct: float = -5.0        # 진입가 대비 하드스탑 (net 기준)
    stop_loss_below_ma_pct: float = -2.0    # 20MA -2% 이탈 시 손절
    ma_break_grace_minutes: int = 10        # 20MA 이탈 후 손절 유예 시간 (분)
    ma_break_eod_hour: int = 14             # 장마감 전 즉시손절 기준 시각 (시)
    ma_break_eod_minute: int = 50           # 장마감 전 즉시손절 기준 시각 (분)
    take_profit_pct: float = 15.0           # 진입가 대비 +15% 익절 상한
    take_profit_lower_pct: float = 10.0     # +10% 부터 익절 구간 시작
    partial_sell_ratio: float = 0.5         # 50% 분할 매도

    # 자금 관리
    total_portfolio_krw: int = 10_000_000
    position_size_pct: float = 5.0
    min_qty: int = 2                        # 분할 매도를 위해 최소 2주
    cooldown_days: int = 3                  # 손절 후 재진입 금지 기간 (일)


@dataclass
class FPPositionState:
    """첫 눌림목 포지션 추적 상태."""
    entry_price: int                    # 진입가
    entry_date: str                     # 진입일 (YYYYMMDD)
    peak_price: int                     # 진입 후 최고가
    surge_day_high: int                 # 급등기 고점 (익절 참조가)
    partial_sold: bool = False          # deprecated (JSON 하위호환용)
    last_partial_sell_price: int = 0    # 마지막 부분익절 가격 (0=미실행, >0=기준가)
    mfe_pct: float = 0.0               # Maximum Favorable Excursion (진입 후 최대 수익률 %)
    mae_pct: float = 0.0               # Maximum Adverse Excursion (진입 후 최대 손실률 %)
    ma_break_since_ts: Optional[str] = None  # 20MA 이탈 첫 감지 타임스탬프 (YYYYMMDDHHMMSS)
    breakeven_armed: bool = False       # 부분익절 후 본절스탑 활성화 플래그
