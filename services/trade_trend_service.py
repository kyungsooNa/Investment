from __future__ import annotations

import html
import io
import re
import zipfile
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Optional
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

import httpx


@dataclass(frozen=True)
class TradeStatItem:
    period: str
    item_name: str
    item_code: str
    export_amount_usd: int
    import_amount_usd: int
    trade_balance_usd: int
    export_weight: int = 0
    import_weight: int = 0


@dataclass(frozen=True)
class JejuSemiconductorTradeReport:
    period: str
    export_amount_usd: int
    previous_month_export_amount_usd: Optional[int]
    previous_year_export_amount_usd: Optional[int]
    mom_pct: Optional[float]
    yoy_pct: Optional[float]
    jeju_total_export_amount_usd: Optional[int]
    jeju_export_share_pct: Optional[float]
    fetched_at: str
    item_name: str = "전기기기류"

    @property
    def dedup_key(self) -> str:
        return f"jeju_semiconductor:{self.period}:{self.export_amount_usd}"


@dataclass(frozen=True)
class JejuRegionTradeMonth:
    period: str
    export_amount_usd: int
    import_amount_usd: int
    trade_balance_usd: int
    export_mom_pct: Optional[float] = None
    export_yoy_pct: Optional[float] = None
    import_mom_pct: Optional[float] = None
    import_yoy_pct: Optional[float] = None

    @property
    def period_label(self) -> str:
        key = _period_key(self.period)
        if not key:
            return self.period
        return f"{int(key[:4])}년 {int(key[4:])}월"


@dataclass(frozen=True)
class NationalTradeTrendRelease:
    source: str
    phase: str
    title: str
    url: str
    period_label: str
    export_amount_100m_usd: Optional[float] = None
    export_yoy_pct: Optional[float] = None
    export_mom_change_100m_usd: Optional[float] = None
    export_mom_pct: Optional[float] = None
    export_daily_avg_100m_usd: Optional[float] = None
    export_daily_avg_mom_pct: Optional[float] = None
    import_amount_100m_usd: Optional[float] = None
    import_yoy_pct: Optional[float] = None
    import_mom_change_100m_usd: Optional[float] = None
    import_mom_pct: Optional[float] = None
    trade_balance_100m_usd: Optional[float] = None
    trade_balance_label: str = ""
    trade_balance_mom_change_100m_usd: Optional[float] = None
    semiconductor_export_amount_100m_usd: Optional[float] = None
    semiconductor_yoy_pct: Optional[float] = None
    semiconductor_mom_change_100m_usd: Optional[float] = None
    semiconductor_mom_pct: Optional[float] = None
    semiconductor_daily_avg_100m_usd: Optional[float] = None
    semiconductor_daily_avg_mom_pct: Optional[float] = None
    working_days_current: Optional[float] = None
    working_days_previous_year: Optional[float] = None
    published_at: str = ""
    highlights: list[str] = None

    @property
    def dedup_key(self) -> str:
        return (
            f"national_trade:{self.phase}:{self.period_label}:"
            f"{canonicalize_national_trade_url(self.url)}"
        )


def canonicalize_national_trade_url(url: str) -> str:
    """게시판 목록 파라미터 차이로 같은 보도자료가 중복 발송되는 것을 막는다."""
    if not url:
        return url
    parts = urlsplit(url)
    host = parts.netloc.lower()
    query = parse_qsl(parts.query, keep_blank_values=True)
    drop_keys = {"mno", "pageIndex", "currPage", "searchCondition", "searchKeyword"}

    if "motir.go.kr" in host or "motie.go.kr" in host:
        query = [(key, value) for key, value in query if key not in drop_keys]
    elif "customs.go.kr" in host:
        query = [
            (key, value)
            for key, value in query
            if key not in {"currPage", "pageIndex", "searchType", "searchValue"}
        ]

    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query),
            "",
        )
    )


def canonicalize_national_trade_dedup_key(key: str) -> str:
    if not key.startswith("national_trade:"):
        return key
    parts = key.split(":", 3)
    if len(parts) != 4:
        return key
    prefix, phase, period_label, url = parts
    return f"{prefix}:{phase}:{period_label}:{canonicalize_national_trade_url(url)}"


def _text(item: ET.Element, tag: str, default: str = "") -> str:
    node = item.find(tag)
    if node is None or node.text is None:
        return default
    return node.text.strip()


def _first_text(item: ET.Element, *tags: str, default: str = "") -> str:
    for tag in tags:
        value = _text(item, tag, "")
        if value:
            return value
    return default


def _int_text(item: ET.Element, tag: str) -> int:
    raw = _text(item, tag, "0").replace(",", "")
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return 0


def _first_int_text(item: ET.Element, *tags: str) -> int:
    for tag in tags:
        value = _text(item, tag, "")
        if value:
            return _int_text(item, tag)
    return 0


def _filter_item_rows(rows: list[TradeStatItem], item_code: str) -> list[TradeStatItem]:
    code = str(item_code or "").strip()
    if not code:
        return rows
    exact_or_child = [row for row in rows if row.item_code.startswith(code)]
    if exact_or_child:
        return exact_or_child
    if len(code) > 2:
        chapter_code = code[:2]
        return [row for row in rows if row.item_code == chapter_code]
    return []


def _month_index(yyyymm: str) -> int:
    return int(yyyymm[:4]) * 12 + int(yyyymm[4:6]) - 1


def _split_yyyymm_ranges(
    begin_yyyymm: str,
    end_yyyymm: str,
    *,
    max_months: int = 12,
) -> list[tuple[str, str]]:
    ranges = []
    cursor = begin_yyyymm
    end_index = _month_index(end_yyyymm)
    while _month_index(cursor) <= end_index:
        chunk_end = min(
            shift_yyyymm(cursor, max_months - 1),
            end_yyyymm,
            key=_month_index,
        )
        ranges.append((cursor, chunk_end))
        cursor = shift_yyyymm(chunk_end, 1)
    return ranges


