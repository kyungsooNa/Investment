/* view/web/static/js/favorite.js — 관심종목 페이지 동적 이벤트 관리 */

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
async function loadFavoriteList() {
    const tbody = document.getElementById('favorite-list-body');
    if (!tbody) return;

    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;">로딩 중...</td></tr>';

    try {
        const resp = await fetch('/api/favorite');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const items = await resp.json();

        if (!items || !items.length) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-secondary);">등록된 관심종목이 없습니다.</td></tr>';
            return;
        }

        tbody.innerHTML = items.map(item => _buildRow(item)).join('');
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; color:#ff6b6b;">불러오기 실패: ${e.message}</td></tr>`;
    }
}

function _buildRow(item) {
    const rate = parseFloat(item.rate || 0);
    const rateClass = rate > 0 ? 'price-up' : rate < 0 ? 'price-down' : '';
    const rateStr = item.rate != null ? `${rate > 0 ? '+' : ''}${parseFloat(item.rate).toFixed(2)}%` : '-';
    const priceStr = item.price != null ? Number(item.price).toLocaleString() + '원' : '-';

    return `<tr id="fav-row-${item.code}">
        <td><a href="/stock?code=${item.code}" style="color:var(--accent)">${item.code}</a></td>
        <td>${item.name}</td>
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

        const row = document.getElementById(`fav-row-${code}`);
        if (row) row.remove();

        const tbody = document.getElementById('favorite-list-body');
        if (tbody && !tbody.querySelector('tr[id^="fav-row-"]')) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-secondary);">등록된 관심종목이 없습니다.</td></tr>';
        }
        showToast('관심종목에서 삭제되었습니다.', 'success');
    } catch (e) {
        showToast(`삭제 실패: ${e.message}`, 'error');
    }
}

/* ── 구독 해제 (페이지 이탈 시) ── */
window.addEventListener('beforeunload', function () {
    navigator.sendBeacon('/api/streaming/unsubscribe-favorite');
});

/* ── 초기 로드 ── */
document.addEventListener('DOMContentLoaded', loadFavoriteList);

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
    loadFavoriteList();
});
