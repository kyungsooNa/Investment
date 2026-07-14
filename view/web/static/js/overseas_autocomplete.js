/* view/web/static/js/overseas_autocomplete.js — 미국장 심볼 자동완성 */

window.ALL_OVERSEAS_STOCKS = (function () {
    try {
        const cached = localStorage.getItem('all_overseas_stocks_v1');
        if (cached) return JSON.parse(cached);
    } catch (_) {}
    return null;
})();

let _overseasStocksLoading = false;

function _ensureOverseasStocksLoaded() {
    if (window.ALL_OVERSEAS_STOCKS || _overseasStocksLoading) return;
    _overseasStocksLoading = true;
    fetch('/api/overseas/stocks/list')
        .then(response => response.json())
        .then(json => {
            window.ALL_OVERSEAS_STOCKS = json.stocks || [];
            try {
                localStorage.setItem(
                    'all_overseas_stocks_v1',
                    JSON.stringify(window.ALL_OVERSEAS_STOCKS),
                );
            } catch (_) {}
            document.dispatchEvent(new CustomEvent(
                'all-overseas-stocks-ready',
                { detail: window.ALL_OVERSEAS_STOCKS },
            ));
        })
        .catch(() => { window.ALL_OVERSEAS_STOCKS = []; })
        .finally(() => { _overseasStocksLoading = false; });
}

function initOverseasAutocomplete() {
    if (typeof StockAutocomplete !== 'function') return;

    StockAutocomplete({
        inputId: 'overseas-symbol',
        listId: 'overseas-autocomplete-list',
        valueKey: 's',
        readyEvent: 'all-overseas-stocks-ready',
        getInitial: () => window.ALL_OVERSEAS_STOCKS || null,
        searchImpl: _overseasStockSearch,
        formatItem: stock => `${stock.s} — ${stock.n} (${stock.e})`,
        onSelect: (symbol, _name, stock) => {
            const input = document.getElementById('overseas-symbol');
            const exchange = document.getElementById('overseas-exchange');
            if (input) input.value = symbol;
            if (exchange && stock && stock.e) exchange.value = stock.e;
            if (typeof loadOverseasQuote === 'function') loadOverseasQuote();
        },
        onConfirm: () => {
            if (typeof loadOverseasQuote === 'function') loadOverseasQuote();
        },
    });
    _ensureOverseasStocksLoaded();
}

initOverseasAutocomplete();

document.addEventListener('pjax:ready', event => {
    if (event.detail?.path === '/overseas') initOverseasAutocomplete();
});
