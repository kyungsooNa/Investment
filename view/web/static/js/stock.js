/* view/web/static/js/stock.js — 종목 조회 (searchStock) */

let _currentExchange = 'KRX';
let _currentStockCode = null;

// SSE 실시간 틱 수신 → 가격·전일대비·당일시세(고가·저가) UI 업데이트
document.addEventListener('stock-price-tick', function (e) {
    const tick = e.detail;
    const priceEl    = document.getElementById('rt-price');
    const changeEl   = document.getElementById('rt-change-rate');
    const highEl     = document.getElementById('rt-high');
    const lowEl      = document.getElementById('rt-low');
    if (!priceEl) return;  // 종목 조회 화면이 아닐 때는 무시

    const fmt = (n) => {
        const v = parseFloat(String(n).replace(/,/g, ''));
        return isNaN(v) ? String(n) : v.toLocaleString();
    };

    const sign    = tick.sign || '3';
    const isUp    = sign === '1' || sign === '2';
    const isDown  = sign === '4' || sign === '5';
    const prefix  = isUp ? '+' : (isDown ? '-' : '');
    const changeAbs = Math.abs(parseFloat(tick.change) || 0);
    const rateAbs   = Math.abs(parseFloat(tick.rate)   || 0);

    priceEl.textContent = fmt(tick.price) + '원';
    priceEl.className   = 'price ' + (isUp ? 'text-red' : isDown ? 'text-blue' : '');
    if (changeEl) changeEl.textContent = `전일대비: ${prefix}${fmt(changeAbs)} (${isUp ? '+' : isDown ? '-' : ''}${rateAbs.toFixed(2)}%)`;
    if (highEl && tick.high > 0) highEl.textContent = fmt(tick.high);
    if (lowEl  && tick.low  > 0) lowEl.textContent  = fmt(tick.low);
});

/* ── 국내/해외 조회 모드 토글 (V2.1) ── */
function setStockMarketMode(mode) {
    const domesticRow = document.getElementById('domestic-stock-row');
    const overseasRow = document.getElementById('overseas-stock-row');
    const domesticBtn = document.getElementById('stock-mode-domestic');
    const overseasBtn = document.getElementById('stock-mode-overseas');
    const isOverseas = mode === 'overseas';

    if (domesticRow) domesticRow.style.display = isOverseas ? 'none' : '';
    if (overseasRow) overseasRow.style.display = isOverseas ? '' : 'none';
    if (domesticBtn) domesticBtn.classList.toggle('active', !isOverseas);
    if (overseasBtn) overseasBtn.classList.toggle('active', isOverseas);

    // 모드 전환 시 결과/차트 영역 초기화 (국내↔해외 잔여 UI 방지)
    const resultDiv = document.getElementById('stock-result');
    if (resultDiv) resultDiv.innerHTML = '';
    const chartCard = document.getElementById('stock-chart-card');
    const sectionStock = document.getElementById('section-stock');
    if (chartCard && sectionStock && chartCard.parentElement !== sectionStock) {
        sectionStock.appendChild(chartCard);
    }
    if (chartCard) chartCard.style.display = 'none';
}

/* overseas_us가 enabled된 run에서만 해외 조회 허용 (fail-close) */
async function _ensureOverseasEnabledForStock() {
    try {
        const res = await fetchWithTimeout('/api/market-mode', {}, 5000);
        if (!res.ok) return false;
        const json = await res.json();
        return Array.isArray(json.enabled_market_modes) && json.enabled_market_modes.includes('overseas_us');
    } catch (_) {
        return false;
    }
}

