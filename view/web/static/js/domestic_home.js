/* 한국장 메인: 축소 종목 검색 후 현재가 상세 화면으로 이동 */

let DOMESTIC_HOME_STOCKS = null;

function _setDomesticSearchError(message) {
    const error = document.getElementById('domestic-stock-search-error');
    if (error) error.textContent = message || '';
}

function _navigateToDomesticStock(code) {
    const href = `/stock?code=${encodeURIComponent(code)}`;
    if (typeof navigatePjax === 'function') {
        void navigatePjax(href);
    } else {
        window.location.href = href;
    }
}

function _resolveDomesticHomeStock(raw) {
    if (/^\d{6}$/.test(raw)) return { code: raw };

    const stocks = Array.isArray(DOMESTIC_HOME_STOCKS) ? DOMESTIC_HOME_STOCKS : [];
    const exact = stocks.find(stock =>
        String(stock.n || '').toLowerCase() === raw.toLowerCase()
    );
    if (exact) return { code: exact.c };

    const matches = _defaultStockSearch(raw, stocks);
    if (matches.length === 1) return { code: matches[0].c };
    if (matches.length > 1) return { error: '검색 결과가 여러 개입니다. 자동완성에서 종목을 선택하세요.' };
    return { error: '일치하는 종목을 찾을 수 없습니다.' };
}

function submitDomesticStockSearch(event) {
    if (event) event.preventDefault();
    const input = document.getElementById('domestic-stock-search');
    const raw = input ? input.value.trim() : '';
    if (!raw) {
        _setDomesticSearchError('종목코드 또는 종목명을 입력하세요.');
        return false;
    }

    const resolved = _resolveDomesticHomeStock(raw);
    if (resolved.error) {
        _setDomesticSearchError(resolved.error);
        return false;
    }

    _setDomesticSearchError('');
    _navigateToDomesticStock(resolved.code);
    return false;
}

function _loadDomesticHomeStocks() {
    try {
        const cached = localStorage.getItem('all_stocks_v2');
        if (cached) DOMESTIC_HOME_STOCKS = JSON.parse(cached);
    } catch (_) {}

    if (DOMESTIC_HOME_STOCKS) {
        document.dispatchEvent(new CustomEvent('domestic-home-stocks-ready', {
            detail: DOMESTIC_HOME_STOCKS,
        }));
        return;
    }

    fetch('/api/stocks/list')
        .then(response => response.json())
        .then(json => {
            DOMESTIC_HOME_STOCKS = json.stocks || [];
            try {
                localStorage.setItem('all_stocks_v2', JSON.stringify(DOMESTIC_HOME_STOCKS));
            } catch (_) {}
            document.dispatchEvent(new CustomEvent('domestic-home-stocks-ready', {
                detail: DOMESTIC_HOME_STOCKS,
            }));
        })
        .catch(() => {
            DOMESTIC_HOME_STOCKS = [];
            _setDomesticSearchError('종목 목록을 불러오지 못했습니다.');
        });
}

function initDomesticHome() {
    const input = document.getElementById('domestic-stock-search');
    if (!input || input.dataset.autocompleteReady === 'true') return;
    input.dataset.autocompleteReady = 'true';

    StockAutocomplete({
        inputId: 'domestic-stock-search',
        listId: 'domestic-stock-autocomplete',
        getInitial: () => DOMESTIC_HOME_STOCKS,
        readyEvent: 'domestic-home-stocks-ready',
        onSelect: code => _navigateToDomesticStock(code),
        onConfirm: () => submitDomesticStockSearch(),
    });
    _loadDomesticHomeStocks();
}

document.addEventListener('DOMContentLoaded', initDomesticHome);
document.addEventListener('pjax:ready', event => {
    if (event.detail?.path === '/domestic') initDomesticHome();
});
