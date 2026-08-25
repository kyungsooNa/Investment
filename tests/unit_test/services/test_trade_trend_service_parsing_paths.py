"""무역통계 모듈의 파서 헬퍼·포매터 분기 테스트.

기존 테스트가 정상 발표문 파싱 흐름을 다루므로, 여기서는 값이 비었을 때의
fallback("-"), 발표처별 우선순위, 목록 HTML 의 스킵 조건, httpx 자체 클라이언트
경로처럼 실제 보도자료가 어긋났을 때만 타는 분기를 채운다.
"""
import zipfile
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from services import trade_trend_service as mod
from services.trade_trend_service import (
    CustomsTradeStatClient,
    JejuRegionTradeMonth,
    JejuSemiconductorTradeReport,
    NationalTradeTrendRelease,
    NationalTradeTrendWebClient,
    format_jeju_semiconductor_report_html,
    format_national_trade_trend_report_html,
    parse_customs_trade_xml,
)


def _release(**kwargs):
    base = dict(source="customs", phase="customs_monthly", title="제목",
                url="https://www.customs.go.kr/a", period_label="2026년 5월")
    base.update(kwargs)
    return NationalTradeTrendRelease(**base)


# --- 기간 라벨 / 숫자 파싱 ----------------------------------------------------

@pytest.mark.parametrize(
    "period, expected",
    [("2026.05", "2026년 5월"), ("202605", "2026년 5월"), ("기간미상", "기간미상")],
)
def test_region_month_period_label_falls_back_to_the_raw_period(period, expected):
    month = JejuRegionTradeMonth(period=period, export_amount_usd=1,
                                 import_amount_usd=1, trade_balance_usd=0)

    assert month.period_label == expected


def test_unparsable_amounts_in_the_customs_xml_become_zero():
    xml = """<response><header><resultCode>00</resultCode></header><body><items>
      <item><year>202605</year><statKor>반도체</statKor><hsCode>8542</hsCode>
        <expDlr>숫자아님</expDlr><impDlr></impDlr><balPayments>1,500</balPayments>
      </item></items></body></response>"""

    rows = parse_customs_trade_xml(xml)

    assert rows[0].export_amount_usd == 0
    assert rows[0].import_amount_usd == 0
    assert rows[0].trade_balance_usd == 1500


# --- 발표처 판별 / 우선순위 ---------------------------------------------------

@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.motie.go.kr/a", "motie"),
        ("https://www.motir.go.kr/a", "motie"),
        ("https://www.customs.go.kr/a", "customs"),
        ("https://tradedata.go.kr/a", "customs"),
        ("https://example.com/a", "unknown"),
    ],
)
def test_source_detection_from_the_url(url, expected):
    assert mod._source_from_url(url) == expected


@pytest.mark.parametrize(
    "title, expected",
    [
        ("2026년 5월 1~10일 수출입 현황", "customs_10d"),
        ("2026년 5월 1~20일 수출입 현황", "customs_20d"),
        ("2026년 5월 수출입 현황(확정치)", "customs_monthly_final"),
        ("2026년 5월 수출입 현황(잠정치)", "customs_monthly"),
        ("2026년 5월 수출입 동향", "motie_monthly"),
    ],
)
def test_phase_detection_from_the_title(title, expected):
    assert mod._phase_from_title(title) == expected


def test_period_label_falls_back_to_the_title_when_year_or_month_is_missing():
    assert mod._period_from_title("수출입 현황", "customs_monthly") == "수출입 현황"


@pytest.mark.parametrize(
    "phase, expected",
    [("customs_10d", "2026년 5월 1~10일"), ("customs_20d", "2026년 5월 1~20일"),
     ("customs_monthly", "2026년 5월")],
)
def test_period_label_is_built_per_phase(phase, expected):
    assert mod._period_from_title("2026년 5월 수출입", phase) == expected