async function searchOverseasStock() {
    const symbolInput = document.getElementById('overseas-stock-symbol');
    const exchangeSel = document.getElementById('overseas-stock-exchange');
    const symbol = symbolInput ? symbolInput.value.trim().toUpperCase() : '';
    const exchange = exchangeSel ? exchangeSel.value : 'NASD';
    const resultDiv = document.getElementById('stock-result');
    if (!symbol) {
        alert('심볼을 입력하세요.');
        return;
    }
    if (symbolInput) symbolInput.value = symbol;
    if (!resultDiv) return;

    // 해외 모드에서는 국내 전용 차트 카드를 숨긴다.
    const chartCard = document.getElementById('stock-chart-card');
    if (chartCard) chartCard.style.display = 'none';

    showLoading(resultDiv, '해외 종목 조회 중...');
    try {
        const enabled = await _ensureOverseasEnabledForStock();
        if (!enabled) {
            resultDiv.innerHTML = '<p class="error">해외주식 조회는 overseas_us가 enabled된 run에서만 사용할 수 있습니다.</p>';
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
        const usd = (v) => {
            const n = Number(v);
            return Number.isFinite(n) ? '$' + n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '-';
        };
        const num = (v) => {
            const n = Number(String(v ?? '').replace(/,/g, ''));
            return Number.isFinite(n) ? n.toLocaleString() : '-';
        };
        resultDiv.innerHTML = `
            <div class="card stock-info-box">
                <h3 style="margin:0;">${data.symbol || symbol} <span style="color:#aaa;font-size:0.85rem;">${data.exchange || exchange} ${data.currency || 'USD'}</span></h3>
                <p class="price ${rateClass}">${usd(data.price)}</p>
                <p class="change-rate">등락률: <span class="${rateClass}">${Number.isFinite(rate) ? (rate > 0 ? '+' : '') + rate.toFixed(2) + '%' : '-'}</span></p>
                <p>거래량: ${num(data.volume)}</p>
                <p>시각: ${data.timestamp || '-'}</p>
            </div>
        `;
    } catch (e) {
        if (e.name === 'AbortError') {
            resultDiv.innerHTML = `<p class="error">요청 시간이 초과되었습니다. 다시 시도해주세요.</p>`;
        } else {
            resultDiv.innerHTML = `<p class="error">통신 오류: ${e.message || e}</p>`;
        }
    }
}

function changeExchange(exchange, btn) {
    if (_currentExchange === exchange) return;
    _currentExchange = exchange;
    // 버튼 active 상태 갱신
    const btns = document.querySelectorAll('.exchange-btn');
    btns.forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');
    // 현재 종목이 있으면 재조회
    if (_currentStockCode) {
        searchStock(_currentStockCode, exchange);
    }
}

/* ── 종목명 자동완성 (autocomplete.js 모듈 사용) ── */
StockAutocomplete({
    inputId: 'stock-code-input',
    listId: 'stock-autocomplete-list',
    onSelect: function(code) {
        const input = document.getElementById('stock-code-input');
        if (input) input.value = code;
        searchStock(code);
    },
    onConfirm: function() { searchStock(); }
});

/* ── Pjax 재방문 시 자동완성 재초기화 ── */
document.addEventListener('pjax:ready', (e) => {
    if (e.detail?.path !== '/stock') return;
    StockAutocomplete({
        inputId: 'stock-code-input',
        listId: 'stock-autocomplete-list',
        onSelect: function(code) {
            const input = document.getElementById('stock-code-input');
            if (input) input.value = code;
            searchStock(code);
        },
        onConfirm: function() { searchStock(); }
    });

        const urlParams = new URLSearchParams(window.location.search);
        const code = urlParams.get('code');
        if (code) {
            searchStock(code);
        }
});

document.addEventListener('DOMContentLoaded', () => {
    if (window.location.pathname !== '/stock') return;
    
    const urlParams = new URLSearchParams(window.location.search);
    const code = urlParams.get('code');
    if (code) {
        searchStock(code);
    }
});

/**
 * 입력값을 종목코드로 변환. 6자리 숫자면 그대로, 아니면 ALL_STOCKS에서 탐색.
 * 반환: { code, error } — code가 있으면 성공, error가 있으면 실패 메시지.
 */
function _resolveStockCode(raw) {
    // 6자리 숫자 → 종목코드 그대로
    if (/^\d{6}$/.test(raw)) return { code: raw };

    const stocks = (ALL_STOCKS && Array.isArray(ALL_STOCKS)) ? ALL_STOCKS : [];

    // 숫자지만 6자리 미만 → 코드 앞자리 매칭
    if (/^\d+$/.test(raw)) {
        const matches = stocks.filter(s => s.c.startsWith(raw));
        if (matches.length === 1) return { code: matches[0].code || matches[0].c };
        if (matches.length > 1) return { error: `'${raw}'으로 시작하는 종목이 ${matches.length}개 있습니다. 자동완성에서 선택해주세요.` };
        return { error: `'${raw}'으로 시작하는 종목코드를 찾을 수 없습니다.` };
    }

    // 종목명 검색 — exact 우선, 아니면 혼합(초성+텍스트) 부분 매칭
    const exact = stocks.find(s => s.n.toLowerCase() === raw.toLowerCase());
    if (exact) return { code: exact.c };

    const partial = stocks.filter(s => _matchMixed(raw, s.n));
    if (partial.length === 1) return { code: partial[0].c };
    if (partial.length > 1) return { error: `'${raw}'에 해당하는 종목이 ${partial.length}개 있습니다. 자동완성에서 선택해주세요.` };
    return { error: `'${raw}'에 해당하는 종목을 찾을 수 없습니다.` };
}

async function searchStock(codeOverride, exchangeOverride) {
    const input = document.getElementById('stock-code-input');
    const raw = codeOverride || (input ? input.value.trim() : '');
    if (!raw) {
        alert("종목코드 또는 종목명을 입력하세요.");
        return;
    }

    // 자동완성 드롭다운 닫기
    const acList = document.getElementById('stock-autocomplete-list');
    if (acList) { acList.innerHTML = ''; acList.style.display = 'none'; }

    // 클라이언트에서 종목코드 변환
    const resolved = _resolveStockCode(raw);
    if (resolved.error) {
        const resultDiv = document.getElementById('stock-result');
        if (resultDiv) resultDiv.innerHTML = `<p class="error">${resolved.error}</p>`;
        return;
    }
    const code = resolved.code;
    _currentStockCode = code;
    if (exchangeOverride) _currentExchange = exchangeOverride;
    if (input) input.value = code;

    const resultDiv = document.getElementById('stock-result');
    const chartCard = document.getElementById('stock-chart-card');

    // 차트 카드 대피 (innerHTML 덮어쓰기 방지)
    const sectionStock = document.getElementById('section-stock');
    if (chartCard && sectionStock && chartCard.parentElement !== sectionStock) {
        sectionStock.appendChild(chartCard);
        chartCard.style.display = 'none';
    }

    if (!resultDiv) return;
    showLoading(resultDiv, '종목 정보 조회 중...');

    try {
        const res = await fetchWithTimeout(`/api/stock/${code}?exchange=${_currentExchange}`);
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
        const fmcap = (n) => {
            if (n === null || n === undefined || String(n).trim() === '' || String(n).toLowerCase() === 'n/a') return 'N/A';
            try {
                const val = parseFloat(String(n).replace(/,/g, ''));
                if (isNaN(val)) return n;
                if (val >= 10000) {
                    const jo = Math.floor(val / 10000);
                    const uk = val % 10000;
                    return jo.toLocaleString() + "조" + (uk > 0 ? " " + uk.toLocaleString() + "억" : "");
                }
                return val.toLocaleString() + "억";
            } catch { return n; }
        };

        const changeVal = parseInt(data.change) || 0;
        const changeClass = (changeVal > 0) ? 'text-red' : (changeVal < 0 ? 'text-blue' : '');
        const sign = data.sign || '';
        const newHighBadge = (data.is_new_high) ? '<span class="badge new-high">🔥 신고가</span>' : '';
        const newLowBadge = (data.is_new_low) ? '<span class="badge new-low">💧 신저가</span>' : '';

        const styles = `
            <style>
                .exchange-toggle { display: flex; gap: 4px; }
                .exchange-btn { padding: 4px 10px; font-size: 12px; background: var(--bg-primary, #f5f5f5); border: 1px solid var(--border, #ccc); color: var(--text-secondary, #555); border-radius: 4px; cursor: pointer; }
                .exchange-btn.active { background: var(--accent, #4a90e2); color: white; border-color: var(--accent, #4a90e2); font-weight: bold; }
                .exchange-btn:hover { background: var(--bg-card, #e8e8e8); }
                .stock-title { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
                .badge.new-high {
                    background-color: #ff6b35; color: white; font-size: 0.8em;
                    padding: 0.2em 0.6em; border-radius: 10px; font-weight: bold; white-space: nowrap;
                }
                .badge.new-low {
                    background-color: #1e90ff; color: white; font-size: 0.8em;
                    padding: 0.2em 0.6em; border-radius: 10px; font-weight: bold; white-space: nowrap;
                }
                .stock-info-box .stock-details {
                    display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-top: 1.5rem;
                }
                .stock-info-box .detail-group {
                    background-color: var(--background-light); border: 1px solid var(--border-color);
                    border-radius: 8px; padding: 1rem;
                }
                .stock-info-box .detail-group.full-width { grid-column: 1 / -1; }
                .stock-info-box .detail-group h4 {
                    margin-top: 0; margin-bottom: 0.8rem;
                    border-bottom: 2px solid var(--accent); padding-bottom: 0.4rem; color: var(--text-primary);
                }
                .stock-info-box .detail-group p {
                    margin: 0.4rem 0; display: flex; justify-content: space-between;
                }
                 .stock-info-box .detail-group p strong { color: var(--text-secondary); }
                 .stock-info-box .price.text-red { color: #e94560; }
                 .stock-info-box .price.text-blue { color: #1e90ff; }
                 .fav-star-btn {
                     position: absolute;
                     top: 16px;
                     right: 16px;
                     background: var(--bg-primary, #f5f5f5);
                     border: 1px solid var(--border, #ccc);
                     border-radius: 8px;
                     width: 38px;
                     height: 38px;
                     display: flex;
                     align-items: center;
                     justify-content: center;
                     font-size: 1.5rem;
                     cursor: pointer;
                     padding: 0;
                     color: #aaa;
                     transition: all 0.2s;
                     box-shadow: 0 2px 4px rgba(0,0,0,0.05);
                 }
                 .fav-star-btn:hover { transform: scale(1.05); background: var(--bg-card, #e8e8e8); }
                 .fav-star-btn.active { color: #ffc107; border-color: #ffc107; }
                 .detail-loading { color: var(--text-secondary, #999); font-style: italic; }
                 .stage-badge-box {
                     position: absolute;
                     top: 16px;
                     right: 142px;
                     background: var(--bg-primary, #f5f5f5);
                     border: 1px solid var(--border, #ccc);
                     border-radius: 8px;
                     height: 38px;
                     display: none;
                     align-items: center;
                     justify-content: center;
                     padding: 0 10px;
                     font-weight: bold;
                     font-size: 0.85rem;
                     box-shadow: 0 2px 4px rgba(0,0,0,0.05);
                 }
                 .rs-rating-box {
                     position: absolute;
                     top: 16px;
                     right: 64px;
                     background: var(--bg-primary, #f5f5f5);
                     border: 1px solid var(--border, #ccc);
                     border-radius: 8px;
                     height: 38px;
                     display: flex;
                     align-items: center;
                     justify-content: center;
                     padding: 0 10px;
                     font-weight: bold;
                     color: var(--text-secondary, #555);
                     box-shadow: 0 2px 4px rgba(0,0,0,0.05);
                     font-size: 0.9rem;
                 }
                 .rs-rating-box .val {
                     color: var(--text-primary, #333);
                     margin-left: 6px;
                     font-size: 1.2rem;
                 }
            </style>
        `;

        const exchangeButtons = `
            <div class="exchange-toggle" style="margin-bottom:8px;">
                <button class="exchange-btn${_currentExchange==='KRX'?' active':''}" onclick="changeExchange('KRX', this)">KRX</button>
                <button class="exchange-btn${_currentExchange==='NXT'?' active':''}" onclick="changeExchange('NXT', this)">NXT</button>
                <button class="exchange-btn${_currentExchange==='UN'?' active':''}" onclick="changeExchange('UN', this)">통합</button>
            </div>
        `;

        // Phase 1 렌더: 캐시/DB 데이터로 즉시 표기 (상세 필드는 로딩 placeholder)
        const loading = '<span class="detail-loading">조회 중...</span>';

        resultDiv.innerHTML = styles + `
            <div class="card stock-info-box" style="position: relative;">
                <div id="stage-badge-container" class="stage-badge-box" title="Minervini Stage (1~4단계)">
                    <span id="stage-badge-val">-</span>
                </div>
                <div id="rs-rating-container" class="rs-rating-box" style="display: none;" title="IBD/오닐 RS Rating">
                    RS<span id="rs-rating-val" class="val">-</span>
                </div>
                <button id="fav-toggle-btn" class="fav-star-btn" onclick="toggleFavorite('${data.code}')" title="관심종목">☆</button>
                ${exchangeButtons}
                <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
                    <h3 class="stock-title" style="margin:0;" id="stock-title-area">${data.name} (${data.code}) ${newHighBadge}${newLowBadge}</h3>
                </div>
                <p id="rt-price" class="price ${changeClass}">${fnum(data.price, '원')}</p>
                <p id="rt-change-rate" class="change-rate">전일대비: ${sign}${fnum(data.change_absolute || Math.abs(data.change))} (${frate(data.rate)})</p>

                <div id="chart-placeholder" style="margin: 16px 0;"></div>

                <div class="stock-details">
                    <div class="detail-group">
                        <h4>ℹ️ 기본 정보</h4>
                        <p><strong>업종:</strong> <span id="detail-sector">${data.bstp_kor_isnm || loading}</span></p>
                        <p><strong>상태:</strong> <span id="detail-status">${data.iscd_stat_cls_code_desc || loading}</span></p>
                        <p><strong>시가총액:</strong> <span id="detail-mcap">${data.hts_avls ? fmcap(data.hts_avls) : loading}</span></p>
                    </div>
                    <div class="detail-group">
                        <h4>📊 당일 시세</h4>
                        <p><strong>시가:</strong> <span>${fnum(data.open)}</span></p>
                        <p><strong>고가:</strong> <span id="rt-high">${fnum(data.high)}</span></p>
                        <p><strong>저가:</strong> <span id="rt-low">${fnum(data.low)}</span></p>
                        <p><strong>기준가:</strong> <span>${fnum(data.prev_close)}</span></p>
                    </div>
                    <div class="detail-group">
                        <h4>📈 거래 정보</h4>
                        <p><strong>누적 거래량:</strong> <span>${fnum(data.acml_vol, ' 주')}</span></p>
                        <p><strong>누적 거래대금:</strong> <span>${formatTradingValue(data.acml_tr_pbmn)}</span></p>
                        <p><strong>전일 대비 거래량:</strong> <span id="detail-vol-rate">${data.prdy_vrss_vol_rate != null ? frate(data.prdy_vrss_vol_rate) : loading}</span></p>
                    </div>
                    <div class="detail-group">
                        <h4>🌐 수급 정보</h4>
                        <p><strong>외국인 순매수:</strong> <span id="detail-frgn">${data.frgn_ntby_qty != null ? fnum(data.frgn_ntby_qty, ' 주') : loading}</span></p>
                        <p><strong>프로그램 순매수:</strong> <span id="detail-pgtr">${data.pgtr_ntby_qty != null ? fnum(data.pgtr_ntby_qty, ' 주') : loading}</span></p>
                    </div>
                     <div class="detail-group full-width">
                        <h4>💹 투자 지표</h4>
                        <div style="display: flex; justify-content: space-around;">
                           <p style="flex-direction: column; align-items: center;"><strong>PER:</strong> <span id="detail-per">${data.per != null ? frate(data.per, ' 배') : loading}</span></p>
                           <p style="flex-direction: column; align-items: center;"><strong>PBR:</strong> <span id="detail-pbr">${data.pbr != null ? frate(data.pbr, ' 배') : loading}</span></p>
                           <p style="flex-direction: column; align-items: center;"><strong>EPS:</strong> <span id="detail-eps">${data.eps != null ? fnum(data.eps) : loading}</span></p>
                           <p style="flex-direction: column; align-items: center;"><strong>BPS:</strong> <span id="detail-bps">${data.bps != null ? fnum(data.bps) : loading}</span></p>
                        </div>
                    </div>
                    <div class="detail-group full-width">
                        <h4>📅 주요 가격 정보</h4>
                        <p><strong>52주 최고:</strong> <span id="detail-w52h">${data.w52_hgpr ? fnum(data.w52_hgpr) + ' (' + (data.w52_hgpr_date||'') + ') | 대비: ' + frate(data.w52_hgpr_vrss_prpr_ctrt) : loading}</span></p>
                        <p><strong>52주 최저:</strong> <span id="detail-w52l">${data.w52_lwpr ? fnum(data.w52_lwpr) + ' (' + (data.w52_lwpr_date||'') + ') | 대비: ' + frate(data.w52_lwpr_vrss_prpr_ctrt) : loading}</span></p>
                        <p><strong>250일 최고:</strong> <span id="detail-d250h">${data.d250_hgpr ? fnum(data.d250_hgpr) + ' (' + (data.d250_hgpr_date||'') + ') | 대비: ' + frate(data.d250_hgpr_vrss_prpr_rate) : loading}</span></p>
                        <p><strong>250일 최저:</strong> <span id="detail-d250l">${data.d250_lwpr ? fnum(data.d250_lwpr) + ' (' + (data.d250_lwpr_date||'') + ') | 대비: ' + frate(data.d250_lwpr_vrss_prpr_rate) : loading}</span></p>
                    </div>
                    <div class="detail-group full-width">
                        <h4>📋 기타 상태</h4>
                        <p><strong>신용 가능:</strong> <span id="detail-crdt">${data.crdt_able_yn || loading}</span></p>
                        <p><strong>관리 종목:</strong> <span id="detail-mang">${data.mang_issu_cls_code || loading}</span></p>
                        <p><strong>단기 과열:</strong> <span id="detail-short">${data.short_over_yn || loading}</span></p>
                        <p><strong>정리 매매:</strong> <span id="detail-sltr">${data.sltr_yn || loading}</span></p>
                    </div>
                </div>
            </div>
        `;

        // 차트 카드를 원하는 위치(placeholder)로 이동
        const placeholder = document.getElementById('chart-placeholder');
        if (chartCard && placeholder) {
            placeholder.appendChild(chartCard);
        }

        const orderCodeInput = document.getElementById('order-code');
        if (orderCodeInput) {
            orderCodeInput.value = code;
        }

        // 차트 로드 및 렌더링
        if (typeof loadAndRenderStockChart === 'function') {
            loadAndRenderStockChart(code);
        }

        // 관심종목 버튼 초기 상태 설정
        _updateFavBtn(code);

        // Phase 2: 증권사 API 상세 정보 백그라운드 로드
        _loadStockDetail(code);
        _loadRsRating(code);
        _loadStage(code);

    } catch (e) {
        console.error("Error in searchStock:", e);
        if (e.name === 'AbortError') {
            resultDiv.innerHTML = `<p class="error">요청 시간이 초과되었습니다. 다시 시도해주세요.</p>`;
        } else {
            resultDiv.innerHTML = `<p class="error">오류 발생: ${e.message}</p>`;
        }
        if(chartCard) chartCard.style.display = 'none';
    }
}

/* ── 관심종목 토글 버튼 ── */
async function _updateFavBtn(code) {
    const btn = document.getElementById('fav-toggle-btn');
    if (!btn) return;
    try {
        const resp = await fetch(`/api/favorite/${code}/status`);
        if (!resp.ok) return;
        const data = await resp.json();
        _setFavBtnState(btn, data.is_favorite);
    } catch (_) {}
}

function _setFavBtnState(btn, isFav) {
    if (isFav) {
        btn.textContent = '★';
        btn.classList.add('active');
        btn.title = '관심종목 해제';
    } else {
        btn.textContent = '☆';
        btn.classList.remove('active');
        btn.title = '관심종목 추가';
    }
}

async function toggleFavorite(code) {
    const btn = document.getElementById('fav-toggle-btn');
    if (!btn) return;
    const isFav = btn.classList.contains('active');
    try {
        const method = isFav ? 'DELETE' : 'POST';
        const resp = await fetch(`/api/favorite/${code}`, { method });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        _setFavBtnState(btn, !isFav);
        if (typeof showToast === 'function') {
            showToast(isFav ? '관심종목에서 해제되었습니다.' : '관심종목에 추가되었습니다.', 'success');
        }
    } catch (e) {
        if (typeof showToast === 'function') showToast(`오류: ${e.message}`, 'error');
    }
}

/* ── Minervini Stage 로드 ── */
async function _loadStage(code) {
    try {
        const res = await fetchWithTimeout(`/api/stock/${code}/stage`, {}, 8000);
        if (!res.ok) return;
        const json = await res.json();
        if (json.rt_cd === "0" && json.data && json.data.stage > 0) {
            const container = document.getElementById('stage-badge-container');
            const valEl = document.getElementById('stage-badge-val');
            if (container && valEl) {
                const stage = json.data.stage;
                const labels = { 1: 'S1 무관심', 2: 'S2 상승', 3: 'S3 고점', 4: 'S4 하락' };
                const colors = { 1: '#6c757d', 2: '#28a745', 3: '#fd7e14', 4: '#dc3545' };
                valEl.textContent = labels[stage] || `S${stage}`;
                container.style.background = colors[stage] || '#6c757d';
                container.style.color = '#fff';
                container.style.borderColor = colors[stage] || '#6c757d';
                container.style.display = 'flex';
                // 툴팁: API에서 반환한 판정 이유가 있으면 title에 설정
                const reason = json.data.reason || '';
                if (reason && reason.trim().length > 0) {
                    container.title = `판정 이유: ${reason}`;
                } else {
                    container.title = 'Minervini Stage (1~4단계)';
                }
            }
        }
    } catch (e) {
        console.debug('[stock] Minervini Stage 조회 실패:', e.message);
    }
}

/* ── RS Rating 로드 ── */
async function _loadRsRating(code) {
    try {
        const res = await fetchWithTimeout(`/api/stock/${code}/rs_rating`, {}, 5000);
        if (!res.ok) return;
        const json = await res.json();
        if (json.rt_cd === "0" && json.data) {
            const container = document.getElementById('rs-rating-container');
            const valEl = document.getElementById('rs-rating-val');
            if (container && valEl) {
                const rating = json.data.rs_rating;
                valEl.textContent = rating || '-';
                container.style.display = 'flex';
                if (rating >= 80) valEl.style.color = '#e94560';
                else if (rating >= 50) valEl.style.color = '#feca57';
                else valEl.style.color = '#1e90ff';
            }
        }
    } catch (e) {
        console.debug('[stock] RS Rating 조회 실패:', e.message);
    }
}

/* ── Phase 2: 증권사 API 상세 정보 로드 & DOM 업데이트 ── */
async function _loadStockDetail(code) {
    try {
        const res = await fetchWithTimeout(`/api/stock/${code}/detail?exchange=${_currentExchange}`, {}, 15000);
        if (!res.ok) return;
        const json = await res.json();
        if (json.rt_cd !== "0" || !json.data) return;
        _updateDetailDOM(json.data);
    } catch (e) {
        // 상세 조회 실패 시 조용히 무시 (Phase 1 데이터는 이미 표기됨)
        console.debug('[stock] 상세 정보 조회 실패:', e.message);
    }
}

function _updateDetailDOM(data) {
    const fnum = (n, suffix = "") => {
        if (n === null || n === undefined || String(n).trim() === '' || String(n).toLowerCase() === 'n/a') return 'N/A';
        try {
            const val = parseFloat(String(n).replace(/,/g, ''));
            if (isNaN(val)) return String(n);
            return val.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 }) + suffix;
        } catch { return String(n); }
    };
    const frate = (n, suffix = "%") => {
        if (n === null || n === undefined || String(n).trim() === '' || String(n).toLowerCase() === 'n/a') return 'N/A';
        try {
            const val = parseFloat(String(n).replace(/,/g, ''));
            if (isNaN(val)) return String(n);
            return (val > 0 ? '+' : '') + val.toFixed(2) + suffix;
        } catch { return String(n); }
    };
    const fmcap = (n) => {
        if (n === null || n === undefined || String(n).trim() === '' || String(n).toLowerCase() === 'n/a') return 'N/A';
        try {
            const val = parseFloat(String(n).replace(/,/g, ''));
            if (isNaN(val)) return String(n);
            if (val >= 10000) {
                const jo = Math.floor(val / 10000);
                const uk = val % 10000;
                return jo.toLocaleString() + "조" + (uk > 0 ? " " + uk.toLocaleString() + "억" : "");
            }
            return val.toLocaleString() + "억";
        } catch { return String(n); }
    };

    const set = (id, val) => { const el = document.getElementById(id); if (el && val !== undefined) el.innerHTML = val; };

    // ℹ️ 기본 정보
    set('detail-sector', data.bstp_kor_isnm || 'N/A');
    set('detail-status', data.iscd_stat_cls_code_desc || 'N/A');
    set('detail-mcap', data.hts_avls ? fmcap(data.hts_avls) : 'N/A');

    // 📈 거래 정보
    set('detail-vol-rate', frate(data.prdy_vrss_vol_rate));

    // 🌐 수급 정보
    set('detail-frgn', fnum(data.frgn_ntby_qty, ' 주'));
    set('detail-pgtr', fnum(data.pgtr_ntby_qty, ' 주'));

    // 💹 투자 지표
    set('detail-per', frate(data.per, ' 배'));
    set('detail-pbr', frate(data.pbr, ' 배'));
    set('detail-eps', fnum(data.eps));
    set('detail-bps', fnum(data.bps));

    // 📅 주요 가격 정보
    if (data.w52_hgpr) set('detail-w52h', `${fnum(data.w52_hgpr)} (${data.w52_hgpr_date || ''}) | 대비: ${frate(data.w52_hgpr_vrss_prpr_ctrt)}`);
    if (data.w52_lwpr) set('detail-w52l', `${fnum(data.w52_lwpr)} (${data.w52_lwpr_date || ''}) | 대비: ${frate(data.w52_lwpr_vrss_prpr_ctrt)}`);
    if (data.d250_hgpr) set('detail-d250h', `${fnum(data.d250_hgpr)} (${data.d250_hgpr_date || ''}) | 대비: ${frate(data.d250_hgpr_vrss_prpr_rate)}`);
    if (data.d250_lwpr) set('detail-d250l', `${fnum(data.d250_lwpr)} (${data.d250_lwpr_date || ''}) | 대비: ${frate(data.d250_lwpr_vrss_prpr_rate)}`);

    // 📋 기타 상태
    set('detail-crdt', data.crdt_able_yn || 'N/A');
    set('detail-mang', data.mang_issu_cls_code || 'N/A');
    set('detail-short', data.short_over_yn || 'N/A');
    set('detail-sltr', data.sltr_yn || 'N/A');

    // 신고가/신저가 배지 업데이트
    const titleArea = document.getElementById('stock-title-area');
    if (titleArea && (data.is_new_high !== undefined || data.is_new_low !== undefined)) {
        const newHighBadge = data.is_new_high ? '<span class="badge new-high">🔥 신고가</span>' : '';
        const newLowBadge  = data.is_new_low  ? '<span class="badge new-low">💧 신저가</span>'  : '';
        // 배지만 교체 (종목명·코드 유지)
        const badges = titleArea.querySelectorAll('.badge');
        badges.forEach(b => b.remove());
        if (newHighBadge) titleArea.insertAdjacentHTML('beforeend', newHighBadge);
        if (newLowBadge)  titleArea.insertAdjacentHTML('beforeend', newLowBadge);
    }
}
