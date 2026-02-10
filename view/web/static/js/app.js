/* view/web/static/js/app.js */

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
            if (btn.dataset.tab === 'marketcap') loadTopMarketCap();
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
        const changeClass = (parseInt(data.prdy_vrss) > 0) ? 'text-red' : (parseInt(data.prdy_vrss) < 0 ? 'text-blue' : '');
        
        resultDiv.innerHTML = `
            <div class="stock-info-box">
                <h3>${data.stck_shrn_iscd || code} (현재가)</h3>
                <p class="price ${changeClass}">${parseInt(data.stck_prpr).toLocaleString()}원</p>
                <p>전일대비: ${data.prdy_vrss}원 (${data.prdy_ctrt}%)</p>
                <p>거래량: ${parseInt(data.acml_vol).toLocaleString()}</p>
                <hr>
                <p>시가: ${data.stck_oprc} | 고가: ${data.stck_hgpr} | 저가: ${data.stck_lwpr}</p>
            </div>
        `;
        
        // 주문 탭의 코드 입력창에도 자동 입력
        document.getElementById('order-code').value = code;

    } catch (e) {
        resultDiv.innerHTML = `<p class="error">오류 발생: ${e}</p>`;
    }
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
        const summary = (json.data.output2 && json.data.output2.length > 0) ? json.data.output2[0] : {};
        // output1: 보유 종목
        const stocks = json.data.output1 || [];

        // [추가됨] 계좌 정보 표시 로직
        const accInfo = json.account_info || { number: '-', type: '-' };
        // '실전투자'일 경우 빨간색(real), 모의투자는 노란색(paper) 뱃지 사용
        const badgeClass = (accInfo.type === '실전투자') ? 'real' : 'paper';

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
                        <th>종목</th>
                        <th>보유수량</th>
                        <th>매입가</th>
                        <th>현재가</th>
                        <th>수익률</th>
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

        let html = `
            <table class="data-table">
            <thead><tr><th>순위</th><th>종목명</th><th>현재가</th><th>등락률</th><th>거래량</th></tr></thead>
            <tbody>
        `;
        json.data.forEach(item => {
            const rate = parseFloat(item.prdy_ctrt || 0);
            const color = rate > 0 ? 'text-red' : (rate < 0 ? 'text-blue' : '');
            html += `
                <tr>
                    <td>${item.data_rank || item.rank || '-'}</td>
                    <td>${item.hts_kor_isnm || item.name}</td>
                    <td>${parseInt(item.stck_prpr || 0).toLocaleString()}</td>
                    <td class="${color}">${rate}%</td>
                    <td>${parseInt(item.acml_vol || 0).toLocaleString()}</td>
                </tr>
            `;
        });
        html += "</tbody></table>";
        div.innerHTML = html;

    } catch (e) {
        div.innerHTML = "오류: " + e;
    }
}

