/* view/web/static/js/app.js */

// ==========================================
// 유틸리티 함수
// ==========================================
function formatTradingValue(val) {
    const num = parseInt(val || '0');
    if (num >= 1e8) return (num / 1e8).toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '억';
    if (num >= 1e4) return (num / 1e4).toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '만';
    return num.toLocaleString();
}

function formatMarketCap(val) {
    // stck_avls는 억원 단위
    const num = parseInt(val || '0');
    if (num >= 10000) {
        const jo = num / 10000;
        return (jo >= 10 ? Math.round(jo).toLocaleString() : jo.toFixed(1)) + '조';
    }
    return num.toLocaleString() + '억';
}

// ==========================================
// 1. 공통/초기화 로직
// ==========================================
document.addEventListener('DOMContentLoaded', () => {
    updateStatus();
    setInterval(updateStatus, 5000); // 5초마다 상태 갱신

    // 탭 전환 이벤트
    const navButtons = document.querySelectorAll('.nav button');
    navButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            // 1) 버튼 활성화 스타일
            navButtons.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            // 2) 섹션 보이기/숨기기
            const targetId = `section-${btn.dataset.tab}`;
            document.querySelectorAll('.section').forEach(sec => sec.classList.remove('active'));
            document.getElementById(targetId).classList.add('active');

            // 3) 탭별 초기 데이터 로드 (필요시)
            if (btn.dataset.tab === 'balance') loadBalance();
            if (btn.dataset.tab === 'ranking') loadRanking('rise'); // 기본값
            if (btn.dataset.tab === 'marketcap') loadTopMarketCap('0001');
            if (btn.dataset.tab === 'virtual') loadVirtualHistory();
        });
    });
});

async function updateStatus() {
    try {
        const res = await fetch('/api/status');
        const data = await res.json();
        
        // 시간
        document.getElementById('status-time').innerText = data.current_time || '--:--:--';
        
        // 시장 상태
        const marketBadge = document.getElementById('status-market');
        if (data.market_open) {
            marketBadge.innerText = "장중";
            marketBadge.className = "badge open";
        } else {
            marketBadge.innerText = "장마감";
            marketBadge.className = "badge closed";
        }

        // 환경 (모의/실전)
        const envBadge = document.getElementById('status-env');
        envBadge.innerText = data.env_type || "Unknown";
        if (data.env_type === "모의투자") {
            envBadge.className = "badge paper clickable";
        } else if (data.env_type === "실전투자") {
            envBadge.className = "badge real clickable";
        } else {
            envBadge.className = "badge closed clickable";
        }

    } catch (e) {
        console.error("Status update failed:", e);
    }
}

async function toggleEnvironment() {
    if (!confirm("거래 환경을 전환하시겠습니까? (서버 재설정)")) return;
    
    // 현재 상태 확인
    const currentText = document.getElementById('status-env').innerText;
    const isCurrentlyPaper = (currentText === "모의투자");
    
    // 반대로 요청
    const targetIsPaper = !isCurrentlyPaper;

    try {
        const res = await fetch('/api/environment', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ is_paper: targetIsPaper })
        });
        const data = await res.json();
        
        if (data.success) {
            alert(`환경이 [${data.env_type}]로 전환되었습니다.`);
            updateStatus();
        } else {
            alert("환경 전환 실패: " + (data.detail || "알 수 없는 오류"));
        }
    } catch(e) {
        alert("요청 중 오류 발생: " + e);
    }
}


// ==========================================
// 2. 주식 조회 / 주문 / 잔고
// ==========================================