def test_period_year_helpers_return_none_without_a_match():
    assert mod._period_year_month("기간미상") is None
    assert mod._period_year_from_title("연도 없는 제목") is None
    assert mod._previous_month_key("기간미상", "customs_monthly") is None
    assert mod._release_month_key(_release(period_label="기간미상")) is None


def test_previous_month_key_rolls_back_over_the_year_boundary():
    assert mod._previous_month_key("2026년 1월", "customs_monthly") == (
        "customs_monthly", 2025, 12
    )


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.customs.go.kr/a", 3),
        ("https://www.motir.go.kr/a", 2),
        ("https://www.motie.go.kr/a", 2),
        ("https://tradedata.go.kr/a", 1),
        ("https://example.com/a", 0),
    ],
)
def test_release_preference_score_ranks_the_official_sources_first(url, expected):
    assert mod._release_preference_score(_release(url=url)) == expected


def test_lower_ranked_duplicate_release_does_not_replace_the_preferred_one():
    preferred = _release(url="https://www.customs.go.kr/a")
    duplicate = _release(url="https://tradedata.go.kr/a")

    merged = mod._coalesce_duplicate_period_releases([preferred, duplicate])

    assert merged == [preferred]


# --- 본문 수치 추출 -----------------------------------------------------------

def test_float_helper_returns_none_without_a_match_or_on_a_bad_number():
    assert mod._float(r"수출 ([0-9.]+)억", "본문에 수치 없음") is None
    assert mod._float(r"수출 ([0-9.,]+)억", "수출 1.2.3억") is None


def test_amount_extraction_stops_at_the_next_indicator():
    assert mod._extract_amount_after("수출", "수출은 증가했다. 수입 380억 달러") is None


def test_yoy_extraction_skips_windows_without_a_percentage():
    assert mod._extract_yoy_after("수출", "수출 400억 달러") is None


def test_signed_pct_marks_a_decrease_as_negative():
    assert mod._signed_pct(3.0, "전년 대비 감소했다") == -3.0
    assert mod._signed_pct(3.0, "△3.0") == -3.0
    assert mod._signed_pct(3.0, "증가했다") == 3.0
    assert mod._signed_pct(None, "감소") is None


def test_trade_label_pattern_separates_export_from_import():
    assert mod._trade_label_pattern("수출").startswith("(?<!반도체)")
    assert mod._trade_label_pattern("수입") == r"(?<!수출)수입"
    assert mod._trade_label_pattern("무역수지") == r"무역수지"


def test_semiconductor_yoy_requires_a_year_over_year_phrase_nearby():
    assert mod._extract_semiconductor_yoy("반도체(12.3%)") is None
    assert mod._extract_semiconductor_yoy("전년동기대비 반도체(△12.3%)") == -12.3


# --- 목록 HTML 링크 추출 ------------------------------------------------------

def test_untitled_and_unrelated_list_rows_are_skipped():
    html_text = """
      <a href="/a"></a>
      <a href="/b">공지사항</a>
    """

    assert mod._extract_national_trade_links(html_text, "https://www.customs.go.kr") == []


def test_customs_row_without_a_post_id_is_skipped():
    html_text = (
        '<a class="nttInfoBtn" href="#">2026년 5월 수출입 현황(잠정치)</a>'
    )

    assert mod._extract_national_trade_links(html_text, "https://www.customs.go.kr") == []


def test_motie_row_without_an_article_id_is_skipped():
    html_text = '<a href="javascript:void(0);">2026년 5월 수출입 동향</a>'

    assert mod._extract_national_trade_links(html_text, "https://www.motir.go.kr") == []


def test_tradedata_row_without_link_params_is_skipped():
    html_text = (
        '<a href="ets_f_prccMenuAdmin(...)">2026년 5월 수출입 현황(잠정치)</a>'
    )

    assert mod._extract_national_trade_links(html_text, "https://tradedata.go.kr") == []


