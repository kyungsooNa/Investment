# services/theme_classification_collector_service.py
"""
네이버 금융 테마 분류를 스크래핑하여 StockClassificationRepository에 적재하는 서비스.

- 목록: finance.naver.com/sise/sise_group.naver?type=theme  → 테마명 + detail no
- 상세: finance.naver.com/sise/sise_group_detail.naver?type=theme&no=N → 구성종목(code, name)
- 개별 실패는 warning 후 skip(부분 성공 허용). 파싱은 정적 메서드로 분리해 테스트 가능.
- HTML 구조 변경 시 깨질 수 있으므로 graceful degrade(예외 흡수, 0 반환)를 유지한다.
"""
import re
import asyncio
import logging
from datetime import datetime
from typing import List, Optional, Tuple, TYPE_CHECKING

import aiohttp
from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from repositories.stock_classification_repository import StockClassificationRepository

_BASE = "https://finance.naver.com"
_LIST_URL = _BASE + "/sise/sise_group.naver?type=theme"
_DETAIL_URL = _BASE + "/sise/sise_group_detail.naver?type=theme&no={no}"

_RE_DETAIL_NO = re.compile(r"no=(\d+)")
_RE_CODE = re.compile(r"code=(\d{6})")


class ThemeClassificationCollectorService:
    """네이버 테마 분류 수집기 (NAVER, category_type=theme)."""

    SOURCE = "NAVER"
    CATEGORY_TYPE = "theme"

    def __init__(
        self,
        classification_repository: "StockClassificationRepository",
        logger: Optional[logging.Logger] = None,
        request_delay: float = 0.3,
    ):
        self._repo = classification_repository
        self._logger = logger or logging.getLogger(__name__)
        self._request_delay = request_delay
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }

    async def collect_naver_themes(self) -> int:
        """네이버 테마 전체를 수집·저장하고 저장한 레코드 수를 반환한다."""
        try:
            list_html = await self._fetch_html(_LIST_URL)
            themes = self._parse_theme_list(list_html)
        except Exception as e:
            self._logger.warning({"event": "theme_list_fetch_failed", "error": str(e)})
            return 0

        if not themes:
            self._logger.warning({"event": "theme_list_empty"})
            return 0

        alias_map = await self._repo.get_alias_map(self.SOURCE)
        collected_at = datetime.now().isoformat(timespec="seconds")
        records: List[dict] = []

        for no, raw_name in themes:
            try:
                detail_html = await self._fetch_html(_DETAIL_URL.format(no=no))
                members = self._parse_theme_members(detail_html)
            except Exception as e:
                self._logger.warning({"event": "theme_detail_fetch_failed", "no": no, "error": str(e)})
                continue

            normalized = alias_map.get(raw_name, raw_name)
            for code, name in members:
                records.append({
                    "source": self.SOURCE,
                    "category_type": self.CATEGORY_TYPE,
                    "group_name": raw_name,
                    "normalized_name": normalized,
                    "code": code,
                    "name": name,
                    "collected_at": collected_at,
                })
            if self._request_delay:
                await asyncio.sleep(self._request_delay)

        if not records:
            return 0
        saved = await self._repo.upsert_classifications(records)
        self._logger.info({"event": "theme_collect_done", "themes": len(themes), "records": saved})
        return saved

    async def _fetch_html(self, url: str) -> str:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers, timeout=10) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
                # 네이버 금융은 EUC-KR 인코딩
                return await response.text(encoding="euc-kr", errors="replace")

    @staticmethod
    def _parse_theme_list(html: str) -> List[Tuple[str, str]]:
        """테마 목록 페이지에서 [(no, raw_name)] 추출."""
        soup = BeautifulSoup(html, "html.parser")
        out: List[Tuple[str, str]] = []
        seen = set()
        for a in soup.select('a[href*="sise_group_detail.naver"]'):
            href = a.get("href", "")
            m = _RE_DETAIL_NO.search(href)
            if not m:
                continue
            no = m.group(1)
            name = a.get_text(strip=True)
            if not name or no in seen:
                continue
            seen.add(no)
            out.append((no, name))
        return out

    @staticmethod
    def _parse_theme_members(html: str) -> List[Tuple[str, str]]:
        """테마 상세 페이지에서 [(code, name)] 추출."""
        soup = BeautifulSoup(html, "html.parser")
        out: List[Tuple[str, str]] = []
        seen = set()
        for a in soup.select('a[href*="code="]'):
            href = a.get("href", "")
            m = _RE_CODE.search(href)
            if not m:
                continue
            code = m.group(1)
            name = a.get_text(strip=True)
            if not name or code in seen:
                continue
            seen.add(code)
            out.append((code, name))
        return out
