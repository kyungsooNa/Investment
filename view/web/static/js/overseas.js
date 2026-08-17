/* view/web/static/js/overseas.js — 미국주식 조회/주문 */

let _overseasMarketCapSequence = 0;

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
    const n = Number(String(value ?? '').replace(/,/g, ''));
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
    // 나머지 화면과 동일한 조/억 표기를 위해 공용 formatTradingValue 를 사용한다.
    return formatTradingValue(n);
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
    const tabs = ['overview', 'marketcap', 'favorite', 'orders'];
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
    if (tabName === 'favorite') await loadOverseasFavorites();
    // 잔고·미체결은 브로커 호출이라 버튼으로 두지만, 원장은 로컬 DB 읽기라 바로 보여준다.
    if (tabName === 'orders') await loadOverseasTrades();
}

function _initialOverseasTab() {
    if (window.location.pathname === '/overseas-favorite') return 'favorite';
    return 'overview';
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
            showError(resultDiv, 'overseas_us가 enabled되어 있지 않습니다.');
            return;
        }
        const res = await fetchWithTimeout(`/api/overseas/stock/${encodeURIComponent(symbol)}?exchange=${encodeURIComponent(exchange)}`, {}, 12000);
        const { json, error } = await readJsonResponse(res);
        if (error || json.rt_cd !== '0') {
            showError(resultDiv, `조회 실패: ${error || json.msg1 || res.status}`);
            return;
        }
        const data = json.data || {};
        const rate = Number(data.change_rate || 0);
        const rateClass = rate > 0 ? 'text-red' : (rate < 0 ? 'text-blue' : '');
        resultDiv.innerHTML = `
            <div class="card">
                <h3>${escapeHtml(data.symbol || symbol)} <span style="color:#aaa;font-size:0.85rem;">${escapeHtml(data.exchange || exchange)} ${escapeHtml(data.currency || 'USD')}</span></h3>
                <p class="price ${rateClass}">${_formatUsd(data.price)}</p>
                <p>등락률: <span class="${rateClass}">${Number.isFinite(rate) ? rate.toFixed(2) + '%' : '-'}</span></p>
                <p>거래량: ${_formatNumber(data.volume)}</p>
                <p>시각: ${escapeHtml(data.timestamp || '-')}</p>
            </div>
        `;
    } catch (e) {
        console.error('[overseas] 통신 오류', e);
        showError(resultDiv, e.name === 'AbortError' ? '요청 시간이 초과되었습니다.' : '통신 오류가 발생했습니다.');
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
            showError(resultDiv, 'overseas_us가 enabled되어 있지 않습니다.');
            return;
        }
        const res = await fetchWithTimeout(`/api/overseas/chart/${encodeURIComponent(symbol)}?exchange=${encodeURIComponent(exchange)}&period=D`, {}, 12000);
        const { json, error } = await readJsonResponse(res);
        if (error || json.rt_cd !== '0') {
            showError(resultDiv, `일봉 조회 실패: ${error || json.msg1 || res.status}`);
            return;
        }
        const data = json.data || {};
        const list = Array.isArray(data) ? data : (Array.isArray(data.output2) ? data.output2 : []);
        const body = list.slice(-10).reverse().map(row => `
            <tr>
                <td>${escapeHtml(row.date || row.xymd || row.stck_bsop_date || '-')}</td>
                <td>${_formatUsd(row.close || row.clos || row.clpr || row.ovrs_nmix_prpr)}</td>
                <td>${_formatNumber(row.volume || row.tvol || row.acml_vol)}</td>
            </tr>
        `).join('');
        resultDiv.innerHTML = `
            <div class="card">
                <h3>${escapeHtml(symbol)} 일봉</h3>
                <table class="data-table">
                    <thead><tr><th>일자</th><th>종가</th><th>거래량</th></tr></thead>
                    <tbody>${body || '<tr><td colspan="3">데이터 없음</td></tr>'}</tbody>
                </table>
            </div>
        `;
    } catch (e) {
        console.error('[overseas] 통신 오류', e);
        showError(resultDiv, e.name === 'AbortError' ? '요청 시간이 초과되었습니다.' : '통신 오류가 발생했습니다.');
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
            showError(resultDiv, 'overseas_us가 enabled되어 있지 않습니다.');
            return;
        }
        const res = await fetchWithTimeout('/api/overseas/market-cap', {}, 12000);
        const { json, error } = await readJsonResponse(res);
        if (!isLatestRequest()) return;
        if (error || json.rt_cd !== '0') {
            showError(resultDiv, `시가총액 조회 실패: ${error || json.msg1 || res.status}`);
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
                <td>${escapeHtml(item.symbol || '-')}</td>
                <td>${escapeHtml(item.name || '-')}</td>
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
        showError(resultDiv, e.name === 'AbortError' ? '요청 시간이 초과되었습니다.' : '시가총액을 불러오지 못했습니다.');
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
            showError(resultDiv, 'overseas_us가 enabled되어 있지 않습니다.');
            return;
        }
        const res = await fetchWithTimeout(`/api/overseas/balance?exchange=${encodeURIComponent(exchange)}&currency=USD`, {}, 12000);
        const { json, error } = await readJsonResponse(res);
        if (error || json.rt_cd !== '0') {
            showError(resultDiv, `잔고 조회 실패: ${error || json.msg1 || res.status}`);
            return;
        }
        const data = json.data || {};
        const rows = Array.isArray(data.output1) ? data.output1 : (Array.isArray(data) ? data : []);
        const body = rows.map(row => `
            <tr>
                <td>${escapeHtml(row.ovrs_pdno || row.pdno || row.symbol || '-')}</td>
                <td>${escapeHtml(row.ovrs_item_name || row.prdt_name || row.name || '-')}</td>
                <td>${_formatNumber(row.ovrs_cblc_qty || row.hldg_qty || row.qty)}</td>
                <td>${_formatUsd(row.now_pric2 || row.price || row.ovrs_now_pric1)}</td>
            </tr>
        `).join('');
        resultDiv.innerHTML = `
            <div class="card">
                <h3>미국주식 잔고 <span style="color:#aaa;font-size:0.85rem;">${escapeHtml(exchange)} USD</span></h3>
                <table class="data-table">
                    <thead><tr><th>심볼</th><th>이름</th><th>수량</th><th>현재가</th></tr></thead>
                    <tbody>${body || '<tr><td colspan="4">보유 없음</td></tr>'}</tbody>
                </table>
            </div>
        `;
    } catch (e) {
        console.error('[overseas] 통신 오류', e);
        showError(resultDiv, e.name === 'AbortError' ? '요청 시간이 초과되었습니다.' : '통신 오류가 발생했습니다.');
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
            showError(resultDiv, 'overseas_us가 enabled되어 있지 않습니다.');
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
        const { json, error } = await readJsonResponse(res);
        if (error || json.rt_cd !== '0') {
            showError(resultDiv, `주문 실패: ${error || json.msg1 || res.status}`);
            return;
        }
        const orderNo = json.data && (json.data.broker_order_no || json.data.ord_no || json.data.ODNO);
        resultDiv.innerHTML = `<p class="success">주문 접수: ${escapeHtml(orderNo || '-')}</p>`;
    } catch (e) {
        console.error('[overseas] 통신 오류', e);
        showError(resultDiv, e.name === 'AbortError' ? '요청 시간이 초과되었습니다.' : '통신 오류가 발생했습니다.');
    }
}

