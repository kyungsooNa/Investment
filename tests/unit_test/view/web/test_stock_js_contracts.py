"""stock.js 소스 계약(회귀 잠금) — 해외 기능이 국내 차트 경로를 깨지 않도록 강제.

런타임 DOM 행위는 tests/integration_test/.../test_it_stock_js_dom_regression.py(jsdom)가
검증한다. 본 모듈은 node toolchain 없이도 항상 도는 in-gate 정적 계약으로,
"차트 카드 대피가 stock-result 비우기보다 먼저 온다"는 핵심 순서를 잠근다.
"""
from pathlib import Path

import pytest

_STOCK_JS = Path("view/web/static/js/stock.js")


@pytest.fixture(scope="module")
def stock_js() -> str:
    return _STOCK_JS.read_text(encoding="utf-8")


def _function_body(src: str, name: str) -> str:
    """`function name(` 부터 다음 top-level `\\nfunction ` 직전까지를 반환(근사)."""
    start = src.index(f"function {name}(")
    rest = src[start + 1:]
    nxt = rest.find("\nfunction ")
    return rest if nxt == -1 else rest[:nxt]


def test_detach_helper_defined(stock_js):
    assert "function _detachStockChartCard(" in stock_js


def test_set_market_mode_detaches_chart_before_clearing_result(stock_js):
    """회귀 잠금: setStockMarketMode 는 stock-result 를 비우기 전에 차트 카드를 대피해야 한다.

    순서가 뒤바뀌면 stock-result 안으로 이동돼 있던 차트 카드가 innerHTML 초기화로 파괴된다.
    """
    body = _function_body(stock_js, "setStockMarketMode")
    detach_at = body.find("_detachStockChartCard()")
    clear_at = body.find("innerHTML = ''")
    assert detach_at != -1, "setStockMarketMode 가 _detachStockChartCard 를 호출하지 않음"
    assert clear_at != -1, "setStockMarketMode 가 stock-result 를 비우지 않음"
    assert detach_at < clear_at, "차트 카드 대피가 stock-result 비우기보다 먼저 와야 함"


def test_search_overseas_detaches_chart_before_overwriting_result(stock_js):
    """회귀 잠금: searchOverseasStock 도 결과를 덮어쓰기(showLoading) 전에 차트 카드를 대피."""
    body = _function_body(stock_js, "searchOverseasStock")
    detach_at = body.find("_detachStockChartCard()")
    loading_at = body.find("showLoading(")
    assert detach_at != -1, "searchOverseasStock 가 _detachStockChartCard 를 호출하지 않음"
    assert loading_at != -1, "searchOverseasStock 가 showLoading 으로 결과를 덮어쓰지 않음"
    assert detach_at < loading_at, "차트 카드 대피가 결과 덮어쓰기보다 먼저 와야 함"


def test_overseas_render_does_not_reuse_domestic_sse_ids(stock_js):
    """해외 렌더는 국내 전용 SSE 타깃 id(rt-price/rt-change-rate/rt-high/rt-low)를 만들면 안 된다.

    재사용 시 stock-price-tick 핸들러가 해외 결과 DOM 을 국내 틱으로 덮어쓸 수 있다.
    """
    body = _function_body(stock_js, "searchOverseasStock")
    for forbidden in ('id="rt-price"', 'id="rt-change-rate"', 'id="rt-high"', 'id="rt-low"'):
        assert forbidden not in body, f"해외 렌더가 국내 전용 {forbidden} 를 생성함"
