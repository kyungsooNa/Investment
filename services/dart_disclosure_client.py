"""OpenDART 공시검색 API의 최소 비동기 클라이언트."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import httpx


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
