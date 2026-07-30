"""홈 화면 S&P500 히트맵의 템플릿-스크립트 배선을 잠그는 정적 계약 테스트."""

import re
from pathlib import Path


def _heatmap_js() -> str:
    return Path("view/web/static/js/market_heatmap.js").read_text(encoding="utf-8")


def _home_template() -> str:
    return Path("view/web/templates/index.html").read_text(encoding="utf-8")


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


def test_heatmap_styles_are_defined_in_home_template():
    template = _home_template()

    for selector in (
        "#market-heatmap, #domestic-heatmap {",
        ".heatmap-canvas",
        ".heatmap-sector",
        ".heatmap-sector-title",
        ".heatmap-sector-body",
        ".heatmap-tile",
        ".heatmap-legend",
    ):
        assert selector in template, f"{selector} 스타일이 없음"


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
    template = _home_template()

    palette = set(re.findall(r"color: '(#[0-9a-f]{6})'", script))
    assert len(palette) == 8, f"상승/하락 4단계 색이 정의되어야 함 (실제 {len(palette)}종)"
    for color in palette:
        assert color in template, f"범례에 {color} 가 빠져 스크립트와 어긋남"


def test_heatmap_follows_domestic_up_red_convention():
    script = _heatmap_js()

    up_block = script.split("HEATMAP_UP_COLORS")[1].split("]")[0]
    down_block = script.split("HEATMAP_DOWN_COLORS")[1].split("]")[0]
    # 상승은 적색(R>B), 하락은 청색(B>R) 계열이어야 한다.
    for hex_color in re.findall(r"#([0-9a-f]{6})", up_block):
        assert int(hex_color[0:2], 16) > int(hex_color[4:6], 16), f"#{hex_color} 는 상승 색으로 부적절"
    for hex_color in re.findall(r"#([0-9a-f]{6})", down_block):
        assert int(hex_color[4:6], 16) > int(hex_color[0:2], 16), f"#{hex_color} 는 하락 색으로 부적절"
