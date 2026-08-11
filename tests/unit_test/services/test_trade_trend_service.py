from datetime import datetime
import io
from unittest.mock import AsyncMock
import zipfile

import pytest

from services.trade_trend_service import (
    CustomsTradeStatClient,
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
        import_amount_100m_usd=235,
        import_yoy_pct=17.4,
        trade_balance_100m_usd=64,
        trade_balance_label="흑자",
        highlights=["반도체 수출 증가"],
    )

    message = format_national_trade_trend_report_html(release)

    assert "전국 수출입 잠정치" in message
    assert "수출: <b>298.0억 달러</b> (+53.9%)" in message
    assert "수입: <b>235.0억 달러</b> (+17.4%)" in message
    assert "무역수지: <b>64.0억 달러 흑자</b>" in message
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