def test_tradedata_link_params_require_every_key():
    assert mod._extract_tradedata_link_params("ets_f_prccMenuAdmin({blbrTpcd:'A'})") is None


# --- 첨부파일(hwpx) 처리 ------------------------------------------------------

def test_attachment_url_is_skipped_when_the_row_is_not_hwpx_or_has_no_file_id():
    assert mod._extract_tradedata_hwpx_attachment_url(
        '<li class="btn_download_detl" data-attch-file-id="1">보도자료.pdf</li>'
    ) == ""
    assert mod._extract_tradedata_hwpx_attachment_url(
        '<li class="btn_download_detl">보도자료.hwpx</li>'
    ) == ""


def test_non_zip_attachment_content_yields_no_text():
    assert mod._extract_hwpx_text(b"not a zip") == ""


def test_hwpx_text_extraction_only_reads_content_xml_parts():
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Contents/section0.xml", "<p>수출 400억 달러</p>")
        archive.writestr("Contents/empty.xml", "<p>   </p>")
        archive.writestr("META-INF/manifest.xml", "<p>무시</p>")
        archive.writestr("Contents/preview.png", "무시")

    assert mod._extract_hwpx_text(buffer.getvalue()) == "수출 400억 달러"


@pytest.mark.asyncio
async def test_attachment_download_failure_keeps_the_detail_text_unchanged():
    client = NationalTradeTrendWebClient(http_client=MagicMock())
    client._fetch_bytes = AsyncMock(side_effect=httpx.ConnectError("타임아웃"))
    detail = '<li class="btn_download_detl" data-attch-file-id="1">a.hwpx</li>'

    result = await client._append_tradedata_attachment_text(
        detail, "https://tradedata.go.kr/cts/x"
    )

    assert result == detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "detail, url",
    [
        ("본문", "https://www.customs.go.kr/x"),   # tradedata 가 아니면 그대로
        ("첨부 없음", "https://tradedata.go.kr/x"),  # 첨부 링크가 없으면 그대로
    ],
)
async def test_attachment_append_is_a_no_op_without_a_tradedata_attachment(detail, url):
    client = NationalTradeTrendWebClient(http_client=MagicMock())

    assert await client._append_tradedata_attachment_text(detail, url) == detail


@pytest.mark.asyncio
async def test_empty_attachment_text_keeps_the_detail_text_unchanged():
    client = NationalTradeTrendWebClient(http_client=MagicMock())
    client._fetch_bytes = AsyncMock(return_value=b"not a zip")
    detail = '<li class="btn_download_detl" data-attch-file-id="1">a.hwpx</li>'

    assert await client._append_tradedata_attachment_text(
        detail, "https://tradedata.go.kr/x"
    ) == detail


# --- HTTP 클라이언트 미주입 경로 ----------------------------------------------

@pytest.mark.asyncio
async def test_customs_client_opens_its_own_httpx_client_when_none_is_injected(mocker):
    response = MagicMock()
    response.text = ("<response><header><resultCode>00</resultCode></header>"
                     "<body><items></items></body></response>")
    inner = MagicMock()
    inner.get = AsyncMock(return_value=response)
    async_client = MagicMock()
    async_client.__aenter__ = AsyncMock(return_value=inner)
    async_client.__aexit__ = AsyncMock(return_value=False)
    mocker.patch("services.trade_trend_service.httpx.AsyncClient", return_value=async_client)
    client = CustomsTradeStatClient(service_key="key")

    assert await client.fetch_sido_total_month("202605") == []
    inner.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_customs_client_range_and_item_queries_carry_the_sido_code():
    http_client = MagicMock()
    response = MagicMock()
    response.text = ("<response><header><resultCode>00</resultCode></header>"
                     "<body><items></items></body></response>")
    http_client.get = AsyncMock(return_value=response)
    client = CustomsTradeStatClient(service_key="key", http_client=http_client)

    await client.fetch_sido_total_range("202601", "202605")
    await client.fetch_sido_item_month("202605", "8542")

    for call in http_client.get.await_args_list:
        assert call.kwargs["params"]["sidoCd"] == "50"