// ... (기존 searchStock, loadBalance, placeOrder 함수들은 그대로 유지) ...
async function searchStock(codeOverride) {
    const input = document.getElementById('stock-code-input');
    const code = codeOverride || input.value.trim();
    if (!code) {
        alert("종목코드를 입력하세요.");
        return;
    }
    
    // 인풋창 업데이트 (링크 클릭 시)
    input.value = code;

    const resultDiv = document.getElementById('stock-result');
    resultDiv.innerHTML = "조회 중...";

    try {
        const res = await fetch(`/api/stock/${code}`);
        const json = await res.json();
        
        if (json.rt_cd !== "0") {
            resultDiv.innerHTML = `<p class="error">조회 실패: ${json.msg1} (${json.rt_cd})</p>`;
            return;
        }

        const data = json.data;
        const changeVal = parseInt(data.change) || 0;
        const changeClass = (changeVal > 0) ? 'text-red' : (changeVal < 0 ? 'text-blue' : '');

        resultDiv.innerHTML = `
            <div class="stock-info-box">
                <h3>${data.code || code} (현재가)</h3>
                <p class="price ${changeClass}">${parseInt(data.price).toLocaleString()}원</p>
                <p>전일대비: ${data.change}원 (${data.rate}%)</p>
                <p>거래량: ${parseInt(data.volume).toLocaleString()}</p>
                <hr>
                <p>시가: ${data.open} | 고가: ${data.high} | 저가: ${data.low}</p>
            </div>
        `;
        
        // 주문 탭의 코드 입력창에도 자동 입력
        document.getElementById('order-code').value = code;

    } catch (e) {
        resultDiv.innerHTML = `<p class="error">오류 발생: ${e}</p>`;
    }
}

// 계좌잔고 정렬 상태
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

    // 정렬 적용
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
    html += `</tbody></table>`;
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

        // output2: 계좌 요약
        balanceSummaryCache = (json.data.output2 && json.data.output2.length > 0) ? json.data.output2[0] : {};
        // output1: 보유 종목
        balanceStocksCache = json.data.output1 || [];

        // [추가됨] 계좌 정보 표시 로직
        balanceAccInfoCache = json.account_info || { number: '-', type: '-' };

        // 정렬 상태 초기화
        balanceSortState = { key: null, dir: 'asc' };

        renderBalanceTable();

    } catch (e) {
        div.innerHTML = `<p class="error">오류: ${e}</p>`;
    }
}

async function placeOrder(side) {
    const code = document.getElementById('order-code').value;
    const qty = document.getElementById('order-qty').value;
    const price = document.getElementById('order-price').value;

    if(!code || !qty || !price) {
        alert("모든 필드를 입력하세요.");
        return;
    }
    if(!confirm(`${side === 'buy' ? '매수' : '매도'} 주문하시겠습니까?\n종목: ${code}\n수량: ${qty}\n가격: ${price}`)) {
        return;
    }

    const resDiv = document.getElementById('order-result');
    resDiv.innerHTML = "주문 전송 중...";

    try {
        const res = await fetch('/api/order', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ code, qty, price, side })
        });
        const json = await res.json();
        
        if (json.rt_cd === "0") {
            resDiv.innerHTML = `<p class="success">주문 성공! (주문번호: ${json.data.ord_no})</p>`;
        } else {
            resDiv.innerHTML = `<p class="error">주문 실패: ${json.msg1}</p>`;
        }
    } catch (e) {
        resDiv.innerHTML = `<p class="error">통신 오류: ${e}</p>`;
    }
}

// ==========================================
// 3. 랭킹 & 시가총액
// ==========================================

async function loadRanking(category) {
    // 탭 스타일
    document.querySelectorAll('.ranking-tab').forEach(b => {
        b.classList.remove('active');
        if (b.dataset.cat === category) b.classList.add('active');
    });

    const div = document.getElementById('ranking-result');
    div.innerHTML = "로딩 중...";

    try {
        const res = await fetch(`/api/ranking/${category}`);
        const json = await res.json();
        
        if (json.rt_cd !== "0") {
            div.innerHTML = `<p class="error">실패: ${json.msg1}</p>`;
            return;
        }

        const isTradingValue = category === 'trading_value';
        const lastColHeader = isTradingValue ? '거래대금' : '거래량';

        let html = `
            <table class="data-table">
            <thead><tr><th>순위</th><th>종목명</th><th>현재가</th><th>등락률</th><th>${lastColHeader}</th></tr></thead>
            <tbody>
        `;
        json.data.forEach(item => {
            const rate = parseFloat(item.prdy_ctrt || 0);
            const color = rate > 0 ? 'text-red' : (rate < 0 ? 'text-blue' : '');
            const lastCol = isTradingValue
                ? formatTradingValue(item.acml_tr_pbmn)
                : parseInt(item.acml_vol || 0).toLocaleString();
            html += `
                <tr>
                    <td>${item.data_rank || item.rank || '-'}</td>
                    <td>${item.hts_kor_isnm || item.name}</td>
                    <td>${parseInt(item.stck_prpr || 0).toLocaleString()}</td>
                    <td class="${color}">${rate}%</td>
                    <td>${lastCol}</td>
                </tr>
            `;
        });
        html += "</tbody></table>";
        div.innerHTML = html;

    } catch (e) {
        div.innerHTML = "오류: " + e;
    }
}

