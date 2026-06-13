/* view/web/static/js/overseas.js — 해외주식 수동 조회/주문 */

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
    if (!Number.isFinite(n)) return value === undefined || value === null ? '-' : String(value);
    return '$' + n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function _formatNumber(value) {
    const n = Number(String(value ?? '').replace(/,/g, ''));
    if (!Number.isFinite(n)) return value === undefined || value === null || value === '' ? '-' : String(value);
    return n.toLocaleString();
}

function _setOverseasOrderSymbol(symbol, exchange) {
    const symbolInput = document.getElementById('overseas-order-symbol');
    const exchangeSelect = document.getElementById('overseas-order-exchange');
    if (symbolInput && symbol) symbolInput.value = symbol;
    if (exchangeSelect && exchange) exchangeSelect.value = exchange;
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
        const enabled = await _ensureOverseasEnabled();
        if (!enabled) {
            resultDiv.innerHTML = '<p class="error">overseas_us가 enabled되어 있지 않습니다.</p>';
            return;
        }
        const res = await fetchWithTimeout(`/api/overseas/stock/${encodeURIComponent(symbol)}?exchange=${exchange}`, {}, 12000);
        const json = await res.json();
        if (!res.ok || json.rt_cd !== '0') {
            resultDiv.innerHTML = `<p class="error">조회 실패: ${json.msg1 || res.status}</p>`;
            return;
        }
        const data = json.data || {};
        const rate = Number(data.change_rate || 0);
        const rateClass = rate > 0 ? 'text-red' : (rate < 0 ? 'text-blue' : '');
        resultDiv.innerHTML = `
            <div class="card">
                <h3>${data.symbol || symbol} <span style="color:#aaa;font-size:0.85rem;">${data.exchange || exchange} ${data.currency || 'USD'}</span></h3>
                <p class="price ${rateClass}">${_formatUsd(data.price)}</p>
                <p>등락률: <span class="${rateClass}">${Number.isFinite(rate) ? rate.toFixed(2) + '%' : '-'}</span></p>
                <p>거래량: ${_formatNumber(data.volume)}</p>
                <p>시각: ${data.timestamp || '-'}</p>
            </div>
        `;
    } catch (e) {
        resultDiv.innerHTML = `<p class="error">통신 오류: ${e.message || e}</p>`;
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
        const enabled = await _ensureOverseasEnabled();
        if (!enabled) {
            resultDiv.innerHTML = '<p class="error">overseas_us가 enabled되어 있지 않습니다.</p>';
            return;
        }
        const res = await fetchWithTimeout(`/api/overseas/chart/${encodeURIComponent(symbol)}?exchange=${exchange}&period=D`, {}, 12000);
        const json = await res.json();
        if (!res.ok || json.rt_cd !== '0') {
            resultDiv.innerHTML = `<p class="error">일봉 조회 실패: ${json.msg1 || res.status}</p>`;
            return;
        }
        // KIS 해외 일봉은 원본 body(dict)를 그대로 반환하므로 output2 배열을 꺼낸다.
        // (이미 배열로 정규화된 형태도 하위호환으로 허용)
        const data = json.data || {};
        const list = Array.isArray(data)
            ? data
            : (Array.isArray(data.output2) ? data.output2 : []);
        const rows = list.slice(-10).reverse();
        const body = rows.map((row) => `
            <tr>
                <td>${row.date || row.xymd || row.stck_bsop_date || '-'}</td>
                <td>${_formatUsd(row.close || row.clos || row.clpr || row.ovrs_nmix_prpr)}</td>
                <td>${_formatNumber(row.volume || row.tvol || row.acml_vol)}</td>
            </tr>
        `).join('');
        resultDiv.innerHTML = `
            <div class="card">
                <h3>${symbol} 일봉</h3>
                <table>
                    <thead><tr><th>일자</th><th>종가</th><th>거래량</th></tr></thead>
                    <tbody>${body || '<tr><td colspan="3">데이터 없음</td></tr>'}</tbody>
                </table>
            </div>
        `;
    } catch (e) {
        resultDiv.innerHTML = `<p class="error">통신 오류: ${e.message || e}</p>`;
    }
}

