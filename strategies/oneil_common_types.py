# strategies/oneil/common_types.py
from dataclasses import dataclass
import os
from strategies.base_strategy_config import BaseStrategyConfig

@dataclass
class OneilUniverseConfig(BaseStrategyConfig):
    """오닐 유니버스(종목 발굴) 관련 설정."""
    # 유니버스 필터
    min_avg_trading_value_5d: int = 10_000_000_000  # 5일 평균 거래대금 100억 원
    near_52w_high_pct: float = 20.0                  # 52주 최고가 대비 20% 이내
    max_watchlist: int = 60                           # 최대 감시 종목 수

    # 워치리스트 갱신 시각 (장 시작 후 경과 분)
    watchlist_refresh_minutes: tuple = (10, 30, 60, 90, 60*3, 60*5)

    # 스퀴즈 조건
    bb_period: int = 20
    bb_std_dev: float = 2.0
    squeeze_tolerance: float = 1.2  # BB 폭이 20일 최소폭의 1.2배 이내

    # 마켓 타이밍
    kosdaq_etf_code: str = "229200"   # KODEX 코스닥150
    kospi_etf_code: str = "069500"    # KODEX 200
    market_ma_period: int = 20
    market_ma_rising_days: int = 3

    # V2 스코어링
    rs_period_days: int = 63
    rs_top_percentile: float = 10.0
    rs_score_points: float = 30.0
    profit_growth_threshold_pct: float = 25.0
    profit_growth_score_points: float = 20.0
    api_chunk_size: int = 10

    # Pool A/B 설정
    pool_a_file: str = os.path.join("data", "osb_pool_a.json")
    pool_a_size_per_market: int = 15
    pool_a_market_cap_min: int = 200_000_000_000
    pool_a_market_cap_max: int = 2_000_000_000_000
    pool_b_size: int = 30
    
    # 돌파 기준 기간 (데이터 수집용)
    high_breakout_period: int = 20


@dataclass
class OneilBreakoutConfig(BaseStrategyConfig):
    """오닐 돌파 매매 전략(Strategy B) 설정."""
    # 매수 조건
    volume_breakout_multiplier: float = 1.5       # 20일 평균 거래량의 150%
    program_net_buy_min: int = 0                  # pgtr_ntby_qty > 0
    program_to_trade_value_pct: float = 10.0      # (프로그램순매수금/거래대금) >= 10%
    program_to_market_cap_pct: float = 0.5        # (프로그램순매수금/시총) >= 0.5%

    # 매도 조건
    stop_loss_pct: float = -5.0
    trailing_stop_pct: float = 8.0
    time_stop_days: int = 5
    time_stop_box_range_pct: float = 2.0
    trend_exit_ma_period: int = 10

    # 자금 관리
    total_portfolio_krw: int = 10_000_000
    position_size_pct: float = 5.0
    min_qty: int = 1


@dataclass
class OSBWatchlistItem:
    """감시 종목 정보 (Universe Service -> Strategy 전달 객체)."""
    code: str
    name: str
    market: str             # "KOSPI" or "KOSDAQ"
    high_20d: int           # 20일 최고가 (돌파 기준)
    ma_20d: float           # 20일 이동평균
    ma_50d: float           # 50일 이동평균
    avg_vol_20d: float      # 20일 평균 거래량
    bb_width_min_20d: float # 최근 20일간 BB 밴드폭 최소값
    prev_bb_width: float    # 전일 BB 밴드폭
    w52_hgpr: int           # 52주 최고가
    avg_trading_value_5d: float  # 5일 평균 거래대금
    market_cap: int = 0         # 시가총액

    # 스코어링
    rs_return_3m: float = 0.0
    rs_score: float = 0.0
    profit_growth_score: float = 0.0
    total_score: float = 0.0


@dataclass
class OSBPositionState:
    """보유 포지션 추적 상태."""
    entry_price: int        # 진입가
    entry_date: str         # 진입일 (YYYYMMDD)
    peak_price: int         # 진입 후 최고가 (트레일링 스탑용)
    breakout_level: int     # 진입 시 20일 최고가