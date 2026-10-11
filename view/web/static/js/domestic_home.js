/* 한국장 메인: 축소 종목 검색 후 현재가 상세 화면으로 이동 + 당일 주도 테마 */

let DOMESTIC_HOME_STOCKS = null;
let _domesticThemeTimer = null;
const DOMESTIC_THEME_LIMIT = 5;
const DOMESTIC_THEME_REFRESH_MS = 60000;

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

function _renderDomesticThemeLeaders(box, themes) {
    box.innerHTML = '<ol class="domestic-theme-list">' + themes.slice(0, DOMESTIC_THEME_LIMIT).map((theme, index) => {
        const leaders = (theme.leaders || []).map(leader => {
            const rate = parseFloat(leader.change_rate || 0);
            const rateClass = rate > 0 ? 'text-red' : (rate < 0 ? 'text-blue' : '');
            return `<a href="/stock?code=${encodeURIComponent(leader.code || '')}" class="stock-link">${escapeHtml(leader.name || leader.code || '-')}</a>`
                + ` <span class="${rateClass}">${rate > 0 ? '+' : ''}${rate.toFixed(2)}%</span>`;
        }).join('<span class="domestic-theme-sep">·</span>');
        return `<li class="domestic-theme-item">
            <span class="domestic-theme-rank">${index + 1}</span>
            <div class="domestic-theme-body">
                <div class="domestic-theme-head">
                    <strong>${escapeHtml(theme.normalized_name || '')}</strong>
                    <span class="small">거래대금 ${formatTradingValue(theme.trading_value_sum_won)} · 상승 확산 ${Number(theme.advancing_ratio || 0).toFixed(0)}%</span>
                </div>
                <div class="domestic-theme-leaders">${leaders}</div>
            </div>
        </li>`;
    }).join('') + '</ol>';
}

async function loadDomesticThemeLeaders() {
    if (_domesticThemeTimer) {
        clearTimeout(_domesticThemeTimer);
        _domesticThemeTimer = null;
    }
    const box = document.getElementById('domestic-theme-leaders');
    if (!box) return;
    const updated = document.getElementById('domestic-theme-updated');

    try {
        const response = await fetch('/api/ranking/themes/intraday');
        const json = await response.json();
        const themes = json.rt_cd === '0' && Array.isArray(json.data) ? json.data : [];
        if (themes.length) {
            _renderDomesticThemeLeaders(box, themes);
        } else {
            box.innerHTML = `<p class="small">${escapeHtml(json.msg1 || '당일 주도 테마 데이터가 없습니다.')}</p>`;
        }
        if (updated) updated.textContent = json.captured_at ? `기준 ${json.captured_at}` : '';
    } catch (_) {
        box.innerHTML = '<p class="small">주도 테마를 불러오지 못했습니다.</p>';
    }

    // 장중 태스크가 매분 갱신하므로 홈에 머무는 동안만 1분 주기로 다시 읽는다.
    if (document.getElementById('domestic-theme-leaders')) {
        _domesticThemeTimer = setTimeout(loadDomesticThemeLeaders, DOMESTIC_THEME_REFRESH_MS);
    }
}

function initDomesticHome() {
    void loadDomesticThemeLeaders();
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
