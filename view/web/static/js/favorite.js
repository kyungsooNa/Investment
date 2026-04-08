/* view/web/static/js/favorite.js — 관심종목 페이지 동적 이벤트 관리 */

/* ── 자동완성 (stock.js의 _stocks 데이터 재사용) ── */
(function () {
    let _favStocks = [];
    let _activeIndex = -1;

    function _setupStocks(raw) {
        _favStocks = raw || [];
        for (let i = 0; i < _favStocks.length; i++) {
            if (!_favStocks[i].ch && typeof _getChosung === 'function') {
                _favStocks[i].ch = _getChosung(_favStocks[i].n);
            }
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        const input = document.getElementById('fav-search-input');
        const list = document.getElementById('fav-autocomplete-list');
        if (!input || !list) return;

        if (typeof ALL_STOCKS !== 'undefined' && ALL_STOCKS) _setupStocks(ALL_STOCKS);
        document.addEventListener('all-stocks-ready', function (e) { _setupStocks(e.detail); });

        input.addEventListener('input', function () {
            const q = input.value.trim();
            _activeIndex = -1;
            if (!q) { list.innerHTML = ''; list.style.display = 'none'; return; }

            const results = [];
            const isDigit = /^\d+$/.test(q);
            const isCho = typeof _isChosung === 'function' ? _isChosung(q) : false;
            const qLower = q.toLowerCase();

            for (let i = 0; i < _favStocks.length && results.length < 20; i++) {
                const s = _favStocks[i];
                if (isDigit) {
                    if (s.c.startsWith(q)) results.push(s);
                } else if (isCho) {
                    if (s.ch && s.ch.startsWith(q)) results.push(s);
                } else {
                    if (s.n.toLowerCase().includes(qLower)) results.push(s);
                }
            }

            if (!results.length) { list.innerHTML = ''; list.style.display = 'none'; return; }

            list.innerHTML = results.map(s =>
                `<div class="autocomplete-item" data-code="${s.c}" data-name="${s.n}">${s.n} (${s.c})</div>`
            ).join('');
            list.style.display = 'block';

            list.querySelectorAll('.autocomplete-item').forEach(item => {
                item.addEventListener('click', function () {
                    input.value = `${this.dataset.name} (${this.dataset.code})`;
                    input.dataset.selectedCode = this.dataset.code;
                    list.innerHTML = '';
                    list.style.display = 'none';
                });
            });
        });

        input.addEventListener('keydown', function (e) {
            const items = list.querySelectorAll('.autocomplete-item');
            if (e.key === 'ArrowDown') { _activeIndex = Math.min(_activeIndex + 1, items.length - 1); }
            else if (e.key === 'ArrowUp') { _activeIndex = Math.max(_activeIndex - 1, 0); }
            else { return; }
            items.forEach((el, i) => el.classList.toggle('active', i === _activeIndex));
            if (items[_activeIndex]) {
                input.value = `${items[_activeIndex].dataset.name} (${items[_activeIndex].dataset.code})`;
                input.dataset.selectedCode = items[_activeIndex].dataset.code;
            }
            e.preventDefault();
        });

        document.addEventListener('click', function (e) {
            if (!input.contains(e.target) && !list.contains(e.target)) {
                list.innerHTML = '';
                list.style.display = 'none';
            }
        });
    });
})();

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
