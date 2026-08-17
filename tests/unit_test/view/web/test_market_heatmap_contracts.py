"""히트맵 전용 페이지(/heatmap)의 템플릿-스크립트 배선을 잠그는 정적 계약 테스트."""

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


def test_home_does_not_duplicate_heatmap_panels():
    """히트맵은 전용 페이지가 소유한다 — 홈은 조회도 폴링도 하지 않는다."""
    template = _home_template()

    for element_id in ("market-heatmap-card", "market-heatmap", "domestic-heatmap-card", "domestic-heatmap"):
        assert f'id="{element_id}"' not in template, f"{element_id} 가 홈에 남아 중복 조회를 유발함"
    assert "/static/js/market_heatmap.js" not in template
    assert "/static/css/heatmap.css" not in template


def test_home_menu_links_to_heatmap_page():
    template = _home_template()

    assert 'href="/heatmap"' in template, "홈에서 전용 페이지로 갈 수 있어야 함"


def test_domestic_heatmap_shows_whether_it_is_live_or_a_close():
    script = _heatmap_js()

    # 장중에는 실시간, 장외에는 장마감 종가다 — 어느 쪽인지 화면이 밝힌다.
    assert "/api/heatmap/domestic" in _heatmap_page_js()
    assert "기준일" in script and "종가" in script
    assert "실시간" in script


def test_domestic_heatmap_groups_by_industry_when_available():
    """업종 분류가 채워지면 섹터 블록, 수집 전이면 단일 그리드."""
    script = _heatmap_js()

    assert "_groupBySector" in script
    # 분류 수집 전에는 어느 종목에도 sector 가 없어 예전처럼 단일 그룹으로 떨어져야 한다.
    assert "sector: null" in script


def test_heatmap_component_styles_live_in_a_stylesheet():
    """타일/섹터/범례 스타일은 템플릿 인라인이 아니라 스타일시트가 소유한다."""
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
        assert selector in css, f"{selector} 스타일이 스타일시트에 없음"

    template = _heatmap_page_template()
    assert "/static/css/heatmap.css" in template, "히트맵 스타일시트가 연결되지 않음"
    assert ".heatmap-tile {" not in template, "컴포넌트 CSS 가 템플릿에 복제되어 있음"


def test_heatmap_reuses_overseas_market_cap_snapshot():
    # 별도 수집 없이 미국 시가총액 화면과 같은 스냅샷(TTL 캐시 공유)을 쓴다.
    page_script = _heatmap_page_js()
    assert "/api/overseas/top-market-cap" in page_script
    assert "HEATMAP_PAGE_OVERSEAS_LIMIT = 500" in page_script

    # 미국장이 꺼진 run 에서는 조회 대신 탭을 감춘다.
    script = _heatmap_js()
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
    template = _heatmap_page_template()
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


def test_heatmap_page_hosts_period_control():
    """기간은 색(등락률)의 기준 구간이라 서버 재조회가 필요하다 — 화면·스크립트·API 값이 맞아야 한다."""
    template = _heatmap_page_template()
    page_script = _heatmap_page_js()

    assert 'id="heatmap-page-period"' in template
    assert 'id="heatmap-page-period-wrap"' in template, "미국 탭에서 감출 대상 컨테이너가 필요함"
    assert "setHeatmapPeriod(this.value)" in template

    for period in ("1d", "1w", "1m", "3m", "6m", "ytd", "1y"):
        assert f'value="{period}"' in template, f"{period} 기간 선택지가 없음"
        assert f"'{period}'" in page_script, f"{period} 가 스크립트 허용 목록에 없음"
    assert "period=${_heatmapPagePeriod}" in page_script, "조회 URL 에 기간이 실리지 않음"


def test_heatmap_page_hosts_market_control():
    """시장(공통/코스피/코스닥)은 조회 조건이라 화면·스크립트·API 값이 맞아야 한다."""
    template = _heatmap_page_template()
    page_script = _heatmap_page_js()

    assert 'id="heatmap-page-market"' in template
    assert 'id="heatmap-page-market-wrap"' in template, "미국 탭에서 감출 대상 컨테이너가 필요함"
    assert "setHeatmapMarket(this.value)" in template

    # 저장소가 "ALL"/"KOSPI"/"KOSDAQ" 를 그대로 해석한다(ALL=필터 없음).
    for market in ("ALL", "KOSPI", "KOSDAQ"):
        assert f'value="{market}"' in template, f"{market} 선택지가 없음"
        assert f"'{market}'" in page_script, f"{market} 가 스크립트 허용 목록에 없음"
    assert "market=${_heatmapPageMarket}" in page_script, "조회 URL 에 시장이 실리지 않음"


