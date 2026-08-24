"""ThemeClassificationCollectorService 단위 테스트.

- 테마 목록/구성종목 HTML 파싱
- collect 흐름: alias 적용 + upsert 레코드 형태
- 상세 페이지 실패 시 해당 테마만 skip(부분 성공)
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

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
    r.replace_source_classifications = AsyncMock(side_effect=lambda src, cat, recs: len(recs))
    r.upsert_aliases = AsyncMock(return_value=0)
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
    src, cat, recs = repo.replace_source_classifications.call_args[0]
    assert (src, cat) == ("NAVER", "theme")
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
    recs = repo.replace_source_classifications.call_args[0][2]
    assert [r["code"] for r in recs] == ["000660"]
    assert recs[0]["group_name"] == "테마B"


@pytest.mark.asyncio
async def test_collect_empty_list_returns_zero(repo):
    svc = ThemeClassificationCollectorService(repo, logger=MagicMock(), request_delay=0)
    svc._fetch_html = AsyncMock(return_value="<html>no themes</html>")
    saved = await svc.collect_naver_themes()
    assert saved == 0
    repo.replace_source_classifications.assert_not_called()


@pytest.mark.asyncio
async def test_collect_list_fetch_failure_returns_zero(repo):
    svc = ThemeClassificationCollectorService(repo, logger=MagicMock(), request_delay=0)
    svc._fetch_html = AsyncMock(side_effect=RuntimeError("목록 실패"))
    saved = await svc.collect_naver_themes()
    assert saved == 0


@pytest.mark.asyncio
async def test_collect_syncs_alias_config(tmp_path, repo):
    """alias yaml 이 있으면 파싱해 upsert_aliases 로 반영한다."""
    cfg = tmp_path / "theme_aliases.yaml"
    cfg.write_text(
        "aliases:\n  2차전지:\n    - 2차전지 소재\n    - 2차전지(소재)\n",
        encoding="utf-8",
    )
    svc = ThemeClassificationCollectorService(
        repo, logger=MagicMock(), request_delay=0, alias_config_path=str(cfg)
    )
    svc._fetch_html = AsyncMock(side_effect=[
        _list_html([("1", "2차전지 소재")]),
        _detail_html([("247540", "에코프로비엠")]),
    ])

    await svc.collect_naver_themes()

    repo.upsert_aliases.assert_awaited_once()
    alias_recs = repo.upsert_aliases.call_args[0][0]
    assert {"source": "NAVER", "raw_name": "2차전지 소재", "normalized_name": "2차전지"} in alias_recs
    assert {"source": "NAVER", "raw_name": "2차전지(소재)", "normalized_name": "2차전지"} in alias_recs


@pytest.mark.asyncio
async def test_collect_without_alias_config_skips_alias_upsert(repo):
    """alias 설정 파일이 없으면 upsert_aliases 를 호출하지 않고 정상 수집한다."""
    svc = ThemeClassificationCollectorService(
        repo, logger=MagicMock(), request_delay=0,
        alias_config_path="config/__nonexistent_alias__.yaml",
    )
    svc._fetch_html = AsyncMock(side_effect=[
        _list_html([("1", "로봇")]),
        _detail_html([("005930", "삼성전자")]),
    ])
    saved = await svc.collect_naver_themes()
    assert saved == 1
    repo.upsert_aliases.assert_not_called()


# ── 업종(upjong) 수집 ────────────────────────────────────────
# 업종 페이지는 테마 페이지와 URL 패턴·마크업이 같아 같은 파서를 쓴다.
# 다른 점은 type=upjong, category_type='industry', 그리고 alias 미적용이다.

@pytest.mark.asyncio
async def test_collect_industries_uses_upjong_urls(repo):
    svc = ThemeClassificationCollectorService(repo, logger=MagicMock(), request_delay=0)
    svc._fetch_html = AsyncMock(side_effect=[
        _list_html([("278", "반도체와반도체장비")]),
        _detail_html([("005930", "삼성전자")]),
    ])

    await svc.collect_naver_industries()

    urls = [call.args[0] for call in svc._fetch_html.call_args_list]
    assert all("type=upjong" in url for url in urls), f"업종 URL 이어야 함: {urls}"
    assert "no=278" in urls[1]


@pytest.mark.asyncio
async def test_collect_industries_stores_industry_category(repo):
    svc = ThemeClassificationCollectorService(repo, logger=MagicMock(), request_delay=0)
    svc._fetch_html = AsyncMock(side_effect=[
        _list_html([("278", "반도체와반도체장비")]),
        _detail_html([("005930", "삼성전자"), ("000660", "SK하이닉스")]),
    ])

    saved = await svc.collect_naver_industries()

    assert saved == 2
    src, cat, recs = repo.replace_source_classifications.call_args[0]
    assert (src, cat) == ("NAVER", "industry")
    assert recs[0]["category_type"] == "industry"
    assert recs[0]["group_name"] == "반도체와반도체장비"
    assert [r["code"] for r in recs] == ["005930", "000660"]


@pytest.mark.asyncio
async def test_collect_industries_does_not_apply_theme_aliases(repo):
    """alias 는 테마 전용 개념이다. 업종명을 테마 alias 로 바꾸면 분류가 뒤섞인다."""
    repo.get_alias_map = AsyncMock(return_value={"반도체와반도체장비": "2차전지"})
    svc = ThemeClassificationCollectorService(repo, logger=MagicMock(), request_delay=0)
    svc._fetch_html = AsyncMock(side_effect=[
        _list_html([("278", "반도체와반도체장비")]),
        _detail_html([("005930", "삼성전자")]),
    ])

    await svc.collect_naver_industries()

    recs = repo.replace_source_classifications.call_args[0][2]
    assert recs[0]["normalized_name"] == "반도체와반도체장비"
    repo.upsert_aliases.assert_not_called()


@pytest.mark.asyncio
async def test_collect_industries_empty_list_returns_zero(repo):
    svc = ThemeClassificationCollectorService(repo, logger=MagicMock(), request_delay=0)
    svc._fetch_html = AsyncMock(return_value="<html>개편된 페이지</html>")

    assert await svc.collect_naver_industries() == 0
    repo.replace_source_classifications.assert_not_called()


def _svc(repo, **kwargs):
    kwargs.setdefault("logger", MagicMock())
    kwargs.setdefault("request_delay", 0)
    return ThemeClassificationCollectorService(repo, **kwargs)


@pytest.mark.asyncio
async def test_request_delay_is_awaited_between_detail_pages(repo):
    svc = _svc(repo, request_delay=0.01)
    svc._fetch_html = AsyncMock(side_effect=[
        _list_html([("1", "2차전지")]),
        _detail_html([("247540", "에코프로비엠")]),
    ])

    with patch(
        "services.theme_classification_collector_service.asyncio.sleep",
        new_callable=AsyncMock,
    ) as sleep_mock:
        await svc.collect_naver_themes()

    sleep_mock.assert_awaited()


@pytest.mark.asyncio
async def test_alias_config_load_failure_is_logged_and_skipped(tmp_path, repo):
    cfg = tmp_path / "theme_aliases.yaml"
    cfg.write_text("aliases: [불완전한 yaml", encoding="utf-8")
    svc = _svc(repo, alias_config_path=str(cfg))
    svc._fetch_html = AsyncMock(side_effect=[
        _list_html([("1", "2차전지")]),
        _detail_html([("247540", "에코프로비엠")]),
    ])

    await svc.collect_naver_themes()

    repo.upsert_aliases.assert_not_awaited()
    svc._logger.warning.assert_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["aliases: 문자열\n", "리스트\n", "aliases:\n  2차전지: 리스트아님\n"])
async def test_alias_config_with_unusable_shape_yields_no_aliases(tmp_path, repo, payload):
    cfg = tmp_path / "theme_aliases.yaml"
    cfg.write_text(payload, encoding="utf-8")
    svc = _svc(repo, alias_config_path=str(cfg))

    assert svc._load_alias_config() == []


@pytest.mark.asyncio
async def test_fetch_html_decodes_euc_kr_and_raises_on_error_status(repo):
    svc = _svc(repo)

    class _Response:
        def __init__(self, status):
            self.status = status

        async def text(self, encoding=None, errors=None):
            assert encoding == "euc-kr"
            return "본문"

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
        "services.theme_classification_collector_service.aiohttp.ClientSession",
        lambda *a, **k: _Session(200),
    ):
        assert await svc._fetch_html("https://example") == "본문"

    with patch(
        "services.theme_classification_collector_service.aiohttp.ClientSession",
        lambda *a, **k: _Session(503),
    ):
        with pytest.raises(RuntimeError, match="HTTP 503"):
            await svc._fetch_html("https://example")


def test_theme_list_parser_ignores_anchors_without_a_group_number():
    html = '<a href="/sise/sise_group_detail.naver?type=theme">번호없음</a>'

    assert ThemeClassificationCollectorService._parse_theme_list(html) == []


def test_theme_list_parser_ignores_anchors_without_text():
    html = '<a href="/sise/sise_group_detail.naver?type=theme&no=1"></a>'

    assert ThemeClassificationCollectorService._parse_theme_list(html) == []


def test_theme_member_parser_ignores_anchors_without_a_code_or_text():
    html = (
        '<a href="/item/main.naver?code=">코드없음</a>'
        '<a href="/item/main.naver?code=005930"></a>'
    )

    assert ThemeClassificationCollectorService._parse_theme_members(html) == []
