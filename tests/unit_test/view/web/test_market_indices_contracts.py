"""홈 화면 시장 지수 패널의 템플릿-스크립트 배선을 잠그는 정적 계약 테스트."""

from pathlib import Path

# 미장/원자재는 TradingView 위젯. 거래소 실지수는 무료 임베드가 차단하므로
# 실제로 시세가 그려지는 CFD/ETF 심볼만 쓴다.
EXPECTED_WIDGET_SYMBOLS = [
    "CAPITALCOM:US100",
    "CAPITALCOM:US500",
    "NASDAQ:SOXX",
    "CAPITALCOM:VIX",
    "CAPITALCOM:DXY",
    "TVC:GOLD",
    "TVC:USOIL",
    "CBOE:DRAM",
]

# 위젯에서 "이 심볼은 트레이딩뷰에서만 쓸 수 있습니다"로 막히는 심볼 (회귀 방지)
BLOCKED_WIDGET_SYMBOLS = [
    "KRX:KOSPI",
    "KRX:KOSDAQ",
    "SP:SPX",
    "NASDAQ:NDX",
    "NASDAQ:SOX",
    "CBOE:VIX",
    "TVC:DXY",
    "TVC:US10Y",
    "TVC:US02Y",
]

EXPECTED_GROUP_TITLES = ["국장", "미장", "원자재"]


def _market_indices_js() -> str:
    return Path("view/web/static/js/market_indices.js").read_text(encoding="utf-8")


def test_home_template_hosts_market_indices_panel():
    template = Path("view/web/templates/index.html").read_text(encoding="utf-8")

    assert 'id="market-indices"' in template
    assert "/static/js/market_indices.js" in template
    # 국장 스파크라인은 Chart.js 를 쓴다.
    assert "chart.js" in template


def test_market_indices_js_covers_all_widget_symbols():
    script = _market_indices_js()

    for symbol in EXPECTED_WIDGET_SYMBOLS:
        assert symbol in script, f"{symbol} 지수 설정이 없음"
    assert "embed-widget-mini-symbol-overview.js" in script


def test_market_indices_js_avoids_blocked_widget_symbols():
    script = _market_indices_js()

    for symbol in BLOCKED_WIDGET_SYMBOLS:
        assert f"'{symbol}'" not in script and f'"{symbol}"' not in script, (
            f"{symbol} 은 무료 임베드에서 차트가 표시되지 않는 심볼임"
        )


def test_market_indices_js_declares_market_groups():
    script = _market_indices_js()

    for title in EXPECTED_GROUP_TITLES:
        assert f"'{title}'" in script, f"{title} 그룹 설정이 없음"


def test_domestic_indices_use_kis_api_not_widget():
    script = _market_indices_js()

    assert "/api/market-index/" in script
    assert "'0001'" in script and "'1001'" in script


def test_market_indices_styles_are_defined():
    css = Path("view/web/static/css/style.css").read_text(encoding="utf-8")

    for selector in (
        ".market-indices-grid",
        ".market-index-card",
        ".market-index-group-title",
        ".market-index-value",
        ".market-index-spark",
        ".market-index-period",
        ".market-index-flow",
        ".market-index-flow-value",
    ):
        assert selector in css, f"{selector} 스타일이 없음"


def _css_rule_body(css: str, selector: str) -> str:
    start = css.index(f"\n{selector} {{") + len(selector) + 3
    return css[start:css.index("}", start)]


def test_market_index_color_utilities_are_not_overridden():
    """등락 색은 .text-red/.text-blue 가 잡는다.

    두 유틸리티는 스타일시트 앞쪽(명시도 동일)에 있어서, 뒤에 오는 지수 카드 규칙이
    color 를 직접 잡으면 색이 통째로 죽는다. 기본색은 컨테이너에만 둔다.
    """
    css = Path("view/web/static/css/style.css").read_text(encoding="utf-8")

    for selector in (".market-index-change", ".market-index-flow-value"):
        assert "color:" not in _css_rule_body(css, selector), (
            f"{selector} 가 color 를 직접 잡으면 .text-red/.text-blue 가 무시된다"
        )
    for selector in (".market-index-body", ".market-index-flow"):
        assert "color:" in _css_rule_body(css, selector), f"{selector} 에 기본 글자색이 없음"


def test_domestic_index_flow_is_wired_to_its_own_endpoint():
    script = _market_indices_js()

    # 수급은 기간과 무관하므로 차트와 다른 엔드포인트를 쓴다.
    assert "/flow`" in script, "수급 조회 엔드포인트가 없음"
    for label in ("'개인'", "'외국인'", "'기관'", "'상승'", "'보합'", "'하락'"):
        assert label in script, f"{label} 수급 항목이 없음"


def test_market_index_flow_route_exists():
    routes = Path("view/web/routes/stock.py").read_text(encoding="utf-8")

    assert '"/market-index/{index_code}/flow"' in routes
    assert "get_index_flow" in routes


def test_domestic_index_period_selector_is_wired():
    script = _market_indices_js()

    for period in ("'1D'", "'1W'", "'1M'", "'1Y'"):
        assert period in script, f"{period} 기간 설정이 없음"
    # 기간은 쿼리 파라미터로 서버에 전달된다.
    assert "?period=" in script
