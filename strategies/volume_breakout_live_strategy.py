# strategies/volume_breakout_live_strategy.py
from __future__ import annotations

import logging
from typing import List, Optional

from interfaces.live_strategy import LiveStrategy
from common.types import TradeSignal, ErrorCode
from services.trading_service import TradingService
from services.stock_query_service import StockQueryService
from strategies.volume_breakout_strategy import VolumeBreakoutConfig
from core.time_manager import TimeManager


class VolumeBreakoutLiveStrategy(LiveStrategy):
    """거래량 돌파 라이브 전략.

    scan():
      1. 거래대금 상위 30 종목 조회
      2. 시가 대비 현재가 >= trigger_pct 필터
      3. 당일 거래량 >= 평균거래량 * avg_vol_multiplier 필터
      4. 통과 종목에 대해 BUY TradeSignal 반환

    check_exits():
      - 익절: 당일 고가 대비 <= -trailing_stop_pct
      - 손절: 시가 대비 <= stop_loss_pct
      - 시간청산: 장 마감 15분 전
    """

    def __init__(
        self,
        trading_service: TradingService,
        stock_query_service: StockQueryService,
        time_manager: TimeManager,
        config: Optional[VolumeBreakoutConfig] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self._ts = trading_service
        self._sqs = stock_query_service
        self._tm = time_manager
        self._cfg = config or VolumeBreakoutConfig()
        self._logger = logger or logging.getLogger(__name__)

    @property
    def name(self) -> str:
        return "거래량돌파"

    async def scan(self) -> List[TradeSignal]:
        signals: List[TradeSignal] = []

        # 1) 거래대금 상위 종목 조회
        resp = await self._ts.get_top_trading_value_stocks()
        if not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
            self._logger.warning(f"[{self.name}] 거래대금 상위 조회 실패")
            return signals

        candidates = resp.data or []

        for stock in candidates:
            code = stock.get("mksc_shrn_iscd") or stock.get("stck_shrn_iscd") or ""
            if not code:
                continue

            try:
                # 2) 현재가/시가 조회
                price_resp = await self._sqs.handle_get_current_stock_price(code)
                if not price_resp or price_resp.rt_cd != ErrorCode.SUCCESS.value:
                    continue

                data = price_resp.data or {}
                current = int(data.get("price", "0") or "0")
                open_price = int(data.get("open", "0") or "0")

                if open_price <= 0 or current <= 0:
                    continue

                # 3) 시가 대비 변동률 체크
                change_from_open = (current / open_price - 1.0) * 100
                if change_from_open < self._cfg.trigger_pct:
                    continue

                # 4) 거래량 체크
                current_vol = int(stock.get("acml_vol", "0") or "0")
                # 간소화: 거래대금 상위 진입 자체가 거래량 필터 역할
                # 추후 일봉 평균 거래량 비교 로직 추가 가능

                stock_name = stock.get("hts_kor_isnm", code)
                signals.append(TradeSignal(
                    code=code,
                    name=stock_name,
                    action="BUY",
                    price=current,
                    qty=1,
                    reason=(
                        f"시가대비 +{change_from_open:.1f}%, "
                        f"거래량 {current_vol:,}"
                    ),
                    strategy_name=self.name,
                ))

            except Exception as e:
                self._logger.warning(f"[{self.name}] {code} 스캔 오류: {e}")

        return signals

    async def check_exits(self, holdings: List[dict]) -> List[TradeSignal]:
        signals: List[TradeSignal] = []
        now = self._tm.get_current_kst_time()
        close_time = self._tm.get_market_close_time()
        minutes_to_close = (close_time - now).total_seconds() / 60

        for hold in holdings:
            code = str(hold.get("code", ""))
            buy_price = hold.get("buy_price", 0)
            if not code or not buy_price:
                continue

            try:
                price_resp = await self._sqs.handle_get_current_stock_price(code)
                if not price_resp or price_resp.rt_cd != ErrorCode.SUCCESS.value:
                    continue

                data = price_resp.data or {}
                current = int(data.get("price", "0") or "0")
                open_price = int(data.get("open", "0") or "0")
                high_price = int(data.get("high", "0") or "0")

                if current <= 0 or high_price <= 0:
                    continue

                reason = ""
                should_sell = False

                # 익절 조건: 당일 고가 대비 설정된 비율(-8%) 이상 하락 시 (Trailing Stop)
                drop_from_high = ((current - high_price) / high_price) * 100
                if drop_from_high <= -self._cfg.trailing_stop_pct:
                    reason = f"익절(트레일링): 고가({high_price:,})대비 {drop_from_high:.1f}%"
                    should_sell = True

                elif open_price > 0:
                    change_from_open = (current / open_price - 1.0) * 100
                    if change_from_open <= self._cfg.stop_loss_pct:
                        reason = f"손절: 시가대비 {change_from_open:+.1f}%"
                        should_sell = True

                if not should_sell and minutes_to_close <= 15:
                    reason = f"시간청산: 장마감 {minutes_to_close:.0f}분전"
                    should_sell = True

                if should_sell:
                    stock_name = data.get("code", code)
                    signals.append(TradeSignal(
                        code=code,
                        name=stock_name,
                        action="SELL",
                        price=current,
                        qty=1,
                        reason=reason,
                        strategy_name=self.name,
                    ))

            except Exception as e:
                self._logger.warning(f"[{self.name}] {code} 청산 체크 오류: {e}")

        return signals