async function loadTopMarketCap() {
    const div = document.getElementById('marketcap-result');
    div.innerHTML = "로딩 중...";
    try {
        const res = await fetch('/api/top-market-cap?limit=20');
        const json = await res.json();
        if (json.rt_cd !== "0") {
            div.innerHTML = `<p class="error">실패: ${json.msg1}</p>`;
            return;
        }
        let html = `
            <table class="data-table">
            <thead><tr><th>순위</th><th>종목명</th><th>코드</th><th>현재가</th></tr></thead>
            <tbody>
        `;
        json.data.forEach((item, idx) => {
            html += `
                <tr>
                    <td>${item.rank || (idx+1)}</td>
                    <td>${item.name}</td>
                    <td><a href="#" onclick="searchStock('${item.code}'); return false;">${item.code}</a></td>
                    <td>${parseInt(item.current_price).toLocaleString()}</td>
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

async function loadVirtualHistory() {
    const summaryBox = document.getElementById('virtual-summary-box');
    const tbody = document.getElementById('virtual-history-body');
    const tabContainer = document.getElementById('virtual-strategy-tabs');
    
    try {
        summaryBox.innerHTML = '<span>데이터 로드 중...</span>';
        
        // 1. 전체 기록 가져오기
        let allVirtualData = [];
        const listRes = await fetch('/api/virtual/history');
        if (listRes.ok) {
            allVirtualData = await listRes.json();
        }

        // ▼ [수정됨] '수동매매'를 기본 전략에 포함시킵니다.
        const defaultStrategies = ['수동매매', 'Momentum', 'VolumeBreakout', 'GapUpPullback'];
        
        // 데이터에 있는 전략 이름 + 기본 전략 이름을 합칩니다 (중복 제거)
        const dataStrategies = allVirtualData ? allVirtualData.map(item => item.strategy) : [];
        const strategies = ['ALL', ...new Set([...defaultStrategies, ...dataStrategies])];

        // 3. 탭 버튼 생성
        tabContainer.innerHTML = strategies.map(strat => 
            `<button class="btn sub-tab-btn" onclick="filterVirtualStrategy('${strat}', this)">${strat}</button>`
        ).join('');

        // 4. 탭 활성화 로직
        // 현재 활성화된 탭이 있으면 유지, 없으면 'ALL' 선택
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

// 전역 스코프에 함수 등록 (onclick에서 접근 가능하도록)
window.filterVirtualStrategy = function(strategyName, btnElement) {
    // 1. 버튼 스타일 활성화
    const buttons = document.querySelectorAll('#virtual-strategy-tabs .sub-tab-btn');
    buttons.forEach(b => b.classList.remove('active'));
    if(btnElement) btnElement.classList.add('active');

    // 2. 데이터 필터링
    let filteredData = allVirtualData;
    if (strategyName !== 'ALL') {
        filteredData = allVirtualData.filter(item => item.strategy === strategyName);
    }

    // 3. 통계 재계산
    const totalTrades = filteredData.length;
    const soldTrades = filteredData.filter(item => item.status === 'SOLD');
    const winTrades = soldTrades.filter(item => item.return_rate > 0).length;
    
    // 승률 & 수익률
    const winRate = soldTrades.length > 0 ? (winTrades / soldTrades.length * 100) : 0;
    const totalReturn = soldTrades.reduce((sum, item) => sum + (item.return_rate || 0), 0);
    const avgReturn = soldTrades.length > 0 ? (totalReturn / soldTrades.length) : 0;

    // 4. 요약 박스 업데이트
    const summaryBox = document.getElementById('virtual-summary-box');
    summaryBox.innerHTML = `
        <div style="font-size: 0.9em; color: #666; margin-bottom: 5px;">[ ${strategyName} 결과 ]</div>
        <strong>거래:</strong> ${totalTrades}건 (완료 ${soldTrades.length}) | 
        <strong>승률:</strong> ${winRate.toFixed(1)}% | 
        <strong>평균수익:</strong> <span class="${avgReturn > 0 ? 'text-red' : (avgReturn < 0 ? 'text-blue' : '')}">
            ${avgReturn.toFixed(2)}%
        </span>
    `;

    // 5. 테이블 업데이트
    const tbody = document.getElementById('virtual-history-body');
    tbody.innerHTML = '';

    if (filteredData.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center">해당 전략의 기록이 없습니다.</td></tr>';
        return;
    }

    filteredData.slice().reverse().forEach(item => { // 최신순 정렬
        const ror = item.return_rate || 0;
        const rorClass = ror > 0 ? 'text-red' : (ror < 0 ? 'text-blue' : '');
        const buyDate = item.buy_date ? item.buy_date.split(' ')[0] : '-';
        const sellDate = item.sell_date ? item.sell_date.split(' ')[0] : '-';

        const row = `
            <tr>
                <td>${item.strategy}</td>
                <td><a href="#" onclick="searchStock('${item.code}'); return false;">${item.code}</a></td>
                <td>
                    <div>${buyDate}</div>
                    <div class="small">${Number(item.buy_price).toLocaleString()}원</div>
                </td>
                <td>
                    <div>${sellDate}</div>
                    <div class="small">${item.sell_price ? Number(item.sell_price).toLocaleString() + '원' : '-'}</div>
                </td>
                <td class="${rorClass}"><strong>${ror.toFixed(2)}%</strong></td>
                <td>
                    <span class="badge ${item.status === 'SOLD' ? 'closed' : 'paper'}">${item.status}</span>
                </td>
            </tr>
        `;
        tbody.insertAdjacentHTML('beforeend', row);
    });
};