async function loadTopMarketCap(market = '0001') {
    // 버튼 active 상태 전환
    document.querySelectorAll('#section-marketcap .ranking-tab').forEach(b => {
        b.classList.remove('active');
        if (b.dataset.market === market) b.classList.add('active');
    });

    const div = document.getElementById('marketcap-result');
    div.innerHTML = "로딩 중...";
    try {
        const res = await fetch(`/api/top-market-cap?limit=30&market=${market}`);
        const json = await res.json();
        if (json.rt_cd !== "0") {
            div.innerHTML = `<p class="error">실패: ${json.msg1}</p>`;
            return;
        }
        let html = `
            <table class="data-table">
            <thead><tr><th>순위</th><th>종목명</th><th>코드</th><th>현재가</th><th>시가총액</th></tr></thead>
            <tbody>
        `;
        json.data.forEach((item, idx) => {
            const rate = parseFloat(item.change_rate || 0);
            const color = rate > 0 ? 'text-red' : (rate < 0 ? 'text-blue' : '');
            const rateStr = rate > 0 ? `+${rate}%` : `${rate}%`;
            html += `
                <tr>
                    <td>${item.rank || (idx+1)}</td>
                    <td>${item.name}</td>
                    <td><a href="#" onclick="searchStock('${item.code}'); return false;">${item.code}</a></td>
                    <td>${parseInt(item.current_price).toLocaleString()} <small class="${color}">(${rateStr})</small></td>
                    <td>${formatMarketCap(item.market_cap)}</td>
                </tr>
            `;
        });
        html += "</tbody></table>";
        div.innerHTML = html;
    } catch(e) {
        div.innerHTML = "오류: " + e;
    }
}

// ==========================================
// 4. 모의투자 (Virtual Trading)
// ==========================================
let allVirtualData = [];
let dailyChanges = {};
let weeklyChanges = {};

async function loadVirtualHistory() {
    const summaryBox = document.getElementById('virtual-summary-box');
    const tabContainer = document.getElementById('virtual-strategy-tabs');

    // 탭 컨테이너가 없으면(HTML 반영 전이면) 중단
    if (!tabContainer) return;

    try {
        summaryBox.innerHTML = '<span>데이터 로드 중...</span>';

        // 1. 데이터 가져오기
        const listRes = await fetch('/api/virtual/history');
        console.log('[Virtual] response status:', listRes.status);
        if (listRes.ok) {
            const body = await listRes.json();
            allVirtualData = body.trades || [];
            dailyChanges = body.daily_changes || {};
            weeklyChanges = body.weekly_changes || {};
            console.log('[Virtual] data count:', allVirtualData.length, 'sample:', allVirtualData[0]);
        } else {
            const errText = await listRes.text();
            console.error('[Virtual] API error:', listRes.status, errText);
            allVirtualData = [];
            dailyChanges = {};
            weeklyChanges = {};
        }

        // 2. 탭 버튼 목록 생성
        // '수동매매'는 항상 보이게 하고, 나머지는 데이터에서 추출
        const defaultStrategies = ['수동매매'];
        const dataStrategies = allVirtualData.map(item => item.strategy);
        const strategies = ['ALL', ...new Set([...defaultStrategies, ...dataStrategies])];

        // 3. 버튼 HTML 생성 (CSS 클래스: sub-tab-btn 사용)
        tabContainer.innerHTML = strategies.map(strat => 
            `<button class="sub-tab-btn" onclick="filterVirtualStrategy('${strat}', this)">${strat}</button>`
        ).join('');

        // 4. 초기 탭 선택 (기존 선택 유지 또는 ALL)
        const currentActive = document.querySelector('#virtual-strategy-tabs .sub-tab-btn.active');
        if (currentActive) {
            filterVirtualStrategy(currentActive.innerText, currentActive);
        } else {
            const allBtn = tabContainer.querySelector('button');
            if (allBtn) filterVirtualStrategy('ALL', allBtn);
        }

    } catch (e) {
        console.error("Virtual history error:", e);
        summaryBox.innerText = "데이터 로드 실패";
    }
}

