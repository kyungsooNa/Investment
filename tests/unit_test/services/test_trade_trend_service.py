from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from services.trade_trend_service import (
    CustomsTradeStatClient,
    TradeStatItem,
    build_jeju_semiconductor_report,
    parse_customs_trade_xml,
)


CUSTOMS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header>
    <resultCode>00</resultCode>
    <resultMsg>NORMAL SERVICE.</resultMsg>
  </header>
  <body>
    <items>
      <item>
        <year>2026.05</year>
        <statKor>집적회로 반도체</statKor>
        <hsCode>8542</hsCode>
        <expDlr>46585000</expDlr>
        <expWgt>1200</expWgt>
        <impDlr>1000</impDlr>
        <impWgt>10</impWgt>
        <balPayments>46584000</balPayments>
      </item>
      <item>
        <year>총계</year>
        <statKor>-</statKor>
        <hsCode>-</hsCode>
        <expDlr>46585000</expDlr>
        <impDlr>1000</impDlr>
        <balPayments>46584000</balPayments>
      </item>
    </items>
  </body>
</response>
"""


def test_parse_customs_trade_xml_skips_total_row_and_parses_amounts():
    rows = parse_customs_trade_xml(CUSTOMS_XML)

    assert rows == [
        TradeStatItem(
            period="2026.05",
            item_name="집적회로 반도체",
            item_code="8542",
            export_amount_usd=46585000,
            import_amount_usd=1000,
            trade_balance_usd=46584000,
            export_weight=1200,
            import_weight=10,
        )
    ]


def test_parse_customs_trade_xml_raises_on_api_error():
    xml = "<response><header><resultCode>99</resultCode><resultMsg>bad key</resultMsg></header></response>"

    with pytest.raises(ValueError, match="bad key"):
        parse_customs_trade_xml(xml)


class DummyResponse:
    status_code = 200
    text = CUSTOMS_XML

    def raise_for_status(self):
        return None


class DummyHttpClient:
    def __init__(self):
        self.get = AsyncMock(return_value=DummyResponse())


@pytest.mark.asyncio
async def test_customs_client_calls_sido_item_endpoint_with_configured_params():
    http_client = DummyHttpClient()
    client = CustomsTradeStatClient(
        service_key="test-key",
        http_client=http_client,
        base_url="https://example.test/openapi/service/newTradestatistics",
        sido_param_name="searchSidoCd",
        sido_code="50",
    )

    rows = await client.fetch_sido_item_month("202605", "8542")

    assert rows[0].export_amount_usd == 46585000
    http_client.get.assert_awaited_once()
    url = http_client.get.await_args.args[0]
    params = http_client.get.await_args.kwargs["params"]
    assert url.endswith("/getsidoitemtradeList")
    assert params["searchBgnDe"] == "202605"
    assert params["searchEndDe"] == "202605"
    assert params["searchItemCd"] == "8542"
    assert params["searchSidoCd"] == "50"
    assert params["serviceKey"] == "test-key"


def test_build_jeju_semiconductor_report_calculates_mom_yoy_and_share():
    current = TradeStatItem("2026.05", "집적회로 반도체", "8542", 46585000, 0, 0)
    prev = TradeStatItem("2026.04", "집적회로 반도체", "8542", 30000000, 0, 0)
    prev_year = TradeStatItem("2025.05", "집적회로 반도체", "8542", 10000000, 0, 0)
    total = TradeStatItem("2026.05", "제주 전체", "-", 63590000, 0, 0)

    report = build_jeju_semiconductor_report(
        current=current,
        previous_month=prev,
        previous_year=prev_year,
        jeju_total=total,
        fetched_at=datetime(2026, 6, 16, 9, 30),
    )

    assert report.period == "2026.05"
    assert report.export_amount_usd == 46585000
    assert report.mom_pct == pytest.approx(55.2833)
    assert report.yoy_pct == pytest.approx(365.85)
    assert report.jeju_export_share_pct == pytest.approx(73.2584)
    assert report.dedup_key == "jeju_semiconductor:2026.05:46585000"