async function loadOverseasOrders() {
    const exchange = _overseasExchangeValue('overseas-order-exchange');
    const resultDiv = document.getElementById('overseas-orders-result');
    showLoading(resultDiv, '미체결 조회 중...');

    try {
        if (!await _ensureOverseasEnabled()) {
            showError(resultDiv, 'overseas_us가 enabled되어 있지 않습니다.');
            return;
        }
        const res = await fetchWithTimeout(`/api/overseas/orders?exchange=${encodeURIComponent(exchange)}&ccld_nccs_dvsn=02`, {}, 12000);
        const { json, error } = await readJsonResponse(res);
        if (error || json.rt_cd !== '0') {
            showError(resultDiv, `미체결 조회 실패: ${error || json.msg1 || res.status}`);
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
                    <td>${escapeHtml(odno || '-')}</td>
                    <td>${escapeHtml(symbol || '-')}</td>
                    <td>${escapeHtml(row.sll_buy_dvsn_cd_name || '-')}</td>
                    <td>${_formatNumber(row.ft_ord_qty || row.qty)}</td>
                    <td>${_formatNumber(qty)}</td>
                    <td>${_formatUsd(row.ft_ord_unpr3 || row.price)}</td>
                    <td><button type="button" class="btn btn-sell" data-overseas-cancel data-order-no="${escapeHtml(odno)}" data-symbol="${escapeHtml(symbol)}" data-exchange="${escapeHtml(orderExchange)}" data-qty="${qty}">취소</button></td>
                </tr>
            `;
        }).join('');
        resultDiv.innerHTML = `
            <div class="card">
                <h3>미체결 주문 <span style="color:#aaa;font-size:0.85rem;">${escapeHtml(exchange)} USD</span></h3>
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
        showError(resultDiv, e.name === 'AbortError' ? '요청 시간이 초과되었습니다.' : '통신 오류가 발생했습니다.');
    }
}

// USD 거래 원장 — 국내 /api/virtual/* 과 분리돼 있다(통화가 달라 같은 표에 섞을 수 없다).
// 원장은 주문 접수 시점 기록이라 미체결도 일단 보유로 잡히고, 체결 대사에서 미체결로
// 확인된 lot 은 지워지지 않고 CANCELED 로 남는다 — 보유와 구분돼 보여야 오해가 없다.
const OVERSEAS_TRADE_STATUS_LABELS = { HOLD: '보유', SOLD: '청산', CANCELED: '취소' };

function _overseasTradeStatusLabel(status) {
    return OVERSEAS_TRADE_STATUS_LABELS[String(status || '')] || String(status || '-');
}

function _overseasReturnCell(trade) {
    if (String(trade.status) !== 'SOLD') return '-';
    const rate = Number(trade.return_rate) || 0;
    // 국내 관례(상승=빨강)를 따른다.
    const color = rate > 0 ? '#e74c3c' : (rate < 0 ? '#3498db' : '');
    return `<span style="color:${color}">${rate.toFixed(2)}%</span>`;
}

async function loadOverseasTrades() {
    const resultDiv = document.getElementById('overseas-trades-result');
    if (!resultDiv) return;
    showLoading(resultDiv, '거래 기록 조회 중...');

    try {
        if (!await _ensureOverseasEnabled()) {
            showError(resultDiv, 'overseas_us가 enabled되어 있지 않습니다.');
            return;
        }
        const res = await fetchWithTimeout('/api/overseas/trades', {}, 12000);
        const { json, error } = await readJsonResponse(res);
        if (error || json.rt_cd !== '0') {
            showError(resultDiv, `거래 기록 조회 실패: ${error || json.msg1 || res.status}`);
            return;
        }
        const data = json.data || {};
        const summary = data.summary || {};
        const trades = Array.isArray(data.trades) ? data.trades : [];

        const canceled = Number(summary.canceled_trades) || 0;
        // 취소분은 total 에서 이미 빠져 있다(성과 분모 오염 방지) — 별도로만 밝힌다.
        const canceledNote = canceled > 0
            ? ` <span style="color:#aaa;">· 대사 취소 ${canceled}건(집계 제외)</span>`
            : '';
        const body = trades.map(trade => `
            <tr${String(trade.status) === 'CANCELED' ? ' style="opacity:0.55;"' : ''}>
                <td>${escapeHtml(trade.symbol || '-')}</td>
                <td>${escapeHtml(trade.exchange || '-')}</td>
                <td>${escapeHtml(trade.buy_date || '-')}</td>
                <td>${_formatUsd(trade.buy_price)}</td>
                <td>${_formatNumber(trade.qty)}</td>
                <td>${escapeHtml(trade.sell_date || '-')}</td>
                <td>${trade.sell_price ? _formatUsd(trade.sell_price) : '-'}</td>
                <td>${_overseasReturnCell(trade)}</td>
                <td>${escapeHtml(_overseasTradeStatusLabel(trade.status))}</td>
            </tr>
        `).join('');
        resultDiv.innerHTML = `
            <div class="card">
                <h3>USD 성과 요약 <span style="color:#aaa;font-size:0.85rem;">비용 0.25%/side 반영</span></h3>
                <p>총 ${_formatNumber(summary.total_trades)}건 · 청산 ${_formatNumber(summary.sold_trades)}건
                   · 승률 ${Number(summary.win_rate) || 0}% · 평균 수익률 ${Number(summary.avg_return) || 0}%${canceledNote}</p>
                <table class="data-table">
                    <thead><tr><th>심볼</th><th>거래소</th><th>매수일</th><th>매수가</th><th>수량</th><th>매도일</th><th>매도가</th><th>수익률</th><th>상태</th></tr></thead>
                    <tbody>${body || '<tr><td colspan="9">거래 기록이 없습니다.</td></tr>'}</tbody>
                </table>
            </div>
        `;
    } catch (e) {
        console.error('[overseas] 통신 오류', e);
        showError(resultDiv, e.name === 'AbortError' ? '요청 시간이 초과되었습니다.' : '통신 오류가 발생했습니다.');
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
            showError(resultDiv, 'overseas_us가 enabled되어 있지 않습니다.');
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
        const { json, error } = await readJsonResponse(res);
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

/* ── 미국장 즐겨찾기 ── */

let _overseasFavoriteData = [];

function _initOverseasFavoriteAutocomplete() {
    if (typeof StockAutocomplete !== 'function') return;
    if (!document.getElementById('overseas-fav-symbol')) return;

    StockAutocomplete({
        inputId: 'overseas-fav-symbol',
        listId: 'overseas-fav-autocomplete-list',
        valueKey: 's',
        readyEvent: 'all-overseas-stocks-ready',
        getInitial: () => window.ALL_OVERSEAS_STOCKS || null,
        searchImpl: _overseasStockSearch,
        formatItem: stock => `${stock.s} — ${stock.n} (${stock.e})`,
        onSelect: symbol => {
            const input = document.getElementById('overseas-fav-symbol');
            if (input) input.value = symbol;
        },
        onConfirm: () => { void addOverseasFavorite(); },
    });
}

function _buildOverseasFavoriteRow(item) {
    const rate = Number(item.rate);
    const rateClass = rate > 0 ? 'price-up' : (rate < 0 ? 'price-down' : '');
    const rateStr = Number.isFinite(rate) ? `${rate > 0 ? '+' : ''}${rate.toFixed(2)}%` : '-';
    const symbol = escapeHtml(item.code || '');
    return `<tr>
        <td style="font-weight:bold;">${symbol}</td>
        <td>${escapeHtml(item.name || '-')}</td>
        <td>${escapeHtml(item.exchange || '-')}</td>
        <td class="${rateClass}">${_formatUsd(item.price)}</td>
        <td class="${rateClass}">${rateStr}</td>
        <td><button type="button" class="btn btn-sm" data-overseas-fav-remove data-symbol="${symbol}">삭제</button></td>
    </tr>`;
}

function renderOverseasFavoriteTable() {
    const tbody = document.getElementById('overseas-favorite-body');
    if (!tbody) return;

    if (!_overseasFavoriteData.length) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:var(--text-secondary);">등록된 미국장 즐겨찾기가 없습니다.</td></tr>';
        return;
    }

    tbody.innerHTML = _overseasFavoriteData.map(_buildOverseasFavoriteRow).join('');
    tbody.querySelectorAll('[data-overseas-fav-remove]').forEach(button => {
        button.addEventListener('click', () => removeOverseasFavorite(button.dataset.symbol || ''));
    });
}

async function loadOverseasFavorites() {
    const tbody = document.getElementById('overseas-favorite-body');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;">로딩 중...</td></tr>';

    try {
        const res = await fetchWithTimeout('/api/favorite?market=overseas_us', {}, 12000);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        _overseasFavoriteData = await res.json() || [];
        renderOverseasFavoriteTable();
    } catch (e) {
        console.error('[overseas] 즐겨찾기 조회 오류', e);
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:#ff6b6b;">불러오기 실패: ${escapeHtml(e.message)}</td></tr>`;
    }
}

async function addOverseasFavorite() {
    const input = document.getElementById('overseas-fav-symbol');
    const symbol = _overseasSymbolValue('overseas-fav-symbol');
    if (!symbol) { showToast('심볼을 입력하세요.', 'error'); return; }

    try {
        const res = await fetchWithTimeout(
            `/api/favorite/${encodeURIComponent(symbol)}?market=overseas_us`,
            { method: 'POST' },
            12000,
        );
        const json = await res.json();
        if (!res.ok) throw new Error(json.detail || `HTTP ${res.status}`);
        if (!json.added) { showToast('이미 등록된 종목입니다.', 'error'); return; }

        if (input) input.value = '';
        showToast('미국장 즐겨찾기에 추가되었습니다.', 'success');
        await loadOverseasFavorites();
    } catch (e) {
        showToast(`추가 실패: ${e.message}`, 'error');
    }
}

async function removeOverseasFavorite(symbol) {
    if (!symbol) return;
    try {
        const res = await fetchWithTimeout(
            `/api/favorite/${encodeURIComponent(symbol)}?market=overseas_us`,
            { method: 'DELETE' },
            12000,
        );
        const json = await res.json();
        if (!res.ok) throw new Error(json.detail || `HTTP ${res.status}`);

        _overseasFavoriteData = _overseasFavoriteData.filter(item => item.code !== symbol);
        renderOverseasFavoriteTable();
        showToast('미국장 즐겨찾기에서 삭제되었습니다.', 'success');
    } catch (e) {
        showToast(`삭제 실패: ${e.message}`, 'error');
    }
}

function initOverseasPage() {
    _refreshOverseasRealBanner();
    void _refreshOverseasAvailability();
    _initOverseasFavoriteAutocomplete();
    const initialTab = _initialOverseasTab();
    if (initialTab !== 'overview') void setOverseasTab(initialTab);
    if (!window.__overseasRealBannerTimer) {
        window.__overseasRealBannerTimer = setInterval(_refreshOverseasRealBanner, 3000);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    if (!['/overseas', '/overseas-favorite'].includes(window.location.pathname)) return;
    initOverseasPage();
});

document.addEventListener('pjax:ready', (e) => {
    if (!['/overseas', '/overseas-favorite'].includes(e.detail?.path)) return;
    initOverseasPage();
});