// 보유일 계산 유틸
function calcDaysHeld(buyDateStr, endDateStr) {
    if (!buyDateStr) return '-';
    const buy = new Date(buyDateStr.split(' ')[0]);
    const end = endDateStr ? new Date(endDateStr.split(' ')[0]) : new Date();
    const diff = Math.floor((end - buy) / (1000 * 60 * 60 * 24));
    return diff;
}

// 종목 표시명
function stockLabel(item) {
    const name = item.stock_name || '';
    return name ? `${name}(${item.code})` : item.code;
}

// 전역 함수로 등록 (onclick에서 호출 가능하도록)
window.filterVirtualStrategy = function(strategyName, btnElement) {
    // 1. 버튼 스타일 업데이트
    const buttons = document.querySelectorAll('#virtual-strategy-tabs .sub-tab-btn');
    buttons.forEach(b => b.classList.remove('active'));
    if(btnElement) btnElement.classList.add('active');

    // 2. 데이터 필터링
    let filteredData = allVirtualData;
    if (strategyName !== 'ALL') {
        filteredData = allVirtualData.filter(item => item.strategy === strategyName);
    }

    const holdData = filteredData.filter(item => item.status === 'HOLD');
    const soldData = filteredData.filter(item => item.status === 'SOLD');

    // 3. 통계 계산
    const totalTrades = filteredData.length;
    // 누적 수익률: 전체 trades의 return_rate 평균
    const totalReturn = filteredData.reduce((sum, item) => sum + (item.return_rate || 0), 0);
    const cumulativeReturn = totalTrades > 0 ? (totalReturn / totalTrades) : 0;
    // 전일대비 / 전주대비: 백엔드 스냅샷 기반
    const dailyChange = dailyChanges[strategyName] ?? cumulativeReturn;
    const weeklyChange = weeklyChanges[strategyName];

    // 색상 헬퍼
    const colorClass = (val) => val > 0 ? 'text-positive' : (val < 0 ? 'text-negative' : '');
    const signPrefix = (val) => val > 0 ? '+' : '';

    // 4. 요약 박스
    const summaryBox = document.getElementById('virtual-summary-box');
    if (!summaryBox) { console.error('[Virtual] virtual-summary-box not found'); return; }
    summaryBox.innerHTML = `
        <div style="margin-bottom: 15px; margin-top: 5px;">
            <div style="background-color: #000000 !important; color: #ffffff !important; padding: 6px 18px; border-radius: 20px; border: 1.5px solid #e94560; display: inline-block; box-shadow: 0 2px 6px rgba(0,0,0,0.3);">
                <span style="color: #e94560; margin-right: 6px; font-size: 1.1em;">📊</span>
                <span style="font-size: 1.05em; font-weight: 700 !important; letter-spacing: 0.5px;">[ ${strategyName} 성과 요약 ]</span>
            </div>
        </div>
        <div style="display: flex; justify-content: center; align-items: center; gap: 12px; flex-wrap: wrap;">
            <div style="background-color: #000000 !important; color: #ffffff !important; padding: 12px 18px; border-radius: 10px; border: 1px solid #30363d; min-width: 125px; box-shadow: 0 4px 8px rgba(0,0,0,0.4);">
                <div style="font-size: 0.85em; color: #a0a0b0 !important; margin-bottom: 4px; font-weight: 600;">총 거래</div>
                <div style="color: #ffffff !important;"><strong style="font-size: 1.35em;">${totalTrades}</strong> <span style="font-size: 1em;">건</span></div>
            </div>
            <div style="background-color: #000000 !important; color: #ffffff !important; padding: 12px 18px; border-radius: 10px; border: 1px solid #30363d; min-width: 125px; box-shadow: 0 4px 8px rgba(0,0,0,0.4);">
                <div style="font-size: 0.85em; color: #a0a0b0 !important; margin-bottom: 4px; font-weight: 600;">누적 수익률</div>
                <strong class="${colorClass(cumulativeReturn)}" style="font-size: 1.35em; font-weight: 800 !important;">
                    ${signPrefix(cumulativeReturn)}${cumulativeReturn.toFixed(2)}%
                </strong>
            </div>
            <div style="background-color: #000000 !important; color: #ffffff !important; padding: 12px 18px; border-radius: 10px; border: 1px solid #30363d; min-width: 125px; box-shadow: 0 4px 8px rgba(0,0,0,0.4);">
                <div style="font-size: 0.85em; color: #a0a0b0 !important; margin-bottom: 4px; font-weight: 600;">전일대비</div>
                <strong class="${colorClass(dailyChange)}" style="font-size: 1.35em; font-weight: 800 !important;">
                    ${signPrefix(dailyChange)}${dailyChange.toFixed(2)}%
                </strong>
            </div>
            <div style="background-color: #000000 !important; color: #ffffff !important; padding: 12px 18px; border-radius: 10px; border: 1px solid #30363d; min-width: 125px; box-shadow: 0 4px 8px rgba(0,0,0,0.4);">
                <div style="font-size: 0.85em; color: #a0a0b0 !important; margin-bottom: 4px; font-weight: 600;">전주대비</div>
                <strong class="${weeklyChange != null ? colorClass(weeklyChange) : ''}" style="font-size: 1.35em; font-weight: 800 !important;">
                    ${weeklyChange != null ? signPrefix(weeklyChange) + weeklyChange.toFixed(2) + '%' : '-'}
                </strong>
            </div>
        </div>
    `;

    // 5. 보유 중 테이블
    const holdBody = document.getElementById('virtual-hold-body');
    if (!holdBody) { console.error('[Virtual] virtual-hold-body not found'); return; }
    holdBody.innerHTML = '';
    if (holdData.length === 0) {
        holdBody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:15px;">보유 종목이 없습니다.</td></tr>';
    } else {
        holdData.forEach(item => {
            const ror = item.return_rate || 0;
            const rorClass = ror > 0 ? 'text-positive' : (ror < 0 ? 'text-negative' : '');
            const buyDate = item.buy_date ? item.buy_date.split(' ')[0] : '-';
            const buyPrice = Number(item.buy_price).toLocaleString();
            const curPrice = item.current_price ? Number(item.current_price).toLocaleString() : '-';
            const days = calcDaysHeld(item.buy_date, null);

            holdBody.insertAdjacentHTML('beforeend', `
                <tr>
                    <td><a href="#" onclick="searchStock('${item.code}'); return false;" style="color:var(--accent); text-decoration:none;">${stockLabel(item)}</a></td>
                    <td>${buyPrice}</td>
                    <td>${curPrice}</td>
                    <td class="${rorClass}"><strong>${ror.toFixed(2)}%</strong></td>
                    <td>${days}일<div style="font-size:0.8em; color:var(--text-secondary);">${buyDate}</div></td>
                </tr>
            `);
        });
    }

    // 6. 매도 완료 테이블
    const soldBody = document.getElementById('virtual-sold-body');
    if (!soldBody) { console.error('[Virtual] virtual-sold-body not found'); return; }
    soldBody.innerHTML = '';
    if (soldData.length === 0) {
        soldBody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:15px;">매도 기록이 없습니다.</td></tr>';
    } else {
        soldData.slice().reverse().forEach(item => {
            const ror = item.return_rate || 0;
            const rorClass = ror > 0 ? 'text-positive' : (ror < 0 ? 'text-negative' : '');
            const buyDate = item.buy_date ? item.buy_date.split(' ')[0] : '-';
            const sellDate = item.sell_date ? item.sell_date.split(' ')[0] : '-';
            const buyPrice = Number(item.buy_price).toLocaleString();
            const sellPrice = (item.sell_price != null && item.sell_price > 0) ? Number(item.sell_price).toLocaleString() : '-';
            const curPrice = item.current_price ? Number(item.current_price).toLocaleString() : '';
            const days = calcDaysHeld(item.buy_date, item.sell_date);

            soldBody.insertAdjacentHTML('beforeend', `
                <tr>
                    <td><a href="#" onclick="searchStock('${item.code}'); return false;" style="color:var(--accent); text-decoration:none;">${stockLabel(item)}</a></td>
                    <td>${buyPrice}</td>
                    <td>${curPrice ? curPrice + '<div style="font-size:0.8em; color:var(--text-secondary);">' + sellPrice + '</div>' : sellPrice}</td>
                    <td class="${rorClass}"><strong>${ror.toFixed(2)}%</strong></td>
                    <td>${days}일<div style="font-size:0.8em; color:var(--text-secondary);">${buyDate} ~ ${sellDate}</div></td>
                </tr>
            `);
        });
    }
};

