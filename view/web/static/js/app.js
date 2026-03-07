/* view/web/static/js/app.js */

// ==========================================
// 유틸리티 함수
// ==========================================
function formatTradingValue(val, isMillion = false) {
    let num = parseInt(val || '0');
    if (isMillion) num *= 1000000;
    const abs = Math.abs(num);
    const sign = num < 0 ? '-' : '';
    if (abs >= 1e8) return sign + (abs / 1e8).toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '억';
    if (abs >= 1e4) return sign + (abs / 1e4).toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '만';
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

function ensureTableInCard(table) {
    if (table && !table.parentElement.classList.contains('card')) {
        const card = document.createElement('div');
        card.className = 'card';
        table.parentNode.insertBefore(card, table);
        card.appendChild(table);
    }
}

// ==========================================
// 1. 공통/초기화 로직
// ==========================================
function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `<span>${type === 'success' ? '✅' : '❌'}</span> <span>${message}</span>`;

    container.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('fade-out');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

document.addEventListener('DOMContentLoaded', () => {
    // [Theme] 항상 light-mode로 설정 (Dark 테마 제거)
    document.body.classList.add('light-mode');

    updateStatus();
    setInterval(updateStatus, 5000); // 5초마다 상태 갱신

    // [수정] 모의투자 데이터 자동 갱신 (5분마다)
    // 가만히 있을 때는 5분 주기로 업데이트하여 API 할당량을 보존합니다.
    setInterval(() => {
        const virtualSection = document.getElementById('section-virtual');
        if (virtualSection && virtualSection.classList.contains('active')) {
            loadVirtualHistory();
        }
    }, 300000);
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
    
    input.value = code;

    const resultDiv = document.getElementById('stock-result');
    const chartCard = document.getElementById('stock-chart-card');

    // [추가] 차트 카드 대피 (innerHTML 덮어쓰기 방지)
    // stock-result 내부 내용을 갱신하기 전에 차트 카드를 안전한 곳(원래 부모)으로 이동
    const sectionStock = document.getElementById('section-stock');
    if (chartCard && sectionStock && chartCard.parentElement !== sectionStock) {
        sectionStock.appendChild(chartCard);
        chartCard.style.display = 'none';
    }

    resultDiv.innerHTML = "조회 중...";

    try {
        const res = await fetch(`/api/stock/${code}`);
        if (!res.ok) {
            const errorText = await res.text();
            console.error("Server error response:", errorText);
            resultDiv.innerHTML = `<p class="error">조회 실패: 서버 오류 (HTTP ${res.status})</p>`;
            if(chartCard) chartCard.style.display = 'none';
            return;
        }

        const json = await res.json();
        
        if (json.rt_cd !== "0") {
            resultDiv.innerHTML = `<p class="error">조회 실패: ${json.msg1} (${json.rt_cd})</p>`;
            if(chartCard) chartCard.style.display = 'none';
            return;
        }

        const data = json.data;

        // Helper functions
        const fnum = (n, suffix = "") => {
            if (n === null || n === undefined || String(n).trim() === '' || String(n).toLowerCase() === 'n/a') return 'N/A';
            try {
                const val = parseFloat(String(n).replace(/,/g, ''));
                if (isNaN(val)) return n;
                return val.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 }) + suffix;
            } catch { return n; }
        };
        const frate = (n, suffix = "%") => {
            if (n === null || n === undefined || String(n).trim() === '' || String(n).toLowerCase() === 'n/a') return 'N/A';
            try {
                const val = parseFloat(String(n).replace(/,/g, ''));
                if (isNaN(val)) return n;
                return (val > 0 ? '+' : '') + val.toFixed(2) + suffix;
            } catch { return n; }
        };

        const changeVal = parseInt(data.change) || 0;
        const changeClass = (changeVal > 0) ? 'text-red' : (changeVal < 0 ? 'text-blue' : '');
        const sign = data.sign || '';
        const newHighBadge = (data.is_new_high) ? '<span class="badge new-high">🔥 신고가</span>' : '';
        const newLowBadge = (data.is_new_low) ? '<span class="badge new-low">💧 신저가</span>' : '';

        // Inject CSS for layout
        const styles = `
            <style>
                .stock-title { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
                .badge.new-high { 
                    background-color: #ff6b35; 
                    color: white; 
                    font-size: 0.8em;
                    padding: 0.2em 0.6em;
                    border-radius: 10px;
                    font-weight: bold;
                    white-space: nowrap;
                }
                .badge.new-low { 
                    background-color: #1e90ff; 
                    color: white; 
                    font-size: 0.8em;
                    padding: 0.2em 0.6em;
                    border-radius: 10px;
                    font-weight: bold;
                    white-space: nowrap;
                }
                .stock-info-box .stock-details {
                    display: grid;
                    grid-template-columns: 1fr 1fr;
                    gap: 1rem;
                    margin-top: 1.5rem;
                }
                .stock-info-box .detail-group {
                    background-color: var(--background-light);
                    border: 1px solid var(--border-color);
                    border-radius: 8px;
                    padding: 1rem;
                }
                .stock-info-box .detail-group.full-width {
                    grid-column: 1 / -1;
                }
                .stock-info-box .detail-group h4 {
                    margin-top: 0;
                    margin-bottom: 0.8rem;
                    border-bottom: 2px solid var(--accent);
                    padding-bottom: 0.4rem;
                    color: var(--text-primary);
                }
                .stock-info-box .detail-group p {
                    margin: 0.4rem 0;
                    display: flex;
                    justify-content: space-between;
                }
                 .stock-info-box .detail-group p strong {
                    color: var(--text-secondary);
                 }
                 .stock-info-box .price.text-red { color: #e94560; }
                 .stock-info-box .price.text-blue { color: #1e90ff; }
            </style>
        `;

        resultDiv.innerHTML = styles + `
            <div class="card stock-info-box">
                <h3 class="stock-title">${data.name} (${data.code}) ${newHighBadge}${newLowBadge}</h3>
                <p class="price ${changeClass}">${fnum(data.price, '원')}</p>
                <p class="change-rate">전일대비: ${sign}${fnum(data.change_absolute || Math.abs(data.change))} (${frate(data.rate)})</p>
                
                <div id="chart-placeholder" style="margin: 16px 0;"></div>

                <div class="stock-details">
                    <div class="detail-group">
                        <h4>ℹ️ 기본 정보</h4>
                        <p><strong>업종:</strong> <span>${data.bstp_kor_isnm || 'N/A'}</span></p>
                        <p><strong>상태:</strong> <span>${data.iscd_stat_cls_code_desc || 'N/A'}</span></p>
                    </div>
                    <div class="detail-group">
                        <h4>📊 당일 시세</h4>
                        <p><strong>시가:</strong> <span>${fnum(data.open)}</span></p>
                        <p><strong>고가:</strong> <span>${fnum(data.high)}</span></p>
                        <p><strong>저가:</strong> <span>${fnum(data.low)}</span></p>
                        <p><strong>기준가:</strong> <span>${fnum(data.prev_close)}</span></p>
                    </div>
                    <div class="detail-group">
                        <h4>📈 거래 정보</h4>
                        <p><strong>누적 거래량:</strong> <span>${fnum(data.acml_vol, ' 주')}</span></p>
                        <p><strong>누적 거래대금:</strong> <span>${formatTradingValue(data.acml_tr_pbmn)}</span></p>
                        <p><strong>전일 대비 거래량:</strong> <span>${frate(data.prdy_vrss_vol_rate)}</span></p>
                    </div>
                    <div class="detail-group">
                        <h4>🌐 수급 정보</h4>
                        <p><strong>외국인 순매수:</strong> <span>${fnum(data.frgn_ntby_qty, ' 주')}</span></p>
                        <p><strong>프로그램 순매수:</strong> <span>${fnum(data.pgtr_ntby_qty, ' 주')}</span></p>
                    </div>
                     <div class="detail-group full-width">
                        <h4>💹 투자 지표</h4>
                        <div style="display: flex; justify-content: space-around;">
                           <p style="flex-direction: column; align-items: center;"><strong>PER:</strong> <span>${frate(data.per, ' 배')}</span></p>
                           <p style="flex-direction: column; align-items: center;"><strong>PBR:</strong> <span>${frate(data.pbr, ' 배')}</span></p>
                           <p style="flex-direction: column; align-items: center;"><strong>EPS:</strong> <span>${fnum(data.eps)}</span></p>
                           <p style="flex-direction: column; align-items: center;"><strong>BPS:</strong> <span>${fnum(data.bps)}</span></p>
                        </div>
                    </div>
                    <div class="detail-group full-width">
                        <h4>📅 주요 가격 정보</h4>
                        <p><strong>52주 최고:</strong> <span>${fnum(data.w52_hgpr)} (${data.w52_hgpr_date}) | 대비: ${frate(data.w52_hgpr_vrss_prpr_ctrt)}</span></p>
                        <p><strong>52주 최저:</strong> <span>${fnum(data.w52_lwpr)} (${data.w52_lwpr_date}) | 대비: ${frate(data.w52_lwpr_vrss_prpr_ctrt)}</span></p>
                        <p><strong>250일 최고:</strong> <span>${fnum(data.d250_hgpr)} (${data.d250_hgpr_date}) | 대비: ${frate(data.d250_hgpr_vrss_prpr_rate)}</span></p>
                        <p><strong>250일 최저:</strong> <span>${fnum(data.d250_lwpr)} (${data.d250_lwpr_date}) | 대비: ${frate(data.d250_lwpr_vrss_prpr_rate)}</span></p>
                    </div>
                    <div class="detail-group full-width">
                        <h4>📋 기타 상태</h4>
                        <p><strong>신용 가능:</strong> <span>${data.crdt_able_yn}</span></p>
                        <p><strong>관리 종목:</strong> <span>${data.mang_issu_cls_code}</span></p>
                        <p><strong>단기 과열:</strong> <span>${data.short_over_yn}</span></p>
                        <p><strong>정리 매매:</strong> <span>${data.sltr_yn}</span></p>
                    </div>
                </div>
            </div>
        `;
        
        // [추가] 차트 카드를 원하는 위치(placeholder)로 이동
        const placeholder = document.getElementById('chart-placeholder');
        if (chartCard && placeholder) {
            placeholder.appendChild(chartCard);
        }

        const orderCodeInput = document.getElementById('order-code');
        if (orderCodeInput) {
            orderCodeInput.value = code;
        }
        
        // [추가] 차트 로드 및 렌더링
        loadAndRenderStockChart(code);

    } catch (e) {
        console.error("Error in searchStock:", e);
        resultDiv.innerHTML = `<p class="error">오류 발생: ${e.message}</p>`;
        if(chartCard) chartCard.style.display = 'none';
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
            if (typeof invalidateVirtualChartCache === 'function') invalidateVirtualChartCache();
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

// 외국인 랭킹 자동 폴링 타이머
let _rankingPollTimer = null;
let _rankingCurrentCategory = null;
// 시장 필터 상태 (향후 NXT 확장용)
let _rankingMarketFilter = 'KRX';

function setMarketFilter(market, btn) {
    _rankingMarketFilter = market;
    document.querySelectorAll('.market-filter').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    // 현재 카테고리 재조회
    if (_rankingCurrentCategory) loadRanking(_rankingCurrentCategory);
}

async function loadRanking(category) {
    // 폴링 타이머 초기화
    if (_rankingPollTimer) {
        clearTimeout(_rankingPollTimer);
        _rankingPollTimer = null;
    }
    _rankingCurrentCategory = category;

    // 탭 스타일
    document.querySelectorAll('.ranking-tab').forEach(b => {
        b.classList.remove('active');
        if (b.dataset.cat === category) b.classList.add('active');
    });

    const div = document.getElementById('ranking-result');
    div.innerHTML = "로딩 중...";

    const isInvestor = ['foreign_buy', 'foreign_sell', 'inst_buy', 'inst_sell', 'prsn_buy', 'prsn_sell'].includes(category);
    const isProgram = ['program_buy', 'program_sell'].includes(category);

    try {
        const res = await fetch(`/api/ranking/${category}`);
        const json = await res.json();

        if (json.rt_cd !== "0") {
            div.innerHTML = `<p class="error">실패: ${json.msg1}</p>`;
            return;
        }

        // 데이터 미준비 시 처리
        if (!json.data || json.data.length === 0) {
            const isPaperBlock = json.msg1 && json.msg1.includes('실전투자 전용');
            if (isPaperBlock) {
                // 모의투자 모드 → 폴링 없이 안내만 표시
                div.innerHTML = `<div class="card" style="text-align:center; padding:40px;">
                    <p style="font-size:1.2em;">${json.msg1}</p>
                </div>`;
            } else {
                // 데이터 수집 중 → 진행률 폴링
                _startProgressPolling(category, div);
            }
            return;
        }

        const isTradingValue = category === 'trading_value';

        // 투자자 카테고리별 필드 매핑
        const pbmnField = {
            'foreign_buy': 'frgn_ntby_tr_pbmn', 'foreign_sell': 'frgn_ntby_tr_pbmn',
            'inst_buy': 'orgn_ntby_tr_pbmn', 'inst_sell': 'orgn_ntby_tr_pbmn',
            'prsn_buy': 'prsn_ntby_tr_pbmn', 'prsn_sell': 'prsn_ntby_tr_pbmn',
            'program_buy': 'whol_smtn_ntby_tr_pbmn', 'program_sell': 'whol_smtn_ntby_tr_pbmn',
        };
        const qtyField = {
            'foreign_buy': 'frgn_ntby_qty', 'foreign_sell': 'frgn_ntby_qty',
            'inst_buy': 'orgn_ntby_qty', 'inst_sell': 'orgn_ntby_qty',
            'prsn_buy': 'prsn_ntby_qty', 'prsn_sell': 'prsn_ntby_qty',
            'program_buy': 'whol_smtn_ntby_qty', 'program_sell': 'whol_smtn_ntby_qty',
        };

        // 모든 카테고리 공통: 순위|종목명|현재가|등락률|거래대금|거래량
        const headerRow = (isInvestor || isProgram)
            ? `<th>순위</th><th>종목명</th><th>현재가</th><th>등락률</th><th>순매수대금</th><th>순매수량</th>`
            : isTradingValue
                ? `<th>순위</th><th>종목명</th><th>현재가</th><th>등락률</th><th>거래대금</th>`
                : `<th>순위</th><th>종목명</th><th>현재가</th><th>등락률</th><th>거래량</th>`;

        let html = `
            <div class="card">
            <table class="data-table">
            <thead><tr>${headerRow}</tr></thead>
            <tbody>
        `;
        json.data.forEach(item => {
            const rate = parseFloat(item.prdy_ctrt || 0);
            const color = rate > 0 ? 'text-red' : (rate < 0 ? 'text-blue' : '');
            let extraCols;
            if (isInvestor) {
                const pbmnVal = formatTradingValue(item[pbmnField[category]], true);
                const qtyVal = parseInt(item[qtyField[category]] || 0).toLocaleString();
                extraCols = `<td>${pbmnVal}</td><td>${qtyVal}</td>`;
            } else if (isProgram) {
                const pbmnVal = formatTradingValue(item[pbmnField[category]]);
                const qtyVal = parseInt(item[qtyField[category]] || 0).toLocaleString();
                extraCols = `<td>${pbmnVal}</td><td>${qtyVal}</td>`;
            } else if (isTradingValue) {
                extraCols = `<td>${formatTradingValue(item.acml_tr_pbmn)}</td>`;
            } else {
                extraCols = `<td>${parseInt(item.acml_vol || 0).toLocaleString()}</td>`;
            }
            html += `
                <tr>
                    <td>${item.data_rank || item.rank || '-'}</td>
                    <td>${item.hts_kor_isnm || item.name}</td>
                    <td>${parseInt(item.stck_prpr || 0).toLocaleString()}</td>
                    <td class="${color}">${rate}%</td>
                    ${extraCols}
                </tr>
            `;
        });
        html += "</tbody></table></div>";
        div.innerHTML = html;

    } catch (e) {
        div.innerHTML = "오류: " + e;
    }
}

function _formatElapsed(sec) {
    if (sec < 60) return `${sec.toFixed(1)}s`;
    const m = Math.floor(sec / 60);
    const s = (sec % 60).toFixed(1);
    return `${m}m ${s}s`;
}

function _startProgressPolling(category, div) {
    div.innerHTML = `<div class="card" style="text-align:center; padding:40px;">
        <p style="font-size:1.2em;">데이터 수집 중...</p>
        <p id="ranking-progress-text" style="color:#888; margin-top:8px;">전체 종목을 순회하여 랭킹을 생성하고 있습니다. 잠시만 기다려주세요.</p>
    </div>`;

    const poll = async () => {
        if (_rankingCurrentCategory !== category) return;
        try {
            const res = await fetch('/api/ranking/progress');
            const p = await res.json();
            const el = document.getElementById('ranking-progress-text');
            if (el && p.total > 0) {
                const pct = (p.processed / p.total * 100).toFixed(1);
                el.textContent = `${p.processed}/${p.total} — ${pct}% | 수집: ${p.collected} | 소요: ${_formatElapsed(p.elapsed)}`;
            }
            if (!p.running && p.processed > 0 && p.processed >= p.total) {
                // 완료 → 데이터 다시 로드
                loadRanking(category);
                return;
            }
        } catch (_) { /* ignore */ }
        _rankingPollTimer = setTimeout(poll, 2000);
    };
    _rankingPollTimer = setTimeout(poll, 1000);
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
            <div class="card">
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
        html += "</tbody></table></div>";
        div.innerHTML = html;
    } catch(e) {
        div.innerHTML = "오류: " + e;
    }
}

// ==========================================
// 4. 모의투자 (Virtual Trading)
// ==========================================
let allVirtualData = [];
let summaryAgg = {};
let cumulativeReturns = {};
let virtualCounts = {};
let dailyChanges = {};
let weeklyChanges = {};
let dailyRefDates = {};
let weeklyRefDates = {};
let firstDates = {};
let virtualHoldSortState = { key: null, dir: 'asc' };
let virtualSoldSortState = { key: null, dir: 'asc' };
let selectedVirtualStrategies = new Set(['ALL']); // 멀티셀렉트 상태
let allVirtualStrategies = []; // 전체 전략 목록 (ALL 제외)
let currentVirtualHoldData = [];
let currentVirtualSoldData = [];

async function loadVirtualHistory(forceCode = null) {
    const summaryBox = document.getElementById('virtual-summary-box');
    const tabContainer = document.getElementById('virtual-strategy-tabs');

    // 탭 컨테이너가 없으면(HTML 반영 전이면) 중단
    if (!tabContainer) return;

    try {
        summaryBox.innerHTML = '<span>데이터 로드 중...</span>';

        // [추가] 비용 적용 체크박스 확인
        const applyCostEl = document.getElementById('apply-cost-chk');
        const applyCost = applyCostEl ? applyCostEl.checked : false;

        // 1. 데이터 가져오기
        let url = '/api/virtual/history';
        const params = [];
        if (forceCode) params.push(`force_code=${forceCode}`);
        if (applyCost) params.push(`apply_cost=true`);
        
        if (params.length > 0) url += '?' + params.join('&');

        const listRes = await fetch(url);
        console.log('[Virtual] response status:', listRes.status);
        if (listRes.ok) {
            const body = await listRes.json();
            allVirtualData = body.trades || [];
            summaryAgg = body.summary_agg || {};
            cumulativeReturns = body.cumulative_returns || {};
            virtualCounts = body.counts || {};
            dailyChanges = body.daily_changes || {};
            weeklyChanges = body.weekly_changes || {};
            dailyRefDates = body.daily_ref_dates || {};
            weeklyRefDates = body.weekly_ref_dates || {};
            firstDates = body.first_dates || {};
            console.log('[Virtual] data count:', allVirtualData.length, 'sample:', allVirtualData[0]);
        } else {
            const errText = await listRes.text();
            console.error('[Virtual] API error:', listRes.status, errText);
            allVirtualData = [];
            summaryAgg = {};
            cumulativeReturns = {};
            virtualCounts = {};
            dailyChanges = {};
            weeklyChanges = {};
            dailyRefDates = {};
            weeklyRefDates = {};
            firstDates = {};
        }

        // 2. 탭 버튼 목록 생성
        // '수동매매'는 항상 보이게 하고, 나머지는 데이터에서 추출
        const defaultStrategies = ['수동매매'];
        const dataStrategies = allVirtualData.map(item => item.strategy);
        const individualStrategies = [...new Set([...defaultStrategies, ...dataStrategies])];
        allVirtualStrategies = individualStrategies;
        const strategies = ['ALL', ...individualStrategies];

        // 3. 버튼 HTML 생성 (멀티셀렉트 지원)
        tabContainer.innerHTML = strategies.map(strat =>
            `<button class="sub-tab-btn" onclick="toggleVirtualStrategy('${strat}', this)">${strat}</button>`
        ).join('');

        // 4. 초기 탭 선택 (기존 멀티셀렉트 상태 복원 또는 ALL)
        const newButtons = tabContainer.querySelectorAll('.sub-tab-btn');
        // 이전 선택 상태가 없거나 ALL이면 ALL 선택
        if (selectedVirtualStrategies.has('ALL') || selectedVirtualStrategies.size === 0) {
            selectedVirtualStrategies = new Set(['ALL']);
        }
        // 버튼 active 상태 복원
        newButtons.forEach(btn => {
            if (selectedVirtualStrategies.has(btn.innerText)) {
                btn.classList.add('active');
            }
        });
        applyVirtualFilter();

        // [UI 개선] 테이블 가시성을 위해 card로 감싸기
        const section = document.getElementById('section-virtual');
        if (section) section.querySelectorAll('table').forEach(ensureTableInCard);

        // forceUpdateStock 등에서 결과를 확인할 수 있도록 데이터 반환
        return allVirtualData.length > 0 ? { trades: allVirtualData } : null;

    } catch (e) {
        console.error("Virtual history error:", e);
        summaryBox.innerText = "데이터 로드 실패";
    }
    return null;
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

// 전략 멀티셀렉트 토글
window.toggleVirtualStrategy = function(strategyName, btnElement) {
    const buttons = document.querySelectorAll('#virtual-strategy-tabs .sub-tab-btn');

    if (strategyName === 'ALL') {
        // ALL 클릭: 전체 선택 ↔ 전체 해제 토글
        if (selectedVirtualStrategies.has('ALL')) {
            // 이미 ALL 선택 → 아무것도 안 함 (최소 1개는 선택)
            return;
        }
        selectedVirtualStrategies = new Set(['ALL']);
        buttons.forEach(b => b.classList.remove('active'));
        if (btnElement) btnElement.classList.add('active');
    } else {
        // 개별 전략 토글
        // ALL이 선택된 상태에서 개별 클릭 → ALL 해제하고 해당 전략만 선택
        if (selectedVirtualStrategies.has('ALL')) {
            selectedVirtualStrategies = new Set([strategyName]);
            buttons.forEach(b => b.classList.remove('active'));
            if (btnElement) btnElement.classList.add('active');
        } else if (selectedVirtualStrategies.has(strategyName)) {
            // 이미 선택된 전략 해제
            if (selectedVirtualStrategies.size <= 1) {
                // 마지막 하나는 해제 불가 → ALL로 복귀
                selectedVirtualStrategies = new Set(['ALL']);
                buttons.forEach(b => b.classList.remove('active'));
                const allBtn = buttons[0]; // ALL 버튼
                if (allBtn) allBtn.classList.add('active');
            } else {
                selectedVirtualStrategies.delete(strategyName);
                if (btnElement) btnElement.classList.remove('active');
            }
        } else {
            // 새 전략 추가 선택
            selectedVirtualStrategies.add(strategyName);
            if (btnElement) btnElement.classList.add('active');
        }

        // 모든 개별 전략이 선택되면 ALL로 전환
        if (allVirtualStrategies.length > 0 &&
            allVirtualStrategies.every(s => selectedVirtualStrategies.has(s))) {
            selectedVirtualStrategies = new Set(['ALL']);
            buttons.forEach(b => b.classList.remove('active'));
            const allBtn = buttons[0];
            if (allBtn) allBtn.classList.add('active');
        }
    }

    applyVirtualFilter();
};

// 현재 선택 상태 기반으로 테이블 + 요약 + 차트 갱신
function applyVirtualFilter() {
    const isAll = selectedVirtualStrategies.has('ALL');
    const selectedArray = isAll ? ['ALL'] : [...selectedVirtualStrategies];
    const displayLabel = isAll ? 'ALL' : selectedArray.join(', ');

    // 1. 데이터 필터링
    let filteredData = allVirtualData;
    if (!isAll) {
        filteredData = allVirtualData.filter(item => selectedVirtualStrategies.has(item.strategy));
    }

    const holdData = filteredData.filter(item => item.status === 'HOLD');
    const soldData = filteredData.filter(item => item.status === 'SOLD');

    // 2. 통계 계산
    const totalTrades = filteredData.length;
    let cumulativeReturn;

    // [수정] 백엔드에서 받은 집계 데이터로 수익률 계산
    if (isAll) {
        cumulativeReturn = cumulativeReturns['ALL'] ?? 0;
    } else if (selectedArray.length === 1) {
        cumulativeReturn = cumulativeReturns[selectedArray[0]] ?? 0;
    } else {
        // 여러 전략 선택 시, 백엔드 집계 데이터를 합산하여 자산 가중 평균 계산
        let totalBuyAmt = 0;
        let totalEvalAmt = 0;
        selectedArray.forEach(strat => {
            const agg = summaryAgg[strat];
            if (agg) {
                totalBuyAmt += agg.buy_sum || 0;
                totalEvalAmt += agg.eval_sum || 0;
            }
        });
        cumulativeReturn = totalBuyAmt > 0 ? ((totalEvalAmt - totalBuyAmt) / totalBuyAmt) * 100 : 0;
    }

    // 전일/전주대비: 멀티셀렉트일 때는 선택된 전략들의 평균
    let dailyChange, weeklyChange, dailyRefDate, weeklyRefDate, firstDate;
    // 날짜 포맷 헬퍼: "2025-02-13" → "250213"
    const toShortDate = (d) => d ? d.slice(2).replace(/-/g, '') : '';
    const todayShort = toShortDate(new Date().toISOString().slice(0, 10));

    if (isAll) {
        weeklyChange = weeklyChanges['ALL'];
        dailyRefDate = dailyRefDates['ALL'];
        weeklyRefDate = weeklyRefDates['ALL'];
        firstDate = firstDates['ALL'];
    } else if (selectedArray.length === 1) {
        dailyChange = dailyChanges[selectedArray[0]];
        weeklyChange = weeklyChanges[selectedArray[0]];
        dailyRefDate = dailyRefDates[selectedArray[0]];
        weeklyRefDate = weeklyRefDates[selectedArray[0]];
        firstDate = firstDates[selectedArray[0]];
    } else {
        // 여러 전략 선택: 백엔드에서 제공하는 집계 데이터가 없으므로 표시하지 않음
        // (프론트엔드에서의 단순 평균 계산은 자산 비중을 고려하지 못해 부정확함)
        dailyChange = null;
        weeklyChange = null;
        dailyRefDate = null;
        weeklyRefDate = null;
        
        const fDates = selectedArray.map(s => firstDates[s]).filter(Boolean).sort();
        firstDate = fDates[0];
    }

    // 카운트 집계 (보유, 금일매수, 금일이탈)
    let holdCount = 0;
    let todayBuyCount = 0;
    let todaySellCount = 0;

    if (isAll) {
        const c = virtualCounts['ALL'] || {};
        holdCount = c.hold || 0;
        todayBuyCount = c.today_buy || 0;
        todaySellCount = c.today_sell || 0;
    } else if (selectedArray.length === 1) {
        const c = virtualCounts[selectedArray[0]] || {};
        holdCount = c.hold || 0;
        todayBuyCount = c.today_buy || 0;
        todaySellCount = c.today_sell || 0;
    } else {
        // 멀티셀렉트: 합산
        selectedArray.forEach(strat => {
            const c = virtualCounts[strat];
            if (c) {
                holdCount += c.hold || 0;
                todayBuyCount += c.today_buy || 0;
                todaySellCount += c.today_sell || 0;
            }
        });
    }

    const cumDateLabel = firstDate ? `${toShortDate(firstDate)}~${todayShort}` : '';
    const dailyDateLabel = dailyRefDate ? toShortDate(dailyRefDate) : '';
    const weeklyDateLabel = weeklyRefDate ? toShortDate(weeklyRefDate) : '';

    // 색상 헬퍼
    const colorClass = (val) => val > 0 ? 'text-positive' : (val < 0 ? 'text-negative' : '');
    const signPrefix = (val) => val > 0 ? '+' : '';

    // 3. 요약 박스
    const summaryBox = document.getElementById('virtual-summary-box');
    if (!summaryBox) { console.error('[Virtual] virtual-summary-box not found'); return; }
    summaryBox.innerHTML = `
        <div style="margin-bottom: 15px; margin-top: 5px;">
            <div style="background-color: #000000 !important; color: #ffffff !important; padding: 6px 18px; border-radius: 20px; border: 1.5px solid #e94560; display: inline-block; box-shadow: 0 2px 6px rgba(0,0,0,0.3);">
                <span style="color: #e94560; margin-right: 6px; font-size: 1.1em;">📊</span>
                <span style="font-size: 1.05em; font-weight: 700 !important; letter-spacing: 0.5px;">[ ${displayLabel} 성과 요약 ]</span>
            </div>
        </div>
        <div style="display: flex; justify-content: center; align-items: center; gap: 12px; flex-wrap: wrap;">
            <div style="background-color: #000000 !important; color: #ffffff !important; padding: 12px 18px; border-radius: 10px; border: 1px solid #30363d; min-width: 125px; box-shadow: 0 4px 8px rgba(0,0,0,0.4);">
                <div style="font-size: 0.85em; color: #a0a0b0 !important; margin-bottom: 4px; font-weight: 600;">총 거래</div>
                <div style="color: #ffffff !important;"><strong style="font-size: 1.35em;">${totalTrades}</strong> <span style="font-size: 1em;">건</span></div>
            </div>
            <div style="background-color: #000000 !important; color: #ffffff !important; padding: 12px 18px; border-radius: 10px; border: 1px solid #30363d; min-width: 160px; box-shadow: 0 4px 8px rgba(0,0,0,0.4);">
                <div style="font-size: 0.85em; color: #a0a0b0 !important; margin-bottom: 4px; font-weight: 600;">포지션 현황</div>
                <div style="color: #ffffff !important; font-size: 0.95em; line-height: 1.4;">
                    보유: <strong>${holdCount}</strong><br>
                    <span style="font-size:0.9em; color:#ccc;">(매수 <span style="color:#ff4d4d">+${todayBuyCount}</span> / 이탈 <span style="color:#4d94ff">-${todaySellCount}</span>)</span>
                </div>
            </div>
            <div style="background-color: #000000 !important; color: #ffffff !important; padding: 12px 18px; border-radius: 10px; border: 1px solid #30363d; min-width: 125px; box-shadow: 0 4px 8px rgba(0,0,0,0.4);">
                <div style="font-size: 0.85em; color: #a0a0b0 !important; margin-bottom: 4px; font-weight: 600;">누적 수익률 <span style="color:#707080; font-size:0.85em;">${cumDateLabel}</span></div>
                <strong class="${colorClass(cumulativeReturn)}" style="font-size: 1.35em; font-weight: 800 !important;">
                    ${signPrefix(cumulativeReturn)}${cumulativeReturn.toFixed(2)}%
                </strong>
            </div>
            <div style="background-color: #000000 !important; color: #ffffff !important; padding: 12px 18px; border-radius: 10px; border: 1px solid #30363d; min-width: 125px; box-shadow: 0 4px 8px rgba(0,0,0,0.4);">
                <div style="font-size: 0.85em; color: #a0a0b0 !important; margin-bottom: 4px; font-weight: 600;">전일대비 <span style="color:#707080; font-size:0.85em;">${dailyDateLabel}</span></div>
                <strong class="${dailyChange != null ? colorClass(dailyChange) : ''}" style="font-size: 1.35em; font-weight: 800 !important;">
                    ${dailyChange != null ? signPrefix(dailyChange) + dailyChange.toFixed(2) + '%' : '-'}
                </strong>
            </div>
            <div style="background-color: #000000 !important; color: #ffffff !important; padding: 12px 18px; border-radius: 10px; border: 1px solid #30363d; min-width: 125px; box-shadow: 0 4px 8px rgba(0,0,0,0.4);">
                <div style="font-size: 0.85em; color: #a0a0b0 !important; margin-bottom: 4px; font-weight: 600;">전주대비 <span style="color:#707080; font-size:0.85em;">${weeklyDateLabel}</span></div>
                <strong class="${weeklyChange != null ? colorClass(weeklyChange) : ''}" style="font-size: 1.35em; font-weight: 800 !important;">
                    ${weeklyChange != null ? signPrefix(weeklyChange) + weeklyChange.toFixed(2) + '%' : '-'}
                </strong>
            </div>
        </div>
    `;

    // 4. 데이터 캐시 후 렌더링
    currentVirtualHoldData = holdData;
    currentVirtualSoldData = soldData.slice().reverse();
    renderVirtualHoldTable();
    renderVirtualSoldTable();

    // 5. 차트도 선택된 전략에 맞게 업데이트
    console.log('[applyVirtualFilter] refreshVirtualChart 호출 예정, selectedArray:', selectedArray,
        'refreshVirtualChart 존재:', typeof refreshVirtualChart);
    if (typeof refreshVirtualChart === 'function') {
        refreshVirtualChart(selectedArray);
    }
}

function virtualSortCompare(data, key, dir) {
    const sorted = data.slice();
    const d = dir === 'asc' ? 1 : -1;
    sorted.sort((a, b) => {
        let va, vb;
        if (key === 'name') {
            va = (a.stock_name || a.code || '').toLowerCase();
            vb = (b.stock_name || b.code || '').toLowerCase();
            return d * va.localeCompare(vb);
        } else if (key === 'buy_price') {
            va = Number(a.buy_price || 0);
            vb = Number(b.buy_price || 0);
        } else if (key === 'current_price') {
            va = Number(a.current_price || 0);
            vb = Number(b.current_price || 0);
        } else if (key === 'sell_price') {
            va = Number(a.sell_price || 0);
            vb = Number(b.sell_price || 0);
        } else if (key === 'return_rate') {
            va = Number(a.return_rate || 0);
            vb = Number(b.return_rate || 0);
        } else if (key === 'days') {
            va = calcDaysHeld(a.buy_date, a.sell_date || null);
            vb = calcDaysHeld(b.buy_date, b.sell_date || null);
            va = typeof va === 'number' ? va : 0;
            vb = typeof vb === 'number' ? vb : 0;
        } else {
            return 0;
        }
        return d * (va - vb);
    });
    return sorted;
}

function virtualSortClass(table, key) {
    const state = table === 'hold' ? virtualHoldSortState : virtualSoldSortState;
    if (state.key !== key) return 'sortable';
    return `sortable sort-${state.dir}`;
}

function updateVirtualSortHeaders(table) {
    const section = document.getElementById('section-virtual');
    const tables = section.querySelectorAll('.data-table');
    const target = table === 'hold' ? tables[0] : tables[1];
    if (!target) return;
    const ths = target.querySelectorAll('thead th');
    const keys = table === 'hold'
        ? ['name', 'buy_price', 'current_price', 'return_rate', 'days']
        : ['name', 'buy_price', 'sell_price', 'return_rate', 'days'];
    ths.forEach((th, i) => {
        if (keys[i]) th.className = virtualSortClass(table, keys[i]);
    });
}

function renderVirtualHoldTable() {
    const holdBody = document.getElementById('virtual-hold-body');
    if (!holdBody) return;
    holdBody.innerHTML = '';
    let data = currentVirtualHoldData;
    if (virtualHoldSortState.key) {
        data = virtualSortCompare(data, virtualHoldSortState.key, virtualHoldSortState.dir);
    }

    updateVirtualSortHeaders('hold');

    const applyCostEl = document.getElementById('apply-cost-chk');
    const showCost = applyCostEl ? applyCostEl.checked : false;

    if (data.length === 0) {
        holdBody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:15px;">보유 종목이 없습니다.</td></tr>';
        return;
    }
    data.forEach(item => {
        const ror = item.return_rate || 0;
        const rorClass = ror > 0 ? 'text-positive' : (ror < 0 ? 'text-negative' : '');
        const buyDate = item.buy_date ? item.buy_date.split(' ')[0] : '-';
        const buyPrice = Number(item.buy_price).toLocaleString();
        const curPrice = item.current_price ? Number(item.current_price).toLocaleString() : '-';
        const days = calcDaysHeld(item.buy_date, null);
        
        const cacheAge = item.cache_ts ? Math.floor(Date.now() / 1000 - item.cache_ts) : 0;
        const isOldCache = item.is_cached && cacheAge > 60;
        const cacheStyle = isOldCache ? 'color: #ff4d4d; opacity: 1; font-weight: bold;' : 'opacity: 0.6;';
        const cacheLabel = item.is_cached ? `<span title="API 호출 실패로 인한 캐시 데이터 (경과: ${Math.floor(cacheAge/60)}분)" style="cursor:help; margin-left:4px; ${cacheStyle}">🕒</span>` : '';
        const forceBtn = `<span onclick="forceUpdateStock('${item.code}', event)" title="강제 업데이트" style="cursor:pointer; margin-left:6px; opacity:0.5; transition: transform 0.3s;">🔄</span>`;

        // 전략명 표시 추가
        const strategyLabel = item.strategy ? `<div style="font-size:0.75em; color:#888; margin-bottom:2px;">${item.strategy}</div>` : '';

        let costHtml = '';
        if (showCost) {
            const qty = Number(item.qty) || 1;
            const bp = Number(item.buy_price || 0);
            const cp = Number(item.current_price || 0);
            if (bp > 0 && cp > 0) {
                const cost = Math.floor((bp * qty * 0.000140527) + (cp * qty * (0.000140527 + 0.002)));
                costHtml = ` <span style="font-size:0.75em; color:#999; font-weight:normal;">(-${cost.toLocaleString()}원)</span>`;
            }
        }

        holdBody.insertAdjacentHTML('beforeend', `
            <tr>
                <td>
                    ${strategyLabel}
                    <a href="#" onclick="searchStock('${item.code}'); return false;" style="color:var(--accent); text-decoration:none;">${stockLabel(item)}</a>
                </td>
                <td>${buyPrice}</td>
                <td>${curPrice}${cacheLabel}${forceBtn}</td>
                <td class="${rorClass}"><strong>${ror.toFixed(2)}%</strong>${costHtml}</td>
                <td>${days}일<div style="font-size:0.8em; color:var(--text-secondary);">${buyDate}</div></td>
            </tr>
        `);
    });
}

function renderVirtualSoldTable() {
    const soldBody = document.getElementById('virtual-sold-body');
    if (!soldBody) return;
    soldBody.innerHTML = '';

    let data = currentVirtualSoldData;
    if (virtualSoldSortState.key) {
        data = virtualSortCompare(data, virtualSoldSortState.key, virtualSoldSortState.dir);
    }

    updateVirtualSortHeaders('sold');

    const applyCostEl = document.getElementById('apply-cost-chk');
    const showCost = applyCostEl ? applyCostEl.checked : false;

    if (data.length === 0) {
        soldBody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:15px;">매도 기록이 없습니다.</td></tr>';
        return;
    }
    data.forEach(item => {
        const ror = item.return_rate || 0;
        const rorClass = ror > 0 ? 'text-positive' : (ror < 0 ? 'text-negative' : '');
        const buyDate = item.buy_date ? item.buy_date.split(' ')[0] : '-';
        const sellDate = item.sell_date ? item.sell_date.split(' ')[0] : '-';
        const buyPrice = Number(item.buy_price).toLocaleString();
        const sellPrice = (item.sell_price != null && item.sell_price > 0) ? Number(item.sell_price).toLocaleString() : '-';
        const curPrice = item.current_price ? Number(item.current_price).toLocaleString() : '';
        const days = calcDaysHeld(item.buy_date, item.sell_date);

        const cacheAge = item.cache_ts ? Math.floor(Date.now() / 1000 - item.cache_ts) : 0;
        const isOldCache = item.is_cached && cacheAge > 60;
        const cacheStyle = isOldCache ? 'color: #ff4d4d; opacity: 1; font-weight: bold;' : 'opacity: 0.6;';
        const cacheLabel = item.is_cached ? `<span title="API 호출 실패로 인한 캐시 데이터 (경과: ${Math.floor(cacheAge/60)}분)" style="cursor:help; margin-left:4px; ${cacheStyle}">🕒</span>` : '';
        const forceBtn = `<span onclick="forceUpdateStock('${item.code}', event)" title="강제 업데이트" style="cursor:pointer; margin-left:6px; opacity:0.5; transition: transform 0.3s;">🔄</span>`;

        // 전략명 표시 추가
        const strategyLabel = item.strategy ? `<div style="font-size:0.75em; color:#888; margin-bottom:2px;">${item.strategy}</div>` : '';

        let costHtml = '';
        if (showCost) {
            const qty = Number(item.qty) || 1;
            const bp = Number(item.buy_price || 0);
            let sp = Number(item.sell_price || 0);
            if (sp === 0 && item.current_price) sp = Number(item.current_price);
            if (bp > 0 && sp > 0) {
                const cost = Math.floor((bp * qty * 0.000140527) + (sp * qty * (0.000140527 + 0.002)));
                costHtml = ` <span style="font-size:0.75em; color:#999; font-weight:normal;">(-${cost.toLocaleString()}원)</span>`;
            }
        }

        soldBody.insertAdjacentHTML('beforeend', `
            <tr>
                <td>
                    ${strategyLabel}
                    <a href="#" onclick="searchStock('${item.code}'); return false;" style="color:var(--accent); text-decoration:none;">${stockLabel(item)}</a>
                </td>
                <td>${buyPrice}</td>
                <td>${sellPrice}<div style="font-size:0.8em; color:var(--text-secondary);">${curPrice}${cacheLabel}${forceBtn}</div></td>
                <td class="${rorClass}"><strong>${ror.toFixed(2)}%</strong>${costHtml}</td>
                <td>${days}일<div style="font-size:0.8em; color:var(--text-secondary);">${buyDate} ~ ${sellDate}</div></td>
            </tr>
        `);
    });
}

function sortVirtual(table, key) {
    const state = table === 'hold' ? virtualHoldSortState : virtualSoldSortState;
    if (state.key === key) {
        state.dir = state.dir === 'asc' ? 'desc' : 'asc';
    } else {
        state.key = key;
        state.dir = 'asc';
    }

    if (table === 'hold') renderVirtualHoldTable();
    else renderVirtualSoldTable();
}

// 특정 종목 강제 업데이트 함수
window.forceUpdateStock = async function(code, event) {
    console.log(`[Virtual] 종목 강제 업데이트 시도: ${code}`);
    if (event && event.currentTarget) {
        event.currentTarget.classList.add('spinning');
    }
    
    const data = await loadVirtualHistory(code);
    
    if (event && event.currentTarget) {
        event.currentTarget.classList.remove('spinning');
    }

    if (data && data.trades) {
        const item = data.trades.find(t => t.code === code);
        if (item) {
            if (item.is_cached) {
                showToast(`${stockLabel(item)} 업데이트 실패 (캐시 데이터 사용)`, 'error');
            } else {
                showToast(`${stockLabel(item)} 최신가 업데이트 완료`, 'success');
            }
        }
    } else {
        showToast('네트워크 오류로 업데이트에 실패했습니다.', 'error');
    }
};

// ==========================================
// 7. 프로그램매매 실시간
// ==========================================
let ptEventSource = null;
let ptChartData = {}; // { code: { totalValue: 0, totalVolume: 0, valueData: [], volumeData: [] } }

let ptRowCount = 0;
let ptSubscribedCodes = new Set();
let ptCodeNameMap = {};   // 종목코드 → 종목명 매핑
let ptFilterCodes = new Set();  // 선택된 필터 종목코드 Set (비어있으면 전체 표시)
let ptDataDirty = false;  // 데이터 변경 여부 플래그
let ptTimeUnit = 1;       // 차트/표 시간 단위 (분)

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
        if (json.stock_name) ptCodeNameMap[code] = json.stock_name;
        
        if (!ptChartData[code]) {
            ptChartData[code] = { totalValue: 0, totalVolume: 0, valueData: [], volumeData: [] };
        }

        ptDataDirty = true; // [추가] 데이터 변경 표시
        // [수정] 구독 즉시 차트 초기화 (데이터 수신 대기 중에도 차트 표시)
        initProgramChart(ptTimeUnit);
        updateProgramChart(ptChartData, ptSubscribedCodes, ptFilterCodes, ptCodeNameMap, ptTimeUnit);

        renderPtChips();
        input.value = '';

        if (!ptEventSource) {
            ptEventSource = new EventSource('/api/program-trading/stream');
            ptEventSource.onmessage = (event) => {
                const d = JSON.parse(event.data);
                handleProgramTradingData(d); // 새 핸들러 호출
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
    delete ptCodeNameMap[code];
    delete ptChartData[code];
    ptFilterCodes.delete(code);
    ptDataDirty = true;

    updateProgramChart(ptChartData, ptSubscribedCodes, ptFilterCodes, ptCodeNameMap, ptTimeUnit);
    renderPtChips();

    const statusDiv = document.getElementById('pt-status');
    if (ptSubscribedCodes.size === 0) {
        if (ptEventSource) { ptEventSource.close(); ptEventSource = null; }
        statusDiv.innerHTML = '<span>구독 중지됨</span>';
        destroyProgramChart();
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
    ptCodeNameMap = {};
    ptFilterCodes.clear();

    destroyProgramChart();

    ptChartData = {};
    ptDataDirty = true; // [추가] 데이터 변경 표시

    renderPtChips();
    document.getElementById('pt-status').innerHTML = '<span>구독 중지됨</span>';
}

function renderPtChips() {
    const container = document.getElementById('pt-subscribed-list');
    container.innerHTML = '';
    const showAll = ptFilterCodes.size === 0;

    // ALL 탭
    const allChip = document.createElement('span');
    allChip.style.cssText = `display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:12px;font-size:0.8rem;font-weight:600;cursor:pointer;transition:all 0.2s;`
        + (showAll ? 'background:#ff6b35;color:#fff;box-shadow:0 0 0 2px #ff6b35;' : 'background:var(--neutral);');
    allChip.innerHTML = `<span onclick="togglePtFilter('ALL')">ALL</span>`;
    container.appendChild(allChip);

    for (const code of ptSubscribedCodes) {
        const chip = document.createElement('span');
        const isActive = ptFilterCodes.has(code);
        chip.style.cssText = `display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:12px;font-size:0.8rem;font-weight:600;cursor:pointer;transition:all 0.2s;`
            + (isActive ? 'background:#ff6b35;color:#fff;box-shadow:0 0 0 2px #ff6b35;' : 'background:var(--neutral);');
        const label = ptCodeNameMap[code] ? `${ptCodeNameMap[code]}(${code})` : code;
        chip.innerHTML = `<span onclick="togglePtFilter('${code}')">${label}</span> <span style="cursor:pointer;color:var(--negative);font-weight:bold;margin-left:2px;" onclick="event.stopPropagation();removeProgramTrading('${code}')">&times;</span>`;
        container.appendChild(chip);
    }
}

function togglePtFilter(code) {
    if (code === 'ALL') {
        ptFilterCodes.clear();
    } else {
        if (ptFilterCodes.has(code)) {
            ptFilterCodes.delete(code);
            // 마지막 코드 해제 → ALL로 복귀
            if (ptFilterCodes.size === 0) {
                // 이미 비어있으면 ALL 상태
            }
        } else {
            ptFilterCodes.add(code);
            // 모든 코드 선택됨 → ALL 전환
            if (ptFilterCodes.size === ptSubscribedCodes.size) {
                ptFilterCodes.clear();
            }
        }
    }
    applyPtFilter();
}

function applyPtFilter() {
    renderPtChips();
    _renderPtTable();
    updateProgramChart(ptChartData, ptSubscribedCodes, ptFilterCodes, ptCodeNameMap, ptTimeUnit);
}

// [추가] 시간 단위 변경 함수
function setPtTimeUnit(minutes) {
    ptTimeUnit = minutes;
    
    // 버튼 스타일 업데이트
    document.querySelectorAll('.pt-interval-btn').forEach(btn => {
        if (parseInt(btn.dataset.interval) === minutes) btn.classList.add('active');
        else btn.classList.remove('active');
    });

    updateProgramChart(ptChartData, ptSubscribedCodes, ptFilterCodes, ptCodeNameMap, ptTimeUnit);
    _renderPtTable();
}

function _appendProgramTradingTableRow(d) {
    const tbody = document.getElementById('pt-body');
    const time = d['주식체결시간'] || '';
    const fmtTime = time.length >= 6 ? time.slice(0,2)+':'+time.slice(2,4)+':'+time.slice(4,6) : time;
    const ntby = parseInt(d['순매수체결량'] || '0');
    const ntbyColor = ntby > 0 ? 'text-red' : (ntby < 0 ? 'text-blue' : '');

    const price = d['price'] ? parseInt(d['price']).toLocaleString() : '-';
    const rate = d['rate'] ? parseFloat(d['rate']).toFixed(2) + '%' : '';
    const sign = d['sign'] || '3'; // 1,2:상승, 4,5:하락, 3:보합
    
    let priceClass = '';
    if (sign === '1' || sign === '2') priceClass = 'text-red';
    else if (sign === '4' || sign === '5') priceClass = 'text-blue';

    const stockCode = d['유가증권단축종목코드'] || '-';
    const hidden = (ptFilterCodes.size > 0 && !ptFilterCodes.has(stockCode)) ? ' style="display:none"' : '';
    const row = `<tr data-code="${stockCode}"${hidden}>
        <td>${ptCodeNameMap[stockCode] ? ptCodeNameMap[stockCode] + '(' + stockCode + ')' : stockCode}</td>
        <td>${fmtTime}</td>
        <td class="${priceClass}">${price}<br><small>${rate}</small></td>
        <td>${parseInt(d['매도체결량'] || 0).toLocaleString()}</td>
        <td>${parseInt(d['매수2체결량'] || 0).toLocaleString()}</td>
        <td class="${ntbyColor}">${ntby.toLocaleString()}</td>
        <td>${formatTradingValue(d['순매수거래대금'])}</td>
        <td>${parseInt(d['매도호가잔량'] || 0).toLocaleString()}</td>
        <td>${parseInt(d['매수호가잔량'] || 0).toLocaleString()}</td>
    </tr>`;

    tbody.insertAdjacentHTML('afterbegin', row);
    ptRowCount++;
    if (ptRowCount > 200) {
        tbody.removeChild(tbody.lastElementChild);
        ptRowCount--;
    }
}

// 차트 초기화/업데이트/집계 로직은 program_chart.js로 분리됨


function handleProgramTradingData(d) {
    // _appendProgramTradingTableRow(d); // [수정] 개별 행 추가 대신 전체 갱신으로 변경

    const code = d['유가증권단축종목코드'];
    if (!ptChartData[code]) return;

    const netValue = parseInt(d['순매수거래대금'] || '0');
    const netVolume = parseInt(d['순매수체결량'] || '0');
    // API 데이터가 이미 누적치이므로 그대로 사용합니다.

    const timeStr = d['주식체결시간']; // "HHMMSS"
    if (!timeStr || timeStr.length < 4) return;

    const now = new Date();
    now.setHours(parseInt(timeStr.slice(0, 2)));
    now.setMinutes(parseInt(timeStr.slice(2, 4)));
    now.setSeconds(0); // [수정] 차트는 1분 단위로 표시 (초 절삭)
    now.setMilliseconds(0);
    const timestamp = now.getTime();

    const valueData = ptChartData[code].valueData;
    const volumeData = ptChartData[code].volumeData;

    // [수정] 같은 분(Minute) 데이터 찾기 (lastItem만 비교하면 순서 꼬일 시 중복 발생 가능)
    const existingIdx = valueData.findIndex(item => item.x === timestamp);

    if (existingIdx >= 0) {
        valueData[existingIdx].y = netValue; // 덮어쓰기
        valueData[existingIdx].price = d.price;
        valueData[existingIdx].rate = d.rate;
        valueData[existingIdx].change = d.change;
        valueData[existingIdx].sign = d.sign;
        valueData[existingIdx].netVolume = netVolume; // [추가] 테이블 렌더링용
        
        // [추가] 상세 데이터 저장 (복원용)
        valueData[existingIdx].sellVol = d['매도체결량'];
        valueData[existingIdx].buyVol = d['매수2체결량'];
        valueData[existingIdx].sellRem = d['매도호가잔량'];
        valueData[existingIdx].buyRem = d['매수호가잔량'];

        if (volumeData[existingIdx]) {
            volumeData[existingIdx].y = netVolume;
            volumeData[existingIdx].price = d.price;
        }
    } else {
        const point = { 
            x: timestamp, 
            y: netValue, 
            price: d.price, 
            rate: d.rate, 
            change: d.change, 
            sign: d.sign,
            netVolume: netVolume, // [추가]
            // [추가] 상세 데이터 저장
            sellVol: d['매도체결량'],
            buyVol: d['매수2체결량'],
            sellRem: d['매도호가잔량'],
            buyRem: d['매수호가잔량']
        };
        valueData.push(point);
        
        const volPoint = { x: timestamp, y: netVolume, price: d.price };
        volumeData.push(volPoint);
        // 시간순 정렬 (Line 차트 꼬임 방지)
        valueData.sort((a, b) => a.x - b.x);
        volumeData.sort((a, b) => a.x - b.x);
    }
    
    // 데이터 포인트 제한
    if (valueData.length > 1000) {
        valueData.shift();
        volumeData.shift();
    }
    ptDataDirty = true; // [추가] 데이터 변경 표시
    
    // [수정] 필터링된 상태에서는 해당 종목 데이터가 들어왔을 때만 차트 업데이트
    if (ptFilterCodes.size === 0 || ptFilterCodes.has(code)) {
        updateProgramChart(ptChartData, ptSubscribedCodes, ptFilterCodes, ptCodeNameMap, ptTimeUnit);
        _renderPtTable();
    }
}

// ==========================================
// 8. 전략 스케줄러
// ==========================================
let schedulerPollingId = null;
let allSchedulerHistory = [];
let currentSchedulerFilter = '전체';

async function loadSchedulerStatus() {
    try {
        const [statusRes, historyRes] = await Promise.all([
            fetch('/api/scheduler/status'),
            fetch('/api/scheduler/history'),
        ]);
        const statusData = await statusRes.json();
        const historyData = await historyRes.json();
        renderSchedulerStatus(statusData);

        allSchedulerHistory = historyData.history || [];
        buildSchedulerHistoryTabs(statusData.strategies || []);
        filterSchedulerHistory(currentSchedulerFilter);
    } catch (e) {
        const info = document.getElementById('scheduler-info');
        if (info) info.innerHTML = '<span>스케줄러 상태 조회 실패</span>';
    }
}

function renderSchedulerStatus(data) {
    const badge = document.getElementById('scheduler-status-badge');
    const info = document.getElementById('scheduler-info');
    const strategiesDiv = document.getElementById('scheduler-strategies');

    if (data.running) {
        badge.textContent = '실행 중';
        badge.className = 'badge open';
    } else {
        badge.textContent = '정지';
        badge.className = 'badge closed';
    }

    const dryLabel = data.dry_run ? 'dry-run: CSV만 기록' : '실제 주문 실행';
    info.textContent = dryLabel;

    if (!data.strategies || data.strategies.length === 0) {
        strategiesDiv.innerHTML = '<div class="card"><span>등록된 전략이 없습니다.</span></div>';
        return;
    }

    strategiesDiv.innerHTML = data.strategies.map(s => {
        const enabledBadge = s.enabled
            ? '<span class="badge open">활성</span>'
            : '<span class="badge closed">비활성</span>';
        const positionBadge = `<span class="badge ${s.current_holds >= s.max_positions ? 'closed' : 'paper'}">포지션 ${s.current_holds}/${s.max_positions}</span>`;
        const toggleBtn = s.enabled
            ? `<button class="btn btn-sell" style="padding:4px 12px;font-size:0.85em;" onclick="stopStrategy('${s.name}')">정지</button>`
            : `<button class="btn btn-buy" style="padding:4px 12px;font-size:0.85em;" onclick="startStrategy('${s.name}')">시작</button>`;
        const poolABtn = s.name === '오닐스퀴즈돌파'
            ? `<button class="btn" style="padding:4px 12px;font-size:0.85em;background:var(--accent);" onclick="generatePoolA(this)">Pool A 생성</button>`
            : '';
        return `
        <div class="card" style="margin-bottom:8px;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <div style="display:flex;align-items:center;gap:8px;">
                    <h3 style="margin:0;color:var(--text-primary);">${s.name}</h3>
                    ${enabledBadge}
                </div>
                <div style="display:flex;align-items:center;gap:8px;">
                    ${poolABtn}
                    ${positionBadge}
                    ${toggleBtn}
                </div>
            </div>
            <div style="margin-top:8px;color:var(--text-secondary);font-size:0.9em;">
                실행 주기: ${s.interval_minutes}분 | 마지막 실행: ${s.last_run || '-'}
            </div>
        </div>`;
    }).join('');
}

async function startScheduler() {
    try {
        const res = await fetch('/api/scheduler/start', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            renderSchedulerStatus(data.status);
            startSchedulerPolling();
        }
    } catch (e) {
        alert('스케줄러 시작 실패');
    }
}

async function stopScheduler() {
    try {
        const res = await fetch('/api/scheduler/stop', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            renderSchedulerStatus(data.status);
            stopSchedulerPolling();
        }
    } catch (e) {
        alert('스케줄러 정지 실패');
    }
}

async function startStrategy(name) {
    try {
        const res = await fetch(`/api/scheduler/strategy/${encodeURIComponent(name)}/start`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            renderSchedulerStatus(data.status);
        } else {
            alert(data.detail || '전략 시작 실패');
        }
    } catch (e) {
        alert('전략 시작 실패');
    }
}

async function stopStrategy(name) {
    try {
        const res = await fetch(`/api/scheduler/strategy/${encodeURIComponent(name)}/stop`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            renderSchedulerStatus(data.status);
        } else {
            alert(data.detail || '전략 정지 실패');
        }
    } catch (e) {
        alert('전략 정지 실패');
    }
}

async function generatePoolA(btnEl) {
    const origText = btnEl.textContent;
    btnEl.disabled = true;
    btnEl.textContent = 'Pool A 생성 중...';
    try {
        const res = await fetch('/api/scheduler/strategy/%EC%98%A4%EB%8B%90%EC%8A%A4%ED%80%B4%EC%A6%88%EB%8F%8C%ED%8C%8C/generate-pool-a', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            const r = data.result;
            alert(`Pool A 생성 완료!\n\n스캔: ${r.scanned}종목\n1차 통과: ${r.first_filter_passed}종목\n2차 통과: ${r.second_filter_passed}종목\n\nKOSPI: ${r.kospi_count}종목\nKOSDAQ: ${r.kosdaq_count}종목\n시총 범위: ${r.market_cap_filter}`);
        } else {
            alert(data.detail || 'Pool A 생성 실패');
        }
    } catch (e) {
        alert('Pool A 생성 실패: ' + e.message);
    } finally {
        btnEl.disabled = false;
        btnEl.textContent = origText;
    }
}

function buildSchedulerHistoryTabs(strategies) {
    const tabContainer = document.getElementById('scheduler-history-tabs');
    if (!tabContainer) return;

    const names = ['전체', ...strategies.map(s => s.name)];
    tabContainer.innerHTML = names.map(name =>
        `<button class="sub-tab-btn${name === currentSchedulerFilter ? ' active' : ''}" onclick="filterSchedulerHistory('${name}', this)">${name}</button>`
    ).join('');
}

function filterSchedulerHistory(strategyName, btnElement) {
    currentSchedulerFilter = strategyName;

    // 탭 활성화 상태 업데이트
    const tabContainer = document.getElementById('scheduler-history-tabs');
    if (tabContainer) {
        tabContainer.querySelectorAll('.sub-tab-btn').forEach(b => b.classList.remove('active'));
        if (btnElement) {
            btnElement.classList.add('active');
        } else {
            const match = Array.from(tabContainer.querySelectorAll('.sub-tab-btn')).find(b => b.textContent === strategyName);
            if (match) match.classList.add('active');
        }
    }

    const filtered = strategyName === '전체'
        ? allSchedulerHistory
        : allSchedulerHistory.filter(h => h.strategy_name === strategyName);
    renderSchedulerHistory(filtered);
}

function renderSchedulerHistory(history) {
    const tbody = document.getElementById('scheduler-history-body');
    if (!tbody) return;

    ensureTableInCard(tbody.closest('table'));

    if (!history || history.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:15px;">실행 이력이 없습니다.</td></tr>';
        return;
    }

    tbody.innerHTML = history.map(h => {
        const actionClass = h.action === 'BUY' ? 'text-red' : 'text-blue';
        const actionLabel = h.action === 'BUY' ? '매수' : '매도';
        const statusIcon = h.api_success ? '' : ' <span title="API 주문 실패" style="color:orange;">⚠</span>';
        return `<tr>
            <td style="white-space:nowrap;">${h.timestamp}</td>
            <td>${h.strategy_name}</td>
            <td>${h.name}(${h.code})</td>
            <td class="${actionClass}"><strong>${actionLabel}</strong>${statusIcon}</td>
            <td>${Number(h.price).toLocaleString()}</td>
            <td style="font-size:0.85em;">${h.reason}</td>
        </tr>`;
    }).join('');
}

function startSchedulerPolling() {
    stopSchedulerPolling();
    schedulerPollingId = setInterval(loadSchedulerStatus, 10000); // 10초마다
}

function stopSchedulerPolling() {
    if (schedulerPollingId) {
        clearInterval(schedulerPollingId);
        schedulerPollingId = null;
    }
}

// ==========================================
// 9. 데이터 영속성 (LocalStorage)
// ==========================================
async function savePtData() {
    const data = {
        chartData: ptChartData,
        subscribedCodes: Array.from(ptSubscribedCodes),
        codeNameMap: ptCodeNameMap,
        savedAt: new Date().toISOString()
    };
    
    // 1. 브라우저 LocalStorage 저장 (빠른 접근)
    localStorage.setItem('ptData', JSON.stringify(data));

    // 2. 서버 파일 저장 (안정성)
    try {
        await fetch('/api/program-trading/save-data', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
            keepalive: true // [추가] 페이지 종료 시에도 요청 유지
        });
    } catch (e) {
        console.warn('Server save failed:', e);
    }
}

async function loadPtData() {
    // 1. 서버에서 로드 시도
    try {
        const res = await fetch('/api/program-trading/load-data');
        const json = await res.json();
        if (json.success && json.data) {
            console.log("[PT] Loaded data from Server file");
            return applyPtData(json.data);
        }
    } catch (e) {
        console.warn("[PT] Server load failed, trying LocalStorage...", e);
    }

    // 2. 실패 시 LocalStorage 사용
    const raw = localStorage.getItem('ptData');
    if (raw) {
        try {
            const data = JSON.parse(raw);
            console.log("[PT] Loaded data from LocalStorage");
            return applyPtData(data);
        } catch (e) {
            console.error('Failed to parse LocalStorage data', e);
        }
    }
    return false;
}

function applyPtData(data) {
    try {
        // [수정] 당일 데이터만 필터링하여 로드 (시간축은 오늘 기준으로 유지)
        const todayStart = new Date();
        todayStart.setHours(0, 0, 0, 0);
        const todayTs = todayStart.getTime();

        const rawChartData = data.chartData || {};
        ptChartData = {};

        Object.keys(rawChartData).forEach(code => {
            const entry = rawChartData[code];
            ptChartData[code] = {
                totalValue: 0,
                totalVolume: 0,
                valueData: (entry.valueData || []).filter(d => d.x >= todayTs),
                volumeData: (entry.volumeData || []).filter(d => d.x >= todayTs)
            };
        });

        ptSubscribedCodes = new Set(data.subscribedCodes || []);
        ptCodeNameMap = data.codeNameMap || {};
        
        // [추가] 저장된 차트 데이터를 기반으로 테이블 복원
        _renderPtTable(); // [수정] 통합된 렌더링 함수 사용

        return true;
    } catch (e) {
        console.error('Failed to apply PT data', e);
        return false;
    }
}

function _fixProgramTradingTableHeader() {
    const tbody = document.getElementById('pt-body');
    if (!tbody) return;
    const table = tbody.closest('table');
    if (!table) return;
    const thead = table.querySelector('thead tr');
    if (!thead) return;
    
    const ths = Array.from(thead.querySelectorAll('th'));
    const hasPrice = ths.some(th => th.innerText.includes('현재가'));
    
    if (!hasPrice) {
        const timeTh = ths.find(th => th.innerText.includes('시간'));
        if (timeTh) {
            const priceTh = document.createElement('th');
            priceTh.innerText = '현재가';
            timeTh.after(priceTh);
        }
    }
}

// [추가] 테이블 전체 렌더링 함수 (집계 데이터 반영)
function _renderPtTable() {
    const tbody = document.getElementById('pt-body');
    if (!tbody) return;
    tbody.innerHTML = '';
    ptRowCount = 0;

    let allRows = [];
    
    // 모든 구독 종목의 데이터를 수집
    for (const code of ptSubscribedCodes) {
        if (ptFilterCodes.size > 0 && !ptFilterCodes.has(code)) continue;
        if (!ptChartData[code]) continue;

        // 집계된 데이터 가져오기
        const aggData = getAggregatedPtData(code, ptChartData, ptTimeUnit);
        const vData = aggData.value || [];
        const volData = aggData.volume || [];
        
        for (let i = 0; i < vData.length; i++) {
            const valItem = vData[i];
            const volItem = volData[i] || { y: 0 };
            
            allRows.push({
                code: code,
                timestamp: valItem.x,
                price: valItem.price,
                netValue: valItem.y,
                netVolume: valItem.netVolume || volItem.y, // 저장된 netVolume 우선 사용
                // [추가] 상세 데이터 복원
                rate: valItem.rate,
                change: valItem.change,
                sign: valItem.sign,
                sellVol: valItem.sellVol,
                buyVol: valItem.buyVol,
                sellRem: valItem.sellRem,
                buyRem: valItem.buyRem
            });
        }
    }

    // 시간 내림차순 정렬 (최신 -> 과거) - 테이블은 최신이 위로 오게
    allRows.sort((a, b) => b.timestamp - a.timestamp);

    // 최근 200개만 유지
    if (allRows.length > 200) allRows = allRows.slice(0, 200);

    for (const row of allRows) {
        const date = new Date(row.timestamp);
        const hh = String(date.getHours()).padStart(2, '0');
        const mm = String(date.getMinutes()).padStart(2, '0');
        const timeStr = `${hh}${mm}00`; 

        const d = {
            '유가증권단축종목코드': row.code,
            '주식체결시간': timeStr,
            '순매수체결량': row.netVolume,
            '순매수거래대금': row.netValue,
            '매도체결량': row.sellVol || 0, 
            '매수2체결량': row.buyVol || 0,
            '매도호가잔량': row.sellRem || 0,
            '매수호가잔량': row.buyRem || 0,
            'price': row.price,
            'rate': row.rate,
            'change': row.change,
            'sign': row.sign
        };
        
        // _appendProgramTradingTableRow 로직을 인라인으로 포함 (방향은 appendChild)
        const time = d['주식체결시간'] || '';
        const fmtTime = time.length >= 6 ? time.slice(0,2)+':'+time.slice(2,4)+':'+time.slice(4,6) : time;
        const ntby = parseInt(d['순매수체결량'] || '0');
        const ntbyColor = ntby > 0 ? 'text-red' : (ntby < 0 ? 'text-blue' : '');

        const price = d['price'] ? parseInt(d['price']).toLocaleString() : '-';
        const rate = d['rate'] ? parseFloat(d['rate']).toFixed(2) + '%' : '';
        const sign = d['sign'] || '3';
        
        let priceClass = '';
        if (sign === '1' || sign === '2') priceClass = 'text-red';
        else if (sign === '4' || sign === '5') priceClass = 'text-blue';

        const stockCode = d['유가증권단축종목코드'] || '-';
        const tr = document.createElement('tr');
        tr.dataset.code = stockCode;
        tr.innerHTML = `
            <td>${ptCodeNameMap[stockCode] ? ptCodeNameMap[stockCode] + '(' + stockCode + ')' : stockCode}</td>
            <td>${fmtTime}</td>
            <td class="${priceClass}">${price}<br><small>${rate}</small></td>
            <td>${parseInt(d['매도체결량'] || 0).toLocaleString()}</td>
            <td>${parseInt(d['매수2체결량'] || 0).toLocaleString()}</td>
            <td class="${ntbyColor}">${ntby.toLocaleString()}</td>
            <td>${formatTradingValue(d['순매수거래대금'])}</td>
            <td>${parseInt(d['매도호가잔량'] || 0).toLocaleString()}</td>
            <td>${parseInt(d['매수호가잔량'] || 0).toLocaleString()}</td>
        `;
        tbody.appendChild(tr); // 내림차순 정렬되었으므로 appendChild
        ptRowCount++;
    }
}

async function initProgramTrading() {
    // [추가] 테이블 헤더 동적 수정 (현재가 컬럼 추가)
    _fixProgramTradingTableHeader();
    
    // [UI 개선] 테이블 가시성을 위해 card로 감싸기
    const ptBody = document.getElementById('pt-body');
    if (ptBody) ensureTableInCard(ptBody.closest('table'));

    // [통합] UI 요소 동적 추가 (백업 버튼 + 시간 단위 버튼)
    const ptHeader = document.querySelector('#section-program h2');
    if (ptHeader) {
        // 1. 백업 버튼 그룹 추가 (없으면 생성)
        let backupGroup = ptHeader.querySelector('.pt-backup-group');
        if (!backupGroup) {
            backupGroup = document.createElement('span');
            backupGroup.className = 'pt-backup-group';
            backupGroup.style.cssText = 'font-size: 0.6em; margin-left: 15px; vertical-align: middle; font-weight: normal;';
            backupGroup.innerHTML = `
                <button onclick="exportPtDataToFile()" style="padding: 4px 8px; cursor: pointer; background: #333; color: #eee; border: 1px solid #555; border-radius: 4px; margin-right: 5px;">💾 백업 저장</button>
                <button onclick="importPtDataFromFile()" style="padding: 4px 8px; cursor: pointer; background: #333; color: #eee; border: 1px solid #555; border-radius: 4px;">📂 백업 불러오기</button>
            `;
            ptHeader.appendChild(backupGroup);
        }

        // 2. 시간 단위 선택 버튼 UI 동적 추가 (없으면 생성)
        if (!ptHeader.querySelector('.pt-interval-group')) {
            const intervalGroup = document.createElement('span');
            intervalGroup.className = 'pt-interval-group';
            intervalGroup.style.cssText = 'font-size: 0.6em; margin-left: 15px; vertical-align: middle; font-weight: normal;';
            
            const intervals = [1, 3, 5, 10, 30, 60];
            let html = '<span style="margin-right:5px; color:#aaa;">|</span> ';
            intervals.forEach(min => {
                const activeClass = (min === ptTimeUnit) ? 'active' : '';
                html += `<button class="pt-interval-btn ${activeClass}" data-interval="${min}" onclick="setPtTimeUnit(${min})" 
                    style="padding: 2px 6px; margin-right: 2px; cursor: pointer; background: #333; color: #eee; border: 1px solid #555; border-radius: 3px; font-size: 0.9em;">
                    ${min}분
                </button>`;
            });
            intervalGroup.innerHTML = html;
            
            // 백업 버튼 그룹 앞에 삽입
            if (backupGroup) {
                ptHeader.insertBefore(intervalGroup, backupGroup);
            } else {
                ptHeader.appendChild(intervalGroup);
            }

            // active 스타일 동적 추가 (head에 style 태그 삽입) - 중복 방지
            if (!document.getElementById('pt-interval-style')) {
                const style = document.createElement('style');
                style.id = 'pt-interval-style';
                style.innerHTML = `
                    .pt-interval-btn.active { background-color: #e94560 !important; border-color: #e94560 !important; font-weight: bold; }
                    .pt-interval-btn:hover { background-color: #444; }
                `;
                document.head.appendChild(style);
            }
        }
    }

    // 주기적 저장 (5초마다 변경사항이 있으면 저장)
    setInterval(() => {
        if (ptDataDirty) {
            savePtData();
            ptDataDirty = false;
        }
    }, 5000);

    // [추가] 페이지 종료(탭 닫기, 새로고침) 직전에 변경사항 저장
    window.addEventListener('beforeunload', () => {
        if (ptDataDirty) {
            savePtData();
        }
    });

    // loadPtData가 async이므로 await 사용
    if (await loadPtData()) {
        ptDataDirty = true; // [추가] 필터링된 데이터로 갱신하기 위해 저장 플래그 설정
        renderPtChips();
        initProgramChart(ptTimeUnit);
        updateProgramChart(ptChartData, ptSubscribedCodes, ptFilterCodes, ptCodeNameMap, ptTimeUnit);
        
        if (ptSubscribedCodes.size > 0) {
            const statusDiv = document.getElementById('pt-status');
            if (statusDiv) {
                statusDiv.style.display = 'block';
                statusDiv.innerHTML = '<span>이전 구독 복구 중...</span>';
            }
            
            // SSE 연결 및 재구독 요청은 addProgramTrading 로직을 일부 재사용하거나 직접 수행
            // 여기서는 SSE 연결이 없으면 생성하고, 각 종목에 대해 subscribe API 호출
            if (!ptEventSource) {
                ptEventSource = new EventSource('/api/program-trading/stream');
                ptEventSource.onmessage = (event) => {
                    const d = JSON.parse(event.data);
                    handleProgramTradingData(d);
                };
                ptEventSource.onerror = () => {
                    if (statusDiv) statusDiv.innerHTML = '<span class="text-red">SSE 연결 끊김</span>';
                };
            }

            for (const code of ptSubscribedCodes) {
                try {
                    await fetch('/api/program-trading/subscribe', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ code })
                    });
                } catch (e) {
                    console.error(`Failed to resubscribe ${code}`, e);
                }
            }
            if (statusDiv) statusDiv.innerHTML = `<span class="text-green">구독 중: ${ptSubscribedCodes.size}개 종목</span>`;
        }
    }
}

