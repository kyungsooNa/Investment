/* view/web/static/js/heatmap_page.js — 히트맵 전용 페이지(/heatmap)
 *
 * 트리맵 계산·색상·조회는 market_heatmap.js 를 그대로 재사용하고(먼저 로드돼야 한다),
 * 이 파일은 전용 페이지가 추가로 가지는 두 가지만 소유한다.
 *   1) 한국/미국 탭 전환 — 탭을 열 때 처음 한 번만 조회한다.
 *   2) 줌 — CSS transform 이 아니라 캔버스(sizer) 자체를 키우고 다시 그린다.
 *      transform 으로 확대하면 작은 타일의 생략된 라벨이 그대로 없기 때문에,
 *      배율을 렌더에 넘겨 라벨 기준을 완화해야 확대의 의미가 있다.
 */

// 홈 미리보기(200종목)보다 넓게 잡는다 — 작은 타일은 확대로 읽을 수 있다.
const HEATMAP_PAGE_DOMESTIC_LIMIT = 500;
const HEATMAP_PAGE_OVERSEAS_LIMIT = 500;
const HEATMAP_PAGE_ZOOM_STEPS = [1, 1.5, 2, 3, 4, 6, 8];

let _heatmapPageZoomIndex = 0;

function _heatmapPageZoom() {
    return HEATMAP_PAGE_ZOOM_STEPS[_heatmapPageZoomIndex];
}

const HEATMAP_PAGE_SOURCES = {
    domestic: {
        targetId: 'heatmap-page-domestic',
        captionId: 'heatmap-page-domestic-caption',
        url: `/api/heatmap/domestic?limit=${HEATMAP_PAGE_DOMESTIC_LIMIT}`,
        loadingText: '국내 히트맵 조회 중...',
        toGroups: _domesticGroups,
        caption: _domesticCaption,
        zoom: _heatmapPageZoom,
        sequence: 0,
    },
    overseas: {
        targetId: 'heatmap-page-overseas',
        captionId: 'heatmap-page-overseas-caption',
        url: `/api/overseas/top-market-cap?limit=${HEATMAP_PAGE_OVERSEAS_LIMIT}`,
        loadingText: 'S&P 500 히트맵 조회 중...',
        toGroups: _overseasGroups,
        caption: _overseasCaption,
        zoom: _heatmapPageZoom,
        sequence: 0,
    },
};

function _heatmapPageShow(el, visible) {
    if (el) el.style.display = visible ? '' : 'none';
}

async function setHeatmapTab(market) {
    const source = HEATMAP_PAGE_SOURCES[market];
    if (!source) return;

    Object.keys(HEATMAP_PAGE_SOURCES).forEach(key => {
        const active = key === market;
        _heatmapPageShow(document.getElementById(HEATMAP_PAGE_SOURCES[key].targetId), active);
        _heatmapPageShow(document.getElementById(HEATMAP_PAGE_SOURCES[key].captionId), active);
        const tab = document.getElementById(`heatmap-page-tab-${key}`);
        if (tab) tab.classList.toggle('active', active);
    });

    // 실패한 탭은 lastData 가 없으므로 다시 열 때 재시도된다.
    if (!source.lastData) await _loadHeatmap(source);
}

// 확대/축소 후에도 보고 있던 지점이 화면 가운데에 남도록 스크롤 비율을 유지한다.
function _heatmapPageCenterRatio(viewport) {
    if (!viewport || !viewport.scrollWidth || !viewport.scrollHeight) return null;
    return {
        x: (viewport.scrollLeft + viewport.clientWidth / 2) / viewport.scrollWidth,
        y: (viewport.scrollTop + viewport.clientHeight / 2) / viewport.scrollHeight,
    };
}

function _restoreHeatmapPageCenter(viewport, anchor) {
    if (!viewport || !anchor) return;
    viewport.scrollLeft = Math.max(0, anchor.x * viewport.scrollWidth - viewport.clientWidth / 2);
    viewport.scrollTop = Math.max(0, anchor.y * viewport.scrollHeight - viewport.clientHeight / 2);
}

function _applyHeatmapPageZoom() {
    const zoom = _heatmapPageZoom();
    const sizer = document.getElementById('heatmap-page-sizer');
    if (sizer) {
        sizer.style.width = `${zoom * 100}%`;
        sizer.style.height = `${zoom * 100}%`;
    }
    const level = document.getElementById('heatmap-page-zoom-level');
    if (level) level.textContent = `${Math.round(zoom * 100)}%`;

    // 라벨 생략 기준이 배율에 걸려 있어, 이미 받아둔 응답으로 다시 그린다(재조회 없음).
    Object.values(HEATMAP_PAGE_SOURCES).forEach(source => {
        const div = document.getElementById(source.targetId);
        if (div && source.lastData) _renderHeatmap(div, source, source.lastData);
    });
}

function zoomHeatmapPage(step) {
    const next = Math.min(
        HEATMAP_PAGE_ZOOM_STEPS.length - 1,
        Math.max(0, _heatmapPageZoomIndex + step),
    );
    if (next === _heatmapPageZoomIndex) return;

    const viewport = document.getElementById('heatmap-page-viewport');
    const anchor = _heatmapPageCenterRatio(viewport);
    _heatmapPageZoomIndex = next;
    _applyHeatmapPageZoom();
    _restoreHeatmapPageCenter(viewport, anchor);
}

function resetHeatmapPageZoom() {
    _heatmapPageZoomIndex = 0;
    _applyHeatmapPageZoom();
    const viewport = document.getElementById('heatmap-page-viewport');
    if (viewport) {
        viewport.scrollLeft = 0;
        viewport.scrollTop = 0;
    }
}

async function initHeatmapPage() {
    if (!document.getElementById('heatmap-page-viewport')) return;

    // pjax 재진입 시 이전 화면의 응답/배율이 남지 않도록 초기화한다.
    Object.values(HEATMAP_PAGE_SOURCES).forEach(source => {
        source.lastData = null;
        source.sequence = 0;
    });
    _heatmapPageZoomIndex = 0;
    _applyHeatmapPageZoom();

    // 미국장이 꺼진 run 에서는 API 가 400 을 내므로 탭 자체를 감춘다.
    if (!await _heatmapOverseasEnabled()) {
        _heatmapPageShow(document.getElementById('heatmap-page-tab-overseas'), false);
    }

    await setHeatmapTab('domestic');
}

document.addEventListener('DOMContentLoaded', () => {
    if (window.location.pathname !== '/heatmap') return;
    void initHeatmapPage();
});

document.addEventListener('pjax:ready', (e) => {
    if (e.detail?.path !== '/heatmap') return;
    void initHeatmapPage();
});
