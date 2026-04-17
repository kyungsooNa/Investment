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

    # 워치리스트 갱신 시각 (장 시작 후 경과 분)
    watchlist_refresh_minutes: tuple = (10, 30, 60, 90, 60*3, 60*5)

    # 스퀴즈 조건
    bb_period: int = 20
    multiplier: float = 2.0 # 표준편차의 배수
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
    rs_rating_min: int = 70         # RS Rating 최소 기준 (0=비활성, 70~80 권장)
    profit_growth_threshold_pct: float = 25.0
    profit_growth_score_points: float = 20.0
    api_chunk_size: int = 10

    # 전일기준우량주 / 당일급등주 설정
    premium_stocks_file: str = os.path.join("data", "premium_stocks.json")
    premium_stocks_kospi_size: int = 20
    premium_stocks_kosdaq_size: int = 40

    premium_stocks_cap_min: int = 200_000_000_000   # 2000억
    premium_stocks_cap_max: int = 20_000_000_000_000 # 20조
    daily_surge_size: int = 30

    max_watchlist: int = premium_stocks_kospi_size + premium_stocks_kosdaq_size + daily_surge_size # 최대 감시 종목 수

    # 돌파 기준 기간 (데이터 수집용)
    high_breakout_period: int = 20

    # 스마트 머니 (수급) 스코어링
    smart_money_lookback_days: int = 3            # 3일 누적 기준
    smart_money_to_mcap_pct: float = 0.5          # 시총의 0.5% 이상 매집 시
    smart_money_to_tv_pct: float = 10.0           # 또는 누적 거래대금의 10% 이상 매집 시
    smart_money_score_points: float = 15.0        # +15점 부여

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

    # [추가] 돌파 품질: 현재가가 당일 변동폭(고가-저가)의 상단 70% 위에 있어야 함
    osb_min_candle_relative_pos: float = 0.7 
    
    # [추가] 스마트 머니 유연화 기준
    sm_flexible_pg_ratio: float = 7.0           # 프로그램 비중이 약간 낮아도(7%)
    sm_flexible_execution_strength: float = 140.0 # 체결강도가 압도적(140%)이면 인정
    
    # [추가] 시가총액 대비 프로그램 매수 기본 허들 (중소형주 기준)
    program_to_market_cap_pct: float = 0.3

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
    rs_rating: int = 0              # 1~99 IBD/오닐 RS Rating (0=미계산)
    profit_growth_score: float = 0.0
    smart_money_score: float = 0.0
    total_score: float = 0.0

    # 미너비니 트렌드 템플릿
    ma_150d: float = 0.0            # 150일 이동평균 (Trend Template)
    ma_200d: float = 0.0            # 200일 이동평균 (Trend Template)
    w52_lwpr: int = 0               # 52주 최저가 (장중 저가 기준)
    minervini_stage: int = 0        # 1~4단계 (0=미계산)


@dataclass
class OSBPositionState:
    """보유 포지션 추적 상태."""
    entry_price: int        # 진입가
    entry_date: str         # 진입일 (YYYYMMDD)
    peak_price: int         # 진입 후 최고가 (트레일링 스탑용)
    breakout_level: int     # 진입 시 20일 최고가
    mfe_pct: float = 0.0   # Maximum Favorable Excursion (진입 후 최대 수익률 %)
    mae_pct: float = 0.0   # Maximum Adverse Excursion (진입 후 최대 손실률 %)


