/* view/web/static/js/balance.js — 계좌 잔고 조회 */

let balanceSortState = { key: null, dir: 'asc' };
let balanceStocksCache = [];
let balanceSummaryCache = {};
let balanceAccInfoCache = {};

function renderBalanceTable() {
    const div = document.getElementById('balance-result');
    const summary = balanceSummaryCache;
    const accInfo = balanceAccInfoCache;
    const stocks = [...balanceStocksCache];
    const badgeClass = (accInfo.type === '실전투자') ? 'real' : 'paper';

    if (balanceSortState.key && stocks.length > 1) {
        const key = balanceSortState.key;
        const dir = balanceSortState.dir === 'asc' ? 1 : -1;
        stocks.sort((a, b) => {
            let va, vb;
            if (key === 'prdt_name') {
                va = a.prdt_name || '';
                vb = b.prdt_name || '';
                return dir * va.localeCompare(vb, 'ko');
            } else if (key === 'hldg_qty') {
                va = parseInt(a.hldg_qty || 0);
                vb = parseInt(b.hldg_qty || 0);
            } else if (key === 'pchs_avg_pric') {
                va = parseInt(a.pchs_avg_pric || 0);
                vb = parseInt(b.pchs_avg_pric || 0);
            } else if (key === 'prpr') {
                va = parseInt(a.prpr || 0);
                vb = parseInt(b.prpr || 0);
            } else if (key === 'evlu_pfls_rt') {
                va = parseFloat(a.evlu_pfls_rt || 0);
                vb = parseFloat(b.evlu_pfls_rt || 0);
            }
            return dir * (va - vb);
        });
    }

    const sortClass = (key) => {
        if (balanceSortState.key !== key) return 'sortable';
        return `sortable sort-${balanceSortState.dir}`;
    };

    let html = `
        <div class="card">
        <div class="balance-summary">
            <p>
                <strong>계좌번호:</strong> ${accInfo.number}
                <span class="badge ${badgeClass}" style="margin-left:5px; font-size:0.8em;">${accInfo.type}</span>
            </p>
            <p><strong>총 평가금액:</strong> ${parseInt(summary.tot_evlu_amt || 0).toLocaleString()}원</p>
            <p><strong>예수금:</strong> ${parseInt(summary.dnca_tot_amt || 0).toLocaleString()}원</p>
            <p><strong>평가손익:</strong> ${parseInt(summary.evlu_pfls_smtl_amt || 0).toLocaleString()}원</p>
        </div>
        <table class="data-table">
            <thead>
                <tr>
                    <th class="${sortClass('prdt_name')}" onclick="sortBalance('prdt_name')">종목</th>
                    <th class="${sortClass('hldg_qty')}" onclick="sortBalance('hldg_qty')">보유수량</th>
                    <th class="${sortClass('pchs_avg_pric')}" onclick="sortBalance('pchs_avg_pric')">매입가</th>
                    <th class="${sortClass('prpr')}" onclick="sortBalance('prpr')">현재가</th>
                    <th class="${sortClass('evlu_pfls_rt')}" onclick="sortBalance('evlu_pfls_rt')">수익률</th>
                </tr>
            </thead>
            <tbody>
    `;

    if (stocks.length === 0) {
        html += `<tr><td colspan="5" style="text-align:center;">보유 종목이 없습니다.</td></tr>`;
    } else {
        stocks.forEach(s => {
            const profit = parseFloat(s.evlu_pfls_rt || 0);
            const colorClass = profit > 0 ? 'text-red' : (profit < 0 ? 'text-blue' : '');
            html += `
                <tr>
                    <td>${s.prdt_name}<br><small>(${s.pdno})</small></td>
                    <td>${s.hldg_qty}</td>
                    <td>${parseInt(s.pchs_avg_pric).toLocaleString()}</td>
                    <td>${parseInt(s.prpr).toLocaleString()}</td>
                    <td class="${colorClass}">${profit.toFixed(2)}%</td>
                </tr>
            `;
        });
    }
    html += `</tbody></table></div>`;
    div.innerHTML = html;
}

function sortBalance(key) {
    if (balanceSortState.key === key) {
        balanceSortState.dir = balanceSortState.dir === 'asc' ? 'desc' : 'asc';
    } else {
        balanceSortState.key = key;
        balanceSortState.dir = 'asc';
    }
    renderBalanceTable();
}

async function loadBalance() {
    const div = document.getElementById('balance-result');
    div.innerHTML = "조회 중...";
    try {
        const res = await fetch('/api/balance');
        const json = await res.json();

        if (json.rt_cd !== "0") {
            div.innerHTML = `<p class="error">실패: ${json.msg1}</p>`;
            return;
        }

        balanceSummaryCache = (json.data.output2 && json.data.output2.length > 0) ? json.data.output2[0] : {};
        balanceStocksCache = json.data.output1 || [];
        balanceAccInfoCache = json.account_info || { number: '-', type: '-' };
        balanceSortState = { key: null, dir: 'asc' };

        renderBalanceTable();

    } catch (e) {
        div.innerHTML = `<p class="error">오류: ${e}</p>`;
    }
}
