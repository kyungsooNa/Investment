"""
미국장 시가총액/랭킹 통계 서비스.

KIS 에는 이 저장소가 배선한 해외 순위 TR 이 없으므로, 이미 시총 리포트에서 쓰고 있는
Yahoo batch quote(`YahooUsMarketCapProvider`)로 유니버스 전체 스냅샷을 모아 정렬한다.
유니버스는 S&P 500 구성종목(`SP500Repository`)이다.

한 번의 스냅샷 수집으로 시총·등락률·거래량을 모두 낼 수 있어, 화면이 여러 개여도
`ttl_sec` 동안은 외부 호출을 재사용한다.
"""
import asyncio
import logging
import time
from typing import Optional

# 국내 랭킹의 외국인·기관·개인 수급은 KRX 전용 데이터라 미국장에서는 만들 수 없다.
# Yahoo 스냅샷 한 건에서 유도 가능한 4종만 제공한다.
RANKING_CATEGORIES = ("rise", "fall", "volume", "trading_value")

_RANKING_SORTS = {
    "rise": (lambda snap: snap.change_rate, True),
    "fall": (lambda snap: snap.change_rate, False),
    "volume": (lambda snap: snap.volume, True),
    "trading_value": (lambda snap: snap.price * snap.volume, True),
}


class OverseasMarketStatsService:
    def __init__(
        self,
        universe_factory,
        provider,
        logger=None,
        ttl_sec: float = 300.0,
        clock=time.monotonic,
    ):
        # 유니버스는 첫 조회 시점에 만든다. 구성종목 DB 가 없으면 생성에 외부 다운로드가
        # 따라붙는데, 이를 웹 시작 경로에 두면 아무도 안 보는 화면 때문에 부팅이 늦어진다.
        self._universe_factory = universe_factory
        self._universe = None
        self._universe_failed = False
        self._provider = provider
        self._logger = logger or logging.getLogger(__name__)
        self._ttl_sec = ttl_sec
        self._clock = clock
        self._lock = asyncio.Lock()
        self._cached_at: Optional[float] = None
        self._snapshots: list = []
        self._fx_rate: Optional[float] = None

    def _get_universe(self):
        if self._universe is not None or self._universe_failed:
            return self._universe
        try:
            self._universe = self._universe_factory()
        except Exception as exc:
            # 매 요청마다 다운로드를 재시도하지 않도록 실패를 기억한다.
            self._universe_failed = True
            self._logger.warning(f"미국장 유니버스 준비 실패, 시총/랭킹을 비웁니다: {exc}")
        return self._universe

    async def get_snapshots(self) -> tuple[list, Optional[float]]:
        """(스냅샷 목록, USD/KRW 환율)을 TTL 캐시와 함께 반환한다."""
        universe = self._get_universe()
        symbols = universe.all_symbols() if universe else []
        if not symbols:
            return [], None

        async with self._lock:
            if self._cached_at is not None and (self._clock() - self._cached_at) <= self._ttl_sec:
                return self._snapshots, self._fx_rate

            snapshots, fx_rate = await asyncio.gather(
                self._provider.fetch_snapshots(symbols),
                self._fetch_fx_rate(),
            )
            self._snapshots = snapshots
            self._fx_rate = fx_rate
            self._cached_at = self._clock()
            return self._snapshots, self._fx_rate

    async def _fetch_fx_rate(self) -> Optional[float]:
        try:
            return await self._provider.fetch_usdkrw()
        except Exception as exc:
            # 환율은 원화 환산 표시에만 쓰이므로 실패해도 USD 기준 결과는 그대로 낸다.
            self._logger.warning(f"USD/KRW 환율 조회 실패, 원화 환산을 생략합니다: {exc}")
            return None

    async def get_top_market_cap(self, limit: int = 30) -> dict:
        """시가총액 상위 종목. market_cap 이 없는 심볼은 제외한다."""
        snapshots, fx_rate = await self.get_snapshots()
        ranked = sorted(
            (snap for snap in snapshots if snap.market_cap > 0),
            key=lambda snap: snap.market_cap,
            reverse=True,
        )[:limit]

        items = [
            self._to_item(snap, rank, fx_rate)
            for rank, snap in enumerate(ranked, 1)
        ]
        return {"fx_rate": fx_rate, "items": items}

    async def get_ranking(self, category: str, limit: int = 30) -> dict:
        """등락률/거래량/거래대금 랭킹. 시가총액 화면과 스냅샷 캐시를 공유한다."""
        sort = _RANKING_SORTS.get(category)
        if sort is None:
            raise ValueError(f"지원하지 않는 미국장 랭킹 구분: {category}")
        key, descending = sort

        snapshots, fx_rate = await self.get_snapshots()
        ranked = sorted(snapshots, key=key, reverse=descending)[:limit]

        items = [
            self._to_item(snap, rank, fx_rate)
            for rank, snap in enumerate(ranked, 1)
        ]
        return {"fx_rate": fx_rate, "items": items}

    def _to_item(self, snap, rank: int, fx_rate: Optional[float]) -> dict:
        meta = self._universe.get_meta(snap.symbol) if self._universe else None
        meta = meta if isinstance(meta, dict) else None
        return {
            "rank": rank,
            "symbol": snap.symbol,
            "name": (meta or {}).get("name") or snap.name,
            "sector": (meta or {}).get("sector") or "",
            "price": snap.price,
            "change_rate": snap.change_rate,
            "volume": snap.volume,
            "trading_value_usd": int(snap.price * snap.volume),
            "market_cap_usd": snap.market_cap,
            "market_cap_krw": int(snap.market_cap * fx_rate) if fx_rate else None,
        }
