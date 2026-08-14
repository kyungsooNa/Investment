# services/naver_market_snapshot_service.py
"""국내 장중 시세 스냅샷 (네이버 금융 시가총액 페이지).

KIS 에는 전종목 시세 API 가 없고, 2026-08-14 장중 실측에서 다른 경로가 전부 막혀 있음을 확인했다.

- FDR `StockListing('KRX')` 는 GitHub 에 커밋된 일별 CSV 캐시라 장중에 값이 변하지 않는다.
- KRX MDC `getJsonData.cmd` 는 세션 쿠키를 세워도 `LOGOUT` 을 돌려준다.
  같은 엔드포인트를 쓰는 pykrx 와 FDR 의 비캐시 경로도 그래서 함께 죽어 있다.

히트맵은 전종목이 아니라 **시총 상위 N** 만 필요하고 네이버 시총 페이지가 이미 시총
내림차순이라, 상위 몇 페이지만 받으면 된다(페이지당 50종목). 실측으로 코스피·코스닥
10페이지씩 20요청이 병렬 5.4초에 961종목이었다.

행 모양은 `StockOhlcvRepository.get_market_cap_snapshot` 과 일부러 같게 맞춘다 —
라우트가 장중/장외에 따라 두 소스를 갈아끼우기만 하면 되도록.
시가총액은 네이버가 이미 억원 단위로 주므로 daily_prices.market_cap 과 단위가 같다.
"""
import asyncio
import logging
import time
from datetime import datetime
from typing import Optional

import aiohttp
import pytz
from bs4 import BeautifulSoup

_BASE_URL = "https://finance.naver.com/sise/sise_market_sum.naver"
_SOSOK = {"KOSPI": 0, "KOSDAQ": 1}
_SOSOK_NAME = {0: "KOSPI", 1: "KOSDAQ"}
_PAGE_SIZE = 50  # 네이버 시총 페이지가 한 장에 싣는 종목 수


