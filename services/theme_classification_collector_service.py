# services/theme_classification_collector_service.py
"""
네이버 금융 그룹 분류(테마·업종)를 스크래핑하여 StockClassificationRepository에 적재하는 서비스.

- 목록: finance.naver.com/sise/sise_group.naver?type={theme|upjong}  → 그룹명 + detail no
- 상세: finance.naver.com/sise/sise_group_detail.naver?type={...}&no=N → 구성종목(code, name)
- 개별 실패는 warning 후 skip(부분 성공 허용). 파싱은 정적 메서드로 분리해 테스트 가능.
- HTML 구조 변경 시 깨질 수 있으므로 graceful degrade(예외 흡수, 0 반환)를 유지한다.
"""
import os
import re
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

import yaml
import aiohttp
from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from repositories.stock_classification_repository import StockClassificationRepository

_BASE = "https://finance.naver.com"
_LIST_URL = _BASE + "/sise/sise_group.naver?type={group_type}"
_DETAIL_URL = _BASE + "/sise/sise_group_detail.naver?type={group_type}&no={no}"
_DEFAULT_ALIAS_CONFIG = os.path.join("config", "theme_aliases.yaml")

_RE_DETAIL_NO = re.compile(r"no=(\d+)")
_RE_CODE = re.compile(r"code=(\d{6})")


class ThemeClassificationCollectorService:
    """네이버 그룹 분류 수집기 (NAVER) — 테마(category_type=theme)와 업종(industry).

    두 분류는 URL 의 type 값만 다르고 목록·상세 마크업이 같아 파서를 공유한다.
    파서를 복제하면 네이버가 마크업을 바꿀 때 한쪽만 고쳐지는 일이 생긴다.
    """

    SOURCE = "NAVER"
    CATEGORY_TYPE = "theme"
    INDUSTRY_CATEGORY_TYPE = "industry"

    def __init__(
        self,
        classification_repository: "StockClassificationRepository",
        logger: Optional[logging.Logger] = None,
        request_delay: float = 0.3,
        alias_config_path: str = _DEFAULT_ALIAS_CONFIG,
    ):
        self._repo = classification_repository
        self._logger = logger or logging.getLogger(__name__)
        self._request_delay = request_delay
        self._alias_config_path = alias_config_path
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }

    async def collect_naver_themes(self) -> int:
        """네이버 테마 전체를 수집·저장하고 저장한 레코드 수를 반환한다."""
        return await self._collect_groups("theme", self.CATEGORY_TYPE, use_aliases=True)

    async def collect_naver_industries(self) -> int:
        """네이버 업종 전체를 수집·저장하고 저장한 레코드 수를 반환한다.

        업종은 테마와 달리 **배타적**이다(2026-08-14 실측: 두 업종 이상에 속한 종목 0건).
        그래서 히트맵의 섹터 블록처럼 '종목 하나 = 그룹 하나'를 전제하는 화면에 바로 쓸 수 있다.
        alias 는 테마명을 묶기 위한 개념이라 업종에는 적용하지 않는다 — 적용하면 분류가 뒤섞인다.
        """
        return await self._collect_groups("upjong", self.INDUSTRY_CATEGORY_TYPE, use_aliases=False)

    async def _collect_groups(self, group_type: str, category_type: str, *, use_aliases: bool) -> int:
        """네이버 그룹(테마/업종) 분류를 수집해 (SOURCE, category_type) 으로 통째 교체한다."""
        try:
            list_html = await self._fetch_html(_LIST_URL.format(group_type=group_type))
            groups = self._parse_theme_list(list_html)
        except Exception as e:
            self._logger.warning({"event": "group_list_fetch_failed", "type": group_type, "error": str(e)})
            return 0

        if not groups:
            self._logger.warning({"event": "group_list_empty", "type": group_type})
            return 0

        if use_aliases:
            await self._sync_aliases()
            alias_map = await self._repo.get_alias_map(self.SOURCE)
        else:
            alias_map = {}
        collected_at = datetime.now().isoformat(timespec="seconds")
        records: List[dict] = []

        for no, raw_name in groups:
            try:
                detail_html = await self._fetch_html(
                    _DETAIL_URL.format(group_type=group_type, no=no)
                )
                members = self._parse_theme_members(detail_html)
            except Exception as e:
                self._logger.warning(
                    {"event": "group_detail_fetch_failed", "type": group_type, "no": no, "error": str(e)}
                )
                continue

            normalized = alias_map.get(raw_name, raw_name)
            for code, name in members:
                records.append({
                    "source": self.SOURCE,
                    "category_type": category_type,
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
        saved = await self._repo.replace_source_classifications(
            self.SOURCE, category_type, records
        )
        self._logger.info(
            {"event": "group_collect_done", "type": group_type, "groups": len(groups), "records": saved}
        )
        return saved

    async def _sync_aliases(self) -> None:
        """alias 설정 파일을 읽어 theme_aliases 에 반영한다(설정이 있을 때만).

        파일이 없거나 비어 있으면 정규화 없이 raw 테마명을 그대로 사용한다.
        """
        records = self._load_alias_config()
        if records:
            await self._repo.upsert_aliases(records)

    def _load_alias_config(self) -> List[Dict[str, str]]:
        """alias yaml → [{"source","raw_name","normalized_name"}] 변환.

        형식: aliases: {<표준명>: [<원본명>, ...]}. 파싱 실패/형식 불일치는 빈 목록.
        """
        path = self._alias_config_path
        if not path or not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except Exception as e:
            self._logger.warning({"event": "theme_alias_config_load_failed", "error": str(e)})
            return []

        aliases = raw.get("aliases") if isinstance(raw, dict) else None
        if not isinstance(aliases, dict):
            return []
        out: List[Dict[str, str]] = []
        for normalized, raw_names in aliases.items():
            if not isinstance(raw_names, (list, tuple)):
                continue
            for raw_name in raw_names:
                out.append({
                    "source": self.SOURCE,
                    "raw_name": str(raw_name),
                    "normalized_name": str(normalized),
                })
        return out

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
