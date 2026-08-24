"""NaverMarketSnapshotService 단위 테스트.

네트워크는 타지 않는다 — page_fetcher 를 주입해 실제 마크업을 축약한 픽스처를 먹인다.
픽스처는 2026-08-14 실제 응답의 table.type_2 구조를 그대로 옮긴 것이다.
"""
import pytest

from services.naver_market_snapshot_service import NaverMarketSnapshotService


def _row(code, name, price, rate_text, cap):
    """네이버 시총 페이지의 데이터 행 한 줄 (td.number 순서가 계약이다)."""
    return f"""
    <tr onMouseOver="mouseOver(this)">
      <td class="no">1</td>
      <td><a href="/item/main.naver?code={code}" class="tltle">{name}</a></td>
      <td class="number">{price}</td>
      <td class="number">
        <em class="bu_p bu_pup"><span class="blind">상승</span></em><span class="tah p11 red02"> 6,500 </span>
      </td>
      <td class="number"><span class="tah p11 red01"> {rate_text} </span></td>
      <td class="number">100</td>
      <td class="number">{cap}</td>
      <td class="number">5,846,279</td>
      <td class="number">46.70</td>
      <td class="number">21,668,266</td>
      <td class="number">22.19</td>
      <td class="number">10.85</td>
      <td class="center"><a href="/item/board.naver?code={code}">토론</a></td>
    </tr>
    """


def _page(*rows):
    return f"""
    <table class="type_2">
      <tr><th scope="col">N</th><th scope="col">종목명</th><th scope="col">현재가</th>
          <th scope="col">전일비</th><th scope="col">등락률</th><th scope="col">액면가</th>
          <th scope="col">시가총액</th></tr>
      <tr><td colspan="13" class="division_line"></td></tr>
      {''.join(rows)}
    </table>
    """


KOSPI_PAGE = _page(
    _row("005930", "삼성전자", "274,500", "+2.43%", "16,048,035"),
    _row("000660", "SK하이닉스", "1,688,000", "+5.96%", "12,330,711"),
)
KOSDAQ_PAGE = _page(
    _row("247540", "에코프로비엠", "180,000", "-1.20%", "176,000"),
)


def _fetcher(pages, calls=None):
    """(sosok, page) -> HTML. 등록되지 않은 페이지는 빈 표(마지막 페이지)로 응답한다."""
    async def fetch(sosok, page):
        if calls is not None:
            calls.append((sosok, page))
        return pages.get((sosok, page), _page())
    return fetch


def _service(pages, calls=None, **kwargs):
    kwargs.setdefault("today_provider", lambda: "20260814")
    kwargs.setdefault("clock", lambda: 0.0)
    return NaverMarketSnapshotService(page_fetcher=_fetcher(pages, calls), **kwargs)


async def test_parses_rows_into_repository_shape():
    service = _service({(0, 1): KOSPI_PAGE})

    rows = await service.get_snapshot(limit=10, market="KOSPI")

    assert rows[0] == {
        "code": "005930",
        "name": "삼성전자",
        "current_price": 274500,
        "change_rate": "2.43",
        "market_cap": 16048035,
        "market": "KOSPI",
        "trade_date": "20260814",
    }


async def test_market_cap_keeps_naver_unit_which_matches_the_db():
    """네이버 시총은 이미 억원 단위라 daily_prices.market_cap 과 그대로 비교된다."""
    service = _service({(0, 1): KOSPI_PAGE})

    rows = await service.get_snapshot(limit=10, market="KOSPI")

    # 2026-08-13 종가 기준 DB 값이 15,668,027(억) 이었다 — 같은 자릿수여야 한다.
    assert rows[0]["market_cap"] == 16048035


async def test_keeps_the_sign_of_falling_stocks():
    service = _service({(1, 1): KOSDAQ_PAGE})

    rows = await service.get_snapshot(limit=10, market="KOSDAQ")

    assert rows[0]["change_rate"] == "-1.20"
    assert rows[0]["market"] == "KOSDAQ"


async def test_merges_markets_sorted_by_market_cap_desc():
    service = _service({(0, 1): KOSPI_PAGE, (1, 1): KOSDAQ_PAGE})

    rows = await service.get_snapshot(limit=10, market=None)

    assert [r["code"] for r in rows] == ["005930", "000660", "247540"]


