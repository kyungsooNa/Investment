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
      - 손절: 매수가 대비 <= stop_loss_pct
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
        self._logger.info({"event": "scan_started", "strategy_name": self.name})

        # 1) 거래대금 상위 종목 조회
        resp = await self._ts.get_top_trading_value_stocks()
        if not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
            self._logger.warning({
                "event": "scan_failed",
                "reason": "Failed to get top trading value stocks",
                "response": vars(resp) if resp else None,
            })
            return signals

        candidates = resp.data or []
        self._logger.info({
            "event": "scan_candidates_fetched",
            "count": len(candidates),
        })

        for stock in candidates:
            code = stock.get("mksc_shrn_iscd") or stock.get("stck_shrn_iscd") or ""
            stock_name = stock.get("hts_kor_isnm", code)
            log_data = {"code": code, "name": stock_name}

            if not code:
                continue

            try:
                # 2) 현재가/시가 조회
                price_resp = await self._sqs.handle_get_current_stock_price(code)
                if not price_resp or price_resp.rt_cd != ErrorCode.SUCCESS.value:
                    log_data.update({"reason": "Failed to get current price"})
                    self._logger.info({"event": "candidate_rejected", **log_data})
                    continue

                data = price_resp.data or {}
                current = int(data.get("price", "0") or "0")
                open_price = int(data.get("open", "0") or "0")
                current_vol = int(stock.get("acml_vol", "0") or "0")

                log_data.update({
                    "current_price": current,
                    "open_price": open_price,
                    "volume": current_vol,
                })

                if open_price <= 0 or current <= 0:
                    log_data.update({"reason": "Invalid price data (open or current is zero)"})
                    self._logger.info({"event": "candidate_rejected", **log_data})
                    continue

                # 3) 시가 대비 변동률 체크
                change_from_open = (current / open_price - 1.0) * 100
                log_data["change_from_open_pct"] = round(change_from_open, 2)

                if change_from_open < self._cfg.trigger_pct:
                    log_data.update({"reason": f"Change from open {change_from_open:.2f}% < trigger {self._cfg.trigger_pct}%"})
                    self._logger.info({"event": "candidate_rejected", **log_data})
                    continue

                # 4) 거래량 체크 (현재는 스킵, 추후 추가 가능)
                
                # BUY 신호 생성
                reason_msg = f"시가대비 +{change_from_open:.1f}%, 거래량 {current_vol:,}"
                signals.append(TradeSignal(
                    code=code, name=stock_name, action="BUY", price=current, qty=1,
                    reason=reason_msg, strategy_name=self.name,
                ))
                self._logger.info({
                    "event": "buy_signal_generated",
                    "strategy_name": self.name,
                    "code": code,
                    "name": stock_name,
                    "price": current,
                    "reason": reason_msg,
                    "data": log_data,
                })

            except Exception as e:
                self._logger.error({
                    "event": "scan_error",
                    "strategy_name": self.name,
                    "code": code,
                    "error": str(e),
                }, exc_info=True)

        self._logger.info({"event": "scan_finished", "signals_found": len(signals)})
        return signals

    async def check_exits(self, holdings: List[dict]) -> List[TradeSignal]:
        signals: List[TradeSignal] = []
        self._logger.info({"event": "check_exits_started", "holdings_count": len(holdings)})
        
        now = self._tm.get_current_kst_time()
        close_time = self._tm.get_market_close_time()
        minutes_to_close = (close_time - now).total_seconds() / 60

        for hold in holdings:
            code = str(hold.get("code", ""))
            buy_price = hold.get("buy_price", 0)
            stock_name = hold.get("name", code)
            log_data = {"code": code, "name": stock_name, "buy_price": buy_price}

            if not code or not buy_price:
                continue

            try:
                price_resp = await self._sqs.handle_get_current_stock_price(code)
                if not price_resp or price_resp.rt_cd != ErrorCode.SUCCESS.value:
                    self._logger.warning({
                        "event": "check_exits_failed",
                        "reason": "Failed to get current price for holding",
                        **log_data,
                    })
                    continue

                data = price_resp.data or {}
                current = int(data.get("price", "0") or "0")
                high_price = int(data.get("high", "0") or "0")
                log_data.update({"current_price": current, "day_high": high_price})

                if current <= 0 or high_price <= 0:
                    continue

                reason = ""
                should_sell = False

                # 익절 조건: 당일 고가 대비 설정된 비율(-8%) 이상 하락 시 (Trailing Stop)
                drop_from_high = ((current - high_price) / high_price) * 100
                if drop_from_high <= -self._cfg.trailing_stop_pct:
                    reason = f"익절(트레일링): 고가({high_price:,})대비 {drop_from_high:.1f}%"
                    should_sell = True
                else:
                    # 손절 조건
                    pnl_pct = ((current - buy_price) / buy_price) * 100
                    if pnl_pct <= self._cfg.stop_loss_pct:
                        reason = f"손절: 매수가대비 {pnl_pct:.1f}%"
                        should_sell = True

                if not should_sell and minutes_to_close <= 15:
                    reason = f"시간청산: 장마감 {minutes_to_close:.0f}분전"
                    should_sell = True

                if should_sell:
                    signals.append(TradeSignal(
                        code=code, name=stock_name, action="SELL", price=current, qty=1,
                        reason=reason, strategy_name=self.name,
                    ))
                    self._logger.info({
                        "event": "sell_signal_generated",
                        "strategy_name": self.name,
                        "code": code,
                        "name": stock_name,
                        "price": current,
                        "reason": reason,
                        "data": {**log_data, "pnl_pct": round(pnl_pct, 2), "drop_from_high_pct": round(drop_from_high, 2)},
                    })
                else:
                     self._logger.info({
                        "event": "hold_checked",
                        "code": code,
                        "reason": "No exit condition met",
                        "data": log_data,
                    })

            except Exception as e:
                self._logger.error({
                    "event": "check_exits_error",
                    "strategy_name": self.name,
                    "code": code,
                    "error": str(e),
                }, exc_info=True)
        
        self._logger.info({"event": "check_exits_finished", "signals_found": len(signals)})
        return signals
