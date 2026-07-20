"""네이버 금융 종목뉴스 수집기 테스트.

실제 응답 구조(2026-07 확인)를 축약한 고정 HTML fixture 로 파싱을 검증한다.
네트워크는 전부 모킹한다 — 테스트가 네이버로 나가지 않아야 한다.
"""
from unittest.mock import AsyncMock, MagicMock, patch

from services.stock_news_collector_service import StockNewsCollectorService


# 실제 구조: table.type5 > tbody > tr(.first / .relation_tit / .relation_lst)
# relation_lst 는 연관기사 목록을 담은 중첩 table 이라 본문 기사와 같은 셀렉터를 갖는다.
_FIXTURE = """
<table summary="종목뉴스의 제목, 정보제공, 날짜" cellspacing="0" class="type5">
<caption>종목뉴스</caption>
<thead><tr><th scope="col">제목</th><th scope="col">정보제공</th><th scope="col">날짜</th></tr></thead>
<tbody>
  <tr class="first">
    <td class="title">
      <a href="/item/news_read.naver?article_id=0000517071&amp;office_id=629&amp;code=005930&amp;page=&amp;sm="
         class="tit" target=_top>같은 SK하이닉스? &amp;#51648; '프리미엄' &hellip;레버리지 ETF</a>
    </td>
    <td class="info">더팩트</td>
    <td class="date"> 2026.07.20 00:00</td>
  </tr>
  <tr>
    <td class="title">
      <a href="/item/news_read.naver?article_id=0000060055&amp;office_id=053&amp;code=005930"
         class="tit" target=_top>코스피 이번 주 '운명의 한 주'&hellip;알파벳&middot;중동이 가른다</a>
    </td>
    <td class="info">주간조선</td>
    <td class="date"> 2026.07.20 00:00</td>
  </tr>
  <tr class="relation_tit">
    <td class="title">
      <a href="/item/news_read.naver?article_id=0003112659&amp;office_id=119&amp;code=005930"
         class="tit" target=_top>최태원 &quot;메모리 공급난&quot;&hellip;美 공장 검토</a>
    </td>
    <td class="info">데일리안</td>
    <td class="date"> 2026.07.19 22:02</td>
  </tr>
  <tr class="relation_lst _clusterId1190003112659">
    <td colspan="3">
      <table class="type5"><caption>연관기사 목록</caption><tbody>
        <tr>
          <td class="title">
            <a href="/item/news_read.naver?article_id=0004144132&amp;office_id=022&amp;code=005930"
               class="tit" target=_top><span class="ico_reply"></span>연관기사는 제외되어야 한다</a>
          </td>
          <td class="info">세계일보</td>
          <td class="date"> 2026.07.19 16:18</td>
        </tr>
      </tbody></table>
    </td>
  </tr>
  <tr>
    <td class="title">
      <a href="https://n.news.naver.com/article/001/0001?cd=1" class="tit" target=_top>절대 URL 기사</a>
    </td>
    <td class="info">연합뉴스</td>
    <td class="date"> 2026.07.19 09:10</td>
  </tr>
  <tr>
    <td class="title">
      <a href="/item/news_read.naver?article_id=9999&amp;office_id=001&amp;code=005930"
         class="tit" target=_top>코스피 이번 주 '운명의 한 주'&hellip;알파벳&middot;중동이 가른다</a>
    </td>
    <td class="info">중복언론</td>
    <td class="date"> 2026.07.18 08:00</td>
  </tr>
</tbody>
</table>
"""

_EMPTY_FIXTURE = """
<table class="type5"><caption>종목뉴스</caption><tbody>
  <tr><td colspan="3"><div class="info_text_area">
    <p class="txt">최근 1년 내 검색된 <em>''</em> 뉴스가 없습니다.</p>
  </div></td></tr>
</tbody></table>
"""


def test_parse_extracts_title_press_date_and_absolute_url():
    rows = StockNewsCollectorService._parse_news_list(_FIXTURE)

    assert rows[0]["press"] == "더팩트"
    assert rows[0]["published_at"] == "2026.07.20 00:00"
    assert rows[0]["url"].startswith("https://finance.naver.com/item/news_read.naver")
    assert "article_id=0000517071" in rows[0]["url"]
    # HTML 엔티티는 디코딩되어야 한다
    assert "&hellip;" not in rows[0]["title"]
    assert "…" in rows[0]["title"]


def test_parse_excludes_relation_list_but_keeps_relation_head():
    rows = StockNewsCollectorService._parse_news_list(_FIXTURE)
    titles = [r["title"] for r in rows]

    # relation_lst 안의 연관기사는 제외
    assert not any("연관기사는 제외" in t for t in titles)
    # relation_tit(클러스터 대표 기사)은 실제 기사이므로 유지
    assert any("최태원" in t for t in titles)


def test_parse_dedupes_by_title_and_keeps_absolute_url_as_is():
    rows = StockNewsCollectorService._parse_news_list(_FIXTURE)
    titles = [r["title"] for r in rows]

    assert len(titles) == len(set(titles))
    # 중복 제목은 첫 번째(더 최신)만 남는다
    assert [r for r in rows if "운명의 한 주" in r["title"]][0]["press"] == "주간조선"
    absolute = [r for r in rows if r["title"] == "절대 URL 기사"][0]
    assert absolute["url"] == "https://n.news.naver.com/article/001/0001?cd=1"


