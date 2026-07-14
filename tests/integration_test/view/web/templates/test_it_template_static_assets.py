from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from common.types import ResCommonResponse
import view.web.api_common as api_common
import view.web.web_main as web_main


PAGE_PATHS = [
    "/",
    "/stock",
    "/favorite",
    "/balance",
    "/order",
    "/overseas",
    "/ranking",
    "/marketcap",
    "/virtual",
    "/scheduler",
    "/strategy-reports",
    "/program",
    "/system",
]
STATIC_REF_RE = re.compile(
    r"""<(?:script|link)\b[^>]*(?:src|href)=["']([^"']+)["']""",
    re.IGNORECASE,
)


@pytest.fixture(autouse=True)
def clear_web_ctx():
    api_common.set_ctx(None)
    yield
    api_common.set_ctx(None)


@pytest.fixture
def web_client_with_fake_ctx():
    ctx = MagicMock()
    ctx.full_config = {"use_login": False, "auth": {"secret_key": "test-token"}}
    ctx.env = SimpleNamespace(
        is_paper_trading=True,
        active_config={"stock_account_number": "12345678-01"},
    )
    ctx.stock_query_service = MagicMock()
    ctx.stock_query_service.handle_get_account_balance = AsyncMock(
        return_value=ResCommonResponse(
            rt_cd="0",
            msg1="정상",
            data={"cash": 1000000, "stocks": []},
        )
    )

    api_common.set_ctx(ctx)
    return TestClient(web_main.app)


def _local_static_refs(html):
    refs = set()
    for match in STATIC_REF_RE.finditer(html):
        path = urlsplit(match.group(1)).path
        if path.startswith("/static/"):
            refs.add(path)
    return sorted(refs)


def test_rendered_pages_reference_served_static_assets(web_client_with_fake_ctx):
    """렌더링된 HTML의 로컬 static 참조가 실제 앱에서 200으로 응답해야 한다."""
    client = web_client_with_fake_ctx

    for page_path in PAGE_PATHS:
        page = client.get(page_path)

        assert page.status_code == 200
        assert "Investment Login" not in page.text

        for static_path in _local_static_refs(page.text):
            asset = client.get(static_path)

            assert asset.status_code == 200, f"{page_path} references missing asset: {static_path}"
            assert asset.content


@pytest.mark.parametrize(
    ("page_path", "expected_market"),
    [
        ("/stock", "domestic"),
        ("/favorite", "domestic"),
        ("/balance", "domestic"),
        ("/order", "domestic"),
        ("/overseas", "overseas_us"),
        ("/virtual", "common"),
        ("/system", "common"),
    ],
)
def test_rendered_pages_expose_view_market_context(
    web_client_with_fake_ctx,
    page_path,
    expected_market,
):
    page = web_client_with_fake_ctx.get(page_path)

    assert page.status_code == 200
    assert f'data-view-market="{expected_market}"' in page.text


def test_navigation_separates_domestic_overseas_and_common_areas(web_client_with_fake_ctx):
    page = web_client_with_fake_ctx.get("/stock")

    assert page.status_code == 200
    assert 'data-nav-market="domestic"' in page.text
    assert 'data-nav-market="overseas_us"' in page.text
    assert 'data-nav-market="common"' in page.text
    assert '>한국장<' in page.text
    assert '>미국장<' in page.text


def test_stock_page_is_domestic_only(web_client_with_fake_ctx):
    page = web_client_with_fake_ctx.get("/stock")

    assert page.status_code == 200
    assert 'id="stock-market-label"' in page.text
    assert 'id="stock-mode-overseas"' not in page.text
    assert 'id="overseas-stock-row"' not in page.text
    assert "searchOverseasStock" not in page.text


def test_home_groups_market_and_common_entry_points(web_client_with_fake_ctx):
    page = web_client_with_fake_ctx.get("/")

    assert page.status_code == 200
    assert 'data-home-market="domestic"' in page.text
    assert 'data-home-market="overseas_us"' in page.text
    assert 'data-home-market="common"' in page.text
