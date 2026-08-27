"""해외 장중 Buyable Gap-Up (라이브 paper 경로).

갭 비율은 **당일 시가와 전일 종가**로 정해지므로 개장 직후 확정된다 — 세션 준비
시점에 한 번 걸러 감시 목록을 좁힌다. 장중에 남는 판정은 두 가지뿐이다:
양봉 유지(현재가 >= 당일 시가)와 거래량(환산 누적거래량 >= 50일 평균 × 3).

손절가는 dry-run 과 동일하게 당일 저가를 쓰되, 장중에는 **관측된 세션 저가**다
(폴링 간격 사이 저점은 관측되지 않는다 — 해외는 분봉이 없다).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from services.overseas_intraday_strategy_base import OverseasIntradayStrategyBase


@dataclass
class OverseasIntradayBuyableGapUpConfig:
    bgu_gap_pct: float = 4.0
    bgu_volume_multiplier: float = 3.0
    bgu_avg_volume_period: int = 50
    bgu_min_avg_volume_count: int = 20
    hard_stop_pct: float = -7.0


class OverseasIntradayBuyableGapUpService(OverseasIntradayStrategyBase):
    STRATEGY_NAME = "O'NeilBGU_overseas_intraday"
    EVENT_PREFIX = "overseas_intraday_bgu"

    def __init__(self, *args, config: Optional[OverseasIntradayBuyableGapUpConfig] = None,
                 **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._cfg = config or OverseasIntradayBuyableGapUpConfig()

    def _build_setup(self, code, history, today_bar, trade_date) -> Optional[Dict[str, Any]]:
        # 당일 봉이 없으면 시가를 알 수 없다 — 추정 시가로 갭을 계산하지 않는다(fail-closed).
        if not today_bar or not history:
            return None
        open_ = self._f(today_bar.get("open"))
        prev_close = self._f(history[-1].get("close"))
        if open_ <= 0 or prev_close <= 0:
            return None

        gap_pct = (open_ - prev_close) / prev_close * 100.0
        if gap_pct < self._cfg.bgu_gap_pct:
            return None

        period = min(self._cfg.bgu_avg_volume_period, len(history))
        volumes = [self._f(r.get("volume")) for r in history[-period:] if self._f(r.get("volume")) > 0]
        if len(volumes) < self._cfg.bgu_min_avg_volume_count:
            return None

        return {
            "open": open_,
            "prev_close": prev_close,
            "gap_pct": gap_pct,
            "avg_volume": sum(volumes) / len(volumes),
        }

    def _should_enter(self, setup, price, volume, now) -> bool:
        if price < setup["open"]:  # 갭을 메우는 음봉이면 진입하지 않는다
            return False
        return self._volume_ok(setup, volume, now, multiplier=self._cfg.bgu_volume_multiplier)

    def _stop_price(self, setup, price) -> float:
        session_low = setup.get("session_low")
        pct_stop = price * (1 + self._cfg.hard_stop_pct / 100.0)
        if session_low is None or session_low <= 0:
            return pct_stop
        return max(session_low, pct_stop)

    def _entry_reason(self) -> str:
        return "bgu_intraday_gap_up"

    def _signal_extras(self, setup, price) -> Dict[str, Any]:
        return {"gap_pct": setup["gap_pct"], "open": setup["open"]}
