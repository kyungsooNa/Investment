/* Investment Web View - Frontend JS */

const API = '/api';

// --- 유틸리티 ---
function formatNumber(n) {
    if (n === null || n === undefined || n === 'N/A') return 'N/A';
    const num = typeof n === 'string' ? parseFloat(n.replace(/,/g, '')) : n;
    if (isNaN(num)) return n;
    return num.toLocaleString('ko-KR');
}

function colorClass(val) {
    if (val === null || val === undefined || val === 'N/A') return '';
    const s = String(val);
    if (s.startsWith('+') || s.startsWith('상') || parseFloat(s) > 0) return 'text-positive';
    if (s.startsWith('-') || s.startsWith('하') || parseFloat(s) < 0) return 'text-negative';
    return 'text-neutral';
}

function showMessage(containerId, msg, type) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = `<div class="message ${type}">${msg}</div>`;
    setTimeout(() => { if (el.firstChild) el.firstChild.remove(); }, 5000);
}

async function fetchApi(url, options = {}) {
    try {
        const resp = await fetch(url, options);
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            throw new Error(err.detail || `HTTP ${resp.status}`);
        }
        return await resp.json();
    } catch (e) {
        console.error('API Error:', e);
        throw e;
    }
}

// --- 탭 네비게이션 ---
function switchTab(tabName) {
    document.querySelectorAll('.nav button').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.section').forEach(sec => sec.classList.remove('active'));
    document.querySelector(`.nav button[data-tab="${tabName}"]`)?.classList.add('active');
    document.getElementById(`section-${tabName}`)?.classList.add('active');
}

// --- 상태 바 업데이트 ---
async function updateStatus() {
    try {
        const data = await fetchApi(`${API}/status`);
        document.getElementById('status-time').textContent = data.current_time || '--';
        const marketBadge = document.getElementById('status-market');
        marketBadge.textContent = data.market_open ? '개장' : '폐장';
        marketBadge.className = `badge ${data.market_open ? 'open' : 'closed'}`;
        const envBadge = document.getElementById('status-env');
        envBadge.textContent = data.env_type;
        envBadge.className = `badge clickable ${data.env_type === '모의투자' ? 'paper' : 'real'}`;
    } catch (e) { /* 무시 */ }
}

// --- 환경 전환 ---
async function toggleEnvironment() {
    const envBadge = document.getElementById('status-env');
    const currentEnv = envBadge.textContent;
    const switchTo = currentEnv === '모의투자';  // 모의→실전, 실전→모의
    const targetName = switchTo ? '실전투자' : '모의투자';

    if (!confirm(`${targetName}로 전환하시겠습니까?`)) return;

    envBadge.textContent = '전환 중...';
    try {
        const resp = await fetchApi(`${API}/environment`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_paper: !switchTo })
        });
        if (resp.success) {
            await updateStatus();
        }
    } catch (e) {
        alert(`환경 전환 실패: ${e.message}`);
        await updateStatus();
    }
}

// --- 현재가 조회 ---
async function searchStock() {
    const code = document.getElementById('stock-code-input').value.trim();
    if (!code) return;
    const resultDiv = document.getElementById('stock-result');
    resultDiv.innerHTML = '<div class="loading">조회 중...</div>';
    try {
        const resp = await fetchApi(`${API}/stock/${code}`);
        if (resp.rt_cd !== '0') {
            resultDiv.innerHTML = `<div class="message error">${resp.msg1}</div>`;
            return;
        }
        const d = resp.data;
        resultDiv.innerHTML = `
            <div class="price-display">
                <div class="price-item">
                    <div class="label">현재가</div>
                    <div class="value">${formatNumber(d.price)}</div>
                </div>
                <div class="price-item">
                    <div class="label">전일대비</div>
                    <div class="value ${colorClass(d.change)}">${d.change} (${d.rate}%)</div>
                </div>
                <div class="price-item">
                    <div class="label">시가</div>
                    <div class="value">${formatNumber(d.open)}</div>
                </div>
                <div class="price-item">
                    <div class="label">고가</div>
                    <div class="value text-positive">${formatNumber(d.high)}</div>
                </div>
                <div class="price-item">
                    <div class="label">저가</div>
                    <div class="value text-negative">${formatNumber(d.low)}</div>
                </div>
                <div class="price-item">
                    <div class="label">거래량</div>
                    <div class="value">${formatNumber(d.volume)}</div>
                </div>
                <div class="price-item">
                    <div class="label">전일종가</div>
                    <div class="value">${formatNumber(d.prev_close)}</div>
                </div>
                <div class="price-item">
                    <div class="label">체결시각</div>
                    <div class="value">${d.time || 'N/A'}</div>
                </div>
            </div>`;
    } catch (e) {
        resultDiv.innerHTML = `<div class="message error">${e.message}</div>`;
    }
}

