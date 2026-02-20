# strategies/program_buy_follow_strategy.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from interfaces.live_strategy import LiveStrategy
from common.types import TradeSignal, ErrorCode, ResStockFullInfoApiOutput
from services.trading_service import TradingService
from services.stock_query_service import StockQueryService
from core.time_manager import TimeManager


@dataclass
class ProgramBuyFollowConfig:
    """프로그램 매수 추종 전략 설정."""
    min_program_net_buy: int = 0        # 프로그램 순매수 최소 기준 (> 0)
    take_profit_pct: float = 5.0        # 매수가 대비 +5% 익절
    stop_loss_pct: float = -3.0         # 매수가 대비 -3% 손절


class ProgramBuyFollowStrategy(LiveStrategy):
    """거래대금 상위 + 프로그램 순매수 추종 전략.

    scan():
      1. 거래대금 상위 30종목 조회
      2. 각 종목의 pgtr_ntby_qty (프로그램 순매수 수량) 확인
      3. 양수인 종목을 내림차순 정렬, BUY 시그널 반환

    check_exits():
      - 익절: 매수가 대비 +take_profit_pct%
      - 손절: 매수가 대비 stop_loss_pct%
      - 프로그램 매도 전환: pgtr_ntby_qty < 0
      - 시간청산: 장 마감 15분 전
    """

    def __init__(
        self,
        trading_service: TradingService,
        stock_query_service: StockQueryService,
        time_manager: TimeManager,
        config: Optional[ProgramBuyFollowConfig] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self._ts = trading_service
        self._sqs = stock_query_service
        self._tm = time_manager
        self._cfg = config or ProgramBuyFollowConfig()
        self._logger = logger or logging.getLogger(__name__)

    @property
    def name(self) -> str:
        return "프로그램매수추종"

    async def scan(self) -> List[TradeSignal]:
        signals: List[TradeSignal] = []

        # 1) 거래대금 상위 종목 조회
        resp = await self._ts.get_top_trading_value_stocks()
        if not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
            self._logger.warning(f"[{self.name}] 거래대금 상위 조회 실패")
            return signals

        candidates = resp.data or []

        scored = []
        for stock in candidates:
            code = stock.get("mksc_shrn_iscd") or stock.get("stck_shrn_iscd") or ""
            if not code:
                continue

            try:
                # 2) 종목 상세 정보 조회 (pgtr_ntby_qty 포함)
                full_resp = await self._ts.get_current_stock_price(code)
                if not full_resp or full_resp.rt_cd != ErrorCode.SUCCESS.value:
                    continue

                output = self._extract_output(full_resp)
                if output is None:
                    continue

                pgtr_ntby = self._get_int_field(output, "pgtr_ntby_qty")
                if pgtr_ntby <= self._cfg.min_program_net_buy:
                    continue

                current = self._get_int_field(output, "stck_prpr")
                if current <= 0:
                    continue

                stock_name = stock.get("hts_kor_isnm", code)
                scored.append((pgtr_ntby, code, stock_name, current))

            except Exception as e:
                self._logger.warning(f"[{self.name}] {code} 스캔 오류: {e}")

        # 3) 프로그램 순매수 내림차순 정렬
        scored.sort(key=lambda x: x[0], reverse=True)

        for pgtr_ntby, code, stock_name, current in scored:
            signals.append(TradeSignal(
                code=code,
                name=stock_name,
                action="BUY",
                price=current,
                qty=1,
                reason=f"프로그램순매수 {pgtr_ntby:,}주, 거래대금상위",
                strategy_name=self.name,
            ))

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
                full_resp = await self._ts.get_current_stock_price(code)
                if not full_resp or full_resp.rt_cd != ErrorCode.SUCCESS.value:
                    continue

                output = self._extract_output(full_resp)
                if output is None:
                    continue

                current = self._get_int_field(output, "stck_prpr")
                pgtr_ntby = self._get_int_field(output, "pgtr_ntby_qty")

                if current <= 0:
                    continue

                pnl_pct = ((current - buy_price) / buy_price) * 100
                reason = ""
                should_sell = False

                if pnl_pct >= self._cfg.take_profit_pct:
                    reason = f"익절: 매수가대비 +{pnl_pct:.1f}%"
                    should_sell = True
                elif pnl_pct <= self._cfg.stop_loss_pct:
                    reason = f"손절: 매수가대비 {pnl_pct:.1f}%"
                    should_sell = True
                elif pgtr_ntby < 0:
                    reason = f"프로그램매도전환: 순매수 {pgtr_ntby:,}주"
                    should_sell = True
                elif minutes_to_close <= 15:
                    reason = f"시간청산: 장마감 {minutes_to_close:.0f}분전"
                    should_sell = True

                if should_sell:
                    stock_name = self._get_str_field(output, "bstp_kor_isnm") or code
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

    # ── 내부 유틸 ──

    @staticmethod
    def _extract_output(resp):
        """API 응답에서 output 객체(dict 또는 dataclass) 추출."""
        data = resp.data
        if isinstance(data, dict):
            return data.get("output")
        return data

    @staticmethod
    def _get_int_field(output, field_name: str) -> int:
        if isinstance(output, dict):
            val = output.get(field_name, "0")
        else:
            val = getattr(output, field_name, "0")
        try:
            return int(val or "0")
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _get_str_field(output, field_name: str) -> str:
        if isinstance(output, dict):
            return str(output.get(field_name, "") or "")
        return str(getattr(output, field_name, "") or "")
