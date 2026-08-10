from __future__ import annotations

import html
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