def _with_requested_month_period(
    rows: list[TradeStatItem],
    yyyymm: str,
) -> list[TradeStatItem]:
    normalized_period = f"{yyyymm[:4]}.{yyyymm[4:6]}"
    return [
        replace(row, period=normalized_period)
        if not _period_key(row.period)
        else row
        for row in rows
    ]


def parse_customs_trade_xml(xml_text: str) -> list[TradeStatItem]:
    root = ET.fromstring(xml_text)
    result_code = root.findtext("./header/resultCode", default="")
    if result_code and result_code != "00":
        result_msg = root.findtext("./header/resultMsg", default="UNKNOWN ERROR")
        raise ValueError(f"관세청 수출입통계 API 오류: {result_code} {result_msg}")
    gateway_error_code = root.findtext("./cmmMsgHeader/returnReasonCode", default="")
    if gateway_error_code:
        gateway_error_name = root.findtext("./cmmMsgHeader/errMsg", default="")
        gateway_error_msg = root.findtext(
            "./cmmMsgHeader/returnAuthMsg",
            default=gateway_error_name or "UNKNOWN ERROR",
        )
        raise ValueError(
            f"관세청 수출입통계 API 오류: {gateway_error_code} "
            f"{gateway_error_name} {gateway_error_msg}"
        )

    rows: list[TradeStatItem] = []
    for item in root.findall(".//item"):
        period = _first_text(item, "year", "priodTitle")
        if not period or period == "총계":
            continue
        rows.append(
            TradeStatItem(
                period=period,
                item_name=_first_text(item, "statKor", "sidoNm", "korePrlstNm"),
                item_code=_first_text(item, "hsCode", "hsSgn"),
                export_amount_usd=_first_int_text(item, "expDlr", "expUsdAmt"),
                import_amount_usd=_first_int_text(item, "impDlr", "impUsdAmt"),
                trade_balance_usd=_first_int_text(item, "balPayments", "cmtrBlncAmt"),
                export_weight=_int_text(item, "expWgt"),
                import_weight=_int_text(item, "impWgt"),
            )
        )
    return rows


def _raise_customs_gateway_error_if_present(xml_text: str) -> None:
    if "<cmmMsgHeader>" not in xml_text:
        return
    try:
        parse_customs_trade_xml(xml_text)
    except ET.ParseError:
        return


class CustomsTradeStatClient:
    """관세청 GW 수출입통계 API 클라이언트."""

    DEFAULT_BASE_URL = "https://apis.data.go.kr/1220000/sidotrade/"
    DEFAULT_ITEM_BASE_URL = "https://apis.data.go.kr/1220000/sidoitemtrade/"

    def __init__(
        self,
        *,
        service_key: str,
        http_client=None,
        base_url: str = DEFAULT_BASE_URL,
        item_base_url: str = DEFAULT_ITEM_BASE_URL,
        timeout_sec: float = 10.0,
        sido_param_name: str = "sidoCd",
        sido_code: str = "50",
    ) -> None:
        self._service_key = unquote(service_key)
        self._base_url = base_url if base_url.endswith("/") else f"{base_url}/"
        self._item_base_url = (
            item_base_url if item_base_url.endswith("/") else f"{item_base_url}/"
        )
        self._timeout_sec = timeout_sec
        self._http_client = http_client
        self._sido_param_name = sido_param_name
        self._sido_code = sido_code

    async def fetch_sido_item_month(
        self, yyyymm: str, item_code: str
    ) -> list[TradeStatItem]:
        params = {
            "strtYymm": yyyymm,
            "endYymm": yyyymm,
            self._sido_param_name: self._sido_code,
            "serviceKey": self._service_key,
        }
        rows = await self._get(
            "getSidoitemtradeList",
            params,
            base_url=self._item_base_url,
        )
        return _with_requested_month_period(_filter_item_rows(rows, item_code), yyyymm)

    async def fetch_sido_total_month(self, yyyymm: str) -> list[TradeStatItem]:
        params = {
            "strtYymm": yyyymm,
            "endYymm": yyyymm,
            self._sido_param_name: self._sido_code,
            "serviceKey": self._service_key,
        }
        rows = await self._get("getSidotradeList", params)
        return _with_requested_month_period(rows, yyyymm)

    async def fetch_sido_total_range(
        self, begin_yyyymm: str, end_yyyymm: str
    ) -> list[TradeStatItem]:
        rows: list[TradeStatItem] = []
        for chunk_begin, chunk_end in _split_yyyymm_ranges(begin_yyyymm, end_yyyymm):
            params = {
                "strtYymm": chunk_begin,
                "endYymm": chunk_end,
                self._sido_param_name: self._sido_code,
                "serviceKey": self._service_key,
            }
            rows.extend(await self._get("getSidotradeList", params))
        return rows

    async def _get(
        self,
        operation: str,
        params: dict,
        *,
        base_url: str | None = None,
    ) -> list[TradeStatItem]:
        url = urljoin(base_url or self._base_url, operation)
        if self._http_client is not None:
            response = await self._http_client.get(url, params=params, timeout=self._timeout_sec)
            _raise_customs_gateway_error_if_present(response.text)
            response.raise_for_status()
            return parse_customs_trade_xml(response.text)

        async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
            response = await client.get(url, params=params)
            _raise_customs_gateway_error_if_present(response.text)
            response.raise_for_status()
            return parse_customs_trade_xml(response.text)


