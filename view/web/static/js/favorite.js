/* view/web/static/js/favorite.js — 관심종목 페이지 동적 이벤트 관리 */

let _favoriteData = [];
let _favSortState = { key: null, dir: 'desc' };

// 자동 갱신 주기. 국내 현재가는 웹소켓 틱이 채운 메모리 캐시에서 나오므로 되묻는 비용이
// 낮다 — 대부분 외부 호출 없이 끝난다.
const FAVORITE_REFRESH_MS = 30 * 1000;

let _favoriteRefreshTimer = null;

/* ── 종목명 자동완성 (autocomplete.js 모듈 사용) ── */
StockAutocomplete({
    inputId: 'fav-search-input',
    listId: 'fav-autocomplete-list',
    onSelect: function(code, name) {
        const input = document.getElementById('fav-search-input');
        if (input) {
            input.value = name + ' (' + code + ')';
            input.dataset.selectedCode = code;
        }
    },
    onConfirm: function() { addFavoriteFromInput(); }
});

/* ── 목록 로드 ── */
// showLoading:false 는 자동 갱신용이다 — 매 주기마다 '로딩 중'으로 비우면 화면이 깜빡이고,
// 한 번 실패했다고 보고 있던 시세까지 지우면 손해가 더 크다.
async function loadFavoriteList(options = {}) {
    const tbody = document.getElementById('favorite-list-body');
    if (!tbody) return;

    const silent = options.showLoading === false;
    if (!silent) tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;">로딩 중...</td></tr>';

    try {
        const resp = await fetch('/api/favorite');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const items = await resp.json();

            _favoriteData = items || [];
            renderFavoriteTable();
            _renderFavoriteUpdatedAt(new Date());
    } catch (e) {
        // 자동 갱신 실패는 멈춘 갱신 시각으로 드러난다.
        if (silent) return;
        tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; color:#ff6b6b;">불러오기 실패: ${e.message}</td></tr>`;
    }
}

// 값이 그대로인 것과 갱신이 멈춘 것을 화면에서 구분할 수 있어야 한다.
function _renderFavoriteUpdatedAt(when) {
    const el = document.getElementById('favorite-updated');
    if (!el) return;
    el.textContent = `최근 업데이트: ${when.toLocaleTimeString('ko-KR')}`
        + ` (${Math.round(FAVORITE_REFRESH_MS / 1000)}초마다 자동 갱신)`;
}

function renderFavoriteTable() {
    const tbody = document.getElementById('favorite-list-body');
    if (!tbody) return;

    if (!_favoriteData || !_favoriteData.length) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:var(--text-secondary);">등록된 관심종목이 없습니다.</td></tr>';
        return;
    }

    let data = _favoriteData.slice();
    if (_favSortState.key) {
        const k = _favSortState.key;
        const d = _favSortState.dir === 'asc' ? 1 : -1;
        data.sort((a, b) => {
            let va, vb;
            if (k === 'name') {
                va = a.name || '';
                vb = b.name || '';
                return d * va.localeCompare(vb);
            } else if (k === 'minervini_stage') {
                va = a.minervini_stage ? parseInt(a.minervini_stage) : 0;
                vb = b.minervini_stage ? parseInt(b.minervini_stage) : 0;
            } else if (k === 'rs_rating') {
                va = a.rs_rating ? parseInt(a.rs_rating) : -1;
                vb = b.rs_rating ? parseInt(b.rs_rating) : -1;
            } else if (k === 'price') {
                va = a.price != null ? parseFloat(a.price) : -1;
                vb = b.price != null ? parseFloat(b.price) : -1;
            } else if (k === 'rate') {
                va = a.rate != null ? parseFloat(a.rate) : -999;
                vb = b.rate != null ? parseFloat(b.rate) : -999;
            }
            return d * (va - vb);
        });
    }

    tbody.innerHTML = data.map(item => _buildRow(item)).join('');
    _updateFavSortHeaders();
}

function sortFavorites(key) {
    if (_favSortState.key === key) {
        _favSortState.dir = _favSortState.dir === 'asc' ? 'desc' : 'asc';
    } else {
        _favSortState.key = key;
        _favSortState.dir = (key === 'name') ? 'asc' : 'desc';
    }
    renderFavoriteTable();
}

function _updateFavSortHeaders() {
    const ths = document.querySelectorAll('#section-favorite thead th');
    ths.forEach(th => {
        const key = th.getAttribute('data-sort');
        if (!key) return;
        if (_favSortState.key === key) {
            th.className = `sortable sort-${_favSortState.dir}`;
        } else {
            th.className = 'sortable';
        }
    });
}