def test_heatmap_page_hosts_search_control():
    """검색은 재조회가 아니라 그려진 타일을 짚는 기능이라, 이름 매칭 근거(data-name)와 강조 CSS 가 있어야 한다."""
    template = _heatmap_page_template()
    page_script = _heatmap_page_js()
    css = _heatmap_css()

    assert 'id="heatmap-page-search"' in template
    assert "searchHeatmapPage(this.value)" in template
    assert 'id="heatmap-page-search-count"' in template, "일치 개수를 알릴 자리가 필요함"

    assert "data-name=" in _heatmap_js(), "종목명 검색 근거가 타일에 실려야 함"
    assert "dataset.name" in page_script and "dataset.symbol" in page_script, "이름·코드 둘 다로 찾아야 함"
    assert ".heatmap-tile-hit" in css and ".heatmap-searching" in css, "강조/흐림 스타일이 스타일시트에 없음"


def test_heatmap_page_supports_drag_panning():
    """확대한 화면은 마우스로 끌어 옮길 수 있어야 한다 — 배선(JS)과 커서 표시(CSS)가 함께 있어야 한다."""
    template = _heatmap_page_template()
    page_script = _heatmap_page_js()

    assert "'mousedown'" in page_script and "'mousemove'" in page_script and "'mouseup'" in page_script
    assert "heatmap-panning" in page_script
    assert "cursor: grab" in template, "끌 수 있다는 표시가 없음"
    assert ".heatmap-page-viewport.heatmap-panning" in template, "끄는 중 커서 상태 스타일이 없음"


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


def test_render_script_has_no_home_wiring():
    """홈 미리보기를 걷어낸 뒤 남은 배선은 전용 페이지가 전부 소유한다."""
    script = _heatmap_js()

    for symbol in ("initMarketHeatmap", "initDomesticHeatmap", "HEATMAP_REFRESH_MS", "setInterval"):
        assert symbol not in script, f"{symbol} 가 남아 홈 전용 배선이 잔존함"


def test_heatmap_follows_domestic_up_red_convention():
    script = _heatmap_js()

    up_block = script.split("HEATMAP_UP_COLORS")[1].split("]")[0]
    down_block = script.split("HEATMAP_DOWN_COLORS")[1].split("]")[0]
    # 상승은 적색(R>B), 하락은 청색(B>R) 계열이어야 한다.
    for hex_color in re.findall(r"#([0-9a-f]{6})", up_block):
        assert int(hex_color[0:2], 16) > int(hex_color[4:6], 16), f"#{hex_color} 는 상승 색으로 부적절"
    for hex_color in re.findall(r"#([0-9a-f]{6})", down_block):
        assert int(hex_color[4:6], 16) > int(hex_color[0:2], 16), f"#{hex_color} 는 하락 색으로 부적절"


def test_heatmap_page_hosts_a_manual_refresh_button():
    """자동 갱신(60초)과 별개로 즉시 다시 조회할 수단이 있어야 한다."""
    template = _heatmap_page_template()
    page_script = _heatmap_page_js()

    assert 'id="heatmap-page-refresh"' in template
    assert "refreshHeatmapPage()" in template
    assert "async function refreshHeatmapPage" in page_script
    # 연타로 중복 요청이 나가지 않도록 조회 중에는 잠근다.
    assert "button.disabled = true" in page_script
    assert ".heatmap-refresh:disabled" in template, "잠긴 상태가 보이지 않으면 연타하게 된다"


def test_heatmap_page_shows_us_auto_refresh_interval():
    """미국 탭은 자동 갱신되는데 주기가 화면에 없으면 정지한 화면과 구분되지 않는다."""
    template = _heatmap_page_template()
    page_script = _heatmap_page_js()

    assert 'id="heatmap-page-overseas-interval"' in template
    # 주기 문구를 하드코딩하면 상수를 바꿀 때 표기만 남아 거짓이 된다.
    assert "HEATMAP_PAGE_OVERSEAS_REFRESH_MS" in page_script
    assert "heatmap-page-overseas-interval" in page_script