async def test_limit_slices_after_the_merge():
    service = _service({(0, 1): KOSPI_PAGE, (1, 1): KOSDAQ_PAGE})

    rows = await service.get_snapshot(limit=2, market=None)

    assert [r["code"] for r in rows] == ["005930", "000660"]


async def test_market_filter_does_not_fetch_the_other_market():
    calls = []
    service = _service({(0, 1): KOSPI_PAGE}, calls=calls)

    await service.get_snapshot(limit=10, market="KOSPI")

    assert all(sosok == 0 for sosok, _ in calls), f"코스닥을 긁을 이유가 없다: {calls}"


async def test_fetches_only_as_many_pages_as_the_limit_needs():
    """limit 이 필요한 페이지 수를 이미 말해준다 — 그만큼만 긁는다."""
    calls = []
    service = _service({(0, 1): KOSPI_PAGE}, calls=calls)

    await service.get_snapshot(limit=100, market="KOSPI")

    pages = sorted(page for sosok, page in calls if sosok == 0)
    assert pages == [1, 2], f"페이지당 50종목이면 limit 100 은 2페이지 (실제 {pages})"


async def test_caps_pages_so_a_huge_limit_cannot_hammer_naver():
    calls = []
    service = _service({(0, 1): KOSPI_PAGE}, calls=calls)

    await service.get_snapshot(limit=1000, market="KOSPI")

    pages = [page for sosok, page in calls if sosok == 0]
    assert len(pages) <= NaverMarketSnapshotService._MAX_PAGES, f"상한을 넘김 (실제 {len(pages)})"


async def test_reuses_the_snapshot_within_ttl():
    calls = []
    now = [0.0]
    service = _service({(0, 1): KOSPI_PAGE}, calls=calls, clock=lambda: now[0], ttl_sec=45.0)

    await service.get_snapshot(limit=10, market="KOSPI")
    before = len(calls)
    now[0] = 44.0
    await service.get_snapshot(limit=10, market="KOSPI")

    assert len(calls) == before, "TTL 안에서는 다시 긁지 않아야 함"


async def test_refetches_after_ttl():
    calls = []
    now = [0.0]
    service = _service({(0, 1): KOSPI_PAGE}, calls=calls, clock=lambda: now[0], ttl_sec=45.0)

    await service.get_snapshot(limit=10, market="KOSPI")
    before = len(calls)
    now[0] = 46.0
    await service.get_snapshot(limit=10, market="KOSPI")

    assert len(calls) > before, "TTL 이 지나면 새 스냅샷을 받아야 함"


async def test_partial_page_failure_still_returns_what_was_collected():
    async def flaky(sosok, page):
        if page == 2:
            raise RuntimeError("네이버 응답 없음")
        return KOSPI_PAGE if page == 1 else _page()

    service = NaverMarketSnapshotService(
        page_fetcher=flaky, today_provider=lambda: "20260814", clock=lambda: 0.0
    )

    rows = await service.get_snapshot(limit=10, market="KOSPI")

    assert [r["code"] for r in rows] == ["005930", "000660"], "한 페이지가 죽어도 화면이 비면 안 됨"


async def test_allowed_codes_narrows_the_universe_to_the_stored_one():
    """네이버 시총 페이지는 ETF·ETN·우선주까지 싣는다. 히트맵 유니버스(daily_prices)에
    없는 종목이 장중에만 끼어들면 화면 구성이 장외와 달라지므로 걸러낸다."""
    page = _page(
        _row("069500", "KODEX 200", "40,000", "+1.00%", "60,000"),
        _row("005930", "삼성전자", "274,500", "+2.43%", "16,048,035"),
    )
    service = _service({(0, 1): page})

    rows = await service.get_snapshot(limit=10, market="KOSPI", allowed_codes={"005930"})

    assert [r["code"] for r in rows] == ["005930"]


