"""한국장과 미국장 화면의 프런트엔드 소유 경계를 잠그는 정적 계약 테스트."""

from pathlib import Path


def test_stock_js_is_domestic_only():
    stock_js = Path("view/web/static/js/stock.js").read_text(encoding="utf-8")

    assert "setStockMarketMode" not in stock_js
    assert "searchOverseasStock" not in stock_js
    assert "/api/overseas/" not in stock_js


def test_overseas_autocomplete_owns_symbol_lookup():
    autocomplete_js = Path(
        "view/web/static/js/overseas_autocomplete.js"
    ).read_text(encoding="utf-8")

    assert "/api/overseas/stocks/list" in autocomplete_js
    assert "overseas-symbol" in autocomplete_js
    assert "overseas-exchange" in autocomplete_js
    assert "loadOverseasQuote" in autocomplete_js


def test_overseas_template_loads_dedicated_autocomplete():
    template = Path("view/web/templates/overseas.html").read_text(encoding="utf-8")

    assert 'id="overseas-autocomplete-list"' in template
    assert "/static/js/autocomplete.js" in template
    assert "/static/js/overseas_autocomplete.js" in template
