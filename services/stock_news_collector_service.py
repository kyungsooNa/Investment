# services/stock_news_collector_service.py
"""네이버 금융 종목뉴스를 스크래핑해 기사 목록(제목/언론사/시각/링크)을 반환하는 서비스.

- 목록: finance.naver.com/item/news_news.naver?code=NNNNNN&page=&clusterId=
  * 부모 페이지(item/news.naver)는 iframe 셸이라 기사가 없다. 위 iframe 문서를 직접 받는다.
  * **Referer 헤더가 필수**다. 없으면 네이버가 기사 없는 빈 표를 돌려준다(2026-07 확인).
  * clusterId 파라미터가 빠져도 빈 표가 온다. 값은 비워두되 키는 반드시 보낸다.
- 본문은 받지 않는다. 제목·언론사·시각만 AI 검토 입력으로 쓴다.
- HTML 구조 변경 시 깨질 수 있으므로 graceful degrade(예외 흡수, 빈 목록 반환)를 유지한다.
"""
import logging
import re
from typing import Dict, List, Optional

import aiohttp
from bs4 import BeautifulSoup

_BASE = "https://finance.naver.com"
_LIST_URL = _BASE + "/item/news_news.naver?code={code}&page=&clusterId="
_REFERER_URL = _BASE + "/item/news.naver?code={code}"

_RE_CODE = re.compile(r"^\d{6}$")


class StockNewsCollectorService:
    """종목별 최신 뉴스 목록 수집기 (네이버 금융)."""

    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
        timeout_sec: float = 5.0,
    ):
        self._logger = logger or logging.getLogger(__name__)
        self._timeout_sec = timeout_sec

    async def collect(self, code: str, *, limit: int = 15) -> List[Dict[str, str]]:
        """종목코드의 최신 뉴스를 최대 limit 건 반환한다. 실패 시 빈 목록."""
        code = str(code or "").strip()
        if not _RE_CODE.match(code):
            return []

        url = _LIST_URL.format(code=code)
        headers = {"Referer": _REFERER_URL.format(code=code)}
        try:
            html = await self._fetch_html(url, headers)
        except Exception as e:
            self._logger.warning(
                {"event": "stock_news_fetch_failed", "code": code, "error": str(e)}
            )
            return []

        rows = self._parse_news_list(html)
        if not rows:
            self._logger.info({"event": "stock_news_empty", "code": code})
        return rows[:limit] if limit and limit > 0 else rows

    async def _fetch_html(self, url: str, headers: Dict[str, str]) -> str:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers, timeout=self._timeout_sec
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
                # 네이버 금융은 EUC-KR 인코딩
                return await response.text(encoding="euc-kr", errors="replace")

    @staticmethod
    def _parse_news_list(html: str) -> List[Dict[str, str]]:
        """종목뉴스 표에서 [{title, press, published_at, url}] 추출.

        tr.relation_lst 는 연관기사 목록을 담은 중첩 table 이라 본문 기사와 셀렉터가
        같다. 해당 행 자신과 그 하위 행을 모두 제외하고, 클러스터 대표 행
        (tr.relation_tit)은 실제 기사이므로 남긴다.
        (relation_lst 행 자신도 중첩 anchor 때문에 td.title a.tit 에 걸린다.)
        """
        if not html:
            return []
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return []

        table = soup.select_one("table.type5")
        if table is None:
            return []

        out: List[Dict[str, str]] = []
        seen = set()
        for tr in table.select("tr"):
            if "relation_lst" in (tr.get("class") or []):
                continue
            if tr.find_parent("tr", class_="relation_lst"):
                continue
            anchor = tr.select_one("td.title a.tit")
            info = tr.select_one("td.info")
            date = tr.select_one("td.date")
            if not (anchor and info and date):
                continue

            title = anchor.get_text(strip=True)
            if not title or title in seen:
                continue
            seen.add(title)

            href = anchor.get("href", "")
            out.append({
                "title": title,
                "press": info.get_text(strip=True),
                "published_at": date.get_text(strip=True),
                "url": _BASE + href if href.startswith("/") else href,
            })
        return out
