/* view/web/static/js/overseas.js — 미국주식 조회/주문 */

let _overseasMarketCapSequence = 0;

function _escapeOverseasHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
    })[char]);
}

function _overseasIsRealMode() {
    const badge = document.getElementById('status-env');
    return !!(badge && (
        badge.dataset.mode === 'real' || badge.classList.contains('real')
    ));
}

function _overseasSymbolValue(inputId) {
    const input = document.getElementById(inputId);
    return input ? input.value.trim().toUpperCase() : '';
}

function _overseasExchangeValue(selectId) {
    const select = document.getElementById(selectId);
    return select ? select.value : 'NASD';
}

function _formatUsd(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return '-';
    return '$' + n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function _formatNumber(value) {
    const n = Number(String(value ?? '').replace(/,/g, ''));
    return Number.isFinite(n) ? n.toLocaleString() : '-';
}

function _formatUsdMarketCap(value) {
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0) return '-';
    if (n >= 1_000_000_000_000) return `$${(n / 1_000_000_000_000).toFixed(2)}T`;
    if (n >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(2)}B`;
    return _formatUsd(n);
}

function _formatKrwMarketCap(value) {
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0) return '-';
    return n.toLocaleString() + '원';
}

function _setOverseasOrderSymbol(symbol, exchange) {
    const symbolInput = document.getElementById('overseas-order-symbol');
    const exchangeSelect = document.getElementById('overseas-order-exchange');
    if (symbolInput && symbol) symbolInput.value = symbol;
    if (exchangeSelect && exchange) exchangeSelect.value = exchange;
}

function _showOverseasError(element, message) {
    if (element) element.innerHTML = `<p class="error">${_escapeOverseasHtml(message)}</p>`;
}

function _refreshOverseasRealBanner() {
    const isReal = _overseasIsRealMode();
    const banner = document.getElementById('overseas-real-banner');
    const label = document.getElementById('overseas-order-mode-label');
    if (banner) banner.style.display = isReal ? 'block' : 'none';
    if (label) {
        label.innerText = isReal ? '[실전]' : '';
        label.style.color = isReal ? '#ff4d4d' : '#aaa';
    }
}

async function _ensureOverseasEnabled() {
    const res = await fetchWithTimeout('/api/market-mode', {}, 5000);
    if (!res.ok) return false;
    const json = await res.json();
    return Array.isArray(json.enabled_market_modes) && json.enabled_market_modes.includes('overseas_us');
}

async function _readOverseasJson(res) {
    if (!res.ok) return { json: null, error: `HTTP ${res.status}` };
    try {
        const json = await res.json();
        return json && typeof json === 'object'
            ? { json, error: null }
            : { json: null, error: '응답 형식이 올바르지 않습니다.' };
    } catch (_) {
        return { json: null, error: '응답을 처리하지 못했습니다.' };
    }
}

async function _refreshOverseasAvailability() {
    const status = document.getElementById('overseas-status');
    try {
        const enabled = await _ensureOverseasEnabled();
        document.querySelectorAll('[data-overseas-required]').forEach(button => {
            button.disabled = !enabled;
        });
        if (status) {
            status.textContent = enabled
                ? '미국주식 기능이 활성화되어 있습니다.'
                : '미국주식 기능이 비활성화되어 있습니다. 시스템 설정에서 overseas_us를 활성화하세요.';
        }
    } catch (_) {
        if (status) status.textContent = '미국주식 기능 상태를 확인하지 못했습니다.';
    }
}

async function setOverseasTab(tabName) {
    const tabs = ['overview', 'marketcap', 'orders'];
    if (!tabs.includes(tabName)) return;

    tabs.forEach(name => {
        const panel = document.getElementById(`overseas-panel-${name}`);
        const tab = document.getElementById(`overseas-tab-${name}`);
        if (panel) panel.hidden = name !== tabName;
        if (tab) {
            tab.classList.toggle('active', name === tabName);
            tab.setAttribute('aria-selected', String(name === tabName));
        }
    });

    // 탭 전환마다 가용성을 재확인해 초기 확인 실패/모드 변경이 버튼 상태에 반영되게 한다.
    void _refreshOverseasAvailability();

    if (tabName === 'marketcap') await loadOverseasMarketCap();
}

async function loadOverseasQuote() {
    const symbol = _overseasSymbolValue('overseas-symbol');
    const exchange = _overseasExchangeValue('overseas-exchange');
    const resultDiv = document.getElementById('overseas-quote-result');
    if (!symbol) {
        alert('심볼을 입력하세요.');
        return;
    }
    _setOverseasOrderSymbol(symbol, exchange);
    showLoading(resultDiv, '조회 중...');

    try {
        if (!await _ensureOverseasEnabled()) {
            _showOverseasError(resultDiv, 'overseas_us가 enabled되어 있지 않습니다.');
            return;
        }
        const res = await fetchWithTimeout(`/api/overseas/stock/${encodeURIComponent(symbol)}?exchange=${encodeURIComponent(exchange)}`, {}, 12000);
        const { json, error } = await _readOverseasJson(res);
        if (error || json.rt_cd !== '0') {
            _showOverseasError(resultDiv, `조회 실패: ${error || json.msg1 || res.status}`);
            return;
        }
        const data = json.data || {};
        const rate = Number(data.change_rate || 0);
        const rateClass = rate > 0 ? 'text-red' : (rate < 0 ? 'text-blue' : '');
        resultDiv.innerHTML = `
            <div class="card">
                <h3>${_escapeOverseasHtml(data.symbol || symbol)} <span style="color:#aaa;font-size:0.85rem;">${_escapeOverseasHtml(data.exchange || exchange)} ${_escapeOverseasHtml(data.currency || 'USD')}</span></h3>
                <p class="price ${rateClass}">${_formatUsd(data.price)}</p>
                <p>등락률: <span class="${rateClass}">${Number.isFinite(rate) ? rate.toFixed(2) + '%' : '-'}</span></p>
                <p>거래량: ${_formatNumber(data.volume)}</p>
                <p>시각: ${_escapeOverseasHtml(data.timestamp || '-')}</p>
            </div>
        `;
    } catch (e) {
        console.error('[overseas] 통신 오류', e);
        _showOverseasError(resultDiv, e.name === 'AbortError' ? '요청 시간이 초과되었습니다.' : '통신 오류가 발생했습니다.');
    }
}

async function loadOverseasChart() {
    const symbol = _overseasSymbolValue('overseas-symbol');
    const exchange = _overseasExchangeValue('overseas-exchange');
    const resultDiv = document.getElementById('overseas-chart-result');
    if (!symbol) {
        alert('심볼을 입력하세요.');
        return;
    }
    showLoading(resultDiv, '일봉 조회 중...');

    try {
        if (!await _ensureOverseasEnabled()) {
            _showOverseasError(resultDiv, 'overseas_us가 enabled되어 있지 않습니다.');
            return;
        }
        const res = await fetchWithTimeout(`/api/overseas/chart/${encodeURIComponent(symbol)}?exchange=${encodeURIComponent(exchange)}&period=D`, {}, 12000);
        const { json, error } = await _readOverseasJson(res);
        if (error || json.rt_cd !== '0') {
            _showOverseasError(resultDiv, `일봉 조회 실패: ${error || json.msg1 || res.status}`);
            return;
        }
        const data = json.data || {};
        const list = Array.isArray(data) ? data : (Array.isArray(data.output2) ? data.output2 : []);
        const body = list.slice(-10).reverse().map(row => `
            <tr>
                <td>${_escapeOverseasHtml(row.date || row.xymd || row.stck_bsop_date || '-')}</td>
                <td>${_formatUsd(row.close || row.clos || row.clpr || row.ovrs_nmix_prpr)}</td>
                <td>${_formatNumber(row.volume || row.tvol || row.acml_vol)}</td>
            </tr>
        `).join('');
        resultDiv.innerHTML = `
            <div class="card">
                <h3>${_escapeOverseasHtml(symbol)} 일봉</h3>
                <table class="data-table">
                    <thead><tr><th>일자</th><th>종가</th><th>거래량</th></tr></thead>
                    <tbody>${body || '<tr><td colspan="3">데이터 없음</td></tr>'}</tbody>
                </table>
            </div>
        `;
    } catch (e) {
        console.error('[overseas] 통신 오류', e);
        _showOverseasError(resultDiv, e.name === 'AbortError' ? '요청 시간이 초과되었습니다.' : '통신 오류가 발생했습니다.');
    }
}

async function loadOverseasMarketCap() {
    const requestSequence = ++_overseasMarketCapSequence;
    const isLatestRequest = () => requestSequence === _overseasMarketCapSequence;
    const resultDiv = document.getElementById('overseas-marketcap-result');
    showLoading(resultDiv, '주요 미국 대형주 시가총액 조회 중...');

    try {
        if (!await _ensureOverseasEnabled()) {
            if (!isLatestRequest()) return;
            _showOverseasError(resultDiv, 'overseas_us가 enabled되어 있지 않습니다.');
            return;
        }
        const res = await fetchWithTimeout('/api/overseas/market-cap', {}, 12000);
        const { json, error } = await _readOverseasJson(res);
        if (!isLatestRequest()) return;
        if (error || json.rt_cd !== '0') {
            _showOverseasError(resultDiv, `시가총액 조회 실패: ${error || json.msg1 || res.status}`);
            return;
        }
        const data = json.data || {};
        const items = Array.isArray(data.items) ? data.items : [];
        if (!items.length) {
            resultDiv.innerHTML = '<p class="empty">표시할 주요 미국 대형주 시가총액이 없습니다.</p>';
            return;
        }
        const rows = items.map((item, index) => `
            <tr>
                <td>${index + 1}</td>
                <td>${_escapeOverseasHtml(item.symbol || '-')}</td>
                <td>${_escapeOverseasHtml(item.name || '-')}</td>
                <td>${_formatUsdMarketCap(item.market_cap_usd)}</td>
                <td>${_formatKrwMarketCap(item.market_cap_krw)}</td>
            </tr>
        `).join('');
        const fxRate = data.fx_rate == null ? NaN : Number(data.fx_rate);
        const fxText = Number.isFinite(fxRate) ? `USD/KRW ${fxRate.toLocaleString()}` : '환율 정보 없음';
        resultDiv.innerHTML = `
            <div class="card">
                <p class="small">${fxText}</p>
                <table class="data-table">
                    <thead><tr><th>순위</th><th>심볼</th><th>종목명</th><th>시가총액(USD)</th><th>원화 환산</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        `;
    } catch (e) {
        console.error('[overseas] 시가총액 조회 오류', e);
        if (!isLatestRequest()) return;
        _showOverseasError(resultDiv, e.name === 'AbortError' ? '요청 시간이 초과되었습니다.' : '시가총액을 불러오지 못했습니다.');
    }
}

async function loadOverseasBalance() {
    // 잔고 조회 버튼은 '보유·주문' 탭에 있으므로, 같은 탭의 주문 거래소 셀렉트를 기준으로 삼는다.
    // (개요 탭의 overseas-exchange 는 탭 분리 후 숨겨져 있어 사용자가 조작할 수 없다.)
    const exchange = _overseasExchangeValue('overseas-order-exchange');
    const resultDiv = document.getElementById('overseas-balance-result');
    showLoading(resultDiv, '잔고 조회 중...');

    try {
        if (!await _ensureOverseasEnabled()) {
            _showOverseasError(resultDiv, 'overseas_us가 enabled되어 있지 않습니다.');
            return;
        }
        const res = await fetchWithTimeout(`/api/overseas/balance?exchange=${encodeURIComponent(exchange)}&currency=USD`, {}, 12000);
        const { json, error } = await _readOverseasJson(res);
        if (error || json.rt_cd !== '0') {
            _showOverseasError(resultDiv, `잔고 조회 실패: ${error || json.msg1 || res.status}`);
            return;
        }
        const data = json.data || {};
        const rows = Array.isArray(data.output1) ? data.output1 : (Array.isArray(data) ? data : []);
        const body = rows.map(row => `
            <tr>
                <td>${_escapeOverseasHtml(row.ovrs_pdno || row.pdno || row.symbol || '-')}</td>
                <td>${_escapeOverseasHtml(row.ovrs_item_name || row.prdt_name || row.name || '-')}</td>
                <td>${_formatNumber(row.ovrs_cblc_qty || row.hldg_qty || row.qty)}</td>
                <td>${_formatUsd(row.now_pric2 || row.price || row.ovrs_now_pric1)}</td>
            </tr>
        `).join('');
        resultDiv.innerHTML = `
            <div class="card">
                <h3>미국주식 잔고 <span style="color:#aaa;font-size:0.85rem;">${_escapeOverseasHtml(exchange)} USD</span></h3>
                <table class="data-table">
                    <thead><tr><th>심볼</th><th>이름</th><th>수량</th><th>현재가</th></tr></thead>
                    <tbody>${body || '<tr><td colspan="4">보유 없음</td></tr>'}</tbody>
                </table>
            </div>
        `;
    } catch (e) {
        console.error('[overseas] 통신 오류', e);
        _showOverseasError(resultDiv, e.name === 'AbortError' ? '요청 시간이 초과되었습니다.' : '통신 오류가 발생했습니다.');
    }
}

async function placeOverseasOrder(side) {
    const symbol = _overseasSymbolValue('overseas-order-symbol');
    const exchange = _overseasExchangeValue('overseas-order-exchange');
    const qty = Number(document.getElementById('overseas-order-qty')?.value || 0);
    const limitPrice = Number(document.getElementById('overseas-order-price')?.value || 0);
    const resultDiv = document.getElementById('overseas-order-result');

    if (!symbol || qty <= 0 || limitPrice <= 0) {
        alert('심볼, 수량, 지정가를 확인하세요.');
        return;
    }

    const sideKr = side === 'buy' ? '매수' : '매도';
    let realOrderConfirmation = null;
    if (_overseasIsRealMode()) {
        const step1 = confirm(
            `[실전투자] 미국주식 ${sideKr} 주문\n\n` +
            `심볼: ${symbol}\n거래소: ${exchange}\n수량: ${qty}\n지정가: ${limitPrice}\n\n` +
            '계속하시겠습니까?'
        );
        if (!step1) return;
        realOrderConfirmation = prompt('최종 확인을 위해 "REAL"을 입력하세요:');
        if (realOrderConfirmation !== 'REAL') {
            alert('확인 문자열이 일치하지 않아 주문이 취소되었습니다.');
            return;
        }
    } else if (!confirm(`미국주식 ${sideKr} 주문\n심볼: ${symbol}\n수량: ${qty}\n지정가: ${limitPrice}`)) {
        return;
    }

    showLoading(resultDiv, '주문 전송 중...');
    try {
        if (!await _ensureOverseasEnabled()) {
            _showOverseasError(resultDiv, 'overseas_us가 enabled되어 있지 않습니다.');
            return;
        }
        const res = await fetchWithTimeout('/api/overseas/order', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                symbol, exchange, side, qty, limit_price: limitPrice, currency: 'USD',
                real_order_confirmation: realOrderConfirmation,
            }),
        }, 15000);
        const { json, error } = await _readOverseasJson(res);
        if (error || json.rt_cd !== '0') {
            _showOverseasError(resultDiv, `주문 실패: ${error || json.msg1 || res.status}`);
            return;
        }
        const orderNo = json.data && (json.data.broker_order_no || json.data.ord_no || json.data.ODNO);
        resultDiv.innerHTML = `<p class="success">주문 접수: ${_escapeOverseasHtml(orderNo || '-')}</p>`;
    } catch (e) {
        console.error('[overseas] 통신 오류', e);
        _showOverseasError(resultDiv, e.name === 'AbortError' ? '요청 시간이 초과되었습니다.' : '통신 오류가 발생했습니다.');
    }
}

async function loadOverseasOrders() {
    const exchange = _overseasExchangeValue('overseas-order-exchange');
    const resultDiv = document.getElementById('overseas-orders-result');
    showLoading(resultDiv, '미체결 조회 중...');

    try {
        if (!await _ensureOverseasEnabled()) {
            _showOverseasError(resultDiv, 'overseas_us가 enabled되어 있지 않습니다.');
            return;
        }
        const res = await fetchWithTimeout(`/api/overseas/orders?exchange=${encodeURIComponent(exchange)}&ccld_nccs_dvsn=02`, {}, 12000);
        const { json, error } = await _readOverseasJson(res);
        if (error || json.rt_cd !== '0') {
            _showOverseasError(resultDiv, `미체결 조회 실패: ${error || json.msg1 || res.status}`);
            return;
        }
        const data = json.data || {};
        const rows = Array.isArray(data.output) ? data.output : (Array.isArray(data) ? data : []);
        const body = rows.map(row => {
            const odno = String(row.odno || row.ODNO || '');
            const symbol = String(row.pdno || row.ovrs_pdno || row.symbol || '');
            const orderExchange = String(row.ovrs_excg_cd || exchange);
            const qty = Number(String(row.nccs_qty ?? row.ft_ord_qty ?? row.qty ?? '0').replace(/,/g, '')) || 0;
            return `
                <tr>
                    <td>${_escapeOverseasHtml(odno || '-')}</td>
                    <td>${_escapeOverseasHtml(symbol || '-')}</td>
                    <td>${_escapeOverseasHtml(row.sll_buy_dvsn_cd_name || '-')}</td>
                    <td>${_formatNumber(row.ft_ord_qty || row.qty)}</td>
                    <td>${_formatNumber(qty)}</td>
                    <td>${_formatUsd(row.ft_ord_unpr3 || row.price)}</td>
                    <td><button type="button" class="btn btn-sell" data-overseas-cancel data-order-no="${_escapeOverseasHtml(odno)}" data-symbol="${_escapeOverseasHtml(symbol)}" data-exchange="${_escapeOverseasHtml(orderExchange)}" data-qty="${qty}">취소</button></td>
                </tr>
            `;
        }).join('');
        resultDiv.innerHTML = `
            <div class="card">
                <h3>미체결 주문 <span style="color:#aaa;font-size:0.85rem;">${_escapeOverseasHtml(exchange)} USD</span></h3>
                <table class="data-table">
                    <thead><tr><th>주문번호</th><th>심볼</th><th>구분</th><th>수량</th><th>미체결</th><th>지정가</th><th></th></tr></thead>
                    <tbody>${body || '<tr><td colspan="7">미체결 없음</td></tr>'}</tbody>
                </table>
            </div>
        `;
        resultDiv.querySelectorAll('[data-overseas-cancel]').forEach(button => {
            button.addEventListener('click', () => cancelOverseasOrder(
                button.dataset.orderNo || '',
                button.dataset.symbol || '',
                button.dataset.exchange || '',
                Number(button.dataset.qty || 0),
            ));
        });
    } catch (e) {
        console.error('[overseas] 통신 오류', e);
        _showOverseasError(resultDiv, e.name === 'AbortError' ? '요청 시간이 초과되었습니다.' : '통신 오류가 발생했습니다.');
    }
}

async function cancelOverseasOrder(orderNo, symbol, exchange, qty) {
    if (!orderNo) {
        alert('주문번호가 없습니다.');
        return;
    }
    const resultDiv = document.getElementById('overseas-orders-result');
    let realOrderConfirmation = null;
    if (_overseasIsRealMode()) {
        const step1 = confirm(
            `[실전투자] 미국주식 주문 취소\n\n` +
            `심볼: ${symbol}\n거래소: ${exchange}\n주문번호: ${orderNo}\n수량: ${qty}\n\n` +
            '계속하시겠습니까?'
        );
        if (!step1) return;
        realOrderConfirmation = prompt('최종 확인을 위해 "REAL"을 입력하세요:');
        if (realOrderConfirmation !== 'REAL') {
            alert('확인 문자열이 일치하지 않아 취소 요청이 중단되었습니다.');
            return;
        }
    } else if (!confirm(`미국주식 주문 취소\n심볼: ${symbol}\n주문번호: ${orderNo}\n수량: ${qty}`)) {
        return;
    }

    try {
        if (!await _ensureOverseasEnabled()) {
            _showOverseasError(resultDiv, 'overseas_us가 enabled되어 있지 않습니다.');
            return;
        }
        const res = await fetchWithTimeout('/api/overseas/order/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                symbol,
                exchange,
                original_order_no: orderNo,
                qty,
                currency: 'USD',
                real_order_confirmation: realOrderConfirmation,
            }),
        }, 15000);
        const { json, error } = await _readOverseasJson(res);
        if (error || json.rt_cd !== '0') {
            alert(`취소 실패: ${error || json.msg1 || res.status}`);
            return;
        }
        await loadOverseasOrders();
    } catch (e) {
        console.error('[overseas] 주문 취소 오류', e);
        alert(e.name === 'AbortError' ? '요청 시간이 초과되었습니다.' : '통신 오류가 발생했습니다.');
    }
}

function initOverseasPage() {
    _refreshOverseasRealBanner();
    void _refreshOverseasAvailability();
    if (!window.__overseasRealBannerTimer) {
        window.__overseasRealBannerTimer = setInterval(_refreshOverseasRealBanner, 3000);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    if (window.location.pathname !== '/overseas') return;
    initOverseasPage();
});

document.addEventListener('pjax:ready', (e) => {
    if (e.detail?.path !== '/overseas') return;
    initOverseasPage();
});
