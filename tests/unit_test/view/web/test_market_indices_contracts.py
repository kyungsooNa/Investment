"""홈 화면 시장 지수 패널의 템플릿-스크립트 배선을 잠그는 정적 계약 테스트."""

import re
from pathlib import Path

# 미장/원자재는 TradingView 차트 포함 위젯. 거래소 실지수는 무료 임베드가 차단하므로
# 실제로 시세와 그래프가 그려지는 CFD/ETF 심볼만 쓴다.
EXPECTED_WIDGET_SYMBOLS = [
    "CAPITALCOM:US100",
    "CAPITALCOM:US500",
    "NASDAQ:SOXX",
    "CAPITALCOM:VIX",
    "CAPITALCOM:DXY",
    "TVC:GOLD",
    "TVC:USOIL",
    "CBOE:DRAM",
    "BITSTAMP:BTCUSD",
    "BITSTAMP:ETHUSD",
    "FRED:DGS10",
    "FRED:DGS2",
]

# 위젯에서 "이 심볼은 트레이딩뷰에서만 쓸 수 있습니다"로 막히는 심볼 (회귀 방지)
# 국채 금리(US10Y/US02Y)는 2026-08-26 재실측 — mini-symbol-overview·symbol-overview·
# advanced-chart 세 위젯 타입 모두 차단. 금리는 FRED:DGS10/DGS2 로 표시한다.
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

EXPECTED_GROUP_TITLES = ["국장", "미장", "원자재", "가상자산", "채권"]


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
    assert "embed-widget-symbol-overview.js" in script
    assert "embed-widget-mini-symbol-overview.js" not in script
    assert "chartOnly: false" in script


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


def test_market_indices_rerenders_after_pjax_navigation():
    script = _market_indices_js()

    assert "pjax:ready" in script
    assert "renderMarketIndices" in script


def test_domestic_index_card_does_not_block_on_flow_query():
    script = _market_indices_js()

    assert "appendMarketIndexFlow(doc, card, entry.code).catch" in script
    assert "await appendMarketIndexFlow(doc, card, entry.code)" not in script


def test_market_index_flow_route_exists():
    routes = Path("view/web/routes/stock.py").read_text(encoding="utf-8")

    assert '"/market-index/{index_code}/flow"' in routes
    assert "get_index_flow" in routes


# 차트 포함 위젯은 일정 높이가 필요하지만, 홈 첫 화면이 과하게 길어지지 않도록 상한을 둔다.
MARKET_INDEX_BODY_HEIGHT_BUDGET_PX = 220


def _body_height_px(css: str) -> int:
    match = re.search(r"height:\s*(\d+)px", _css_rule_body(css, ".market-index-body"))
    assert match, ".market-index-body 에 height 가 없음"
    return int(match.group(1))


def test_market_index_card_body_height_is_within_budget():
    css = Path("view/web/static/css/style.css").read_text(encoding="utf-8")

    height = _body_height_px(css)
    assert height <= MARKET_INDEX_BODY_HEIGHT_BUDGET_PX, (
        f".market-index-body 가 {height}px — 예산 {MARKET_INDEX_BODY_HEIGHT_BUDGET_PX}px 초과. "
        "카드를 키우려면 예산도 함께 올리고 한 화면에 들어오는지 확인할 것"
    )


def test_widget_height_matches_kis_card_body_height():
    """세 그룹이 같은 그리드를 쓰므로 한쪽만 바뀌면 행이 들쭉날쭉해진다 (#916 부류)."""
    css = Path("view/web/static/css/style.css").read_text(encoding="utf-8")

    match = re.search(r"height:\s*(\d+),", _market_indices_js())
    assert match, "위젯 설정에 height 가 없음"
    assert int(match.group(1)) == _body_height_px(css), (
        "위젯 height 와 .market-index-body height 가 어긋나면 카드 높이가 달라진다"
    )


def test_sparkline_fits_inside_card_body():
    css = Path("view/web/static/css/style.css").read_text(encoding="utf-8")

    match = re.search(r"max-height:\s*(\d+)px", _css_rule_body(css, ".market-index-spark"))
    assert match, ".market-index-spark 에 max-height 가 없음"
    assert int(match.group(1)) < _body_height_px(css), (
        "스파크라인이 본문보다 크면 값·등락률 자리가 없다"
    )


def test_domestic_index_period_selector_is_wired():
    script = _market_indices_js()

    for period in ("'1D'", "'1W'", "'1M'", "'1Y'"):
        assert period in script, f"{period} 기간 설정이 없음"
    # 기간은 쿼리 파라미터로 서버에 전달된다.
    assert "?period=" in script