// ==========================================
// 7. 프로그램매매 실시간
// ==========================================
let ptEventSource = null;
let ptRowCount = 0;
let ptSubscribedCodes = new Set();

async function addProgramTrading() {
    const input = document.getElementById('pt-code-input');
    const code = input.value.trim();
    if (!code) { alert('종목코드를 입력하세요.'); return; }
    if (ptSubscribedCodes.has(code)) { alert('이미 구독 중인 종목입니다.'); return; }

    const statusDiv = document.getElementById('pt-status');
    statusDiv.style.display = 'block';
    statusDiv.innerHTML = '<span>구독 요청 중...</span>';

    try {
        const res = await fetch('/api/program-trading/subscribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code })
        });
        const json = await res.json();
        if (!json.success) {
            statusDiv.innerHTML = '<span class="text-red">구독 실패</span>';
            return;
        }

        ptSubscribedCodes.add(code);
        renderPtChips();
        input.value = '';

        // SSE 연결 (최초 1회)
        if (!ptEventSource) {
            ptEventSource = new EventSource('/api/program-trading/stream');
            ptEventSource.onmessage = (event) => {
                const d = JSON.parse(event.data);
                appendProgramTradingRow(d);
            };
            ptEventSource.onerror = () => {
                statusDiv.innerHTML = '<span class="text-red">SSE 연결 끊김</span>';
            };
        }

        statusDiv.innerHTML = `<span class="text-green">구독 중: ${ptSubscribedCodes.size}개 종목</span>`;
    } catch (e) {
        statusDiv.innerHTML = '<span class="text-red">오류: ' + e + '</span>';
    }
}

