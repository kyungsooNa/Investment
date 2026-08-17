/* view/web/static/js/heatmap_page.js — 히트맵 전용 페이지(/heatmap)
 *
 * 트리맵 계산·색상·조회는 market_heatmap.js 를 그대로 재사용하고(먼저 로드돼야 한다),
 * 이 파일은 전용 페이지가 추가로 가지는 세 가지만 소유한다.
 *   1) 한국/미국 탭 전환 — 탭을 열 때 처음 한 번만 조회한다.
 *   2) 줌 — CSS transform 이 아니라 캔버스(sizer) 자체를 키우고 다시 그린다.
 *      transform 으로 확대하면 작은 타일의 생략된 라벨이 그대로 없기 때문에,
 *      배율을 렌더에 넘겨 라벨 기준을 완화해야 확대의 의미가 있다.
 *      버튼(중앙 고정)과 마우스 휠(커서 고정) 두 경로가 같은 배율 단계를 공유한다.
 *   3) 기간·시장 — 조회 조건. 기간은 색(등락률)의 기준 구간이라 서버가 ohlcv 기준종가로
 *      계산하고(면적=시가총액은 기간과 무관하게 최신 스냅샷 그대로), 시장(공통/코스피/코스닥)은
 *      담기는 종목 자체를 바꾼다 — 둘 다 재조회가 필요하다. 미국 탭은 기간 이력도 시장 구분도
 *      없어 국내 탭에서만 노출한다.
 *   4) 끌어서 이동 — 확대한 화면을 휴대폰처럼 마우스로 잡아끌어 옮긴다.
 *   5) 검색 — 재조회 없이 그려진 타일에서 종목을 짚는다(일치 강조 + 나머지 흐림 + 스크롤).
 */

// 홈 미리보기(200종목)보다 넓게 잡는다 — 작은 타일은 확대로 읽을 수 있다.
const HEATMAP_PAGE_DOMESTIC_LIMIT = 500;
const HEATMAP_PAGE_OVERSEAS_LIMIT = 500;
const HEATMAP_PAGE_ZOOM_STEPS = [1, 1.5, 2, 3, 4, 6, 8];
// 휠 한 칸(Chrome deltaMode=0 기준 deltaY 100) 이 배율 한 단계다. 트랙패드는 한 번 굴려도
// 작은 delta 를 여러 번 보내므로 누적해서 이 값을 넘을 때만 단계를 바꾼다.
const HEATMAP_PAGE_WHEEL_STEP_DELTA = 100;
// 서버(/api/heatmap/domestic?period=)가 받는 값과 같아야 한다.
const HEATMAP_PAGE_PERIODS = ['1d', '1w', '1m', '3m', '6m', 'ytd', '1y'];
const HEATMAP_PAGE_DEFAULT_PERIOD = '1d';
// 저장소가 해석하는 시장 값 — 'ALL'(공통) 은 필터 없음이다.
const HEATMAP_PAGE_MARKETS = ['ALL', 'KOSPI', 'KOSDAQ'];
const HEATMAP_PAGE_DEFAULT_MARKET = 'ALL';
// 자동 갱신 주기. 미국 스냅샷만 대상이다 — 국내는 장마감 후 하루 한 번 수집되는
// daily_prices 종가라 되물어도 같은 값이 온다. 서버 TTL(45초)보다 길게 잡아 매 주기가
// 실제로 새 스냅샷을 받게 한다.
const HEATMAP_PAGE_OVERSEAS_REFRESH_MS = 60 * 1000;

let _heatmapPageZoomIndex = 0;
let _heatmapPageWheelAccum = 0;
let _heatmapPagePeriod = HEATMAP_PAGE_DEFAULT_PERIOD;
let _heatmapPageMarket = HEATMAP_PAGE_DEFAULT_MARKET;
let _heatmapPageTab = 'domestic';
let _heatmapPageRefreshTimer = null;
let _heatmapPageRefreshing = false;

