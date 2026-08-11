from __future__ import annotations

import html
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin
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
    item_name: str = "제주 반도체"

    @property
    def dedup_key(self) -> str:
        return f"jeju_semiconductor:{self.period}:{self.export_amount_usd}"


@dataclass(frozen=True)
class NationalTradeTrendRelease:
    source: str
    phase: str
    title: str
    url: str
    period_label: str
    export_amount_100m_usd: Optional[float] = None
    export_yoy_pct: Optional[float] = None
    import_amount_100m_usd: Optional[float] = None
    import_yoy_pct: Optional[float] = None
    trade_balance_100m_usd: Optional[float] = None
    trade_balance_label: str = ""
    published_at: str = ""
    highlights: list[str] = None

    @property
    def dedup_key(self) -> str:
        return f"national_trade:{self.phase}:{self.period_label}:{self.url}"


def _text(item: ET.Element, tag: str, default: str = "") -> str:
    node = item.find(tag)
    if node is None or node.text is None:
        return default
    return node.text.strip()


def _int_text(item: ET.Element, tag: str) -> int:
    raw = _text(item, tag, "0").replace(",", "")
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return 0


def parse_customs_trade_xml(xml_text: str) -> list[TradeStatItem]:
    root = ET.fromstring(xml_text)
    result_code = root.findtext("./header/resultCode", default="")
    if result_code and result_code != "00":
        result_msg = root.findtext("./header/resultMsg", default="UNKNOWN ERROR")
        raise ValueError(f"관세청 수출입통계 API 오류: {result_code} {result_msg}")

    rows: list[TradeStatItem] = []
    for item in root.findall(".//item"):
        period = _text(item, "year")
        if not period or period == "총계":
            continue
        rows.append(
            TradeStatItem(
                period=period,
                item_name=_text(item, "statKor"),
                item_code=_text(item, "hsCode"),
                export_amount_usd=_int_text(item, "expDlr"),
                import_amount_usd=_int_text(item, "impDlr"),
                trade_balance_usd=_int_text(item, "balPayments"),
                export_weight=_int_text(item, "expWgt"),
                import_weight=_int_text(item, "impWgt"),
            )
        )
    return rows


class CustomsTradeStatClient:
    """관세청 GW 수출입통계 API 클라이언트."""

    DEFAULT_BASE_URL = "https://openapi.customs.go.kr/openapi/service/newTradestatistics/"

    def __init__(
        self,
        *,
        service_key: str,
        http_client=None,
        base_url: str = DEFAULT_BASE_URL,
        timeout_sec: float = 10.0,
        sido_param_name: str = "searchSidoCd",
        sido_code: str = "50",
    ) -> None:
        self._service_key = service_key
        self._base_url = base_url if base_url.endswith("/") else f"{base_url}/"
        self._timeout_sec = timeout_sec
        self._http_client = http_client
        self._sido_param_name = sido_param_name
        self._sido_code = sido_code

    async def fetch_sido_item_month(
        self, yyyymm: str, item_code: str
    ) -> list[TradeStatItem]:
        params = {
            "searchBgnDe": yyyymm,
            "searchEndDe": yyyymm,
            "searchItemCd": item_code,
            self._sido_param_name: self._sido_code,
            "serviceKey": self._service_key,
        }
        return await self._get("getsidoitemtradeList", params)

    async def fetch_sido_total_month(self, yyyymm: str) -> list[TradeStatItem]:
        params = {
            "searchBgnDe": yyyymm,
            "searchEndDe": yyyymm,
            self._sido_param_name: self._sido_code,
            "serviceKey": self._service_key,
        }
        return await self._get("getsidotradeList", params)

    async def _get(self, operation: str, params: dict) -> list[TradeStatItem]:
        url = urljoin(self._base_url, operation)
        if self._http_client is not None:
            response = await self._http_client.get(url, params=params, timeout=self._timeout_sec)
            response.raise_for_status()
            return parse_customs_trade_xml(response.text)

        async with httpx.AsyncClient(timeout=self._timeout_sec) as client:
            response = await client.get(url, params=params)
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
        releases: list[NationalTradeTrendRelease] = []
        detail_url_groups: list[list[tuple[str, str]]] = []
        for list_url in self._list_urls:
            html_text = await self._fetch_text(list_url)
            detail_url_groups.append(_extract_national_trade_links(html_text, list_url))

        seen: set[str] = set()
        for title, url in _interleave(detail_url_groups):
            if url in seen:
                continue
            seen.add(url)
            if len(releases) >= self._max_detail_pages:
                break
            detail_text = await self._fetch_text(url)
            detail_text = await self._append_tradedata_attachment_text(detail_text, url)
            release = parse_national_trade_release(
                title=title,
                url=url,
                source=_source_from_url(url),
                text=detail_text,
            )
            if release.export_amount_100m_usd is None and release.import_amount_100m_usd is None:
                continue
            releases.append(release)
        return releases

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