// --- 계좌 잔고 ---
async function loadBalance() {
    const resultDiv = document.getElementById('balance-result');
    resultDiv.innerHTML = '<div class="loading">조회 중...</div>';
    try {
        const resp = await fetchApi(`${API}/balance`);
        if (resp.rt_cd !== '0') {
            resultDiv.innerHTML = `<div class="message error">${resp.msg1}</div>`;
            return;
        }
        const d = resp.data;
        const output1 = d.output1 || [];
        const output2 = (d.output2 || [])[0] || {};
        let html = `
            <div class="summary-bar">
                <div class="summary-item">
                    <div class="label">예수금</div>
                    <div class="value">${formatNumber(output2.dnca_tot_amt)}</div>
                </div>
                <div class="summary-item">
                    <div class="label">총 평가금액</div>
                    <div class="value">${formatNumber(output2.tot_evlu_amt)}</div>
                </div>
                <div class="summary-item">
                    <div class="label">총 평가손익</div>
                    <div class="value ${colorClass(output2.evlu_pfls_smtl_amt)}">${formatNumber(output2.evlu_pfls_smtl_amt)}</div>
                </div>
                <div class="summary-item">
                    <div class="label">수익률</div>
                    <div class="value ${colorClass(output2.asst_icdc_erng_rt)}">${output2.asst_icdc_erng_rt || '0'}%</div>
                </div>
            </div>`;
        if (output1.length > 0) {
            html += `<table>
                <thead><tr>
                    <th>종목명</th><th>종목코드</th><th>보유수량</th>
                    <th>평균매입가</th><th>현재가</th><th>평가금액</th><th>평가손익</th>
                </tr></thead><tbody>`;
            for (const item of output1) {
                if (parseInt(item.hldg_qty || '0') === 0) continue;
                html += `<tr>
                    <td>${item.prdt_name}</td>
                    <td>${item.pdno}</td>
                    <td>${formatNumber(item.hldg_qty)}</td>
                    <td>${formatNumber(item.pchs_avg_pric)}</td>
                    <td>${formatNumber(item.prpr)}</td>
                    <td>${formatNumber(item.evlu_amt)}</td>
                    <td class="${colorClass(item.evlu_pfls_amt)}">${formatNumber(item.evlu_pfls_amt)}</td>
                </tr>`;
            }
            html += '</tbody></table>';
        } else {
            html += '<p style="color: var(--text-secondary); padding: 20px; text-align: center;">보유 종목 없음</p>';
        }
        resultDiv.innerHTML = html;
    } catch (e) {
        resultDiv.innerHTML = `<div class="message error">${e.message}</div>`;
    }
}

// --- 매수/매도 ---
async function placeOrder(side) {
    const code = document.getElementById('order-code').value.trim();
    const qty = document.getElementById('order-qty').value.trim();
    const price = document.getElementById('order-price').value.trim();
    if (!code || !qty || !price) {
        showMessage('order-result', '종목코드, 수량, 가격을 모두 입력하세요.', 'error');
        return;
    }
    const resultDiv = document.getElementById('order-result');
    resultDiv.innerHTML = '<div class="loading">주문 처리 중...</div>';
    try {
        const resp = await fetchApi(`${API}/order`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code, qty, price, side })
        });
        if (resp.rt_cd === '0') {
            const label = side === 'buy' ? '매수' : '매도';
            resultDiv.innerHTML = `<div class="message success">${label} 주문 성공: ${resp.msg1}</div>`;
        } else {
            resultDiv.innerHTML = `<div class="message error">주문 실패: ${resp.msg1}</div>`;
        }
    } catch (e) {
        resultDiv.innerHTML = `<div class="message error">${e.message}</div>`;
    }
}

