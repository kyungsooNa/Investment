"""ThemeClassificationCollectorService 단위 테스트.

- 테마 목록/구성종목 HTML 파싱
- collect 흐름: alias 적용 + upsert 레코드 형태
- 상세 페이지 실패 시 해당 테마만 skip(부분 성공)
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from services.theme_classification_collector_service import ThemeClassificationCollectorService


def _list_html(themes):
    """themes: [(no, name)] → 목록 페이지 HTML."""
    rows = "".join(
        f'<td><a href="/sise/sise_group_detail.naver?type=theme&no={no}">{name}</a></td>'
        for no, name in themes
    )
    return f"<table>{rows}</table>"


def _detail_html(members):
    """members: [(code, name)] → 상세 페이지 HTML."""
    rows = "".join(
        f'<td class="name"><a href="/item/main.naver?code={code}">{name}</a></td>'
        for code, name in members
    )
    return f"<table class='type_5'>{rows}</table>"


@pytest.fixture
def repo():
    r = MagicMock()
    r.get_alias_map = AsyncMock(return_value={})
    r.upsert_classifications = AsyncMock(side_effect=lambda recs: len(recs))
    r.get_latest_collected_at = AsyncMock(return_value=None)
    return r


def test_parse_theme_list():
    html = _list_html([("123", "2차전지"), ("456", "로봇")])
    out = ThemeClassificationCollectorService._parse_theme_list(html)
    assert out == [("123", "2차전지"), ("456", "로봇")]


def test_parse_theme_list_dedup():
    html = _list_html([("123", "2차전지"), ("123", "2차전지")])
    out = ThemeClassificationCollectorService._parse_theme_list(html)
    assert out == [("123", "2차전지")]


def test_parse_theme_members():
    html = _detail_html([("005930", "삼성전자"), ("000660", "SK하이닉스")])
    out = ThemeClassificationCollectorService._parse_theme_members(html)
    assert out == [("005930", "삼성전자"), ("000660", "SK하이닉스")]


@pytest.mark.asyncio
async def test_collect_builds_records_with_alias(repo):
    repo.get_alias_map = AsyncMock(return_value={"2차전지 소재": "2차전지"})
    svc = ThemeClassificationCollectorService(repo, logger=MagicMock(), request_delay=0)

    svc._fetch_html = AsyncMock(side_effect=[
        _list_html([("1", "2차전지 소재")]),
        _detail_html([("247540", "에코프로비엠")]),
    ])

    saved = await svc.collect_naver_themes()
    assert saved == 1
    recs = repo.upsert_classifications.call_args[0][0]
    assert recs[0]["source"] == "NAVER"
    assert recs[0]["category_type"] == "theme"
    assert recs[0]["group_name"] == "2차전지 소재"
    assert recs[0]["normalized_name"] == "2차전지"   # alias 적용
    assert recs[0]["code"] == "247540"


@pytest.mark.asyncio
async def test_collect_skips_failed_detail(repo):
    svc = ThemeClassificationCollectorService(repo, logger=MagicMock(), request_delay=0)
    svc._fetch_html = AsyncMock(side_effect=[
        _list_html([("1", "테마A"), ("2", "테마B")]),
        RuntimeError("detail A 실패"),                 # 테마A 상세 실패 → skip
        _detail_html([("000660", "SK하이닉스")]),       # 테마B 성공
    ])
    saved = await svc.collect_naver_themes()
    assert saved == 1
    recs = repo.upsert_classifications.call_args[0][0]
    assert [r["code"] for r in recs] == ["000660"]
    assert recs[0]["group_name"] == "테마B"


@pytest.mark.asyncio
async def test_collect_empty_list_returns_zero(repo):
    svc = ThemeClassificationCollectorService(repo, logger=MagicMock(), request_delay=0)
    svc._fetch_html = AsyncMock(return_value="<html>no themes</html>")
    saved = await svc.collect_naver_themes()
    assert saved == 0
    repo.upsert_classifications.assert_not_called()


@pytest.mark.asyncio
async def test_collect_list_fetch_failure_returns_zero(repo):
    svc = ThemeClassificationCollectorService(repo, logger=MagicMock(), request_delay=0)
    svc._fetch_html = AsyncMock(side_effect=RuntimeError("목록 실패"))
    saved = await svc.collect_naver_themes()
    assert saved == 0