async function loadOverseasBalance() {
    const exchange = _overseasExchangeValue('overseas-exchange');
    const resultDiv = document.getElementById('overseas-balance-result');
    showLoading(resultDiv, '잔고 조회 중...');

    try {
        const enabled = await _ensureOverseasEnabled();
        if (!enabled) {
            resultDiv.innerHTML = '<p class="error">overseas_us가 enabled되어 있지 않습니다.</p>';
            return;
        }
        const res = await fetchWithTimeout(`/api/overseas/balance?exchange=${exchange}&currency=USD`, {}, 12000);
        const json = await res.json();
        if (!res.ok || json.rt_cd !== '0') {
            resultDiv.innerHTML = `<p class="error">잔고 조회 실패: ${json.msg1 || res.status}</p>`;
            return;
        }
        const data = json.data || {};
        const rows = Array.isArray(data.output1) ? data.output1 : (Array.isArray(data) ? data : []);
        const body = rows.map((row) => `
            <tr>
                <td>${row.ovrs_pdno || row.pdno || row.symbol || '-'}</td>
                <td>${row.ovrs_item_name || row.prdt_name || row.name || '-'}</td>
                <td>${_formatNumber(row.ovrs_cblc_qty || row.hldg_qty || row.qty)}</td>
                <td>${_formatUsd(row.now_pric2 || row.price || row.ovrs_now_pric1)}</td>
            </tr>
        `).join('');
        resultDiv.innerHTML = `
            <div class="card">
                <h3>해외 잔고 <span style="color:#aaa;font-size:0.85rem;">${exchange} USD</span></h3>
                <table>
                    <thead><tr><th>심볼</th><th>이름</th><th>수량</th><th>현재가</th></tr></thead>
                    <tbody>${body || '<tr><td colspan="4">보유 없음</td></tr>'}</tbody>
                </table>
            </div>
        `;
    } catch (e) {
        resultDiv.innerHTML = `<p class="error">통신 오류: ${e.message || e}</p>`;
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
            `[실전투자] 해외 ${sideKr} 주문\n\n` +
            `심볼: ${symbol}\n거래소: ${exchange}\n수량: ${qty}\n지정가: ${limitPrice}\n\n` +
            `계속하시겠습니까?`
        );
        if (!step1) return;
        realOrderConfirmation = prompt('최종 확인을 위해 "REAL"을 입력하세요:');
        if (realOrderConfirmation !== 'REAL') {
            alert('확인 문자열이 일치하지 않아 주문이 취소되었습니다.');
            return;
        }
    } else if (!confirm(`해외 ${sideKr} 주문\n심볼: ${symbol}\n수량: ${qty}\n지정가: ${limitPrice}`)) {
        return;
    }

    showLoading(resultDiv, '주문 전송 중...');
    try {
        const enabled = await _ensureOverseasEnabled();
        if (!enabled) {
            resultDiv.innerHTML = '<p class="error">overseas_us가 enabled되어 있지 않습니다.</p>';
            return;
        }
        const res = await fetchWithTimeout('/api/overseas/order', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                symbol,
                exchange,
                side,
                qty,
                limit_price: limitPrice,
                currency: 'USD',
                real_order_confirmation: realOrderConfirmation
            })
        }, 15000);
        const json = await res.json();
        if (!res.ok || json.rt_cd !== '0') {
            resultDiv.innerHTML = `<p class="error">주문 실패: ${json.msg1 || res.status}</p>`;
            return;
        }
        const orderNo = json.data && (json.data.broker_order_no || json.data.ord_no || json.data.ODNO);
        resultDiv.innerHTML = `<p class="success">주문 접수: ${orderNo || '-'}</p>`;
    } catch (e) {
        resultDiv.innerHTML = `<p class="error">통신 오류: ${e.message || e}</p>`;
    }
}

function initOverseasPage() {
    _refreshOverseasRealBanner();
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