class NationalTradeTrendWebClient:
    """관세청/산업부 보도자료 목록에서 전국 수출입 발표를 찾는다."""

    DEFAULT_LIST_URLS = [
        "https://www.customs.go.kr/kcs/na/ntt/selectNttList.do?mi=2891&bbsId=1362&searchType=all&searchValue=%EC%88%98%EC%B6%9C%EC%9E%85%20%ED%98%84%ED%99%A9",
        "https://www.motir.go.kr/kor/article/ATCL3f49a5a8c?searchCondition=1&searchKeyword=%EC%88%98%EC%B6%9C%EC%9E%85%20%EB%8F%99%ED%96%A5",
        "https://tradedata.go.kr/cts/index.do",
    ]

    def __init__(
        self,
        *,
        http_client=None,
        list_urls: Optional[list[str]] = None,
        timeout_sec: float = 10.0,
        max_detail_pages: int = 5,
    ) -> None:
        self._http_client = http_client
        self._list_urls = list_urls or list(self.DEFAULT_LIST_URLS)
        self._timeout_sec = timeout_sec
        self._max_detail_pages = max_detail_pages

    async def fetch_recent_releases(self) -> list[NationalTradeTrendRelease]:
        return await self.fetch_releases()

    async def fetch_releases(
        self,
        *,
        year: Optional[int] = None,
        max_detail_pages: Optional[int] = None,
        list_page_count: int = 1,
    ) -> list[NationalTradeTrendRelease]:
        releases: list[NationalTradeTrendRelease] = []
        detail_url_groups: list[list[tuple[str, str]]] = []
        for list_url in _expand_list_page_urls(self._list_urls, list_page_count):
            html_text = await self._fetch_text(list_url)
            links = _extract_national_trade_links(html_text, list_url)
            if year is not None:
                links = [
                    (title, url)
                    for title, url in links
                    if _period_year_from_title(title) == year
                ]
            detail_url_groups.append(links)

        seen: set[str] = set()
        detail_limit = max_detail_pages or self._max_detail_pages
        for title, url in _interleave(detail_url_groups):
            if url in seen:
                continue
            seen.add(url)
            if len(releases) >= detail_limit:
                break
            detail_text = await self._fetch_text(url)
            detail_text = await self._append_motie_attachment_text(detail_text, url)
            detail_text = await self._append_tradedata_attachment_text(detail_text, url)
            detail_text = await self._append_customs_attachment_text(detail_text, url)
            release = parse_national_trade_release(
                title=title,
                url=url,
                source=_source_from_url(url),
                text=detail_text,
            )
            if release.export_amount_100m_usd is None and release.import_amount_100m_usd is None:
                continue
            releases.append(release)
        return _attach_previous_month_changes(_coalesce_duplicate_period_releases(releases))

    async def _fetch_text(self, url: str) -> str:
        if self._http_client is not None:
            response = await self._http_client.get(url, timeout=self._timeout_sec)
            response.raise_for_status()
            return response.text
        async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text

    async def _fetch_bytes(self, url: str) -> bytes:
        if self._http_client is not None:
            response = await self._http_client.get(url, timeout=self._timeout_sec)
            response.raise_for_status()
            return response.content
        async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content

    async def _append_tradedata_attachment_text(self, detail_text: str, detail_url: str) -> str:
        if "tradedata.go.kr" not in detail_url:
            return detail_text
        attachment_url = _extract_tradedata_hwpx_attachment_url(detail_text)
        if not attachment_url:
            return detail_text
        try:
            content = await self._fetch_bytes(attachment_url)
            attachment_text = _extract_hwpx_text(content)
        except (httpx.HTTPError, OSError, zipfile.BadZipFile):
            return detail_text
        if not attachment_text:
            return detail_text
        return f"{detail_text}\n{attachment_text}"

    async def _append_motie_attachment_text(self, detail_text: str, detail_url: str) -> str:
        if "motir.go.kr" not in detail_url and "motie.go.kr" not in detail_url:
            return detail_text
        attachment_url = _extract_motie_pdf_attachment_url(detail_text, detail_url)
        if not attachment_url:
            return detail_text
        try:
            if self._http_client is not None:
                content = await self._fetch_bytes(attachment_url)
            else:
                headers = {
                    "User-Agent": "Mozilla/5.0",
                    "Referer": detail_url,
                    "Accept": "text/html,application/pdf,*/*",
                }
                async with httpx.AsyncClient(
                    timeout=self._timeout_sec,
                    headers=headers,
                ) as client:
                    await client.get(detail_url)
                    response = await client.get(attachment_url)
                    response.raise_for_status()
                    content = response.content
            attachment_text = _extract_pdf_text(content)
        except (httpx.HTTPError, OSError):
            return detail_text
        if not attachment_text:
            return detail_text
        return f"{detail_text}\n{attachment_text}"

    async def _append_customs_attachment_text(self, detail_text: str, detail_url: str) -> str:
        if "customs.go.kr" not in detail_url:
            return detail_text
        if _customs_detail_has_monthly_summary_text(detail_text):
            return detail_text
        attachment_url = _extract_customs_hwpx_attachment_url(detail_text, detail_url)
        if not attachment_url:
            return detail_text
        try:
            content = await self._fetch_bytes(attachment_url)
            attachment_text = _extract_hwpx_text(content)
        except (httpx.HTTPError, OSError, zipfile.BadZipFile):
            return detail_text
        if not attachment_text:
            return detail_text
        return f"{detail_text}\n{attachment_text}"


def _pct(current: Optional[int], base: Optional[int]) -> Optional[float]:
    if current is None or base in (None, 0):
        return None
    return (current - base) / base * 100


def _pct_float(current: Optional[float], base: Optional[float]) -> Optional[float]:
    if current is None or base in (None, 0):
        return None
    return (current - base) / base * 100


def _diff_float(current: Optional[float], base: Optional[float]) -> Optional[float]:
    if current is None or base is None:
        return None
    return current - base


def _value_or_fallback(
    value: Optional[float],
    fallback: Optional[float],
) -> Optional[float]:
    return value if value is not None else fallback


def _safe_div_float(value: Optional[float], divisor: Optional[float]) -> Optional[float]:
    if value is None or divisor in (None, 0):
        return None
    return value / divisor


def _first_export(rows: list[TradeStatItem]) -> Optional[TradeStatItem]:
    if not rows:
        return None
    return max(rows, key=lambda row: row.export_amount_usd)


