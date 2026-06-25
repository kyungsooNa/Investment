# strategies/inverse_etf_regime_types.py
from dataclasses import dataclass
from strategies.base_strategy_config import BaseStrategyConfig


@dataclass
class InverseEtfRegimeConfig(BaseStrategyConfig):
    """레짐 게이트 인버스 ETF 슬리브 설정 (R-2 비상관 엣지).

    KOSPI가 확인된 하락추세(bear)일 때만 -1x 인버스 ETF를 추세추종 매수해
    하락장에서 (-)베타 수익을 얻는다. long-only 7전략과 구조적으로 음의 상관.
    """
    # 종목 (KODEX 인버스, KOSPI200 일간 -1x). 곱버스(-2x)는 변동성 감쇠로 제외.
    inverse_etf_code: str = "114800"
    inverse_etf_name: str = "KODEX 인버스"

    # 레짐 게이트 — 어느 시장의 레짐으로 진입을 통제할지
    regime_market: str = "KOSPI"

    # 추세 확인 — 인버스 ETF 자체가 일봉 상승추세일 때만 진입(낙하 칼날/휩쏘 방지)
    trend_ma_period: int = 20

    # 청산
    hard_stop_pct: float = -5.0       # 진입가 대비 net 손절(%)
    trailing_stop_pct: float = -8.0   # 고점 대비 트레일링 스톱(%)

    # 자금 관리 (디버시파이어이므로 소형 슬롯)
    total_portfolio_krw: int = 10_000_000
    position_size_pct: float = 3.0
    min_qty: int = 1
    cooldown_days: int = 2             # 손절 후 재진입 차단 일수


@dataclass
class InverseEtfPositionState:
    """인버스 ETF 슬리브 포지션 추적 상태."""
    entry_price: int                   # 진입가
    entry_date: str                    # 진입일 (YYYYMMDD)
    peak_price: int                    # 보유 중 기록한 최고가 (트레일링 기준)
