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


def test_ai_analysis_requests_allow_server_retry_and_hide_abort_message():
    stock_js = Path("view/web/static/js/stock.js").read_text(encoding="utf-8")

    assert stock_js.count("60000,") >= 2
    assert "isAiRequestAbort(error)" in stock_js
    assert "AI 응답 시간이 초과되었습니다. 다시 시도해주세요." in stock_js


def test_daily_price_high_low_are_visually_emphasized():
    """당일 시세의 고가·저가는 강조 마크업과 전용 스타일을 갖는다."""
    stock_js = Path("view/web/static/js/stock.js").read_text(encoding="utf-8")

    # 강조 마크업 (고가=상승색, 저가=하락색 행)
    assert 'class="day-range-row high"' in stock_js
    assert 'class="day-range-row low"' in stock_js
    assert 'id="rt-high" class="day-range-val"' in stock_js
    assert 'id="rt-low" class="day-range-val"' in stock_js

    # 강조 스타일 정의
    assert ".stock-info-box .detail-group p.day-range-row.high" in stock_js
    assert ".stock-info-box .detail-group p.day-range-row.low" in stock_js
    assert ".stock-info-box .detail-group .day-range-val" in stock_js


def test_trade_trends_page_uses_cached_jeju_load_and_refresh_bypass():
    trade_trends_js = Path("view/web/static/js/trade_trends.js").read_text(encoding="utf-8")
    template = Path("view/web/templates/trade_trends.html").read_text(encoding="utf-8")

    assert "loadJejuRegionTrade(forceRefresh = false)" in trade_trends_js
    assert "refresh=true" in trade_trends_js
    assert "loadJejuRegionTrade(true)" in template