def build_jeju_semiconductor_report(
    *,
    current: TradeStatItem,
    previous_month: Optional[TradeStatItem],
    previous_year: Optional[TradeStatItem],
    jeju_total: Optional[TradeStatItem],
    fetched_at: datetime,
) -> JejuSemiconductorTradeReport:
    previous_month_amount = (
        previous_month.export_amount_usd if previous_month is not None else None
    )
    previous_year_amount = (
        previous_year.export_amount_usd if previous_year is not None else None
    )
    total_amount = jeju_total.export_amount_usd if jeju_total is not None else None
    share = None
    if total_amount:
        share = current.export_amount_usd / total_amount * 100
    return JejuSemiconductorTradeReport(
        period=current.period,
        export_amount_usd=current.export_amount_usd,
        previous_month_export_amount_usd=previous_month_amount,
        previous_year_export_amount_usd=previous_year_amount,
        mom_pct=_pct(current.export_amount_usd, previous_month_amount),
        yoy_pct=_pct(current.export_amount_usd, previous_year_amount),
        jeju_total_export_amount_usd=total_amount,
        jeju_export_share_pct=share,
        fetched_at=fetched_at.isoformat(),
        item_name=current.item_name or "전기기기류",
    )


def select_export_row(rows: list[TradeStatItem]) -> Optional[TradeStatItem]:
    return _first_export(rows)


def _period_key(period: str) -> str:
    """관세청 기간 표기("2026.05")를 "202605" 형태로 정규화한다."""
    match = re.match(r"\s*(\d{4})\D*(\d{1,2})", str(period or ""))
    if not match:
        return ""
    return f"{int(match.group(1)):04d}{int(match.group(2)):02d}"


def shift_yyyymm(yyyymm: str, delta_months: int) -> str:
    year = int(yyyymm[:4])
    month = int(yyyymm[4:6]) + delta_months
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return f"{year:04d}{month:02d}"


def build_jeju_region_trade_series(
    rows: list[TradeStatItem],
) -> list[JejuRegionTradeMonth]:
    """월별 제주지역 수출입 실적에 전월비·전년동월비를 붙여 최신순으로 반환한다."""
    by_key: dict[str, TradeStatItem] = {}
    for row in rows:
        key = _period_key(row.period)
        if key:
            by_key[key] = row

    series: list[JejuRegionTradeMonth] = []
    for key in sorted(by_key, reverse=True):
        row = by_key[key]
        previous_month = by_key.get(shift_yyyymm(key, -1))
        previous_year = by_key.get(shift_yyyymm(key, -12))
        series.append(
            JejuRegionTradeMonth(
                period=row.period,
                export_amount_usd=row.export_amount_usd,
                import_amount_usd=row.import_amount_usd,
                trade_balance_usd=row.trade_balance_usd,
                export_mom_pct=_pct(
                    row.export_amount_usd,
                    previous_month.export_amount_usd if previous_month else None,
                ),
                export_yoy_pct=_pct(
                    row.export_amount_usd,
                    previous_year.export_amount_usd if previous_year else None,
                ),
                import_mom_pct=_pct(
                    row.import_amount_usd,
                    previous_month.import_amount_usd if previous_month else None,
                ),
                import_yoy_pct=_pct(
                    row.import_amount_usd,
                    previous_year.import_amount_usd if previous_year else None,
                ),
            )
        )
    return series


def _clean_text(text: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(text or "")))
    text = re.sub(r"[\u00a0\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(\d)\s+(년|월|일|억|달러|%)", r"\1\2", text)
    return text.strip()


def _source_from_url(url: str) -> str:
    if "motir.go.kr" in url or "motie.go.kr" in url:
        return "motie"
    if "customs.go.kr" in url or "tradedata.go.kr" in url:
        return "customs"
    return "unknown"


def _phase_from_title(title: str) -> str:
    if re.search(r"1\s*(?:일)?\s*[~∼-]\s*(?:\d{1,2}\s*월\s*)?10\s*일", title):
        return "customs_10d"
    if re.search(r"1\s*(?:일)?\s*[~∼-]\s*(?:\d{1,2}\s*월\s*)?20\s*일", title):
        return "customs_20d"
    if "확정치" in title:
        return "customs_monthly_final"
    if "잠정치" in title:
        return "customs_monthly"
    return "motie_monthly"


def _period_from_title(title: str, phase: str) -> str:
    year_match = re.search(r"(\d{4})년", title)
    month_match = re.search(r"(\d{1,2})월", title)
    year = year_match.group(1) if year_match else ""
    month = month_match.group(1) if month_match else ""
    if not year or not month:
        return title
    if phase == "customs_10d":
        return f"{year}년 {month}월 1~10일"
    if phase == "customs_20d":
        return f"{year}년 {month}월 1~20일"
    return f"{year}년 {month}월"


def _period_year_month(period_label: str) -> Optional[tuple[int, int]]:
    match = re.search(r"(\d{4})년\s*(\d{1,2})월", period_label)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _period_year_from_title(title: str) -> Optional[int]:
    match = re.search(r"(\d{4})년", title)
    if not match:
        return None
    return int(match.group(1))


def _previous_month_key(period_label: str, phase: str) -> Optional[tuple[str, int, int]]:
    year_month = _period_year_month(period_label)
    if year_month is None:
        return None
    year, month = year_month
    month -= 1
    if month == 0:
        year -= 1
        month = 12
    return phase, year, month


def _previous_month_keys(period_label: str, phase: str) -> list[tuple[str, int, int]]:
    key = _previous_month_key(period_label, phase)
    if key is None:
        return []
    _, year, month = key
    if phase == "customs_monthly":
        return [key, ("customs_monthly_final", year, month), ("motie_monthly", year, month)]
    if phase == "customs_monthly_final":
        return [key, ("customs_monthly", year, month), ("motie_monthly", year, month)]
    if phase == "motie_monthly":
        return [key, ("customs_monthly_final", year, month), ("customs_monthly", year, month)]
    return [key]


def _release_month_key(release: NationalTradeTrendRelease) -> Optional[tuple[str, int, int]]:
    year_month = _period_year_month(release.period_label)
    if year_month is None:
        return None
    year, month = year_month
    return release.phase, year, month


