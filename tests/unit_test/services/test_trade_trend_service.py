from datetime import datetime
import io
from unittest.mock import AsyncMock
import zipfile

import httpx
import pytest

from services.trade_trend_service import (
    CustomsTradeStatClient,
    build_jeju_region_trade_series,
    NationalTradeTrendRelease,
    NationalTradeTrendWebClient,
    TradeStatItem,
    build_jeju_semiconductor_report,
    format_national_trade_trend_report_html,
    parse_national_trade_release,
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


CUSTOMS_SIDO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header>
    <resultCode>00</resultCode>
    <resultMsg>NORMAL SERVICE.</resultMsg>
  </header>
  <body>
    <items>
      <item>
        <priodTitle>2026.05</priodTitle>
        <sidoNm>제주</sidoNm>
        <expUsdAmt>25000000</expUsdAmt>
        <impUsdAmt>9900000</impUsdAmt>
        <cmtrBlncAmt>15100000</cmtrBlncAmt>
      </item>
    </items>
  </body>
</response>
"""


CUSTOMS_SIDO_ITEM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header>
    <resultCode>00</resultCode>
    <resultMsg>NORMAL SERVICE.</resultMsg>
  </header>
  <body>
    <items>
      <item>
        <priodTitle>2026.05</priodTitle>
        <korePrlstNm>집적회로 반도체</korePrlstNm>
        <hsSgn>8542</hsSgn>
        <expUsdAmt>46585000</expUsdAmt>
        <impUsdAmt>1000</impUsdAmt>
        <cmtrBlncAmt>46584000</cmtrBlncAmt>
      </item>
      <item>
        <priodTitle>2026.05</priodTitle>
        <korePrlstNm>기타</korePrlstNm>
        <hsSgn>9999</hsSgn>
        <expUsdAmt>1</expUsdAmt>
        <impUsdAmt>2</impUsdAmt>
        <cmtrBlncAmt>-1</cmtrBlncAmt>
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


def test_parse_customs_trade_xml_parses_current_sido_fields():
    rows = parse_customs_trade_xml(CUSTOMS_SIDO_XML)

    assert rows == [
        TradeStatItem(
            period="2026.05",
            item_name="제주",
            item_code="",
            export_amount_usd=25000000,
            import_amount_usd=9900000,
            trade_balance_usd=15100000,
            export_weight=0,
            import_weight=0,
        )
    ]


def test_parse_customs_trade_xml_raises_on_api_error():
    xml = "<response><header><resultCode>99</resultCode><resultMsg>bad key</resultMsg></header></response>"

    with pytest.raises(ValueError, match="bad key"):
        parse_customs_trade_xml(xml)


def test_parse_customs_trade_xml_raises_on_gateway_auth_error():
    xml = (
        "<OpenAPI_ServiceResponse><cmmMsgHeader>"
        "<errMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</errMsg>"
        "<returnAuthMsg>등록되지 않은 서비스키</returnAuthMsg>"
        "<returnReasonCode>30</returnReasonCode>"
        "</cmmMsgHeader></OpenAPI_ServiceResponse>"
    )

    with pytest.raises(ValueError, match="30.*등록되지 않은 서비스키"):
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
    http_client.get = AsyncMock(return_value=type("Response", (), {
        "text": CUSTOMS_SIDO_ITEM_XML,
        "raise_for_status": lambda self: None,
    })())
    client = CustomsTradeStatClient(
        service_key="encoded%2Fkey",
        http_client=http_client,
        base_url="https://example.test/sidotrade",
        item_base_url="https://example.test/sidoitemtrade",
        sido_param_name="sidoCd",
        sido_code="50",
    )

    rows = await client.fetch_sido_item_month("202605", "8542")

    assert len(rows) == 1
    assert rows[0].export_amount_usd == 46585000
    http_client.get.assert_awaited_once()
    url = http_client.get.await_args.args[0]
    params = http_client.get.await_args.kwargs["params"]
    assert url.endswith("/getSidoitemtradeList")
    assert params["strtYymm"] == "202605"
    assert params["endYymm"] == "202605"
    assert "searchItemCd" not in params
    assert params["sidoCd"] == "50"
    assert params["serviceKey"] == "encoded/key"


@pytest.mark.asyncio
async def test_customs_client_falls_back_to_hs_chapter_when_detail_code_is_unavailable():
    http_client = DummyHttpClient()
    http_client.get = AsyncMock(return_value=type("Response", (), {
        "text": CUSTOMS_SIDO_ITEM_XML.replace("<hsSgn>8542</hsSgn>", "<hsSgn>85</hsSgn>"),
        "raise_for_status": lambda self: None,
    })())
    client = CustomsTradeStatClient(
        service_key="test-key",
        http_client=http_client,
        item_base_url="https://example.test/sidoitemtrade",
    )

    rows = await client.fetch_sido_item_month("202605", "8542")

    assert len(rows) == 1
    assert rows[0].item_code == "85"
    assert rows[0].export_amount_usd == 46585000


JEJU_TOTAL_RANGE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header>
    <resultCode>00</resultCode>
    <resultMsg>NORMAL SERVICE.</resultMsg>
  </header>
  <body>
    <items>
      <item>
        <year>2025.05</year>
        <statKor>제주</statKor>
        <hsCode>-</hsCode>
        <expDlr>10000000</expDlr>
        <impDlr>8000000</impDlr>
        <balPayments>2000000</balPayments>
      </item>
      <item>
        <year>2026.04</year>
        <statKor>제주</statKor>
        <hsCode>-</hsCode>
        <expDlr>20000000</expDlr>
        <impDlr>9000000</impDlr>
        <balPayments>11000000</balPayments>
      </item>
      <item>
        <year>2026.05</year>
        <statKor>제주</statKor>
        <hsCode>-</hsCode>
        <expDlr>25000000</expDlr>
        <impDlr>9900000</impDlr>
        <balPayments>15100000</balPayments>
      </item>
    </items>
  </body>
</response>
"""


class DummyRangeResponse:
    status_code = 200
    text = CUSTOMS_SIDO_XML

    def raise_for_status(self):
        return None


@pytest.mark.asyncio
async def test_customs_client_fetches_sido_total_range_with_period_range():
    http_client = DummyHttpClient()
    http_client.get = AsyncMock(return_value=DummyRangeResponse())
    client = CustomsTradeStatClient(
        service_key="test-key",
        http_client=http_client,
        base_url="https://example.test/sidotrade",
        sido_param_name="sidoCd",
        sido_code="50",
    )

    rows = await client.fetch_sido_total_range("202506", "202605")

    assert [row.period for row in rows] == ["2026.05"]
    url = http_client.get.await_args.args[0]
    params = http_client.get.await_args.kwargs["params"]
    assert url.endswith("/getSidotradeList")
    assert params["strtYymm"] == "202506"
    assert params["endYymm"] == "202605"
    assert params["sidoCd"] == "50"
    assert "searchItemCd" not in params


@pytest.mark.asyncio
async def test_customs_client_normalizes_year_only_sido_total_month_period():
    response = type("Response", (), {
        "text": CUSTOMS_SIDO_XML.replace("2026.05", "2026"),
        "raise_for_status": lambda self: None,
    })()
    http_client = DummyHttpClient()
    http_client.get = AsyncMock(return_value=response)
    client = CustomsTradeStatClient(
        service_key="test-key",
        http_client=http_client,
        base_url="https://example.test/sidotrade",
    )

    rows = await client.fetch_sido_total_month("202607")

    assert [row.period for row in rows] == ["2026.07"]


@pytest.mark.asyncio
async def test_customs_client_splits_sido_total_range_to_api_limit():
    first_response = type("Response", (), {
        "text": CUSTOMS_SIDO_XML.replace("2026.05", "2026.06"),
        "raise_for_status": lambda self: None,
    })()
    second_response = type("Response", (), {
        "text": CUSTOMS_SIDO_XML.replace("2026.05", "2026.07"),
        "raise_for_status": lambda self: None,
    })()
    http_client = DummyHttpClient()
    http_client.get = AsyncMock(side_effect=[first_response, second_response])
    client = CustomsTradeStatClient(
        service_key="test-key",
        http_client=http_client,
        base_url="https://example.test/sidotrade",
    )

    rows = await client.fetch_sido_total_range("202507", "202607")

    assert [row.period for row in rows] == ["2026.06", "2026.07"]
    requested_ranges = [
        (call.kwargs["params"]["strtYymm"], call.kwargs["params"]["endYymm"])
        for call in http_client.get.await_args_list
    ]
    assert requested_ranges == [("202507", "202606"), ("202607", "202607")]


@pytest.mark.asyncio
async def test_customs_client_surfaces_gateway_error_body_before_http_status():
    response = type("Response", (), {
        "text": (
            "<OpenAPI_ServiceResponse><cmmMsgHeader>"
            "<errMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</errMsg>"
            "<returnAuthMsg>등록되지 않은 서비스키</returnAuthMsg>"
            "<returnReasonCode>30</returnReasonCode>"
            "</cmmMsgHeader></OpenAPI_ServiceResponse>"
        ),
        "raise_for_status": lambda self: (_ for _ in ()).throw(
            httpx.HTTPStatusError(
                "403 Forbidden",
                request=httpx.Request("GET", "https://example.test"),
                response=httpx.Response(403),
            )
        ),
    })()
    http_client = DummyHttpClient()
    http_client.get = AsyncMock(return_value=response)
    client = CustomsTradeStatClient(service_key="test-key", http_client=http_client)

    with pytest.raises(ValueError, match="SERVICE_KEY_IS_NOT_REGISTERED_ERROR"):
        await client.fetch_sido_total_month("202607")


def test_build_jeju_region_trade_series_calculates_mom_and_yoy():
    rows = parse_customs_trade_xml(JEJU_TOTAL_RANGE_XML)

    series = build_jeju_region_trade_series(rows)

    assert [item.period for item in series] == ["2026.05", "2026.04", "2025.05"]
    latest = series[0]
    assert latest.export_amount_usd == 25000000
    assert latest.import_amount_usd == 9900000
    assert latest.trade_balance_usd == 15100000
    assert latest.export_mom_pct == pytest.approx(25.0)
    assert latest.export_yoy_pct == pytest.approx(150.0)
    assert latest.import_mom_pct == pytest.approx(10.0)
    assert latest.import_yoy_pct == pytest.approx(23.75)
    # 직전월/전년동월 데이터가 없는 구간은 None 으로 둔다.
    assert series[-1].export_mom_pct is None
    assert series[-1].export_yoy_pct is None


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


def test_parse_national_customs_20d_release_extracts_summary_numbers():
    text = """
    2026년 7월 1일 ~ 7월 20일 수출입 현황 [잠정치]
    등록일 2026.07.21
    관세청은 21 일, 7 월 1 일~20 일 기간의 수출입 현황 잠정치를 발표 했다.
    동기간 수출은 549 억 달러 로 전년동기대비 52.3% 증가, 수입은 427 억 달러 로
    20.0% 증가 했으며, 무역수지는 122 억 달러 흑자 를 기록했다고 밝혔다.
    반도체(221 억 달러) 수출 7 월 기준 역대최대
    """

    release = parse_national_trade_release(
        title="2026년 7월 1일 ~ 7월 20일 수출입 현황 [잠정치]",
        url="https://customs.example/20d",
        source="customs",
        text=text,
    )

    assert release.phase == "customs_20d"
    assert release.period_label == "2026년 7월 1~20일"
    assert release.export_amount_100m_usd == pytest.approx(549)
    assert release.export_yoy_pct == pytest.approx(52.3)
    assert release.import_amount_100m_usd == pytest.approx(427)
    assert release.import_yoy_pct == pytest.approx(20.0)
    assert release.trade_balance_100m_usd == pytest.approx(122)
    assert release.trade_balance_label == "흑자"
    assert "반도체" in release.highlights[0]
    assert release.dedup_key == "national_trade:customs_20d:2026년 7월 1~20일:https://customs.example/20d"


def test_parse_national_release_ignores_semiconductor_headline_for_export_amount():
    """제목의 '반도체 수출 400억 달러'가 본문 총수출액보다 먼저 나온다."""
    text = """
    2026년 6월 수출입 현황 [잠정치]
    - 수출 및 무역수지 역대 최대 / 월수출 1천억 달러, 반도체 수출 400억 달러 최초 돌파
    등록일 2026.07.02
    ◇ 관세청은 1 일, 6 월 1 일~30 일 기간의 수출입 현황 잠정치를 발표 했다.
    ◇ 수출은 1,023 억 달러 로 전년동기대비 70.9% 증가, 수입은 661 억 달러 로
    30.1% 증가 했고, 무역수지는 361 억 달러 흑자 를 기록했다고 밝혔다.
    """

    release = parse_national_trade_release(
        title="2026년 6월 수출입 현황 [잠정치]",
        url="https://customs.example/monthly",
        source="customs",
        text=text,
    )

    assert release.export_amount_100m_usd == pytest.approx(1023)
    assert release.import_amount_100m_usd == pytest.approx(661)
    assert release.export_yoy_pct == pytest.approx(70.9)


def test_parse_national_customs_release_extracts_semiconductor_and_working_days():
    text = """
    ◇ 동기간 수출은 213억 달러로 전년동기대비 45.3% 증가, 수입은 195억 달러로
    23.1% 증가했으며, 무역수지는 18억 달러 흑자를 기록했다.
    → 동기간 (1~10일) 수출 8월 기준 역대최대 / 반도체 (100억 달러) 수출 8월 기준 역대최대
    ※ 조업일수 [(’25) 7.0일, (’26) 7.0일] 고려 시 일평균수출액 [(’25.8.) 20.9, (’26.8.) 30.4억 달러] 45.3% 증가
    ㅇ (주요품목) 전년동기대비 반도체 (155.4%), 석유제품 (65.3%) 등 증가
    """

    release = parse_national_trade_release(
        title="2026년 8월 1일 ~ 8월 10일 수출입 현황 [잠정치]",
        url="https://customs.example/10d",
        source="customs",
        text=text,
    )

    assert release.semiconductor_export_amount_100m_usd == pytest.approx(100)
    assert release.semiconductor_yoy_pct == pytest.approx(155.4)
    assert release.working_days_current == pytest.approx(7.0)
    assert release.working_days_previous_year == pytest.approx(7.0)
    assert release.export_daily_avg_100m_usd == pytest.approx(30.4)
    assert release.semiconductor_daily_avg_100m_usd == pytest.approx(100 / 7)


def test_parse_national_motie_monthly_release_extracts_summary_numbers():
    text = """
    2026년 5월 수출입 동향
    5월 수출은 전년 동월 대비 53.2% 증가한 877.5억 달러로 월 기준 역대 최대실적을 기록했다.
    5월 수입은 전년 동월 대비 20.8% 증가한 608.0억 달러를 기록했다.
    무역수지는 269.5억 달러 흑자로 전년 동월 대비 200.3억 달러 증가하였다.
    반도체 수출이 169.4% 증가해 전체 수출 증가세를 주도했다.
    """

    release = parse_national_trade_release(
        title="2026년 5월 수출입 동향",
        url="https://motie.example/monthly",
        source="motie",
        text=text,
    )

    assert release.phase == "motie_monthly"
    assert release.period_label == "2026년 5월"
    assert release.export_amount_100m_usd == pytest.approx(877.5)
    assert release.export_yoy_pct == pytest.approx(53.2)
    assert release.import_amount_100m_usd == pytest.approx(608.0)
    assert release.import_yoy_pct == pytest.approx(20.8)
    assert release.trade_balance_100m_usd == pytest.approx(269.5)
    assert release.trade_balance_label == "흑자"


def test_format_national_trade_trend_report_html_includes_link_and_numbers():
    release = NationalTradeTrendRelease(
        source="customs",
        phase="customs_10d",
        title="2026년 7월 1일 ~ 7월 10일 수출입 현황 [잠정치]",
        url="https://customs.example/10d?a=1&b=2",
        period_label="2026년 7월 1~10일",
        export_amount_100m_usd=298,
        export_yoy_pct=53.9,
        export_mom_change_100m_usd=41,
        export_mom_pct=15.95,
        working_days_current=8.5,
        export_daily_avg_100m_usd=35.1,
        export_daily_avg_mom_pct=12.0,
        semiconductor_export_amount_100m_usd=112,
        semiconductor_yoy_pct=120.5,
        semiconductor_mom_change_100m_usd=12,
        semiconductor_mom_pct=12.0,
        semiconductor_daily_avg_100m_usd=13.176,
        semiconductor_daily_avg_mom_pct=18.0,
        import_amount_100m_usd=235,
        import_yoy_pct=17.4,
        import_mom_change_100m_usd=-12,
        import_mom_pct=-4.86,
        trade_balance_100m_usd=64,
        trade_balance_label="흑자",
        trade_balance_mom_change_100m_usd=53,
        highlights=["반도체 수출 증가"],
    )

    message = format_national_trade_trend_report_html(release)

    assert "전국 수출입 잠정치" in message
    assert "수출: <b>298.0억 달러</b> (전년비 +53.9% / 전월비 +41.0억, +15.9%)" in message
    assert "일평균 수출: <b>35.1억 달러</b> (전월비 +12.0%, 조업 8.5일)" in message
    assert "반도체: <b>112.0억 달러</b> (전년비 +120.5% / 전월비 +12.0억, +12.0% / 일평균 +18.0%)" in message
    assert "수입: <b>235.0억 달러</b> (전년비 +17.4% / 전월비 -12.0억, -4.9%)" in message
    assert "무역수지: <b>64.0억 달러 흑자</b> (전월차 +53.0억)" in message
    assert "https://customs.example/10d?a=1&amp;b=2" in message


class DummyTextResponse:
    status_code = 200

    def __init__(self, text):
        self.text = text
        self.content = text.encode("utf-8")

    def raise_for_status(self):
        return None


class DummyBinaryResponse:
    status_code = 200

    def __init__(self, content):
        self.content = content
        self.text = ""

    def raise_for_status(self):
        return None


@pytest.mark.asyncio
async def test_national_web_client_discovers_matching_links_and_fetches_details():
    list_html = """
    <a href="javascript:" data-id="10170523" data-url="abc123" class="nttInfoBtn"
       title="2026년 7월 1일 ~ 7월 20일 수출입 현황 [잠정치]">
        2026년 7월 1일 ~ 7월 20일 수출입 현황 [잠정치]
    </a>
    <a href="/kcs/na/ntt/selectNttInfo.do?nttSn=2">다른 보도자료</a>
    """
    detail_html = """
    <h3>2026년 7월 1일 ~ 7월 20일 수출입 현황 [잠정치]</h3>
    동기간 수출은 549 억 달러 로 전년동기대비 52.3% 증가, 수입은 427 억 달러 로
    20.0% 증가 했으며, 무역수지는 122 억 달러 흑자 를 기록했다고 밝혔다.
    """
    http_client = DummyHttpClient()
    http_client.get = AsyncMock(
        side_effect=[
            DummyTextResponse(list_html),
            DummyTextResponse(detail_html),
        ]
    )
    client = NationalTradeTrendWebClient(
        http_client=http_client,
        list_urls=["https://www.customs.go.kr/kcs/na/ntt/selectNttList.do?mi=2891&bbsId=1362"],
    )

    releases = await client.fetch_recent_releases()

    assert len(releases) == 1
    assert releases[0].phase == "customs_20d"
    assert releases[0].url == (
        "https://www.customs.go.kr/kcs/na/ntt/selectNttInfo.do"
        "?bbsId=1362&mi=2891&nttSn=10170523&nttSnUrl=abc123"
    )
    assert releases[0].export_amount_100m_usd == pytest.approx(549)


@pytest.mark.asyncio
async def test_national_web_client_skips_customs_final_releases():
    list_html = """
    <a href="javascript:" data-id="10170123" class="nttInfoBtn"
       title="2026년 6월 월간 수출입 현황 [확정치]">final</a>
    """
    http_client = DummyHttpClient()
    http_client.get = AsyncMock(return_value=DummyTextResponse(list_html))
    client = NationalTradeTrendWebClient(
        http_client=http_client,
        list_urls=["https://www.customs.go.kr/kcs/na/ntt/selectNttList.do?mi=2891&bbsId=1362"],
    )

    releases = await client.fetch_recent_releases()

    assert releases == []
    http_client.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_national_web_client_discovers_motie_article_view_links():
    list_html = """
    <a href="javascript:article.view('172077');"><i>2026년 7월 수출입 동향</i></a>
    """
    detail_html = """
    <h3>2026년 7월 수출입 동향</h3>
    7월 수출은 전년 동월 대비 21.1% 증가한 620.0억 달러로 집계됐다.
    수입은 전년 동월 대비 12.3% 증가한 530.0억 달러를 기록했다.
    무역수지는 90.0억 달러 흑자였다.
    """
    http_client = DummyHttpClient()
    http_client.get = AsyncMock(
        side_effect=[
            DummyTextResponse(list_html),
            DummyTextResponse(detail_html),
        ]
    )
    client = NationalTradeTrendWebClient(
        http_client=http_client,
        list_urls=["https://www.motir.go.kr/kor/article/ATCL3f49a5a8c?searchKeyword=x"],
    )

    releases = await client.fetch_recent_releases()

    assert len(releases) == 1
    assert releases[0].phase == "motie_monthly"
    assert releases[0].url == "https://www.motir.go.kr/kor/article/ATCL3f49a5a8c/172077/view"
    assert releases[0].export_amount_100m_usd == pytest.approx(620.0)


@pytest.mark.asyncio
async def test_national_web_client_discovers_tradedata_links_and_reads_hwpx_attachment():
    list_html = """
    <a href="javascript:ets_f_prccMenuAdmin('/cts/hmpg/openETS0100210Q.do',
        {blbrTpcd:'21', ntarSrno:'2412', menuId:'ETS_MNK_50100000'});">
        [잠정치] 2026년 8월(1~10일) 수출입 현황
    </a>
    """
    detail_html = """
    <table><tr><th>제목</th><td><span>[ 잠정치 ]</span> 2026년 8월(1~10일) 수출입 현황</td></tr></table>
    <li class="btn_download_detl" data-attch-file-id="%2Babc%3D">
        <span class="ms-2">260811 26년 8월 1일 - 8월 10일 수출입현황.hwpx</span>
    </li>
    """
    hwpx = io.BytesIO()
    with zipfile.ZipFile(hwpx, "w") as archive:
        archive.writestr(
            "Contents/section0.xml",
            """
            <root>
              <p>동기간 수출은 221억 달러로 전년동기대비 50.2% 증가, 수입은 205억 달러로 17.8% 증가했으며, 무역수지는 16억 달러 흑자</p>
              <p>반도체 수출 증가</p>
            </root>
            """,
        )
    http_client = DummyHttpClient()
    http_client.get = AsyncMock(
        side_effect=[
            DummyTextResponse(list_html),
            DummyTextResponse(detail_html),
            DummyBinaryResponse(hwpx.getvalue()),
        ]
    )
    client = NationalTradeTrendWebClient(
        http_client=http_client,
        list_urls=["https://tradedata.go.kr/cts/index.do"],
    )

    releases = await client.fetch_recent_releases()

    assert len(releases) == 1
    assert releases[0].source == "customs"
    assert releases[0].phase == "customs_10d"
    assert releases[0].period_label == "2026년 8월 1~10일"
    assert releases[0].export_amount_100m_usd == pytest.approx(221)
    assert releases[0].import_yoy_pct == pytest.approx(17.8)


@pytest.mark.asyncio
async def test_national_web_client_coalesces_same_period_from_customs_and_tradedata():
    customs_list_html = """
    <a href="javascript:" data-id="10173983" data-url="official" class="nttInfoBtn"
       title="2026년 8월 1일 ~ 8월 20일 수출입 현황 [잠정치]">
        2026년 8월 1일 ~ 8월 20일 수출입 현황 [잠정치]
    </a>
    """
    tradedata_list_html = """
    <a href="javascript:ets_f_prccMenuAdmin('/cts/hmpg/openETS0100210Q.do',
        {blbrTpcd:'21', ntarSrno:'2414', menuId:'ETS_MNK_50100000'});">
        [잠정치] 2026년 8월(1~20일) 수출입 현황
    </a>
    """
    detail_html = """
    동기간 수출은 552억 달러로 전년동기대비 56.0% 증가, 수입은 412억 달러로
    19.0% 증가했으며, 무역수지는 140억 달러 흑자
    """
    http_client = DummyHttpClient()
    http_client.get = AsyncMock(
        side_effect=[
            DummyTextResponse(customs_list_html),
            DummyTextResponse(tradedata_list_html),
            DummyTextResponse(detail_html),
            DummyTextResponse(detail_html),
        ]
    )
    client = NationalTradeTrendWebClient(
        http_client=http_client,
        list_urls=[
            "https://www.customs.go.kr/kcs/na/ntt/selectNttList.do?mi=2891&bbsId=1362",
            "https://tradedata.go.kr/cts/index.do",
        ],
        max_detail_pages=5,
    )

    releases = await client.fetch_recent_releases()

    assert len(releases) == 1
    assert releases[0].period_label == "2026년 8월 1~20일"
    assert releases[0].url == (
        "https://www.customs.go.kr/kcs/na/ntt/selectNttInfo.do"
        "?bbsId=1362&mi=2891&nttSn=10173983&nttSnUrl=official"
    )


@pytest.mark.asyncio
async def test_national_web_client_calculates_previous_month_changes_for_same_phase():
    list_html = """
    <a href="/now">2026년 8월 1일 ~ 8월 10일 수출입 현황 [잠정치]</a>
    <a href="/prev20">2026년 7월 1일 ~ 7월 20일 수출입 현황 [잠정치]</a>
    <a href="/prev10">2026년 7월 1일 ~ 7월 10일 수출입 현황 [잠정치]</a>
    """
    now_detail = "수출은 213억 달러로 전년동기대비 45.3% 증가, 수입은 195억 달러로 23.1% 증가, 무역수지는 18억 달러 흑자"
    prev20_detail = "수출은 549억 달러로 전년동기대비 52.3% 증가, 수입은 427억 달러로 20.0% 증가, 무역수지는 122억 달러 흑자"
    prev10_detail = "수출은 150억 달러로 전년동기대비 10.0% 증가, 수입은 180억 달러로 5.0% 증가, 무역수지는 30억 달러 적자"
    http_client = DummyHttpClient()
    http_client.get = AsyncMock(
        side_effect=[
            DummyTextResponse(list_html),
            DummyTextResponse(now_detail),
            DummyTextResponse(prev20_detail),
            DummyTextResponse(prev10_detail),
        ]
    )
    client = NationalTradeTrendWebClient(
        http_client=http_client,
        list_urls=["https://www.customs.go.kr/kcs/na/ntt/selectNttList.do?mi=2891&bbsId=1362"],
        max_detail_pages=3,
    )

    releases = await client.fetch_recent_releases()

    current = releases[0]
    assert current.period_label == "2026년 8월 1~10일"
    assert current.export_mom_change_100m_usd == pytest.approx(63)
    assert current.export_mom_pct == pytest.approx(42.0)
    assert current.import_mom_change_100m_usd == pytest.approx(15)
    assert current.import_mom_pct == pytest.approx(8.3333, rel=1e-4)
    assert current.trade_balance_mom_change_100m_usd == pytest.approx(48)


@pytest.mark.asyncio
async def test_national_web_client_calculates_semiconductor_daily_changes():
    list_html = """
    <a href="/now">2026년 8월 1일 ~ 8월 10일 수출입 현황 [잠정치]</a>
    <a href="/prev">2026년 7월 1일 ~ 7월 10일 수출입 현황 [잠정치]</a>
    """
    now_detail = """
    수출은 213억 달러로 전년동기대비 45.3% 증가, 수입은 195억 달러로 23.1% 증가,
    무역수지는 18억 달러 흑자. 반도체 (100억 달러)
    ※ 조업일수 [(’25) 7.0일, (’26) 7.0일] 고려 시 일평균수출액 [(’25.8.) 20.9, (’26.8.) 30.4억 달러]
    """
    prev_detail = """
    수출은 298억 달러로 전년동기대비 53.9% 증가, 수입은 235억 달러로 17.4% 증가,
    무역수지는 64억 달러 흑자. 반도체 (112억 달러)
    ※ 조업일수 [(’25) 8.5일, (’26) 8.5일] 고려 시 일평균수출액 [(’25.7.) 22.8, (’26.7.) 35.1억 달러]
    """
    http_client = DummyHttpClient()
    http_client.get = AsyncMock(
        side_effect=[
            DummyTextResponse(list_html),
            DummyTextResponse(now_detail),
            DummyTextResponse(prev_detail),
        ]
    )
    client = NationalTradeTrendWebClient(
        http_client=http_client,
        list_urls=["https://www.customs.go.kr/kcs/na/ntt/selectNttList.do?mi=2891&bbsId=1362"],
        max_detail_pages=2,
    )

    releases = await client.fetch_recent_releases()

    current = releases[0]
    assert current.semiconductor_mom_change_100m_usd == pytest.approx(-12)
    assert current.semiconductor_mom_pct == pytest.approx(-10.7142, rel=1e-4)
    assert current.semiconductor_daily_avg_100m_usd == pytest.approx(100 / 7)
    assert current.semiconductor_daily_avg_mom_pct == pytest.approx(8.4183, rel=1e-4)
    assert current.export_daily_avg_mom_pct == pytest.approx(-13.3903, rel=1e-4)


@pytest.mark.asyncio
async def test_national_web_client_interleaves_sources_before_detail_limit():
    customs_list_html = """
    <a href="javascript:" data-id="10170523" class="nttInfoBtn"
       title="2026년 7월 1일 ~ 7월 20일 수출입 현황 [잠정치]">customs latest</a>
    <a href="javascript:" data-id="10169503" class="nttInfoBtn"
       title="2026년 7월 1일 ~ 7월 10일 수출입 현황 [잠정치]">customs previous</a>
    """
    motie_list_html = """
    <a href="javascript:article.view('172077');"><i>2026년 7월 수출입 동향</i></a>
    """
    customs_detail_html = "수출은 549억 달러로 전년동기대비 52.3% 증가, 수입은 427억 달러로 20.0% 증가, 무역수지는 122억 달러 흑자"
    motie_detail_html = "수출은 전년 동월 대비 21.1% 증가한 620억 달러, 수입은 전년 동월 대비 12.3% 증가한 530억 달러, 무역수지는 90억 달러 흑자"
    http_client = DummyHttpClient()
    http_client.get = AsyncMock(
        side_effect=[
            DummyTextResponse(customs_list_html),
            DummyTextResponse(motie_list_html),
            DummyTextResponse(customs_detail_html),
            DummyTextResponse(motie_detail_html),
        ]
    )
    client = NationalTradeTrendWebClient(
        http_client=http_client,
        list_urls=[
            "https://www.customs.go.kr/kcs/na/ntt/selectNttList.do?mi=2891&bbsId=1362",
            "https://www.motir.go.kr/kor/article/ATCL3f49a5a8c?searchKeyword=x",
        ],
        max_detail_pages=2,
    )

    releases = await client.fetch_recent_releases()

    assert [release.source for release in releases] == ["customs", "motie"]


@pytest.mark.asyncio
async def test_national_web_client_fetches_year_releases_from_paginated_lists():
    first_page = """
    <a href="/aug">2026년 8월 1일 ~ 8월 10일 수출입 현황 [잠정치]</a>
    """
    second_page = """
    <a href="/dec">2025년 12월 1일 ~ 12월 20일 수출입 현황 [잠정치]</a>
    <a href="/jan">2026년 1월 1일 ~ 1월 10일 수출입 현황 [잠정치]</a>
    """
    aug_detail = "수출은 213억 달러로 전년동기대비 45.3% 증가, 수입은 195억 달러로 23.1% 증가, 무역수지는 18억 달러 흑자"
    jan_detail = "수출은 180억 달러로 전년동기대비 12.0% 증가, 수입은 170억 달러로 10.0% 증가, 무역수지는 10억 달러 흑자"
    http_client = DummyHttpClient()
    http_client.get = AsyncMock(
        side_effect=[
            DummyTextResponse(first_page),
            DummyTextResponse(second_page),
            DummyTextResponse(aug_detail),
            DummyTextResponse(jan_detail),
        ]
    )
    client = NationalTradeTrendWebClient(
        http_client=http_client,
        list_urls=["https://www.customs.go.kr/kcs/na/ntt/selectNttList.do?mi=2891&bbsId=1362"],
        max_detail_pages=10,
    )

    releases = await client.fetch_releases(year=2026, list_page_count=2)

    assert [release.period_label for release in releases] == [
        "2026년 8월 1~10일",
        "2026년 1월 1~10일",
    ]
    requested_urls = [call.args[0] for call in http_client.get.await_args_list[:2]]
    assert requested_urls[0].endswith("bbsId=1362")
    assert "currPage=2" in requested_urls[1]


@pytest.mark.asyncio
async def test_national_web_client_uses_page_param_matching_each_list_host():
    http_client = DummyHttpClient()
    http_client.get = AsyncMock(return_value=DummyTextResponse(""))
    client = NationalTradeTrendWebClient(
        http_client=http_client,
        list_urls=[
            "https://www.customs.go.kr/kcs/na/ntt/selectNttList.do?mi=2891&bbsId=1362",
            "https://www.motir.go.kr/kor/article/ATCL3f49a5a8c?searchCondition=1",
        ],
    )

    await client.fetch_releases(year=2026, list_page_count=2)

    requested_urls = [call.args[0] for call in http_client.get.await_args_list]
    customs_urls = [url for url in requested_urls if "customs.go.kr" in url]
    motie_urls = [url for url in requested_urls if "motir.go.kr" in url]
    assert any("currPage=2" in url for url in customs_urls)
    assert not any("pageIndex" in url for url in customs_urls)
    assert any("pageIndex=2" in url for url in motie_urls)
    assert not any("currPage" in url for url in motie_urls)


@pytest.mark.asyncio
async def test_national_web_client_skips_unparseable_and_unrelated_motie_releases():
    list_html = """
    <a href="javascript:article.view('1');"><i>2026년 7월 수출입 동향</i></a>
    <a href="javascript:article.view('2');"><i>2026년 상반기 및 6월 정보통신산업(ICT) 수출입 동향</i></a>
    <a href="javascript:article.view('3');"><i>2026년 6월 수출입 동향</i></a>
    """
    empty_detail_html = "<h3>2026년 7월 수출입 동향</h3>보도자료입니다."
    parseable_detail_html = "수출은 전년 동월 대비 53.2% 증가한 877.5억 달러, 수입은 전년 동월 대비 20.8% 증가한 608.0억 달러"
    http_client = DummyHttpClient()
    http_client.get = AsyncMock(
        side_effect=[
            DummyTextResponse(list_html),
            DummyTextResponse(empty_detail_html),
            DummyTextResponse(parseable_detail_html),
        ]
    )
    client = NationalTradeTrendWebClient(
        http_client=http_client,
        list_urls=["https://www.motir.go.kr/kor/article/ATCL3f49a5a8c?searchKeyword=x"],
        max_detail_pages=2,
    )

    releases = await client.fetch_recent_releases()

    assert len(releases) == 1
    assert releases[0].period_label == "2026년 6월"