function _heatmapPageZoom() {
    return HEATMAP_PAGE_ZOOM_STEPS[_heatmapPageZoomIndex];
}

const HEATMAP_PAGE_SOURCES = {
    domestic: {
        targetId: 'heatmap-page-domestic',
        captionId: 'heatmap-page-domestic-caption',
        // 조회 조건(기간·시장)이 바뀌면 URL 도 바뀌므로 함수로 둔다(_loadHeatmap 이 호출 시점에 평가한다).
        url: () => `/api/heatmap/domestic?limit=${HEATMAP_PAGE_DOMESTIC_LIMIT}`
            + `&period=${_heatmapPagePeriod}&market=${_heatmapPageMarket}`,
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

// 자동 갱신 주기 표기 — 미국 탭은 스스로 갱신되지만 그 사실이 화면에 없으면 멈춘 화면과
// 구분되지 않는다. 문구를 상수에서 만들어 주기를 바꿔도 표기가 따라오게 한다.
function _renderHeatmapPageInterval() {
    const el = document.getElementById('heatmap-page-overseas-interval');
    if (!el) return;
    el.textContent = `${Math.round(HEATMAP_PAGE_OVERSEAS_REFRESH_MS / 1000)}초마다 자동 갱신`;
}

async function setHeatmapTab(market) {
    const source = HEATMAP_PAGE_SOURCES[market];
    if (!source) return;
    _heatmapPageTab = market;

    Object.keys(HEATMAP_PAGE_SOURCES).forEach(key => {
        const active = key === market;
        _heatmapPageShow(document.getElementById(HEATMAP_PAGE_SOURCES[key].targetId), active);
        _heatmapPageShow(document.getElementById(HEATMAP_PAGE_SOURCES[key].captionId), active);
        const tab = document.getElementById(`heatmap-page-tab-${key}`);
        if (tab) tab.classList.toggle('active', active);
    });

    // 미국 스냅샷(Yahoo)은 일간 등락률만 있어 기간 선택이 의미가 없고, 시장도 S&P 500 고정이다.
    _heatmapPageShow(document.getElementById('heatmap-page-period-wrap'), market === 'domestic');
    _heatmapPageShow(document.getElementById('heatmap-page-market-wrap'), market === 'domestic');
    // 주기 표기는 미국 탭에만 둔다 — 국내는 종가 응답이면 폴링을 건너뛰므로 같은 주기를
    // 약속하면 거짓이 된다.
    _heatmapPageShow(document.getElementById('heatmap-page-overseas-interval'), market === 'overseas');

    // 실패한 탭은 lastData 가 없으므로 다시 열 때 재시도된다.
    if (!source.lastData) await _loadHeatmap(source);
    // 탭을 바꾸면 일치 개수의 대상(보이는 패널)이 달라진다.
    _applyHeatmapPageSearch();
}

async function setHeatmapPeriod(period) {
    if (!HEATMAP_PAGE_PERIODS.includes(period) || period === _heatmapPagePeriod) return;
    _heatmapPagePeriod = period;

    // 색 기준이 바뀌었으므로 이전 응답은 못 쓴다. 배율은 렌더 때 다시 반영되어 유지된다.
    const source = HEATMAP_PAGE_SOURCES.domestic;
    source.lastData = null;
    await _loadHeatmap(source);
    _applyHeatmapPageSearch();
}

async function setHeatmapMarket(market) {
    if (!HEATMAP_PAGE_MARKETS.includes(market) || market === _heatmapPageMarket) return;
    _heatmapPageMarket = market;

    // 종목 구성 자체가 달라지므로 서버에서 다시 받아야 한다(면적 = 시가총액 상위 N).
    const source = HEATMAP_PAGE_SOURCES.domestic;
    source.lastData = null;
    await _loadHeatmap(source);
    _applyHeatmapPageSearch();
}

// 검색은 재조회가 아니라 이미 그려진 타일을 짚어 주는 기능이다. 일치하지 않는 종목을 지우면
// 남은 타일이 화면을 다시 채워 면적(시가총액)이 뜻을 잃으므로, 흐리게 두고 일치만 도드라지게 한다.
const HEATMAP_PAGE_SEARCHING_CLASS = 'heatmap-searching';
const HEATMAP_PAGE_HIT_CLASS = 'heatmap-tile-hit';

let _heatmapPageQuery = '';

function _heatmapPageMatches(tile, query) {
    const symbol = String(tile.dataset.symbol || '').toLowerCase();
    const name = String(tile.dataset.name || '').toLowerCase();
    return symbol.includes(query) || name.includes(query);
}

// 확대·재조회는 타일 DOM 을 통째로 새로 만든다 — 그릴 때마다 현재 검색어를 다시 입힌다.
function _applyHeatmapPageSearch() {
    const query = _heatmapPageQuery;
    let hits = 0;

    Object.values(HEATMAP_PAGE_SOURCES).forEach(source => {
        const div = document.getElementById(source.targetId);
        if (!div) return;
        div.classList.toggle(HEATMAP_PAGE_SEARCHING_CLASS, Boolean(query));
        div.querySelectorAll('.heatmap-tile').forEach(tile => {
            const hit = Boolean(query) && _heatmapPageMatches(tile, query);
            tile.classList.toggle(HEATMAP_PAGE_HIT_CLASS, hit);
            if (hit && div.style.display !== 'none') hits += 1;
        });
    });

    const countEl = document.getElementById('heatmap-page-search-count');
    if (countEl) {
        if (!query) countEl.textContent = '';
        else countEl.textContent = hits ? `${hits}개 일치` : '일치하는 종목이 없습니다';
    }
    return hits;
}

// 찾은 종목이 확대된 화면 밖에 있으면 강조해도 보이지 않는다 — 첫(가장 큰) 일치로 스크롤한다.
function _scrollHeatmapPageToFirstHit() {
    const viewport = document.getElementById('heatmap-page-viewport');
    if (!viewport || typeof viewport.getBoundingClientRect !== 'function') return;

    const visible = Object.values(HEATMAP_PAGE_SOURCES)
        .map(source => document.getElementById(source.targetId))
        .find(div => div && div.style.display !== 'none');
    const tile = visible && visible.querySelector(`.${HEATMAP_PAGE_HIT_CLASS}`);
    if (!tile) return;

    const tileRect = tile.getBoundingClientRect();
    const viewRect = viewport.getBoundingClientRect();
    if (!tileRect.width && !tileRect.height) return;  // 레이아웃이 없으면 스크롤할 곳도 없다

    viewport.scrollLeft = Math.max(
        0,
        viewport.scrollLeft + (tileRect.left - viewRect.left) - (viewport.clientWidth - tileRect.width) / 2,
    );
    viewport.scrollTop = Math.max(
        0,
        viewport.scrollTop + (tileRect.top - viewRect.top) - (viewport.clientHeight - tileRect.height) / 2,
    );
}

function searchHeatmapPage(query) {
    _heatmapPageQuery = String(query || '').trim().toLowerCase();
    if (_applyHeatmapPageSearch()) _scrollHeatmapPageToFirstHit();
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
    _applyHeatmapPageSearch();
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

// 스크롤바(오른쪽·아래 여백) 위인지. clientWidth/Height 는 스크롤바를 뺀 안쪽 크기라
// 그 밖의 좁은 띠가 스크롤바 영역이다. 스크롤바가 없으면 두께가 0 이라 판정되지 않는다.
function _heatmapPageOnScrollbar(viewport, clientX, clientY) {
    if (!viewport || typeof viewport.getBoundingClientRect !== 'function') return false;
    const rect = viewport.getBoundingClientRect();
    const onVertical = viewport.offsetWidth - viewport.clientWidth > 0
        && clientX - rect.left >= viewport.clientWidth;
    const onHorizontal = viewport.offsetHeight - viewport.clientHeight > 0
        && clientY - rect.top >= viewport.clientHeight;
    return onVertical || onHorizontal;
}

// 스크롤바 위에서는 배율이 아니라 이동이 자연스럽다. Ctrl 을 누르면 좌우로 움직인다
// (가로 스크롤바까지 커서를 내려가지 않고도 옆으로 밀 수 있게).
function _heatmapPageWheelPan(viewport, delta, horizontal) {
    if (horizontal) viewport.scrollLeft = Math.max(0, viewport.scrollLeft + delta);
    else viewport.scrollTop = Math.max(0, viewport.scrollTop + delta);
}

function _onHeatmapPageWheel(event) {
    // 히트맵 위에서 휠은 배율 조작이다 — 페이지까지 같이 스크롤되면 보던 지점이 튄다.
    // Ctrl+휠 의 브라우저 페이지 확대도 여기서 막는다.
    event.preventDefault();

    const delta = _heatmapPageWheelDelta(event);
    if (!delta) return;

    const viewport = event.currentTarget;
    if (_heatmapPageOnScrollbar(viewport, event.clientX, event.clientY)) {
        // 이동은 배율 단계와 무관하므로 휠 누적에 섞지 않는다.
        _heatmapPageWheelPan(viewport, delta, event.ctrlKey);
        return;
    }

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

// 휴대폰에서 화면을 밀듯 히트맵을 잡아끌어 옮긴다 — 확대해 놓으면 스크롤바를 찾아가는 것보다 빠르다.
// (터치 기기는 브라우저 기본 스크롤이 이미 같은 동작이라 마우스만 다룬다.)
const HEATMAP_PAGE_PANNING_CLASS = 'heatmap-panning';

let _heatmapPagePan = null;

function _onHeatmapPagePanStart(event) {
    if (event.button !== 0) return;
    const viewport = event.currentTarget;
    // 스크롤바를 잡은 드래그는 브라우저 기본 동작이 이미 정확하다 — 손대면 두 배로 움직인다.
    if (_heatmapPageOnScrollbar(viewport, event.clientX, event.clientY)) return;

    _heatmapPagePan = {
        viewport,
        startX: event.clientX,
        startY: event.clientY,
        scrollLeft: viewport.scrollLeft,
        scrollTop: viewport.scrollTop,
    };
    viewport.classList.add(HEATMAP_PAGE_PANNING_CLASS);
    // 끄는 동안 타일 라벨이 텍스트로 선택되는 것을 막는다.
    event.preventDefault();
}

// 이동량은 직전 위치가 아니라 시작점 기준이다 — 프레임을 누적하면 오차가 쌓인다.
function _onHeatmapPagePanMove(event) {
    const pan = _heatmapPagePan;
    if (!pan) return;
    pan.viewport.scrollLeft = Math.max(0, pan.scrollLeft - (event.clientX - pan.startX));
    pan.viewport.scrollTop = Math.max(0, pan.scrollTop - (event.clientY - pan.startY));
}

function _onHeatmapPagePanEnd() {
    if (!_heatmapPagePan) return;
    _heatmapPagePan.viewport.classList.remove(HEATMAP_PAGE_PANNING_CLASS);
    _heatmapPagePan = null;
}

// 이동·종료는 document 에 건다 — 커서가 히트맵 밖으로 나가도 끌기가 이어지고, 밖에서 손을 떼도 풀린다.
let _heatmapPagePanDocumentBound = false;

function _bindHeatmapPageDragPan(viewport) {
    if (viewport.dataset.dragPanBound !== '1') {
        viewport.addEventListener('mousedown', _onHeatmapPagePanStart);
        viewport.dataset.dragPanBound = '1';
    }
    if (_heatmapPagePanDocumentBound) return;
    document.addEventListener('mousemove', _onHeatmapPagePanMove);
    document.addEventListener('mouseup', _onHeatmapPagePanEnd);
    _heatmapPagePanDocumentBound = true;
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

// 자동 갱신 — 보고 있는 탭만 조용히(로딩 문구 없이) 갈아끼운다. 매 주기마다
// '조회 중...' 을 띄우면 화면이 1분마다 깜빡이고 배율·검색 상태가 눈에 띄게 흔들린다.
//
// 국내는 서버가 장중에만 실시간 스냅샷을 주고 장외에는 daily_prices 종가를 준다.
// 종가 응답(realtime=false)에는 되물어도 같은 값이 오므로 폴링하지 않는다.
function _heatmapPageWorthRefreshing(market) {
    if (market === 'overseas') return true;
    return Boolean(HEATMAP_PAGE_SOURCES.domestic.lastData?.realtime);
}

async function _refreshHeatmapPageTick() {
    // pjax 로 다른 화면에 가 있으면 스스로 멈춘다 (타이머는 문서를 떠나도 계속 돈다).
    if (!document.getElementById('heatmap-page-viewport')) {
        _stopHeatmapPageAutoRefresh();
        return;
    }
    if (document.hidden || !_heatmapPageWorthRefreshing(_heatmapPageTab)) return;

    await _loadHeatmap(HEATMAP_PAGE_SOURCES[_heatmapPageTab], { showLoading: false });
    _applyHeatmapPageSearch();
}

// 수동 새로고침 — 자동 갱신과 두 가지가 다르다.
//  1) 종가(realtime=false) 상태에서도 조회한다. 자동은 값이 안 바뀌어 건너뛰지만
//     버튼을 누른 건 '지금 보고 싶다'는 사용자 의사다.
//  2) 방금 받아왔으므로 다음 자동 갱신까지 한 주기를 새로 센다.
async function refreshHeatmapPage() {
    if (_heatmapPageRefreshing) return;
    const source = HEATMAP_PAGE_SOURCES[_heatmapPageTab];
    if (!source) return;

    _heatmapPageRefreshing = true;
    const button = document.getElementById('heatmap-page-refresh');
    if (button) button.disabled = true;
    try {
        await _loadHeatmap(source, { showLoading: false });
        _applyHeatmapPageSearch();
        _startHeatmapPageAutoRefresh();
    } finally {
        _heatmapPageRefreshing = false;
        if (button) button.disabled = false;
    }
}

function _stopHeatmapPageAutoRefresh() {
    if (_heatmapPageRefreshTimer === null) return;
    clearInterval(_heatmapPageRefreshTimer);
    _heatmapPageRefreshTimer = null;
}

function _startHeatmapPageAutoRefresh() {
    _stopHeatmapPageAutoRefresh();  // pjax 재진입 시 타이머가 겹쳐 걸리지 않게 한다.
    _heatmapPageRefreshTimer = setInterval(_refreshHeatmapPageTick, HEATMAP_PAGE_OVERSEAS_REFRESH_MS);
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
    _heatmapPageMarket = HEATMAP_PAGE_DEFAULT_MARKET;
    const marketSelect = document.getElementById('heatmap-page-market');
    if (marketSelect) marketSelect.value = HEATMAP_PAGE_DEFAULT_MARKET;
    _heatmapPageQuery = '';
    const searchInput = document.getElementById('heatmap-page-search');
    if (searchInput) searchInput.value = '';
    _heatmapPagePan = null;
    _applyHeatmapPageZoom();
    _renderHeatmapPageInterval();
    _bindHeatmapPageWheelZoom(viewport);
    _bindHeatmapPageDragPan(viewport);

    // 미국장이 꺼진 run 에서는 API 가 400 을 내므로 탭 자체를 감춘다.
    if (!await _heatmapOverseasEnabled()) {
        _heatmapPageShow(document.getElementById('heatmap-page-tab-overseas'), false);
    }

    await setHeatmapTab('domestic');
    _startHeatmapPageAutoRefresh();
}

document.addEventListener('DOMContentLoaded', () => {
    if (window.location.pathname !== '/heatmap') return;
    void initHeatmapPage();
});

document.addEventListener('pjax:ready', (e) => {
    if (e.detail?.path !== '/heatmap') return;
    void initHeatmapPage();
});