def _attach_previous_month_changes(
    releases: list[NationalTradeTrendRelease],
) -> list[NationalTradeTrendRelease]:
    by_period = {
        key: release
        for release in releases
        if (key := _release_month_key(release)) is not None
    }
    enriched = []
    for release in releases:
        previous_candidates = [
            by_period[key]
            for key in _previous_month_keys(release.period_label, release.phase)
            if key in by_period
        ]
        previous = previous_candidates[0] if previous_candidates else None
        if previous is None:
            enriched.append(release)
            continue
        previous_daily = _first_release_with_value(
            previous_candidates,
            "export_daily_avg_100m_usd",
        )
        previous_semiconductor = _first_release_with_value(
            previous_candidates,
            "semiconductor_export_amount_100m_usd",
        )
        previous_semiconductor_daily = _first_release_with_value(
            previous_candidates,
            "semiconductor_daily_avg_100m_usd",
        )
        enriched.append(
            replace(
                release,
                export_mom_change_100m_usd=_value_or_fallback(
                    release.export_mom_change_100m_usd,
                    _diff_float(
                        release.export_amount_100m_usd,
                        previous.export_amount_100m_usd,
                    ),
                ),
                export_mom_pct=_value_or_fallback(
                    release.export_mom_pct,
                    _pct_float(
                        release.export_amount_100m_usd,
                        previous.export_amount_100m_usd,
                    ),
                ),
                export_daily_avg_mom_pct=_value_or_fallback(
                    release.export_daily_avg_mom_pct,
                    _pct_float(
                        release.export_daily_avg_100m_usd,
                        previous_daily.export_daily_avg_100m_usd
                        if previous_daily is not None
                        else None,
                    ),
                ),
                import_mom_change_100m_usd=_value_or_fallback(
                    release.import_mom_change_100m_usd,
                    _diff_float(
                        release.import_amount_100m_usd,
                        previous.import_amount_100m_usd,
                    ),
                ),
                import_mom_pct=_value_or_fallback(
                    release.import_mom_pct,
                    _pct_float(
                        release.import_amount_100m_usd,
                        previous.import_amount_100m_usd,
                    ),
                ),
                trade_balance_mom_change_100m_usd=_value_or_fallback(
                    release.trade_balance_mom_change_100m_usd,
                    _diff_float(
                        release.trade_balance_100m_usd,
                        previous.trade_balance_100m_usd,
                    ),
                ),
                semiconductor_mom_change_100m_usd=_value_or_fallback(
                    release.semiconductor_mom_change_100m_usd,
                    _diff_float(
                        release.semiconductor_export_amount_100m_usd,
                        previous_semiconductor.semiconductor_export_amount_100m_usd
                        if previous_semiconductor is not None
                        else None,
                    ),
                ),
                semiconductor_mom_pct=_value_or_fallback(
                    release.semiconductor_mom_pct,
                    _pct_float(
                        release.semiconductor_export_amount_100m_usd,
                        previous_semiconductor.semiconductor_export_amount_100m_usd
                        if previous_semiconductor is not None
                        else None,
                    ),
                ),
                semiconductor_daily_avg_mom_pct=_value_or_fallback(
                    release.semiconductor_daily_avg_mom_pct,
                    _pct_float(
                        release.semiconductor_daily_avg_100m_usd,
                        previous_semiconductor_daily.semiconductor_daily_avg_100m_usd
                        if previous_semiconductor_daily is not None
                        else None,
                    ),
                ),
            )
        )
    return enriched


def _first_release_with_value(
    releases: list[NationalTradeTrendRelease],
    field_name: str,
) -> Optional[NationalTradeTrendRelease]:
    return next(
        (
            release
            for release in releases
            if getattr(release, field_name) is not None
        ),
        None,
    )


def _coalesce_duplicate_period_releases(
    releases: list[NationalTradeTrendRelease],
) -> list[NationalTradeTrendRelease]:
    by_period: dict[tuple[str, str], NationalTradeTrendRelease] = {}
    order: list[tuple[str, str]] = []
    for release in releases:
        key = (release.phase, release.period_label)
        if key not in by_period:
            by_period[key] = release
            order.append(key)
            continue
        if _release_preference_score(release) > _release_preference_score(by_period[key]):
            by_period[key] = release
    return [by_period[key] for key in order]


def _release_preference_score(release: NationalTradeTrendRelease) -> int:
    if "www.customs.go.kr" in release.url:
        return 3
    if "motir.go.kr" in release.url or "motie.go.kr" in release.url:
        return 2
    if "tradedata.go.kr" in release.url:
        return 1
    return 0