// 캔들 차트 렌더링은 candle_chart.js로 분리됨

// ==========================================
// 10. 데이터 파일 백업 (Export/Import)
// ==========================================
function exportPtDataToFile() {
    const data = {
        chartData: ptChartData,
        subscribedCodes: Array.from(ptSubscribedCodes),
        codeNameMap: ptCodeNameMap,
        savedAt: new Date().toLocaleString()
    };
    const jsonStr = JSON.stringify(data, null, 2);
    const blob = new Blob([jsonStr], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    
    const a = document.createElement('a');
    a.href = url;
    a.download = `program_trading_backup_${new Date().toISOString().slice(0,10)}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function importPtDataFromFile() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = e => {
        const file = e.target.files[0];
        if (!file) return;
        
        const reader = new FileReader();
        reader.onload = event => {
            try {
                const data = JSON.parse(event.target.result);
                if (data.chartData && data.subscribedCodes) {
                    if(!confirm(`[${data.savedAt}] 시점의 데이터를 불러오시겠습니까?\n현재 데이터는 덮어씌워집니다.`)) return;

                    ptChartData = data.chartData;
                    ptSubscribedCodes = new Set(data.subscribedCodes);
                    ptCodeNameMap = data.codeNameMap || {};
                    
                    savePtData(); // 로컬 스토리지 즉시 반영
                    location.reload(); // 깔끔하게 새로고침하여 반영
                } else {
                    alert('올바르지 않은 데이터 파일입니다.');
                }
            } catch (err) {
                alert('파일 읽기 오류: ' + err);
            }
        };
        reader.readAsText(file);
    };
    input.click();
}
