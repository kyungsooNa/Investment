# services/overseas_candidate_service.py
"""해외 후보 심볼 소스 (Phase 1-3).

국내 랭킹 API(get_top_trading_value_stocks 등)는 해외에 없으므로,
OverseasStockCodeRepository(전 심볼) + 일봉 거래대금 필터로 watchlist 를 산출한다.
일봉은 StockQueryService.get_recent_daily_ohlcv(exchange=해외) — Phase 1-1 어댑터 경유.

배선(VBO 주입)은 Phase 3. 본 서비스는 후보 리스트 산출만 담당한다.
"""
import asyncio
import logging
import time
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
        cache_ttl_sec: float = 3600.0,
        monotonic=time.monotonic,
    ):
        self._repo = overseas_stock_code_repository
        self._sqs = stock_query_service
        self._logger = logger if logger else logging.getLogger(__name__)
        self._lookback_days = lookback_days
        self._min_avg_trading_value = min_avg_trading_value
        self._top_n = top_n
        self._max_universe = max_universe
        self._concurrency = concurrency
        # 유니버스 스캔(최대 300종목 일봉)은 호출자마다 반복되면 그대로 곱해진다.
        # 장중 전략 6종이 각자 prepare_session 을 돌리면 한 세션 준비에 스캔이 11회
        # 나가 dailyprice 가 7,000콜을 넘고 절반이 rate limit 으로 거부됐다(2026-09-10).
        # 점수는 최근 5일 평균 거래대금이라 한 시간 안에서는 사실상 바뀌지 않으므로,
        # 점수 매긴 유니버스를 재사용하고 호출자별 필터만 뒤에 적용한다.
        self._cache_ttl_sec = cache_ttl_sec
        self._monotonic = monotonic
        self._scored_cache: Dict[str, tuple] = {}

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

        scored_universe = await self._scored_universe(exchange, symbols)

        candidates = [c for c in scored_universe if c["avg_trading_value"] >= min_tv]
        candidates.sort(key=lambda c: c["avg_trading_value"], reverse=True)
        return candidates[:cap] if cap else candidates

    async def _scored_universe(
        self, exchange: OverseasExchange, symbols: Optional[List[str]],
    ) -> List[Dict[str, Any]]:
        """거래대금 점수를 매긴 유니버스. TTL 안이면 직전 스캔 결과를 그대로 쓴다."""
        cache_key = f"{exchange.value}:{','.join(symbols) if symbols else '*'}"
        cached = self._scored_cache.get(cache_key)
        if cached is not None and (self._monotonic() - cached[0]) < self._cache_ttl_sec:
            return list(cached[1])

        meta = self._resolve_universe(exchange, symbols)
        if not meta:
            return []

        sem = asyncio.Semaphore(self._concurrency)

        async def _score(entry: Dict[str, str]) -> Optional[Dict[str, Any]]:
            async with sem:
                avg_tv = await self._avg_trading_value(entry["code"], exchange)
            if avg_tv is None:
                return None
            return {
                "code": entry["code"],
                "name": entry.get("name", entry["code"]),
                "exchange": exchange.value,
                "avg_trading_value": avg_tv,
            }

        scored = await asyncio.gather(*[_score(e) for e in meta], return_exceptions=True)

        universe: List[Dict[str, Any]] = []
        for r in scored:
            if isinstance(r, Exception):
                self._logger.warning({"event": "overseas_candidate_error", "error": str(r)})
                continue
            if r:
                universe.append(r)

        # 전량 실패를 캐시하면 TTL 내내 후보가 0으로 굳는다 — 다음 호출에서 다시 훑는다.
        if universe:
            self._scored_cache[cache_key] = (self._monotonic(), list(universe))
        return universe

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