async def test_allowed_codes_filters_before_slicing_the_limit():
    """자른 뒤에 거르면 limit 을 못 채운다 — 거르고 나서 잘라야 한다."""
    page = _page(
        _row("069500", "KODEX 200", "40,000", "+1.00%", "9,000,000"),
        _row("005930", "삼성전자", "274,500", "+2.43%", "8,000,000"),
        _row("000660", "SK하이닉스", "1,688,000", "+5.96%", "7,000,000"),
    )
    service = _service({(0, 1): page})

    rows = await service.get_snapshot(limit=2, market="KOSPI", allowed_codes={"005930", "000660"})

    assert [r["code"] for r in rows] == ["005930", "000660"]


async def test_allowed_codes_none_keeps_everything():
    service = _service({(0, 1): KOSPI_PAGE})

    rows = await service.get_snapshot(limit=10, market="KOSPI", allowed_codes=None)

    assert len(rows) == 2


async def test_returns_empty_when_markup_no_longer_parses():
    """마크업이 바뀌면 조용히 빈 목록이 된다 — 라우트가 저장소로 폴백할 수 있어야 한다."""
    service = _service({(0, 1): "<html><body>개편된 페이지</body></html>"})

    rows = await service.get_snapshot(limit=10, market="KOSPI")

    assert rows == []


async def test_unknown_market_name_fetches_nothing():
    calls = []
    service = _service({}, calls)

    assert await service.get_snapshot(limit=10, market="NASDAQ") == []
    assert calls == []


async def test_rows_duplicated_across_pages_are_kept_once():
    pages = {
        (0, 1): _page(_row("005930", "삼성전자", "70,000", "+1.00%", "400,000")),
        (0, 2): _page(_row("005930", "삼성전자", "70,000", "+1.00%", "400,000")),
    }
    service = _service(pages)

    rows = await service.get_snapshot(limit=200, market="KOSPI")

    assert [r["code"] for r in rows] == ["005930"]


async def test_rows_with_unparsable_numbers_are_dropped():
    pages = {
        (0, 1): _page(
            _row("005930", "삼성전자", "가격없음", "+1.00%", "400,000"),
            _row("000660", "SK하이닉스", "200,000", "등락없음", "150,000"),
            _row("035420", "NAVER", "180,000", "+0.50%", "시총없음"),
            _row("005380", "현대차", "250,000", "+0.30%", "50,000"),
        ),
    }
    service = _service(pages)

    rows = await service.get_snapshot(limit=10, market="KOSPI")

    assert [r["code"] for r in rows] == ["005380"]


async def test_rows_without_enough_number_cells_are_dropped():
    html = (
        '<table class="type_2"><tr>'
        '<td><a class="tltle" href="/item/main.naver?code=005930">삼성전자</a></td>'
        '<td class="number">70,000</td>'
        '</tr></table>'
    )
    service = _service({(0, 1): html})

    assert await service.get_snapshot(limit=10, market="KOSPI") == []


def test_number_and_rate_parsers_reject_unusable_text():
    from services.naver_market_snapshot_service import _to_int, _to_rate

    assert _to_int("70,000") == 70000
    assert _to_int("가격없음") is None
    assert _to_int(None) is None

    assert _to_rate("+2.43%") == "2.43"
    assert _to_rate("-1.20%") == "-1.20"
    assert _to_rate("등락없음") is None


def test_default_today_provider_returns_a_kst_date_string():
    from services.naver_market_snapshot_service import NaverMarketSnapshotService

    today = NaverMarketSnapshotService._today_kst()

    assert len(today) == 8 and today.isdigit()


async def test_default_page_fetcher_decodes_euc_kr_and_raises_on_error_status():
    from unittest.mock import patch

    from services.naver_market_snapshot_service import NaverMarketSnapshotService

    service = NaverMarketSnapshotService()

    class _Response:
        def __init__(self, status):
            self.status = status

        async def text(self, encoding=None):
            assert encoding == "euc-kr"
            return "<html></html>"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _Session:
        def __init__(self, status):
            self._status = status

        def get(self, *args, **kwargs):
            return _Response(self._status)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    with patch(
        "services.naver_market_snapshot_service.aiohttp.ClientSession",
        lambda *a, **k: _Session(200),
    ):
        assert await service._fetch_page(0, 1) == "<html></html>"

    with patch(
        "services.naver_market_snapshot_service.aiohttp.ClientSession",
        lambda *a, **k: _Session(503),
    ):
        with pytest.raises(RuntimeError, match="HTTP 503"):
            await service._fetch_page(0, 1)