function _buildRow(item) {
    const rate = parseFloat(item.rate || 0);
    const rateClass = rate > 0 ? 'price-up' : rate < 0 ? 'price-down' : '';
    const rateStr = item.rate != null ? `${rate > 0 ? '+' : ''}${parseFloat(item.rate).toFixed(2)}%` : '-';
    const priceStr = item.price != null ? Number(item.price).toLocaleString() + '원' : '-';

    const rsVal = item.rs_rating ? item.rs_rating : '-';
    let rsColor = '#1e90ff'; // 파랑
    if (item.rs_rating >= 80) rsColor = '#e94560'; // 빨강
    else if (item.rs_rating >= 50) rsColor = '#feca57'; // 노랑
    const rsBadge = `<span style="display:inline-block; padding:2px 8px; border-radius:4px; background:#f0f0f0; color:${rsColor}; font-weight:bold; font-size:0.85rem; border:1px solid #ccc; text-align:center;" title="RS Rating">${rsVal}</span>`;

    const stageLabels = { 1: 'S1', 2: 'S2', 3: 'S3', 4: 'S4' };
    const stageColors = { 1: '#6c757d', 2: '#28a745', 3: '#fd7e14', 4: '#dc3545' };
    const stageTitles = { 1: '무관심', 2: '상승', 3: '고점', 4: '하락' };
    const stageVal = item.minervini_stage;
    const stageBadge = stageVal
        ? `<span style="display:inline-block; padding:2px 8px; border-radius:4px; background:${stageColors[stageVal]}; color:#fff; font-weight:bold; font-size:0.82rem; text-align:center;" title="Minervini Stage: ${stageTitles[stageVal] || ''}">${stageLabels[stageVal] || stageVal}</span>`
        : '<span style="color:#aaa; font-size:0.82rem;">-</span>';

    return `<tr id="fav-row-${item.code}">
        <td><a href="/stock?code=${item.code}" style="color:var(--accent); font-weight:bold; text-decoration:none;">${item.name} <span style="font-weight:normal; font-size:0.85rem; color:#888;">(${item.code})</span></a></td>
        <td style="text-align:center;">${stageBadge}</td>
        <td style="text-align:center;">${rsBadge}</td>
        <td class="${rateClass}">${priceStr}</td>
        <td class="${rateClass}">${rateStr}</td>
        <td><button class="btn btn-sm" onclick="removeFavorite('${item.code}')">삭제</button></td>
    </tr>`;
}

/* ── 추가 ── */
async function addFavoriteFromInput() {
    const input = document.getElementById('fav-search-input');
    if (!input) return;

    // dataset.selectedCode 우선, 없으면 입력값에서 코드 추출
    let code = input.dataset.selectedCode || '';
    if (!code) {
        const match = input.value.match(/\((\d{6})\)/);
        if (match) code = match[1];
        else if (/^\d{6}$/.test(input.value.trim())) code = input.value.trim();
    }

    if (!code) { showToast('종목을 선택하거나 6자리 코드를 입력하세요.', 'error'); return; }

    try {
        const resp = await fetch(`/api/favorite/${code}`, { method: 'POST' });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);

        if (!data.added) { showToast('이미 등록된 종목입니다.', 'error'); return; }
        input.value = '';
        delete input.dataset.selectedCode;
        showToast('관심종목에 추가되었습니다.', 'success');
        await loadFavoriteList();
    } catch (e) {
        showToast(`추가 실패: ${e.message}`, 'error');
    }
}

/* ── 삭제 ── */
async function removeFavorite(code) {
    try {
        const resp = await fetch(`/api/favorite/${code}`, { method: 'DELETE' });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);

        _favoriteData = _favoriteData.filter(item => item.code !== code);
        renderFavoriteTable();
        showToast('관심종목에서 삭제되었습니다.', 'success');
    } catch (e) {
        showToast(`삭제 실패: ${e.message}`, 'error');
    }
}

/* ── 구독 해제 (페이지 이탈 시) ── */
window.addEventListener('beforeunload', function () {
    navigator.sendBeacon('/api/streaming/unsubscribe-favorite');
});

/* ── 자동 갱신 ── */
async function _favoriteRefreshTick() {
    // pjax 로 다른 화면에 가 있으면 스스로 멈춘다 (타이머는 문서를 떠나도 계속 돈다).
    if (!document.getElementById('favorite-list-body')) {
        _stopFavoriteAutoRefresh();
        return;
    }
    if (document.hidden) return;
    await loadFavoriteList({ showLoading: false });
}

function _stopFavoriteAutoRefresh() {
    if (_favoriteRefreshTimer === null) return;
    clearInterval(_favoriteRefreshTimer);
    _favoriteRefreshTimer = null;
}

function _startFavoriteAutoRefresh() {
    _stopFavoriteAutoRefresh();  // pjax 재진입 시 타이머가 겹쳐 걸리지 않게 한다.
    _favoriteRefreshTimer = setInterval(_favoriteRefreshTick, FAVORITE_REFRESH_MS);
}

// 브라우저는 숨은 탭의 타이머를 늦추고, 위 tick 도 숨은 동안은 건너뛴다 — 돌아왔을 때
// 다음 주기까지 기다리면 화면이 몇 분 전 시세인 채로 남는다. 복귀 즉시 한 번 당겨 받는다.
let _favoriteVisibilityBound = false;

function _bindFavoriteVisibility() {
    if (_favoriteVisibilityBound) return;
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) return;
        void _favoriteRefreshTick();
    });
    _favoriteVisibilityBound = true;
}

/* ── 초기화 (최초 로드 / pjax 재방문 공용) ── */
async function initFavoritePage() {
    _bindFavoriteVisibility();
    _startFavoriteAutoRefresh();
    await loadFavoriteList();
}

document.addEventListener('DOMContentLoaded', () => { void initFavoritePage(); });

/* ── Pjax 재방문 시 재초기화 ── */
document.addEventListener('pjax:ready', (e) => {
    if (e.detail?.path !== '/favorite') return;
    StockAutocomplete({
        inputId: 'fav-search-input',
        listId: 'fav-autocomplete-list',
        onSelect: function(code, name) {
            const input = document.getElementById('fav-search-input');
            if (input) { input.value = name + ' (' + code + ')'; input.dataset.selectedCode = code; }
        },
        onConfirm: function() { addFavoriteFromInput(); }
    });
    void initFavoritePage();
});