def _pct(current: Optional[int], base: Optional[int]) -> Optional[float]:
    if current is None or base in (None, 0):
        return None
    return (current - base) / base * 100


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
        item_name=current.item_name or "제주 반도체",
    )


def select_export_row(rows: list[TradeStatItem]) -> Optional[TradeStatItem]:
    return _first_export(rows)


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
        return r"수출(?!입)"
    if label == "수입":
        return r"(?<!수출)수입"
    return re.escape(label)


def _extract_amount_after(label: str, text: str) -> Optional[float]:
    return _float(
        rf"{_trade_label_pattern(label)}[^。]{{0,180}}?([0-9]+(?:\.[0-9]+)?)\s*억\s*달러",
        text,
    )


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
    published = ""
    published_match = re.search(r"등록일\s*\|?\s*(\d{4}[.-]\d{2}[.-]\d{2})", cleaned_text)
    if published_match:
        published = published_match.group(1).replace(".", "-")
    return NationalTradeTrendRelease(
        source=source,
        phase=phase,
        title=cleaned_title,
        url=url,
        period_label=_period_from_title(cleaned_title, phase),
        export_amount_100m_usd=export_amount,
        export_yoy_pct=_extract_yoy_after("수출", cleaned_text),
        import_amount_100m_usd=import_amount,
        import_yoy_pct=_extract_yoy_after("수입", cleaned_text),
        trade_balance_100m_usd=balance_amount,
        trade_balance_label=balance_label,
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
        is_customs = "수출입 현황" in title and "잠정치" in title
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

    return "\n".join(
        [
            f"📦 <b>제주 반도체 수출 ({html.escape(report.period, quote=False)})</b>",
            f"수출액: <b>{money(report.export_amount_usd)}</b>",
            f"전월비: {pct(report.mom_pct)} / 전년비: {pct(report.yoy_pct)}",
            f"제주 전체 수출 내 비중: {pct(report.jeju_export_share_pct)}",
            f"전월: {money(report.previous_month_export_amount_usd)}",
            f"전년동월: {money(report.previous_year_export_amount_usd)}",
            "",
            "※ 지역 통관 통계이므로 제주반도체 매출과 1:1 대응하지 않습니다.",
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

    title = "전국 수출입 잠정치"
    if release.phase == "motie_monthly":
        title = "전국 월간 수출입동향"
    elif release.phase == "customs_monthly":
        title = "전국 월간 수출입 잠정치"
    elif release.phase == "customs_monthly_final":
        title = "전국 월간 수출입 확정치"
    lines = [
        f"🌐 <b>{title} ({html.escape(release.period_label, quote=False)})</b>",
        f"수출: <b>{money(release.export_amount_100m_usd)}</b> ({pct(release.export_yoy_pct)})",
        f"수입: <b>{money(release.import_amount_100m_usd)}</b> ({pct(release.import_yoy_pct)})",
        f"무역수지: <b>{money(release.trade_balance_100m_usd, include_label=True)}</b>",
    ]
    if release.highlights:
        lines += ["", *[f"• {html.escape(item, quote=False)}" for item in release.highlights[:3]]]
    lines += ["", f'<a href="{html.escape(release.url, quote=False)}">원문 보기</a>']
    return "\n".join(lines)
