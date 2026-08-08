/* view/web/static/js/heatmap_page.js — 히트맵 전용 페이지(/heatmap)
 *
 * 트리맵 계산·색상·조회는 market_heatmap.js 를 그대로 재사용하고(먼저 로드돼야 한다),
 * 이 파일은 전용 페이지가 추가로 가지는 세 가지만 소유한다.
 *   1) 한국/미국 탭 전환 — 탭을 열 때 처음 한 번만 조회한다.
 *   2) 줌 — CSS transform 이 아니라 캔버스(sizer) 자체를 키우고 다시 그린다.
 *      transform 으로 확대하면 작은 타일의 생략된 라벨이 그대로 없기 때문에,
 *      배율을 렌더에 넘겨 라벨 기준을 완화해야 확대의 의미가 있다.
 *      버튼(중앙 고정)과 마우스 휠(커서 고정) 두 경로가 같은 배율 단계를 공유한다.
 *   3) 기간 — 색(등락률)의 기준 구간. 서버가 ohlcv 기준종가로 계산하므로 재조회가 필요하다
 *      (면적=시가총액은 기간과 무관하게 최신 스냅샷 그대로). 미국 스냅샷에는 기간 이력이
 *      없어 국내 탭에서만 노출한다.
 */

// 홈 미리보기(200종목)보다 넓게 잡는다 — 작은 타일은 확대로 읽을 수 있다.
const HEATMAP_PAGE_DOMESTIC_LIMIT = 500;
const HEATMAP_PAGE_OVERSEAS_LIMIT = 500;
const HEATMAP_PAGE_ZOOM_STEPS = [1, 1.5, 2, 3, 4, 6, 8];
// 휠 한 칸(Chrome deltaMode=0 기준 deltaY 100) 이 배율 한 단계다. 트랙패드는 한 번 굴려도
// 작은 delta 를 여러 번 보내므로 누적해서 이 값을 넘을 때만 단계를 바꾼다.
const HEATMAP_PAGE_WHEEL_STEP_DELTA = 100;
// 서버(/api/heatmap/domestic?period=)가 받는 값과 같아야 한다.
const HEATMAP_PAGE_PERIODS = ['1d', '1w', '1m', '3m', '6m', '1y'];
const HEATMAP_PAGE_DEFAULT_PERIOD = '1d';

let _heatmapPageZoomIndex = 0;
let _heatmapPageWheelAccum = 0;
let _heatmapPagePeriod = HEATMAP_PAGE_DEFAULT_PERIOD;

function _heatmapPageZoom() {
    return HEATMAP_PAGE_ZOOM_STEPS[_heatmapPageZoomIndex];
}