// --- 랭킹 ---
async function loadRanking(category) {
    document.querySelectorAll('.ranking-tab').forEach(b => b.classList.remove('active'));
    document.querySelector(`.ranking-tab[data-cat="${category}"]`)?.classList.add('active');

    const resultDiv = document.getElementById('ranking-result');
    resultDiv.innerHTML = '<div class="loading">조회 중...</div>';
    try {
        const resp = await fetchApi(`${API}/ranking/${category}`);
        if (resp.rt_cd !== '0') {
            resultDiv.innerHTML = `<div class="message error">${resp.msg1}</div>`;
            return;
        }
        const items = resp.data || [];
        if (items.length === 0) {
            resultDiv.innerHTML = '<p style="text-align:center; color:var(--text-secondary);">데이터 없음</p>';
            return;
        }
        const isTradingValue = category === 'trading_value';
        const lastColHeader = isTradingValue ? '거래대금' : '거래량';
        let html = `<table>
            <thead><tr>
                <th>순위</th><th>종목명</th><th>현재가</th><th>등락률</th><th>${lastColHeader}</th>
            </tr></thead><tbody>`;
        for (const item of items.slice(0, 30)) {
            const rate = item.prdy_ctrt || '0';
            const lastCol = isTradingValue
                ? formatNumber(item.acml_tr_pbmn)
                : formatNumber(item.acml_vol);
            html += `<tr>
                <td>${item.data_rank || '-'}</td>
                <td>${item.hts_kor_isnm || '-'}</td>
                <td>${formatNumber(item.stck_prpr)}</td>
                <td class="${colorClass(rate)}">${rate}%</td>
                <td>${lastCol}</td>
            </tr>`;
        }
        html += '</tbody></table>';
        resultDiv.innerHTML = html;
    } catch (e) {
        resultDiv.innerHTML = `<div class="message error">${e.message}</div>`;
    }
}

// --- 시가총액 ---
async function loadTopMarketCap() {
    const resultDiv = document.getElementById('marketcap-result');
    resultDiv.innerHTML = '<div class="loading">조회 중...</div>';
    try {
        const resp = await fetchApi(`${API}/top-market-cap?limit=20`);
        if (resp.rt_cd !== '0') {
            resultDiv.innerHTML = `<div class="message error">${resp.msg1}</div>`;
            return;
        }
        const items = resp.data || [];
        let html = `<table>
            <thead><tr>
                <th>순위</th><th>종목명</th><th>종목코드</th><th>현재가</th><th>등락률</th><th>시가총액</th>
            </tr></thead><tbody>`;
        for (const item of items) {
            const rate = item.prdy_ctrt || '0';
            html += `<tr>
                <td>${item.data_rank || '-'}</td>
                <td>${item.hts_kor_isnm || '-'}</td>
                <td>${item.mksc_shrn_iscd || '-'}</td>
                <td>${formatNumber(item.stck_prpr)}</td>
                <td class="${colorClass(rate)}">${rate}%</td>
                <td>${formatNumber(item.stck_avls)}</td>
            </tr>`;
        }
        html += '</tbody></table>';
        resultDiv.innerHTML = html;
    } catch (e) {
        resultDiv.innerHTML = `<div class="message error">${e.message}</div>`;
    }
}

// --- 초기화 ---
document.addEventListener('DOMContentLoaded', () => {
    // 탭 이벤트
    document.querySelectorAll('.nav button[data-tab]').forEach(btn => {
        btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });

    // 현재가 조회 엔터키
    document.getElementById('stock-code-input')?.addEventListener('keydown', e => {
        if (e.key === 'Enter') searchStock();
    });

    // 상태 자동 갱신 (30초)
    updateStatus();
    setInterval(updateStatus, 30000);

    // 기본 탭
    switchTab('stock');
});
