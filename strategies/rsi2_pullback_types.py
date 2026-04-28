# strategies/rsi2_pullback_types.py
from dataclasses import dataclass
from strategies.base_strategy_config import BaseStrategyConfig


@dataclass
class RSI2PullbackConfig(BaseStrategyConfig):
    """래리 코너스 RSI(2) 눌림목 전략 설정."""
    # Phase 1: Setup
    rsi_period: int = 2                    # RSI 기간 (코너스 원안 고정)
    rsi_threshold: float = 10.0            # 진입 허용 RSI 상한
    require_minervini_stage2: bool = True  # OSBWatchlistItem.minervini_stage == 2 강제

    # Phase 2: Trigger
    entry_cutoff_hour: int = 15            # 종가 베팅 진입 시작 시각 (시)
    entry_cutoff_minute: int = 10          # 종가 베팅 진입 시작 시각 (분)
    risk_off_position_ratio: float = 0.5   # 지수 마켓 타이밍 🔴일 때 비중 (0.0~1.0)

    # Phase 3: Exit
    take_profit_ma_period: int = 5         # 익절 기준 이동평균 기간
    hard_stop_pct: float = -5.0            # 가격 기준 손절 (진입가 대비 %)
    trend_break_ma_period: int = 200       # 추세 붕괴 기준 이동평균 기간

    # 자금 관리
    total_portfolio_krw: int = 10_000_000
    position_size_pct: float = 5.0
    min_qty: int = 1
    cooldown_days: int = 2                 # 청산 후 동일 종목 재진입 차단 일수


@dataclass
class RSI2PositionState:
    """RSI(2) 눌림목 포지션 추적 상태."""
    entry_price: int                       # 진입가
    entry_date: str                        # 진입일 (YYYYMMDD)
    entry_rsi: float                       # 진입 시점 RSI(2) 값 (참고)
    risk_off_entry: bool = False           # True면 risk_off_position_ratio로 축소 진입
