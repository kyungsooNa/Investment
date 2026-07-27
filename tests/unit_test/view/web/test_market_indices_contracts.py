"""홈 화면 글로벌 지수 패널의 템플릿-스크립트 배선을 잠그는 정적 계약 테스트."""

from pathlib import Path

EXPECTED_SYMBOLS = [
    "NASDAQ:NDX",
    "NASDAQ:SOX",
    "CBOE:VIX",
    "TVC:DXY",
    "TVC:GOLD",
    "TVC:USOIL",
    "TVC:US10Y",
    "TVC:US02Y",
]


def test_home_template_hosts_market_indices_panel():
    template = Path("view/web/templates/index.html").read_text(encoding="utf-8")

    assert 'id="market-indices"' in template
    assert "/static/js/market_indices.js" in template


def test_market_indices_js_covers_all_symbols():
    script = Path("view/web/static/js/market_indices.js").read_text(encoding="utf-8")

    for symbol in EXPECTED_SYMBOLS:
        assert symbol in script, f"{symbol} 지수 설정이 없음"
    assert "embed-widget-mini-symbol-overview.js" in script


def test_market_indices_styles_are_defined():
    css = Path("view/web/static/css/style.css").read_text(encoding="utf-8")

    assert ".market-indices-grid" in css
    assert ".market-index-card" in css
