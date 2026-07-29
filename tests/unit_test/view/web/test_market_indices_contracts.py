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
    ):
        assert selector in css, f"{selector} 스타일이 없음"


def test_domestic_index_period_selector_is_wired():
    script = _market_indices_js()

    for period in ("'1D'", "'1W'", "'1M'", "'1Y'"):
        assert period in script, f"{period} 기간 설정이 없음"
    # 기간은 쿼리 파라미터로 서버에 전달된다.
    assert "?period=" in script
