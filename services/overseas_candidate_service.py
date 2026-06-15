# services/overseas_candidate_service.py
"""해외 후보 심볼 소스 (Phase 1-3).

국내 랭킹 API(get_top_trading_value_stocks 등)는 해외에 없으므로,
OverseasStockCodeRepository(전 심볼) + 일봉 거래대금 필터로 watchlist 를 산출한다.
일봉은 StockQueryService.get_recent_daily_ohlcv(exchange=해외) — Phase 1-1 어댑터 경유.

배선(VBO 주입)은 Phase 3. 본 서비스는 후보 리스트 산출만 담당한다.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

from common.overseas_types import OverseasExchange
from common.types import ErrorCode


class OverseasCandidateService:
    def __init__(
        self,
        overseas_stock_code_repository,
        stock_query_service,
        logger: Optional[logging.Logger] = None,
        *,
        lookback_days: int = 5,
        min_avg_trading_value: float = 10_000_000.0,
        top_n: int = 50,
        max_universe: int = 300,
        concurrency: int = 10,
    ):
        self._repo = overseas_stock_code_repository
        self._sqs = stock_query_service
        self._logger = logger if logger else logging.getLogger(__name__)
        self._lookback_days = lookback_days
        self._min_avg_trading_value = min_avg_trading_value
        self._top_n = top_n
        self._max_universe = max_universe
        self._concurrency = concurrency

    async def get_candidates(
        self,
        exchange: OverseasExchange = OverseasExchange.NASD,
        *,
        symbols: Optional[List[str]] = None,
        min_avg_trading_value: Optional[float] = None,
        top_n: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """거래대금 필터를 통과한 해외 후보를 내림차순으로 반환한다.

        반환: [{"code","name","exchange","avg_trading_value"}], avg_trading_value 내림차순.
        """
        min_tv = self._min_avg_trading_value if min_avg_trading_value is None else min_avg_trading_value
        cap = self._top_n if top_n is None else top_n

        meta = self._resolve_universe(exchange, symbols)
        if not meta:
            return []

        sem = asyncio.Semaphore(self._concurrency)

        async def _score(entry: Dict[str, str]) -> Optional[Dict[str, Any]]:
            async with sem:
                avg_tv = await self._avg_trading_value(entry["code"], exchange)
            if avg_tv is None or avg_tv < min_tv:
                return None
            return {
                "code": entry["code"],
                "name": entry.get("name", entry["code"]),
                "exchange": exchange.value,
                "avg_trading_value": avg_tv,
            }

        scored = await asyncio.gather(*[_score(e) for e in meta], return_exceptions=True)

        candidates: List[Dict[str, Any]] = []
        for r in scored:
            if isinstance(r, Exception):
                self._logger.warning({"event": "overseas_candidate_error", "error": str(r)})
                continue
            if r:
                candidates.append(r)

        candidates.sort(key=lambda c: c["avg_trading_value"], reverse=True)
        return candidates[:cap] if cap else candidates

    def _resolve_universe(
        self, exchange: OverseasExchange, symbols: Optional[List[str]]
    ) -> List[Dict[str, str]]:
        """평가 대상 (code,name) 메타 리스트를 결정한다."""
        if symbols:
            return [{"code": str(s).upper(), "name": str(s).upper()} for s in symbols]

        try:
            all_symbols = self._repo.all_symbols() or []
        except Exception as e:
            self._logger.warning({"event": "overseas_universe_load_failed", "error": str(e)})
            return []

        ex = exchange.value.upper()
        result: List[Dict[str, str]] = []
        for item in all_symbols:
            if str(item.get("e", "")).upper() != ex:
                continue
            result.append({"code": str(item.get("s", "")).upper(), "name": item.get("n", "")})
            if len(result) >= self._max_universe:
                break
        return result

    async def _avg_trading_value(self, symbol: str, exchange: OverseasExchange) -> Optional[float]:
        """최근 lookback_days 일봉의 평균 거래대금(close*volume)을 USD로 반환. 실패 시 None."""
        try:
            resp = await self._sqs.get_recent_daily_ohlcv(
                symbol, limit=self._lookback_days, exchange=exchange
            )
        except Exception as e:
            self._logger.warning({"event": "overseas_candidate_ohlcv_error", "code": symbol, "error": str(e)})
            return None
        if not resp or resp.rt_cd != ErrorCode.SUCCESS.value or not resp.data:
            return None
        values = []
        for row in resp.data:
            close = float(row.get("close") or 0)
            volume = float(row.get("volume") or 0)
            if close > 0 and volume > 0:
                values.append(close * volume)
        if not values:
            return None
        return sum(values) / len(values)