def test_parse_returns_empty_for_no_news_broken_and_blank_html():
    assert StockNewsCollectorService._parse_news_list(_EMPTY_FIXTURE) == []
    assert StockNewsCollectorService._parse_news_list("") == []
    assert StockNewsCollectorService._parse_news_list('<table class="type5"><tr><td>x') == []
    assert StockNewsCollectorService._parse_news_list("<html><body>다른 페이지</body></html>") == []


async def test_collect_sends_referer_header_and_cluster_id_param():
    """Referer 가 없으면 네이버가 빈 목록을 돌려주므로 필수 헤더다."""
    service = StockNewsCollectorService()
    with patch.object(
        StockNewsCollectorService, "_fetch_html", new=AsyncMock(return_value=_FIXTURE)
    ) as fetch:
        rows = await service.collect("005930")

    assert rows
    url, headers = fetch.await_args.args[0], fetch.await_args.args[1]
    assert "code=005930" in url
    assert "clusterId=" in url
    assert headers["Referer"] == "https://finance.naver.com/item/news.naver?code=005930"


async def test_collect_applies_limit():
    service = StockNewsCollectorService()
    with patch.object(
        StockNewsCollectorService, "_fetch_html", new=AsyncMock(return_value=_FIXTURE)
    ):
        rows = await service.collect("005930", limit=2)

    assert len(rows) == 2


async def test_collect_returns_empty_on_fetch_failure_without_raising():
    logger = MagicMock()
    service = StockNewsCollectorService(logger=logger)
    with patch.object(
        StockNewsCollectorService,
        "_fetch_html",
        new=AsyncMock(side_effect=RuntimeError("HTTP 503")),
    ):
        rows = await service.collect("005930")

    assert rows == []
    logger.warning.assert_called_once()


async def test_collect_rejects_invalid_code_without_network_call():
    service = StockNewsCollectorService()
    with patch.object(
        StockNewsCollectorService, "_fetch_html", new=AsyncMock()
    ) as fetch:
        assert await service.collect("abc") == []
        assert await service.collect("") == []

    fetch.assert_not_awaited()


def _page_html(*titles):
    """지정한 제목들로 구성된 종목뉴스 1페이지 HTML을 만든다."""
    rows = "".join(
        f'<tr><td class="title">'
        f'<a href="/item/news_read.naver?article_id={i}&amp;code=005930" class="tit" target=_top>{title}</a>'
        f'</td><td class="info">언론사</td><td class="date"> 2026.07.20 0{i}:00</td></tr>'
        for i, title in enumerate(titles)
    )
    return f'<table class="type5"><caption>종목뉴스</caption><tbody>{rows}</tbody></table>'


async def test_collect_paginates_and_dedupes_across_pages():
    """1페이지가 limit 에 못 미치면 다음 페이지를 이어서 수집하고, 페이지 경계 중복 제목은 제거한다."""
    page1 = _page_html("기사 A", "기사 B")
    page2 = _page_html("기사 B", "기사 C")
    service = StockNewsCollectorService()
    with patch.object(
        StockNewsCollectorService,
        "_fetch_html",
        new=AsyncMock(side_effect=[page1, page2]),
    ) as fetch:
        rows = await service.collect("005930", limit=3)

    assert [r["title"] for r in rows] == ["기사 A", "기사 B", "기사 C"]
    urls = [call.args[0] for call in fetch.await_args_list]
    assert "page=1" in urls[0]
    assert "page=2" in urls[1]


async def test_collect_stops_when_next_page_has_no_new_articles():
    """마지막 페이지를 넘기면 네이버가 같은 기사를 반복하므로 새 기사가 없으면 중단한다."""
    page = _page_html("기사 A", "기사 B")
    service = StockNewsCollectorService()
    with patch.object(
        StockNewsCollectorService, "_fetch_html", new=AsyncMock(return_value=page)
    ) as fetch:
        rows = await service.collect("005930", limit=30)

    assert len(rows) == 2
    assert fetch.await_count == 2


async def test_collect_keeps_collected_rows_when_later_page_fails():
    """도중 페이지 실패 시 이미 수집한 기사는 유지한다."""
    logger = MagicMock()
    service = StockNewsCollectorService(logger=logger)
    with patch.object(
        StockNewsCollectorService,
        "_fetch_html",
        new=AsyncMock(side_effect=[_page_html("기사 A", "기사 B"), RuntimeError("HTTP 503")]),
    ):
        rows = await service.collect("005930", limit=30)

    assert [r["title"] for r in rows] == ["기사 A", "기사 B"]
    logger.warning.assert_called_once()


async def test_collect_respects_page_cap():
    """limit 이 커도 페이지 조회는 상한(5페이지)을 넘지 않는다."""
    pages = [_page_html(f"기사 {i}A", f"기사 {i}B") for i in range(1, 8)]
    service = StockNewsCollectorService()
    with patch.object(
        StockNewsCollectorService, "_fetch_html", new=AsyncMock(side_effect=pages)
    ) as fetch:
        rows = await service.collect("005930", limit=100)

    assert fetch.await_count == 5
    assert len(rows) == 10