@dataclass
class OneilPocketPivotConfig(BaseStrategyConfig):
    """포켓 피봇 & BGU 전략(Strategy C) 설정."""
    # 공통 스마트 머니 필터
    program_to_trade_value_pct: float = 10.0      # PG순매수금/거래대금 >= 10%
    program_to_market_cap_pct: float = 0.3        # PG순매수금/시총 >= 0.3%
    execution_strength_min: float = 120.0         # 체결강도 >= 120%

    # Entry A: Pocket Pivot
    pp_ma_proximity_lower_pct: float = -2.0       # MA 대비 하한 (-2%)
    pp_ma_proximity_upper_pct: float = 4.0        # MA 대비 상한 (+4%)
    pp_down_day_lookback: int = 10                # 하락일 거래량 비교 기간 (일)

    # Entry B: BGU (Buyable Gap-Up)
    bgu_gap_pct: float = 4.0                      # 시가 갭 >= 4%
    bgu_volume_multiplier: float = 3.0            # 50일 평균거래량의 300%
    bgu_whipsaw_after_minutes: int = 10           # 장 시작 후 10분 경과 후 진입

    # 매도: 손절
    pp_stop_loss_below_ma_pct: float = -2.0       # PP: 지지MA 대비 -2% 이탈 시 손절

    # 매도: 7주 홀딩 룰
    holding_rule_days: int = 35                   # 7주 = 35거래일
    holding_rule_ma_period: int = 50              # 50일선 기준
    holding_profit_anchor_pct: float = 5.0        # +5% 이상 수익 시 holding_start_date 기록 (1회만)

    # 매도: 부분 익절
    partial_profit_trigger_pct: float = 15.0      # +15% 도달 시 50% 익절
    partial_sell_ratio: float = 0.5               # 50% 매도

    # 매도: 하드 스탑
    hard_stop_from_peak_pct: float = -10.0        # 고점 대비 -10% 하락 시 즉시 청산

    # 자금 관리
    total_portfolio_krw: int = 10_000_000
    position_size_pct: float = 5.0
    min_qty: int = 2                              # 부분 익절을 위해 최소 2주 매수

    # [추가] 1. 거래량 노이즈 제거 필터
    # 과거 음봉 최대 거래량의 몇 %를 넘어야 인정할 것인가 (예: 0.9 = 90%)
    pp_down_vol_threshold_ratio: float = 0.9 
    
    # [추가] 2. 스마트 머니 유연화 (상호 보완 조건)
    # 프로그램 비중이 낮더라도 체결강도가 이 수치 이상이면 통과
    sm_flexible_pg_ratio: float = 7.0
    sm_flexible_execution_strength: float = 140.0
    
    # [추가] 3. 캔들 품질 (몸통 위치)
    # 0.0(저가) ~ 1.0(고가) 사이에서 현재가가 최소 어디에 위치해야 하는가
    pp_min_candle_relative_pos: float = 0.5

@dataclass
class PPPositionState:
    """포켓 피봇 / BGU 포지션 추적 상태."""
    entry_type: str                     # "PP" or "BGU"
    entry_price: int                    # 진입가
    entry_date: str                     # 진입일 (YYYYMMDD)
    peak_price: int                     # 진입 후 최고가
    supporting_ma: str                  # PP전용: "10"/"20"/"50" (지지 MA 종류)
    gap_day_low: int                    # BGU전용: 갭업 당일 장중 저가
    partial_sold: bool = False          # deprecated (JSON 하위호환용)
    holding_start_date: str = ""        # 수익 안착일 (+5% 돌파 시 1회만 기록, 7주 룰 기산점)
    last_partial_sell_price: int = 0    # 마지막 부분익절 가격 (0=미실행, >0=기준가)
    mfe_pct: float = 0.0               # Maximum Favorable Excursion (진입 후 최대 수익률 %)
    mae_pct: float = 0.0               # Maximum Adverse Excursion (진입 후 최대 손실률 %)


@dataclass
class HTFConfig(BaseStrategyConfig):
    """하이 타이트 플래그 전략 설정."""
    # Phase 1: 깃대 (Pole)
    pole_lookback_days: int = 40             # 40거래일 스캔
    pole_min_surge_ratio: float = 1.90       # max(high)/min(low) >= 1.90

    # Phase 2: 깃발 (Flag)
    flag_min_days: int = 5                   # 최소 횡보 기간 (한국형: Short Stroke 대응)
    flag_max_days: int = 25                  # 최대 횡보 기간
    flag_max_drawdown_pct: float = 20.0      # 고점 대비 최대 하락폭 (종가 기준)
    flag_volume_shrink_ratio: float = 1.2    # 깃발 평균거래량 < 50일평균 * 1.2

    # Phase 3: 돌파 (Breakout)
    volume_breakout_multiplier: float = 2.0  # 예상거래량 >= 50일평균 * 200%
    execution_strength_min: float = 120.0    # 체결강도 >= 120%

    # Phase 4: 청산 (Exit)
    stop_loss_pct: float = -5.0              # 칼손절
    trailing_ma_period: int = 10             # 10일 MA 트레일링스탑

    # 자금 관리
    total_portfolio_krw: int = 10_000_000
    position_size_pct: float = 5.0
    min_qty: int = 1


@dataclass
class HTFPositionState:
    """HTF 포지션 추적 상태."""
    entry_price: int         # 진입가
    entry_date: str          # 진입일 (YYYYMMDD)
    peak_price: int          # 진입 후 최고가
    pole_high: int           # 깃대 최고점 (돌파 기준가)
    mfe_pct: float = 0.0    # Maximum Favorable Excursion (진입 후 최대 수익률 %)
    mae_pct: float = 0.0    # Maximum Adverse Excursion (진입 후 최대 손실률 %)