def _float(pattern: str, text: str) -> Optional[float]:
    match = re.search(pattern, text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _trade_label_pattern(label: str) -> str:
    if label == "수출":
        # 제목의 "반도체 수출 400억 달러"가 본문 총수출액보다 먼저 잡히는 것을 막는다.
        return r"(?<!반도체)(?<!반도체 )수출(?!입)"
    if label == "수입":
        return r"(?<!수출)수입"
    return re.escape(label)


def _extract_amount_after(label: str, text: str) -> Optional[float]:
    label_pattern = _trade_label_pattern(label)
    # 다음 지표 언급 전까지만 금액을 찾는다. 같은 라벨의 다른 언급도 경계로 둔다.
    stop_pattern = (
        r"(?<!수출)수입|무역수지|수출"
        if label == "수출"
        else r"수출(?!입)|무역수지|(?<!수출)수입"
    )
    for label_match in re.finditer(label_pattern, text):
        window = text[label_match.end() : label_match.end() + 180]
        stop_match = re.search(stop_pattern, window)
        if stop_match:
            window = window[: stop_match.start()]
        amount_match = re.search(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*억\s*달러", window)
        if not amount_match:
            continue
        try:
            return float(amount_match.group(1).replace(",", ""))
        except ValueError:
            continue
    return None


def _signed_pct(value: Optional[float], text: str, start: int = 0) -> Optional[float]:
    if value is None:
        return None
    window = text[start : start + 80]
    if "감소" in window or "△" in window:
        return -abs(value)
    return value


def _extract_yoy_after(label: str, text: str) -> Optional[float]:
    label_pattern = _trade_label_pattern(label)
    stop_pattern = r"(?<!수출)수입|무역수지" if label == "수출" else r"수출(?!입)|무역수지"
    for label_match in re.finditer(label_pattern, text):
        window = text[label_match.end() : label_match.end() + 180]
        stop_match = re.search(stop_pattern, window)
        if stop_match:
            window = window[: stop_match.start()]
        pct_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", window)
        if not pct_match:
            continue
        try:
            value = float(pct_match.group(1))
        except ValueError:
            continue
        return _signed_pct(value, window, pct_match.end())
    return None


def _extract_semiconductor_amount(text: str) -> Optional[float]:
    return _float(r"반도체\s*\(\s*([0-9]+(?:\.[0-9]+)?)\s*억\s*달러\s*\)", text)


def _extract_semiconductor_yoy(text: str) -> Optional[float]:
    for match in re.finditer(r"반도체\s*\(\s*(△)?\s*([0-9]+(?:\.[0-9]+)?)\s*%\s*\)", text):
        window = text[max(0, match.start() - 80) : match.start()]
        if "전년동기대비" not in window and "전년 동기 대비" not in window:
            continue
        value = float(match.group(2))
        return -value if match.group(1) else value
    return None


def _extract_working_days(text: str) -> tuple[Optional[float], Optional[float]]:
    match = re.search(
        r"조업일수\s*\[\s*\([^)]*\)\s*([0-9]+(?:\.[0-9]+)?)\s*일\s*,\s*\([^)]*\)\s*([0-9]+(?:\.[0-9]+)?)\s*일",
        text,
    )
    if not match:
        return None, None
    return float(match.group(2)), float(match.group(1))


def _extract_export_daily_avg(text: str) -> Optional[float]:
    match = re.search(
        r"일평균수출액\s*\[[^\]]*,\s*\([^)]*\)\s*([0-9]+(?:\.[0-9]+)?)\s*억\s*달러",
        text,
    )
    if not match:
        phrase_match = re.search(r"일평균\s*수출(?:액)?(?:은|액은)?", text)
        if phrase_match:
            window = text[phrase_match.end() : phrase_match.end() + 120]
            match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*억\s*달러", window)
    if not match:
        return None
    return float(match.group(1))


def _number_tokens(text: str) -> list[float]:
    numbers = []
    for match in re.finditer(r"-?\d[\d,]*(?:\.\d+)?", text):
        numbers.append(float(match.group(0).replace(",", "")))
    return numbers


def _monthly_table_label_pattern(label: str) -> str:
    if label == "무역수지":
        return r"무역\s*수지"
    return re.escape(label)


def _extract_monthly_table_amounts(
    label: str,
    period_label: str,
    text: str,
) -> tuple[Optional[float], Optional[float]]:
    year_month = _period_year_month(period_label)
    if year_month is None:
        return None, None
    year, month = year_month
    if "월별 수출입현황" not in text:
        return None, None

    row_pattern = _monthly_table_label_pattern(label)
    segment = text[text.find("월별 수출입현황") :]
    match = re.search(
        rf"{row_pattern}\s+{year - 1}\s+금액\s+(.+?)\s+{year}\s+금액\s+(.+?)(?=\s+증감률|\s+수출|\s+수입|\s+무역\s*수지|\s+\*)",
        segment,
    )
    if not match:
        return None, None

    previous_year_amounts = _number_tokens(match.group(1))
    current_year_amounts = _number_tokens(match.group(2))
    if len(current_year_amounts) < month:
        return None, None
    current = current_year_amounts[month - 1] / 100
    if month > 1:
        previous = current_year_amounts[month - 2] / 100
    elif len(previous_year_amounts) >= 12:
        previous = previous_year_amounts[11] / 100
    else:
        return current, None
    return current, previous


def _extract_monthly_table_mom(
    label: str,
    period_label: str,
    text: str,
) -> tuple[Optional[float], Optional[float]]:
    current, previous = _extract_monthly_table_amounts(label, period_label, text)
    return _diff_float(current, previous), _pct_float(current, previous)


def _extract_balance(text: str) -> tuple[Optional[float], str]:
    match = re.search(r"무역수지(?:는)?\s*([0-9]+(?:\.[0-9]+)?)\s*억\s*달러\s*(흑자|적자)", text)
    if not match:
        return None, ""
    amount = float(match.group(1).replace(",", ""))
    label = match.group(2)
    return (-amount if label == "적자" else amount), label


def _extract_highlights(text: str) -> list[str]:
    candidates = []
    for sentence in re.split(r"[.。◇ㅇ]\s*", text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if any(keyword in sentence for keyword in ("반도체", "승용차", "자동차", "선박", "석유제품", "주요 수출국")):
            candidates.append(sentence[:180])
        if len(candidates) >= 3:
            break
    return candidates


def _attr(tag: str, name: str) -> str:
    match = re.search(
        rf"\b{name}\s*=\s*([\"'])(.*?)\1",
        tag,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    return html.unescape(match.group(2)).strip()


def _interleave(groups: list[list[tuple[str, str]]]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    max_len = max((len(group) for group in groups), default=0)
    for index in range(max_len):
        for group in groups:
            if index < len(group):
                items.append(group[index])
    return items


_LIST_PAGE_PARAM_BY_HOST = {"www.customs.go.kr": "currPage"}
_DEFAULT_LIST_PAGE_PARAM = "pageIndex"


def _list_page_param(url: str) -> str:
    """게시판마다 페이지 번호 쿼리 키가 다르다. 관세청은 pageIndex를 무시한다."""
    host = urlsplit(url).netloc.lower()
    return _LIST_PAGE_PARAM_BY_HOST.get(host, _DEFAULT_LIST_PAGE_PARAM)


def _expand_list_page_urls(list_urls: list[str], list_page_count: int) -> list[str]:
    page_count = max(1, int(list_page_count or 1))
    urls = []
    for url in list_urls:
        urls.append(url)
        page_param = _list_page_param(url)
        for page_index in range(2, page_count + 1):
            urls.append(_with_query_param(url, page_param, str(page_index)))
    return urls


def _with_query_param(url: str, key: str, value: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query[key] = value
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query),
            parts.fragment,
        )
    )


def parse_national_trade_release(
    *,
    title: str,
    url: str,
    source: str,
    text: str,
) -> NationalTradeTrendRelease:
    cleaned_title = _clean_text(title)
    cleaned_text = _clean_text(text)
    phase = _phase_from_title(cleaned_title)
    export_amount = _extract_amount_after("수출", cleaned_text)
    import_amount = _extract_amount_after("수입", cleaned_text)
    balance_amount, balance_label = _extract_balance(cleaned_text)
    working_days_current, working_days_previous_year = _extract_working_days(cleaned_text)
    semiconductor_amount = _extract_semiconductor_amount(cleaned_text)
    period_label = _period_from_title(cleaned_title, phase)
    export_mom_change, export_mom_pct = _extract_monthly_table_mom(
        "수출",
        period_label,
        cleaned_text,
    )
    import_mom_change, import_mom_pct = _extract_monthly_table_mom(
        "수입",
        period_label,
        cleaned_text,
    )
    trade_balance_mom_change, _ = _extract_monthly_table_mom(
        "무역수지",
        period_label,
        cleaned_text,
    )
    published = ""
    published_match = re.search(r"등록일\s*\|?\s*(\d{4}[.-]\d{2}[.-]\d{2})", cleaned_text)
    if published_match:
        published = published_match.group(1).replace(".", "-")
    return NationalTradeTrendRelease(
        source=source,
        phase=phase,
        title=cleaned_title,
        url=url,
        period_label=period_label,
        export_amount_100m_usd=export_amount,
        export_yoy_pct=_extract_yoy_after("수출", cleaned_text),
        export_mom_change_100m_usd=export_mom_change,
        export_mom_pct=export_mom_pct,
        export_daily_avg_100m_usd=_extract_export_daily_avg(cleaned_text)
        or _safe_div_float(export_amount, working_days_current),
        import_amount_100m_usd=import_amount,
        import_yoy_pct=_extract_yoy_after("수입", cleaned_text),
        import_mom_change_100m_usd=import_mom_change,
        import_mom_pct=import_mom_pct,
        trade_balance_100m_usd=balance_amount,
        trade_balance_label=balance_label,
        trade_balance_mom_change_100m_usd=trade_balance_mom_change,
        semiconductor_export_amount_100m_usd=semiconductor_amount,
        semiconductor_yoy_pct=_extract_semiconductor_yoy(cleaned_text),
        semiconductor_daily_avg_100m_usd=_safe_div_float(
            semiconductor_amount,
            working_days_current,
        ),
        working_days_current=working_days_current,
        working_days_previous_year=working_days_previous_year,
        published_at=published,
        highlights=_extract_highlights(cleaned_text),
    )


def _extract_national_trade_links(html_text: str, base_url: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    for match in re.finditer(
        r"(<a\b[^>]*>)(.*?)</a>",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        tag = match.group(1)
        href = _attr(tag, "href")
        title = _clean_text(match.group(2))
        title_attr = _attr(tag, "title")
        if title_attr:
            title = title_attr
        if not title:
            continue
        is_customs = "수출입 현황" in title and (
            "잠정치" in title or "확정치" in title
        )
        is_motie = re.fullmatch(r"\d{4}년\s*\d{1,2}월\s*수출입\s*동향", title) is not None
        if not (is_customs or is_motie):
            continue
        if is_customs and "nttInfoBtn" in tag:
            ntt_sn = _attr(tag, "data-id")
            ntt_sn_url = _attr(tag, "data-url")
            if not ntt_sn:
                continue
            href = f"/kcs/na/ntt/selectNttInfo.do?bbsId=1362&mi=2891&nttSn={ntt_sn}"
            if ntt_sn_url:
                href = f"{href}&nttSnUrl={ntt_sn_url}"
        elif is_motie and href.startswith("javascript:"):
            article_match = re.search(r"article\.view\(['\"]?(\d+)", href)
            if not article_match:
                continue
            href = f"/kor/article/ATCL3f49a5a8c/{article_match.group(1)}/view"
        elif is_customs and "ets_f_prccMenuAdmin" in href:
            params = _extract_tradedata_link_params(href)
            if not params:
                continue
            href = (
                "/cts/hmpg/openETS0100210Q.do"
                f"?blbrTpcd={params['blbrTpcd']}"
                f"&ntarSrno={params['ntarSrno']}"
                f"&menuId={params['menuId']}"
            )
        links.append((title, urljoin(base_url, href)))
    return links


def _extract_tradedata_link_params(href: str) -> Optional[dict[str, str]]:
    params = {}
    for key in ("blbrTpcd", "ntarSrno", "menuId"):
        match = re.search(rf"{key}\s*:\s*['\"]([^'\"]+)['\"]", href)
        if not match:
            return None
        params[key] = match.group(1)
    return params


def _extract_tradedata_hwpx_attachment_url(detail_text: str) -> str:
    for match in re.finditer(
        r"<li\b[^>]*class=[\"'][^\"']*btn_download_detl[^\"']*[\"'][^>]*>.*?</li>",
        detail_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        item_html = match.group(0)
        if ".hwpx" not in item_html.lower():
            continue
        file_id = _attr(item_html, "data-attch-file-id")
        if not file_id:
            continue
        return f"https://tradedata.go.kr/cts/filedownload/cubeFiledownload.do?attchFileId={file_id}"
    return ""


def _extract_motie_pdf_attachment_url(detail_text: str, detail_url: str) -> str:
    for match in re.finditer(
        r"(<a\b[^>]*>)(.*?)</a>",
        detail_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        tag = match.group(1)
        href = _attr(tag, "href")
        label = _clean_text(match.group(2))
        if ".pdf" not in f"{href} {label}".lower():
            continue
        location_match = re.search(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", href)
        if location_match:
            href = location_match.group(1)
        if not href or href.startswith("javascript:"):
            continue
        return urljoin(detail_url, href)
    return ""


def _extract_customs_hwpx_attachment_url(detail_text: str, detail_url: str) -> str:
    for match in re.finditer(
        r"(<a\b[^>]*>)(.*?)</a>",
        detail_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        tag = match.group(1)
        href = _attr(tag, "href")
        title = _attr(tag, "title")
        label = _clean_text(match.group(2))
        if not href:
            continue
        if ".hwpx" not in f"{title} {label}".lower():
            continue
        if "nttFileDownload.do" not in href:
            continue
        return urljoin(detail_url, href)
    return ""


def _customs_detail_has_monthly_summary_text(detail_text: str) -> bool:
    cleaned_text = _clean_text(detail_text)
    return "일평균수출액" in cleaned_text and "월별 수출입현황" in cleaned_text


def _extract_hwpx_text(content: bytes) -> str:
    if not content.startswith(b"PK"):
        return ""
    chunks = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for name in archive.namelist():
            if not name.endswith(".xml") or "Contents/" not in name:
                continue
            xml_text = archive.read(name).decode("utf-8", errors="ignore")
            plain = _clean_text(xml_text)
            if plain:
                chunks.append(plain)
    return " ".join(chunks)


def _extract_pdf_text(content: bytes) -> str:
    if not content.startswith(b"%PDF"):
        return ""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""

    try:
        reader = PdfReader(io.BytesIO(content))
        return _clean_text(" ".join(page.extract_text() or "" for page in reader.pages))
    except Exception:
        return ""


def format_jeju_semiconductor_report_html(
    report: JejuSemiconductorTradeReport,
) -> str:
    def money(value: Optional[int]) -> str:
        if value is None:
            return "-"
        return f"{value / 1_000_000:,.1f}백만 달러"

    def pct(value: Optional[float]) -> str:
        if value is None:
            return "-"
        return f"{value:+.1f}%"

    item_name = html.escape(report.item_name or "전기기기류", quote=False)
    return "\n".join(
        [
            f"📦 <b>제주 {item_name} 수출 ({html.escape(report.period, quote=False)})</b>",
            f"수출액: <b>{money(report.export_amount_usd)}</b>",
            f"전월비: {pct(report.mom_pct)} / 전년비: {pct(report.yoy_pct)}",
            f"제주 전체 수출 내 비중: {pct(report.jeju_export_share_pct)}",
            f"전월: {money(report.previous_month_export_amount_usd)}",
            f"전년동월: {money(report.previous_year_export_amount_usd)}",
            "",
            "※ 지역 통관 통계이며 세부 반도체 품목만 분리되지 않을 수 있습니다.",
        ]
    )


def format_national_trade_trend_report_html(
    release: NationalTradeTrendRelease,
) -> str:
    def money(value: Optional[float], include_label: bool = False) -> str:
        if value is None:
            return "-"
        label = f" {release.trade_balance_label}" if include_label and release.trade_balance_label else ""
        return f"{abs(value):,.1f}억 달러{label}"

    def pct(value: Optional[float]) -> str:
        if value is None:
            return "-"
        return f"{value:+.1f}%"

    def diff(value: Optional[float]) -> str:
        if value is None:
            return "-"
        return f"{value:+,.1f}억"

    def change_text(yoy: Optional[float], mom_change: Optional[float], mom_pct: Optional[float]) -> str:
        parts = [f"전년비 {pct(yoy)}"]
        if mom_change is not None or mom_pct is not None:
            parts.append(f"전월비 {diff(mom_change)}, {pct(mom_pct)}")
        return " / ".join(parts)

    title = "전국 수출입 잠정치"
    if release.phase == "motie_monthly":
        title = "전국 월간 수출입동향"
    elif release.phase == "customs_monthly":
        title = "전국 월간 수출입 잠정치"
    elif release.phase == "customs_monthly_final":
        title = "전국 월간 수출입 확정치"
    lines = [
        f"🌐 <b>{title} ({html.escape(release.period_label, quote=False)})</b>",
        f"수출: <b>{money(release.export_amount_100m_usd)}</b> "
        f"({change_text(release.export_yoy_pct, release.export_mom_change_100m_usd, release.export_mom_pct)})",
    ]
    if release.export_daily_avg_100m_usd is not None:
        working_days = (
            f", 조업 {release.working_days_current:.1f}일"
            if release.working_days_current is not None
            else ""
        )
        lines.append(
            f"일평균 수출: <b>{money(release.export_daily_avg_100m_usd)}</b> "
            f"(전월비 {pct(release.export_daily_avg_mom_pct)}{working_days})"
        )
    if release.semiconductor_export_amount_100m_usd is not None:
        semiconductor_parts = []
        if release.semiconductor_yoy_pct is not None:
            semiconductor_parts.append(f"전년비 {pct(release.semiconductor_yoy_pct)}")
        if (
            release.semiconductor_mom_change_100m_usd is not None
            or release.semiconductor_mom_pct is not None
        ):
            semiconductor_parts.append(
                f"전월비 {diff(release.semiconductor_mom_change_100m_usd)}, "
                f"{pct(release.semiconductor_mom_pct)}"
            )
        if release.semiconductor_daily_avg_mom_pct is not None:
            semiconductor_parts.append(f"일평균 {pct(release.semiconductor_daily_avg_mom_pct)}")
        lines.append(
            f"반도체: <b>{money(release.semiconductor_export_amount_100m_usd)}</b> "
            f"({' / '.join(semiconductor_parts)})"
            if semiconductor_parts
            else f"반도체: <b>{money(release.semiconductor_export_amount_100m_usd)}</b>"
        )
    lines += [
        f"수입: <b>{money(release.import_amount_100m_usd)}</b> "
        f"({change_text(release.import_yoy_pct, release.import_mom_change_100m_usd, release.import_mom_pct)})",
        f"무역수지: <b>{money(release.trade_balance_100m_usd, include_label=True)}</b>",
    ]
    if release.trade_balance_mom_change_100m_usd is not None:
        lines[-1] += f" (전월차 {diff(release.trade_balance_mom_change_100m_usd)})"
    if release.highlights:
        lines += ["", *[f"• {html.escape(item, quote=False)}" for item in release.highlights[:3]]]
    lines += ["", f'<a href="{html.escape(release.url, quote=False)}">원문 보기</a>']
    return "\n".join(lines)