@pytest.mark.asyncio
@pytest.mark.parametrize("method, expected", [("_fetch_text", "본문"), ("_fetch_bytes", "본문".encode())])
async def test_web_client_opens_its_own_httpx_client_when_none_is_injected(
    mocker, method, expected
):
    response = MagicMock()
    response.text = "본문"
    response.content = "본문".encode()
    inner = MagicMock()
    inner.get = AsyncMock(return_value=response)
    async_client = MagicMock()
    async_client.__aenter__ = AsyncMock(return_value=inner)
    async_client.__aexit__ = AsyncMock(return_value=False)
    mocker.patch("services.trade_trend_service.httpx.AsyncClient", return_value=async_client)
    client = NationalTradeTrendWebClient()

    assert await getattr(client, method)("https://example.com") == expected


# --- 포매터 ------------------------------------------------------------------

def test_jeju_report_renders_dashes_for_missing_values():
    text = format_jeju_semiconductor_report_html(
        JejuSemiconductorTradeReport(
            period="2026.05", export_amount_usd=None,
            previous_month_export_amount_usd=None, previous_year_export_amount_usd=None,
            mom_pct=None, yoy_pct=None, jeju_total_export_amount_usd=None,
            jeju_export_share_pct=None, fetched_at="2026-06-01T00:00:00",
        )
    )

    assert "수출액: <b>-</b>" in text
    assert "전월비: - / 전년비: -" in text


@pytest.mark.parametrize(
    "phase, expected_title",
    [
        ("motie_monthly", "전국 월간 수출입동향"),
        ("customs_monthly", "전국 월간 수출입 잠정치"),
        ("customs_monthly_final", "전국 월간 수출입 확정치"),
        ("customs_10d", "전국 수출입 잠정치"),
    ],
)
def test_national_report_title_follows_the_phase(phase, expected_title):
    text = format_national_trade_trend_report_html(_release(phase=phase))

    assert expected_title in text


def test_national_report_renders_dashes_for_missing_values():
    text = format_national_trade_trend_report_html(_release())

    assert "수출: <b>-</b>" in text
    assert "전년비 -" in text
    assert "무역수지: <b>-</b>" in text


def test_national_report_includes_the_optional_sections_when_present():
    text = format_national_trade_trend_report_html(_release(
        export_amount_100m_usd=400.0, export_yoy_pct=5.0,
        export_mom_change_100m_usd=10.0, export_mom_pct=2.5,
        export_daily_avg_100m_usd=20.0, export_daily_avg_mom_pct=1.0,
        working_days_current=21.5,
        semiconductor_export_amount_100m_usd=120.0, semiconductor_yoy_pct=12.0,
        semiconductor_mom_change_100m_usd=3.0, semiconductor_mom_pct=2.0,
        semiconductor_daily_avg_mom_pct=1.5,
        import_amount_100m_usd=380.0, trade_balance_100m_usd=20.0,
        trade_balance_label="흑자", trade_balance_mom_change_100m_usd=5.0,
        highlights=["반도체 호조", "자동차 회복", "선박 부진", "네번째는 잘림"],
    ))

    assert "일평균 수출" in text and "조업 21.5일" in text
    assert "반도체: <b>120.0억 달러</b>" in text
    assert "무역수지: <b>20.0억 달러 흑자</b> (전월차 +5.0억)" in text
    assert "• 반도체 호조" in text
    assert "네번째는 잘림" not in text


def test_national_report_semiconductor_line_without_any_change_figures():
    text = format_national_trade_trend_report_html(
        _release(semiconductor_export_amount_100m_usd=120.0)
    )

    assert "반도체: <b>120.0억 달러</b>" in text
    assert "반도체: <b>120.0억 달러</b> (" not in text
