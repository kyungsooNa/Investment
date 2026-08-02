"""홈 화면 S&P500 히트맵의 템플릿-스크립트 배선을 잠그는 정적 계약 테스트."""

import re
from pathlib import Path


def _heatmap_js() -> str:
    return Path("view/web/static/js/market_heatmap.js").read_text(encoding="utf-8")


def _home_template() -> str:
    return Path("view/web/templates/index.html").read_text(encoding="utf-8")


def _heatmap_page_template() -> str:
    return Path("view/web/templates/heatmap.html").read_text(encoding="utf-8")


def _heatmap_page_js() -> str:
    return Path("view/web/static/js/heatmap_page.js").read_text(encoding="utf-8")


def _heatmap_css() -> str:
    return Path("view/web/static/css/heatmap.css").read_text(encoding="utf-8")


def test_home_template_hosts_heatmap_panel():
    template = _home_template()

    assert 'id="market-heatmap-card"' in template
    assert 'id="market-heatmap"' in template
    assert 'id="market-heatmap-updated-at"' in template
    assert "/static/js/market_heatmap.js" in template


def test_home_template_hosts_domestic_heatmap_panel():
    template = _home_template()

    assert 'id="domestic-heatmap-card"' in template
    assert 'id="domestic-heatmap"' in template
    assert 'id="domestic-heatmap-updated-at"' in template


def test_domestic_heatmap_uses_daily_snapshot_and_shows_base_date():
    script = _heatmap_js()

    # 실시간 전종목 시세 소스가 없어 장마감 후 스냅샷을 쓴다 — 기준일을 감추지 않는다.
    assert "/api/heatmap/domestic" in script
    assert "기준일" in script and "종가" in script
    # 배타적 업종 분류가 없으므로 섹터 블록 없이 단일 그룹으로 그린다.
    assert "sector: null" in script


def test_heatmap_component_styles_live_in_one_shared_stylesheet():
    """홈 미리보기와 전용 페이지가 같은 타일을 그리므로 컴포넌트 CSS 는 한 곳에만 둔다."""
    css = _heatmap_css()

    for selector in (
        ".heatmap-canvas",
        ".heatmap-sector",
        ".heatmap-sector-title",
        ".heatmap-sector-body",
        ".heatmap-tile",
        ".heatmap-legend",
        ".heatmap-toolbar",
    ):
        assert selector in css, f"{selector} 스타일이 공용 스타일시트에 없음"

    for template in (_home_template(), _heatmap_page_template()):
        assert "/static/css/heatmap.css" in template, "히트맵 공용 스타일시트가 연결되지 않음"
        assert ".heatmap-tile {" not in template, "컴포넌트 CSS 가 템플릿에 복제되어 있음"


def test_home_template_hosts_heatmap_layout_only():
    """홈은 미리보기 높이만 소유한다(타일 스타일은 공용 시트)."""
    template = _home_template()

    assert "#market-heatmap, #domestic-heatmap {" in template


def test_heatmap_reuses_overseas_market_cap_snapshot():
    script = _heatmap_js()

    # 별도 수집 없이 미국 시가총액 화면과 같은 스냅샷(TTL 캐시 공유)을 쓴다.
    assert "/api/overseas/top-market-cap" in script
    assert "HEATMAP_LIMIT = 500" in script
    # 미국장이 꺼진 run 에서는 조회 대신 카드를 감춘다.
    assert "/api/market-mode" in script
    assert "overseas_us" in script


def test_heatmap_renders_untrusted_strings_as_text():
    script = _heatmap_js()

    assert "escapeHtml" in script
    for field in ("symbol", "name", "sector"):
        assert f"row.{field}" in script


def test_heatmap_legend_colors_match_script_palette():
    script = _heatmap_js()

    palette = set(re.findall(r"color: '(#[0-9a-f]{6})'", script))
    assert len(palette) == 8, f"상승/하락 4단계 색이 정의되어야 함 (실제 {len(palette)}종)"
    for template in (_home_template(), _heatmap_page_template()):
        for color in palette:
            assert color in template, f"범례에 {color} 가 빠져 스크립트와 어긋남"


def test_heatmap_page_hosts_market_tabs_and_panels():
    template = _heatmap_page_template()

    for element_id in (
        "heatmap-page-tab-domestic",
        "heatmap-page-tab-overseas",
        "heatmap-page-domestic",
        "heatmap-page-overseas",
        "heatmap-page-domestic-caption",
        "heatmap-page-overseas-caption",
    ):
        assert f'id="{element_id}"' in template, f"{element_id} 요소가 없음"
    assert "setHeatmapTab('domestic')" in template
    assert "setHeatmapTab('overseas')" in template


def test_heatmap_page_hosts_zoom_controls():
    template = _heatmap_page_template()

    # 줌은 스크롤 가능한 viewport 안에서 캔버스(sizer)를 키우는 방식이라 두 요소가 모두 필요하다.
    assert 'id="heatmap-page-viewport"' in template
    assert 'id="heatmap-page-sizer"' in template
    assert 'id="heatmap-page-zoom-level"' in template
    assert "zoomHeatmapPage(1)" in template
    assert "zoomHeatmapPage(-1)" in template
    assert "resetHeatmapPageZoom()" in template


def test_heatmap_page_reuses_home_render_script():
    """트리맵 계산/색상은 market_heatmap.js 한 곳에만 두고 페이지 스크립트는 배선만 한다."""
    template = _heatmap_page_template()
    page_script = _heatmap_page_js()

    assert template.index("/static/js/market_heatmap.js") < template.index("/static/js/heatmap_page.js"), (
        "공용 렌더 스크립트가 페이지 스크립트보다 먼저 로드돼야 함"
    )
    assert "_squarifyTreemap" not in page_script, "트리맵 계산이 페이지 스크립트에 복제됨"
    assert "HEATMAP_UP_COLORS" not in page_script, "색상 팔레트가 페이지 스크립트에 복제됨"
    assert "_domesticGroups" in page_script and "_overseasGroups" in page_script


def test_heatmap_zoom_scales_label_threshold():
    """줌은 타일만 키우는 게 아니라 라벨 생략 기준도 같이 완화해야 의미가 있다."""
    script = _heatmap_js()

    assert "HEATMAP_MIN_LABEL_WIDTH_PCT" in script
    assert re.search(r"box\.w \* zoom", script), "라벨 기준이 줌 배율을 반영하지 않음"
    assert re.search(r"box\.h \* zoom", script), "라벨 기준이 줌 배율을 반영하지 않음"


def test_home_cards_link_to_heatmap_page():
    template = _home_template()

    assert template.count('href="/heatmap"') >= 2, "홈의 두 히트맵 카드에서 전용 페이지로 갈 수 있어야 함"


def test_heatmap_follows_domestic_up_red_convention():
    script = _heatmap_js()

    up_block = script.split("HEATMAP_UP_COLORS")[1].split("]")[0]
    down_block = script.split("HEATMAP_DOWN_COLORS")[1].split("]")[0]
    # 상승은 적색(R>B), 하락은 청색(B>R) 계열이어야 한다.
    for hex_color in re.findall(r"#([0-9a-f]{6})", up_block):
        assert int(hex_color[0:2], 16) > int(hex_color[4:6], 16), f"#{hex_color} 는 상승 색으로 부적절"
    for hex_color in re.findall(r"#([0-9a-f]{6})", down_block):
        assert int(hex_color[4:6], 16) > int(hex_color[0:2], 16), f"#{hex_color} 는 하락 색으로 부적절"
