/* view/web/static/js/overseas_stock.js — 미국장 종목 현재가 조회 화면 */

function _overseasStockSymbolInput() {
    const input = document.getElementById('overseas-stock-symbol');
    return input ? input.value.trim().toUpperCase() : '';
}

function _overseasStockExchange() {
    const select = document.getElementById('overseas-stock-exchange');
    return select ? select.value : 'NASD';
}

function _overseasStockUsd(value) {
    const n = Number(String(value ?? '').replace(/,/g, ''));
    if (!Number.isFinite(n) || n === 0) return '-';
    return '$' + n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function _overseasStockCount(value) {
    const n = Number(String(value ?? '').replace(/,/g, ''));
    if (!Number.isFinite(n) || n === 0) return '-';
    return n.toLocaleString();
}

function _overseasStockRate(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return '-';
    return (n > 0 ? '+' : '') + n.toFixed(2) + '%';
}

/** KIS 해외 현재가 raw 의 대비(diff)는 부호 없는 절대값이라 등락률로 방향을 붙인다. */
function _overseasStockDiff(diff, rate) {
    const n = Number(String(diff ?? '').replace(/,/g, ''));
    if (!Number.isFinite(n) || n === 0) return '-';
    const r = Number(rate);
    const sign = r < 0 ? '-' : (r > 0 ? '+' : '');
    return sign + Math.abs(n).toFixed(2);
}

async function _overseasStockEnabled() {
    const res = await fetchWithTimeout('/api/market-mode', {}, 5000);
    if (!res.ok) return false;
    const json = await res.json();
    return Array.isArray(json.enabled_market_modes) && json.enabled_market_modes.includes('overseas_us');
}

function _closeOverseasStockAutocomplete() {
    const list = document.getElementById('overseas-stock-autocomplete-list');
    if (list) {
        list.innerHTML = '';
        list.style.display = 'none';
    }
}

function _renderOverseasStockCard(resultDiv, data, symbol, exchange) {
    const raw = (data && typeof data.raw === 'object' && data.raw) ? data.raw : {};
    const rate = Number(data.change_rate);
    const rateClass = rate > 0 ? 'text-red' : (rate < 0 ? 'text-blue' : '');

    resultDiv.innerHTML = `
        <div class="card stock-info-box">
            <div class="stock-title">
                <h3>${escapeHtml(data.symbol || symbol)}</h3>
                <span class="stock-market-badge overseas">${escapeHtml(data.exchange || exchange)}</span>
                <span class="small">${escapeHtml(data.currency || 'USD')}</span>
            </div>
            <p class="price ${rateClass}">${_overseasStockUsd(data.price)}</p>
            <p class="${rateClass}">전일대비 ${_overseasStockDiff(raw.diff, rate)} (${_overseasStockRate(rate)})</p>
            <div class="stock-details">
                <div class="detail-group">
                    <h4>시세</h4>
                    <p><strong>전일종가</strong> <span>${_overseasStockUsd(raw.base)}</span></p>
                    <p><strong>거래량</strong> <span>${_overseasStockCount(data.volume || raw.tvol)}</span></p>
                    <p><strong>전일거래량</strong> <span>${_overseasStockCount(raw.pvol)}</span></p>
                    <p><strong>거래대금</strong> <span>${_overseasStockCount(raw.tamt)}</span></p>
                </div>
                <div class="detail-group">
                    <h4>조회 정보</h4>
                    <p><strong>거래소</strong> <span>${escapeHtml(data.exchange || exchange)}</span></p>
                    <p><strong>기준일</strong> <span>${escapeHtml(data.timestamp || '-')}</span></p>
                </div>
            </div>
        </div>
    `;
}

async function searchOverseasStock(symbolOverride, exchangeOverride) {
    const symbol = String(symbolOverride || _overseasStockSymbolInput()).trim().toUpperCase();
    const exchange = exchangeOverride || _overseasStockExchange();
    const resultDiv = document.getElementById('overseas-stock-result');
    const chartCard = document.getElementById('stock-chart-card');

    if (!symbol) {
        alert('심볼을 입력하세요.');
        return;
    }
    _closeOverseasStockAutocomplete();

    const input = document.getElementById('overseas-stock-symbol');
    if (input) input.value = symbol;
    if (chartCard) chartCard.style.display = 'none';
    if (!resultDiv) return;
    showLoading(resultDiv, '종목 정보 조회 중...');

    try {
        if (!await _overseasStockEnabled()) {
            showError(resultDiv, 'overseas_us가 enabled되어 있지 않습니다. 시스템 설정에서 활성화하세요.');
            return;
        }
        const res = await fetchWithTimeout(
            `/api/overseas/stock/${encodeURIComponent(symbol)}?exchange=${encodeURIComponent(exchange)}`,
            {},
            12000,
        );
        const { json, error } = await readJsonResponse(res);
        if (error || json.rt_cd !== '0') {
            showError(resultDiv, `조회 실패: ${error || json.msg1 || res.status}`);
            return;
        }
        _renderOverseasStockCard(resultDiv, json.data || {}, symbol, exchange);
        if (typeof loadAndRenderOverseasStockChart === 'function') {
            await loadAndRenderOverseasStockChart(symbol, exchange);
        }
    } catch (e) {
        console.error('[overseas-stock] 통신 오류', e);
        showError(resultDiv, e.name === 'AbortError' ? '요청 시간이 초과되었습니다.' : '통신 오류가 발생했습니다.');
    }
}

function _initOverseasStockAutocomplete() {
    if (typeof StockAutocomplete !== 'function') return;

    StockAutocomplete({
        inputId: 'overseas-stock-symbol',
        listId: 'overseas-stock-autocomplete-list',
        valueKey: 's',
        readyEvent: 'all-overseas-stocks-ready',
        getInitial: () => window.ALL_OVERSEAS_STOCKS || null,
        searchImpl: _overseasStockSearch,
        formatItem: stock => `${stock.s} — ${stock.n} (${stock.e})`,
        onSelect: (symbol, _name, stock) => {
            const exchange = document.getElementById('overseas-stock-exchange');
            if (exchange && stock && stock.e) exchange.value = stock.e;
            void searchOverseasStock(symbol, exchange ? exchange.value : undefined);
        },
        onConfirm: () => { void searchOverseasStock(); },
    });
    if (typeof _ensureOverseasStocksLoaded === 'function') _ensureOverseasStocksLoaded();
}

function initOverseasStockPage() {
    _initOverseasStockAutocomplete();

    const symbol = new URLSearchParams(window.location.search).get('symbol');
    if (symbol) {
        const input = document.getElementById('overseas-stock-symbol');
        if (input) input.value = symbol.toUpperCase();
        void searchOverseasStock(symbol);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    if (window.location.pathname !== '/overseas-stock') return;
    initOverseasStockPage();
});

document.addEventListener('pjax:ready', (e) => {
    if (e.detail?.path !== '/overseas-stock') return;
    initOverseasStockPage();
});