class NaverMarketSnapshotService:
    """네이버 시총 페이지에서 국내 장중 스냅샷을 모아 TTL 캐시로 재사용한다."""

    # limit 이 아무리 커도 시장당 이 페이지 수를 넘기지 않는다(= 최대 12요청/시장).
    # 히트맵 최대 limit 500 은 10페이지면 덮이고, 나머지는 여유분이다.
    _MAX_PAGES = 12
    _PAGE_TIMEOUT_SEC = 10

    def __init__(
        self,
        page_fetcher=None,
        logger=None,
        ttl_sec: float = 45.0,
        clock=time.monotonic,
        today_provider=None,
        max_concurrency: int = 6,
    ):
        self._page_fetcher = page_fetcher or self._fetch_page
        self._logger = logger or logging.getLogger(__name__)
        self._ttl_sec = ttl_sec
        self._clock = clock
        self._today_provider = today_provider or self._today_kst
        self._max_concurrency = max_concurrency
        self._lock = asyncio.Lock()
        self._cache: dict[int, list[dict]] = {}
        self._cached_at: dict[int, float] = {}
        self._cached_pages: dict[int, int] = {}

    async def get_snapshot(self, limit: int, market: Optional[str] = None) -> list[dict]:
        """시총 상위 종목 스냅샷. market 이 None/'ALL' 이면 코스피+코스닥을 합쳐 정렬한다."""
        wanted = self._target_sosoks(market)
        if not wanted:
            return []

        pages = min(-(-limit // _PAGE_SIZE), self._MAX_PAGES)
        collected: list[dict] = []
        for sosok in wanted:
            collected.extend(await self._market_rows(sosok, pages))

        collected.sort(key=lambda row: row["market_cap"], reverse=True)
        return collected[:limit]

    def _target_sosoks(self, market: Optional[str]) -> list[int]:
        if not market or market == "ALL":
            return list(_SOSOK.values())
        sosok = _SOSOK.get(str(market).upper())
        return [sosok] if sosok is not None else []

    async def _market_rows(self, sosok: int, pages: int) -> list[dict]:
        async with self._lock:
            cached = self._cache.get(sosok)
            cached_at = self._cached_at.get(sosok)
            fresh = cached_at is not None and (self._clock() - cached_at) <= self._ttl_sec
            # 캐시가 더 얕은 깊이로 받아둔 것이면 다시 받아야 한다(limit 200 뒤에 500 이 올 수 있다).
            if fresh and self._cached_pages.get(sosok, 0) >= pages:
                return cached

            rows = await self._collect_market(sosok, pages)
            # 마크업 개편 등으로 0행이 되면 캐시하지 않는다 — 다음 요청이 다시 시도하고,
            # 그 사이 라우트는 저장소(daily_prices)로 폴백한다.
            if rows:
                self._cache[sosok] = rows
                self._cached_at[sosok] = self._clock()
                self._cached_pages[sosok] = pages
            return rows

    async def _collect_market(self, sosok: int, pages: int) -> list[dict]:
        """필요한 페이지 수만큼 병렬로 받는다. 시장 끝을 넘은 페이지는 0행으로 와 무해하다."""
        semaphore = asyncio.Semaphore(self._max_concurrency)
        results = await asyncio.gather(
            *(self._page_rows(semaphore, sosok, page) for page in range(1, pages + 1))
        )

        rows: list[dict] = []
        seen: set[str] = set()
        for page_rows in results:
            for row in page_rows:
                if row["code"] in seen:
                    continue
                seen.add(row["code"])
                rows.append(row)

        if not rows:
            self._logger.warning(
                {"event": "naver_snapshot_empty", "sosok": sosok,
                 "msg": "네이버 시총 페이지에서 한 행도 파싱하지 못함 (마크업 변경 의심)"}
            )
        return rows

    async def _page_rows(self, semaphore, sosok: int, page: int) -> list[dict]:
        async with semaphore:
            try:
                html = await self._page_fetcher(sosok, page)
            except Exception as exc:
                self._logger.warning(
                    {"event": "naver_snapshot_page_error", "sosok": sosok, "page": page, "error": str(exc)}
                )
                # 실패를 '빈 페이지'로 돌려주면 뒤 페이지까지 끊긴다. 여기서는 빈 목록이지만
                # 호출부가 '실패'와 '마지막 페이지'를 구분하지 못해도 손해는 조기 중단뿐이다.
                return []
        return self._parse(html, sosok)

    def _parse(self, html: str, sosok: int) -> list[dict]:
        soup = BeautifulSoup(html or "", "html.parser")
        table = soup.find("table", class_="type_2")
        if not table:
            return []

        today = self._today_provider()
        market = _SOSOK_NAME[sosok]
        rows = []
        for tr in table.find_all("tr"):
            link = tr.find("a", class_="tltle")
            if not link:
                continue
            cells = tr.find_all("td", class_="number")
            if len(cells) < 5:
                continue
            code = (link.get("href") or "").split("code=")[-1].strip()
            price = _to_int(cells[0].get_text())
            cap = _to_int(cells[4].get_text())
            rate = _to_rate(cells[2].get_text())
            if not code or price is None or cap is None or rate is None:
                continue
            rows.append({
                "code": code,
                "name": link.get_text(strip=True),
                "current_price": price,
                "change_rate": rate,
                "market_cap": cap,
                "market": market,
                "trade_date": today,
            })
        return rows

    async def _fetch_page(self, sosok: int, page: int) -> str:
        url = f"{_BASE_URL}?sosok={sosok}&page={page}"
        timeout = aiohttp.ClientTimeout(total=self._PAGE_TIMEOUT_SEC)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=_HEADERS) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
                # 네이버 금융은 아직 euc-kr 이라 aiohttp 자동 판별에 맡기면 깨진다.
                return await response.text(encoding="euc-kr")

    @staticmethod
    def _today_kst() -> str:
        return datetime.now(pytz.timezone("Asia/Seoul")).strftime("%Y%m%d")


_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def _to_int(text: str):
    try:
        return int(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _to_rate(text: str):
    """'+2.43%' → '2.43', '-1.20%' → '-1.20'. 저장소가 TEXT 로 주는 모양에 맞춘다."""
    cleaned = str(text).replace("%", "").replace("+", "").replace(",", "").strip()
    try:
        return f"{float(cleaned):.2f}"
    except (TypeError, ValueError):
        return None
