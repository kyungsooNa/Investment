"""OpenDART 공시검색 API의 최소 비동기 클라이언트."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

import httpx
from bs4 import BeautifulSoup


@dataclass(frozen=True)
class DartDisclosure:
    corp_class: str
    corp_name: str
    corp_code: str
    stock_code: str
    report_name: str
    receipt_no: str
    filer_name: str
    receipt_date: str
    remarks: str = ""

    @property
    def viewer_url(self) -> str:
        return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={self.receipt_no}"


@dataclass(frozen=True)
class DartDisclosurePage:
    items: List[DartDisclosure]
    page_no: int
    page_count: int
    total_count: int
    total_page: int


class DartApiError(RuntimeError):
    def __init__(self, status: str, message: str):
        self.status = str(status or "")
        self.message = str(message or "OpenDART API 오류")
        super().__init__(f"OpenDART API 오류 ({self.status}): {self.message}")


class DartDisclosureClient:
    LIST_URL = "https://opendart.fss.or.kr/api/list.json"
    MAIN_URL = "https://dart.fss.or.kr/dsaf001/main.do"
    VIEWER_URL = "https://dart.fss.or.kr/report/viewer.do"

    def __init__(
        self,
        api_key: str,
        *,
        http_client: Optional[httpx.AsyncClient] = None,
        timeout_sec: float = 5.0,
    ) -> None:
        self._api_key = api_key
        self._http_client = http_client
        self._timeout_sec = float(timeout_sec)

    async def fetch_disclosures(
        self,
        date: str,
        *,
        page_no: int = 1,
        page_count: int = 100,
    ) -> DartDisclosurePage:
        params = {
            "crtfc_key": self._api_key,
            "bgn_de": date,
            "end_de": date,
            "last_reprt_at": "N",
            "sort": "date",
            "sort_mth": "desc",
            "page_no": int(page_no),
            "page_count": int(page_count),
        }
        if self._http_client is not None:
            response = await self._request(self._http_client, params)
        else:
            async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
                response = await self._request(client, params)
        return self._parse_response(response.json())

    async def fetch_disclosure_text(
        self, receipt_no: str, *, max_chars: int = 16_000
    ) -> str:
        """DART 본문 뷰어의 실제 텍스트를 반환한다.

        목록 API에는 제목과 메타데이터만 있으므로 메인 문서의 dcmNo를 찾아
        HTML 뷰어 본문을 별도로 조회한다. 첨부 PDF까지 확장하지는 않는다.
        """
        receipt_no = str(receipt_no or "").strip()
        if not receipt_no:
            return ""

        async def fetch(client) -> str:
            main_response = await client.get(
                self.MAIN_URL,
                params={"rcpNo": receipt_no},
                timeout=self._timeout_sec,
            )
            main_response.raise_for_status()
            dcm_no = self._extract_dcm_no(main_response.text, receipt_no)
            if not dcm_no:
                return ""

            viewer_response = await client.get(
                self.VIEWER_URL,
                params={
                    "rcpNo": receipt_no,
                    "dcmNo": dcm_no,
                    "eleId": "0",
                    "offset": "0",
                    "length": "0",
                    "dtd": "HTML",
                },
                timeout=self._timeout_sec,
            )
            viewer_response.raise_for_status()
            text = BeautifulSoup(viewer_response.text, "html.parser").get_text(
                "\n", strip=True
            )
            text = re.sub(r"[ \t]+", " ", text)
            text = re.sub(r"\n{2,}", "\n", text).strip()
            return text[: max(1, int(max_chars))]

        if self._http_client is not None:
            return await fetch(self._http_client)
        async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
            return await fetch(client)

    async def _request(self, client, params):
        last_error = None
        for _attempt in range(2):
            try:
                response = await client.get(
                    self.LIST_URL,
                    params=params,
                    timeout=self._timeout_sec,
                )
                response.raise_for_status()
                return response
            except httpx.RequestError as exc:
                last_error = exc
        raise DartApiError("NETWORK", type(last_error).__name__)

    @staticmethod
    def _extract_dcm_no(html: str, receipt_no: str) -> str:
        pattern = (
            r"""viewDoc\(\s*["']"""
            + re.escape(receipt_no)
            + r"""["']\s*,\s*["'](\d+)["']"""
        )
        match = re.search(pattern, str(html or ""))
        return match.group(1) if match else ""

    @staticmethod
    def _parse_response(payload: dict) -> DartDisclosurePage:
        status = str(payload.get("status") or "")
        if status == "013":
            return DartDisclosurePage([], 1, 100, 0, 0)
        if status != "000":
            raise DartApiError(status, payload.get("message", ""))

        items = [
            DartDisclosure(
                corp_class=str(row.get("corp_cls") or ""),
                corp_name=str(row.get("corp_name") or ""),
                corp_code=str(row.get("corp_code") or ""),
                stock_code=str(row.get("stock_code") or ""),
                report_name=str(row.get("report_nm") or ""),
                receipt_no=str(row.get("rcept_no") or ""),
                filer_name=str(row.get("flr_nm") or ""),
                receipt_date=str(row.get("rcept_dt") or ""),
                remarks=str(row.get("rm") or ""),
            )
            for row in (payload.get("list") or [])
        ]
        return DartDisclosurePage(
            items=items,
            page_no=int(payload.get("page_no") or 1),
            page_count=int(payload.get("page_count") or 100),
            total_count=int(payload.get("total_count") or len(items)),
            total_page=int(payload.get("total_page") or (1 if items else 0)),
        )