async function removeProgramTrading(code) {
    try {
        await fetch('/api/program-trading/unsubscribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code })
        });
    } catch (e) { /* ignore */ }

    ptSubscribedCodes.delete(code);
    renderPtChips();

    const statusDiv = document.getElementById('pt-status');
    if (ptSubscribedCodes.size === 0) {
        if (ptEventSource) { ptEventSource.close(); ptEventSource = null; }
        statusDiv.innerHTML = '<span>구독 중지됨</span>';
    } else {
        statusDiv.innerHTML = `<span class="text-green">구독 중: ${ptSubscribedCodes.size}개 종목</span>`;
    }
}

async function stopAllProgramTrading() {
    if (ptEventSource) {
        ptEventSource.close();
        ptEventSource = null;
    }
    try {
        await fetch('/api/program-trading/unsubscribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });
    } catch (e) { /* ignore */ }

    ptSubscribedCodes.clear();
    renderPtChips();
    document.getElementById('pt-status').innerHTML = '<span>구독 중지됨</span>';
}

function renderPtChips() {
    const container = document.getElementById('pt-subscribed-list');
    container.innerHTML = '';
    for (const code of ptSubscribedCodes) {
        const chip = document.createElement('span');
        chip.style.cssText = 'display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:12px;background:var(--neutral);font-size:0.8rem;font-weight:600;';
        chip.innerHTML = `${code} <span style="cursor:pointer;color:var(--negative);font-weight:bold;" onclick="removeProgramTrading('${code}')">&times;</span>`;
        container.appendChild(chip);
    }
}

function appendProgramTradingRow(d) {
    const tbody = document.getElementById('pt-body');
    const time = d['주식체결시간'] || '';
    const fmtTime = time.length >= 6 ? time.slice(0,2)+':'+time.slice(2,4)+':'+time.slice(4,6) : time;
    const ntby = parseInt(d['순매수체결량'] || '0');
    const ntbyColor = ntby > 0 ? 'text-red' : (ntby < 0 ? 'text-blue' : '');

    const row = `<tr>
        <td>${d['유가증권단축종목코드'] || '-'}</td>
        <td>${fmtTime}</td>
        <td>${parseInt(d['매도체결량'] || 0).toLocaleString()}</td>
        <td>${parseInt(d['매수2체결량'] || 0).toLocaleString()}</td>
        <td class="${ntbyColor}">${ntby.toLocaleString()}</td>
        <td>${formatTradingValue(d['순매수거래대금'])}</td>
        <td>${parseInt(d['매도호가잔량'] || 0).toLocaleString()}</td>
        <td>${parseInt(d['매수호가잔량'] || 0).toLocaleString()}</td>
    </tr>`;

    tbody.insertAdjacentHTML('afterbegin', row);
    ptRowCount++;
    // 최대 200행 유지
    if (ptRowCount > 200) {
        tbody.removeChild(tbody.lastElementChild);
        ptRowCount--;
    }
}
