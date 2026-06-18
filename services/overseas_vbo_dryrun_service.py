# services/overseas_vbo_dryrun_service.py
"""해외 VBO dry-run 신호 서비스 (Phase 3).

후보(OverseasCandidateService) → 일봉(StockQueryService, Phase 1-1 어댑터)
 → VBO 일봉 진입 규칙(Phase 2) → shadow 저널 기록.

**주문 경로 없음.** order_execution 의존을 갖지 않으며, "만약 진입했다면" 신호만
shadow 저널에 남긴다. 해외 주문은 실전 TR(모의 없음)만 존재하므로, 라이브 검증
전 단계에서 실주문이 절대 발생하지 않도록 구조적으로 차단한다.

라이브 VBO(분봉/웹소켓 의존)는 해외에서 재생 불가하므로, 본 서비스는 EOD 성격의
일봉 기반 dry-run 신호만 산출한다. 실제 스케줄러/factory 등록은 별도 단계.
"""
import logging
from typing import Any, Dict, List, Optional

from common.overseas_types import OverseasExchange
from common.types import ErrorCode


class OverseasVBODryRunService:
    STRATEGY_NAME = "LarryWilliamsVBO_overseas"
    SIGNAL_SOURCE = "overseas_dryrun"

    def __init__(
        self,
        candidate_service,
        stock_query_service,
        shadow_journal=None,
        logger: Optional[logging.Logger] = None,
        *,
        k_value: float = 0.5,
        stop_loss_pct: float = -3.0,
        exchange: OverseasExchange = OverseasExchange.NASD,
        position_sizing_service=None,
    ):
        self._candidate_service = candidate_service
        self._sqs = stock_query_service
        self._journal = shadow_journal
        self._logger = logger if logger else logging.getLogger(__name__)
        self._k = k_value
        self._stop_loss_pct = stop_loss_pct
        self._default_exchange = exchange
        # 고정 USD 슬롯 사이징(순수 계산, 주문 경로 없음). 미주입 시 qty 미산출.
        self._sizing_service = position_sizing_service

    async def scan_dry_run(
        self,
        exchange: Optional[OverseasExchange] = None,
        *,
        top_n: Optional[int] = None,
        min_avg_trading_value: Optional[float] = None,
        record: bool = True,
    ) -> List[Dict[str, Any]]:
        """후보를 평가해 BUY would-be 신호를 반환하고(선택) shadow 저널에 기록한다."""
        ex = exchange or self._default_exchange
        candidates = await self._candidate_service.get_candidates(
            ex, top_n=top_n, min_avg_trading_value=min_avg_trading_value
        )

        signals: List[Dict[str, Any]] = []
        for cand in candidates or []:
            code = cand.get("code")
            if not code:
                continue
            try:
                resp = await self._sqs.get_recent_daily_ohlcv(code, limit=3, exchange=ex)
            except Exception as e:
                self._logger.warning({"event": "overseas_dryrun_ohlcv_error", "code": code, "error": str(e)})
                continue
            if not resp or resp.rt_cd != ErrorCode.SUCCESS.value or not resp.data:
                continue

            sig = self._evaluate(code, resp.data)
            if not sig:
                continue
            if self._sizing_service is not None:
                sizing = self._sizing_service.size(limit_price_usd=sig["entry_price"])
                sig["qty"] = sizing.get("qty")
                sig["notional_usd"] = sizing.get("notional_usd")
            signals.append(sig)
            if record and self._journal is not None:
                self._journal.record(
                    strategy_name=self.STRATEGY_NAME,
                    code=code,
                    signal=sig,
                    snapshot={"exchange": ex.value, "avg_trading_value": cand.get("avg_trading_value")},
                    signal_source=self.SIGNAL_SOURCE,
                )
        self._logger.info({"event": "overseas_dryrun_scan", "exchange": ex.value,
                           "candidates": len(candidates or []), "signals": len(signals)})
        return signals

    def _evaluate(self, code: str, rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """최근 일봉(오름차순)에서 VBO 일봉 진입 규칙으로 BUY 신호 판정.

        rows[-2]=전일, rows[-1]=당일(가장 최근 완성봉).
        target = 당일시가 + K×전일Range, 당일고 >= target → BUY.
        """
        if not rows or len(rows) < 2:
            return None
        prev, cur = rows[-2], rows[-1]
        prev_range = self._f(prev.get("high")) - self._f(prev.get("low"))
        if prev_range <= 0:
            return None
        open_ = self._f(cur.get("open"))
        high = self._f(cur.get("high"))
        if open_ <= 0:
            return None
        target = open_ + self._k * prev_range
        if high < target:
            return None
        entry = target
        stop = entry * (1 + self._stop_loss_pct / 100.0)
        # 당일 same-day exit(Phase 2 백테스트 모델과 동일): 당일저 <= 손절가면 손절가,
        # 아니면 당일 종가(eod) 청산. 주문 경로 없는 would-be 청산 결과만 동봉한다.
        low = self._f(cur.get("low"))
        close = self._f(cur.get("close"))
        if low <= stop:
            exit_price, exit_reason = stop, "stop"
        else:
            exit_price, exit_reason = close, "eod"
        realized_pct = (exit_price / entry - 1) * 100.0 if entry > 0 else 0.0
        return {
            "code": code,
            "action": "BUY",
            "date": cur.get("date"),
            "entry_price": entry,
            "target": target,
            "stop_price": stop,
            "prev_range": prev_range,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "realized_pct": realized_pct,
            "reason": "vbo_daily_breakout",
        }

    @staticmethod
    def _f(x) -> float:
        try:
            return float(x or 0)
        except (TypeError, ValueError):
            return 0.0