const HEATMAP_PAGE_SOURCES = {
    domestic: {
        targetId: 'heatmap-page-domestic',
        captionId: 'heatmap-page-domestic-caption',
        // 기간이 바뀌면 URL 도 바뀌므로 함수로 둔다(_loadHeatmap 이 호출 시점에 평가한다).
        url: () => `/api/heatmap/domestic?limit=${HEATMAP_PAGE_DOMESTIC_LIMIT}&period=${_heatmapPagePeriod}`,
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

    // 미국 스냅샷(Yahoo)은 일간 등락률만 있어 기간 선택이 의미가 없다.
    _heatmapPageShow(document.getElementById('heatmap-page-period-wrap'), market === 'domestic');

    // 실패한 탭은 lastData 가 없으므로 다시 열 때 재시도된다.
    if (!source.lastData) await _loadHeatmap(source);
}

async function setHeatmapPeriod(period) {
    if (!HEATMAP_PAGE_PERIODS.includes(period) || period === _heatmapPagePeriod) return;
    _heatmapPagePeriod = period;

    // 색 기준이 바뀌었으므로 이전 응답은 못 쓴다. 배율은 렌더 때 다시 반영되어 유지된다.
    const source = HEATMAP_PAGE_SOURCES.domestic;
    source.lastData = null;
    await _loadHeatmap(source);
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

function _heatmapPageNextZoomIndex(step) {
    return Math.min(
        HEATMAP_PAGE_ZOOM_STEPS.length - 1,
        Math.max(0, _heatmapPageZoomIndex + step),
    );
}

function zoomHeatmapPage(step) {
    const next = _heatmapPageNextZoomIndex(step);
    if (next === _heatmapPageZoomIndex) return;

    const viewport = document.getElementById('heatmap-page-viewport');
    const anchor = _heatmapPageCenterRatio(viewport);
    _heatmapPageZoomIndex = next;
    _applyHeatmapPageZoom();
    _restoreHeatmapPageCenter(viewport, anchor);
}

// 휠 줌은 커서 아래 지점을 고정한다 — 중앙 기준으로 되돌리면 방금 겨눈 타일이 화면에서 벗어난다.
function _heatmapPagePointRatio(viewport, clientX, clientY) {
    if (!viewport || !viewport.scrollWidth || !viewport.scrollHeight) return null;
    const rect = viewport.getBoundingClientRect();
    const offsetX = clientX - rect.left;
    const offsetY = clientY - rect.top;
    return {
        x: (viewport.scrollLeft + offsetX) / viewport.scrollWidth,
        y: (viewport.scrollTop + offsetY) / viewport.scrollHeight,
        offsetX,
        offsetY,
    };
}

function _restoreHeatmapPagePoint(viewport, anchor) {
    if (!viewport || !anchor) return;
    viewport.scrollLeft = Math.max(0, anchor.x * viewport.scrollWidth - anchor.offsetX);
    viewport.scrollTop = Math.max(0, anchor.y * viewport.scrollHeight - anchor.offsetY);
}

function zoomHeatmapPageAt(step, clientX, clientY) {
    const next = _heatmapPageNextZoomIndex(step);
    if (next === _heatmapPageZoomIndex) return;

    const viewport = document.getElementById('heatmap-page-viewport');
    const anchor = _heatmapPagePointRatio(viewport, clientX, clientY);
    _heatmapPageZoomIndex = next;
    _applyHeatmapPageZoom();
    _restoreHeatmapPagePoint(viewport, anchor);
}

// 브라우저별 스크롤 단위(0=픽셀, 1=줄, 2=페이지)를 픽셀 기준으로 맞춘다.
function _heatmapPageWheelDelta(event) {
    if (event.deltaMode === 1) return event.deltaY * 40;
    if (event.deltaMode === 2) return event.deltaY * HEATMAP_PAGE_WHEEL_STEP_DELTA;
    return event.deltaY;
}

function _onHeatmapPageWheel(event) {
    // 히트맵 위에서 휠은 배율 조작이다 — 페이지까지 같이 스크롤되면 보던 지점이 튄다.
    event.preventDefault();

    const delta = _heatmapPageWheelDelta(event);
    if (!delta) return;
    // 방향을 바꾸면 반대 방향 누적은 버린다(직전 잔량 때문에 한 칸이 씹히는 것을 막는다).
    if ((delta > 0) !== (_heatmapPageWheelAccum > 0)) _heatmapPageWheelAccum = 0;
    _heatmapPageWheelAccum += delta;

    const notches = Math.trunc(_heatmapPageWheelAccum / HEATMAP_PAGE_WHEEL_STEP_DELTA);
    if (!notches) return;
    _heatmapPageWheelAccum -= notches * HEATMAP_PAGE_WHEEL_STEP_DELTA;
    zoomHeatmapPageAt(-notches, event.clientX, event.clientY);  // 휠 업(delta < 0) = 확대
}

// pjax 재진입 시 viewport 는 새 노드지만, 같은 노드에 두 번 걸리는 것도 막는다.
function _bindHeatmapPageWheelZoom(viewport) {
    if (viewport.dataset.wheelZoomBound === '1') return;
    viewport.addEventListener('wheel', _onHeatmapPageWheel, { passive: false });
    viewport.dataset.wheelZoomBound = '1';
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
    const viewport = document.getElementById('heatmap-page-viewport');
    if (!viewport) return;

    // pjax 재진입 시 이전 화면의 응답/배율이 남지 않도록 초기화한다.
    Object.values(HEATMAP_PAGE_SOURCES).forEach(source => {
        source.lastData = null;
        source.sequence = 0;
    });
    _heatmapPageZoomIndex = 0;
    _heatmapPageWheelAccum = 0;
    _heatmapPagePeriod = HEATMAP_PAGE_DEFAULT_PERIOD;
    const periodSelect = document.getElementById('heatmap-page-period');
    if (periodSelect) periodSelect.value = HEATMAP_PAGE_DEFAULT_PERIOD;
    _applyHeatmapPageZoom();
    _bindHeatmapPageWheelZoom(viewport);

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
