/* view/web/static/js/stock.js — 종목 조회 (searchStock) */

/* ── 초성 추출 유틸 ── */
const _CHO = ['ㄱ','ㄲ','ㄴ','ㄷ','ㄸ','ㄹ','ㅁ','ㅂ','ㅃ','ㅅ','ㅆ','ㅇ','ㅈ','ㅉ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ'];
const _CHO_SET = new Set(_CHO);

function _getChosung(str) {
    let r = '';
    for (let i = 0; i < str.length; i++) {
        const c = str.charCodeAt(i);
        if (c >= 0xAC00 && c <= 0xD7A3) r += _CHO[Math.floor((c - 0xAC00) / 588)];
    }
    return r;
}

function _isChosung(str) {
    for (let i = 0; i < str.length; i++) { if (!_CHO_SET.has(str[i])) return false; }
    return str.length > 0;
}

/* ── 종목명 자동완성 (클라이언트 로컬 검색) ── */
(function() {
    let activeIndex = -1;

    document.addEventListener('DOMContentLoaded', function() {
        const input = document.getElementById('stock-code-input');
        const list = document.getElementById('stock-autocomplete-list');
        if (!input || !list) return;

        const stocks = (typeof ALL_STOCKS !== 'undefined') ? ALL_STOCKS : [];

        // 초성 인덱스를 1회 미리 계산
        for (let i = 0; i < stocks.length; i++) {
            stocks[i].ch = _getChosung(stocks[i].n);
        }

        input.addEventListener('input', function() {
            const q = input.value.trim();
            activeIndex = -1;

            if (!q) {
                list.innerHTML = '';
                list.style.display = 'none';
                return;
            }

            const results = [];
            const isDigit = /^\d+$/.test(q);
            const isCho = _isChosung(q);
            const qLower = q.toLowerCase();

            for (let i = 0; i < stocks.length && results.length < 20; i++) {
                const s = stocks[i];
                if (isDigit) {
                    // 숫자 → 종목코드 앞자리 매칭
                    if (s.c.startsWith(q)) results.push(s);
                } else if (isCho) {
                    // 초성 → 초성 필드에서 매칭
                    if (s.ch.includes(q)) results.push(s);
                } else {
                    // 일반 텍스트 → 종목명 부분 매칭
                    if (s.n.toLowerCase().includes(qLower)) results.push(s);
                }
            }
            renderAutocomplete(results);
        });

        input.addEventListener('keydown', function(e) {
            const items = list.querySelectorAll('li');
            if (!items.length) return;

            if (e.key === 'ArrowDown') {
                e.preventDefault();
                activeIndex = Math.min(activeIndex + 1, items.length - 1);
                updateActive(items);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                activeIndex = Math.max(activeIndex - 1, 0);
                updateActive(items);
            } else if (e.key === 'Enter' && activeIndex >= 0) {
                e.preventDefault();
                items[activeIndex].click();
            }
        });

        // 외부 클릭 시 닫기
        document.addEventListener('click', function(e) {
            if (!input.contains(e.target) && !list.contains(e.target)) {
                list.innerHTML = '';
                list.style.display = 'none';
            }
        });

        function renderAutocomplete(results) {
            list.innerHTML = '';
            if (!results.length) { list.style.display = 'none'; return; }
            results.forEach(item => {
                const li = document.createElement('li');
                li.textContent = `${item.n} (${item.c})`;
                li.addEventListener('click', function() {
                    input.value = item.c;
                    list.innerHTML = '';
                    list.style.display = 'none';
                    searchStock(item.c);
                });
                list.appendChild(li);
            });
            list.style.display = 'block';
        }

        function updateActive(items) {
            items.forEach(li => li.classList.remove('active'));
            if (activeIndex >= 0 && activeIndex < items.length) {
                items[activeIndex].classList.add('active');
            }
        }
    });
})();

/**
 * 입력값을 종목코드로 변환. 6자리 숫자면 그대로, 아니면 ALL_STOCKS에서 탐색.
 * 반환: { code, error } — code가 있으면 성공, error가 있으면 실패 메시지.
 */
function _resolveStockCode(raw) {
    // 6자리 숫자 → 종목코드 그대로
    if (/^\d{6}$/.test(raw)) return { code: raw };

    const stocks = (typeof ALL_STOCKS !== 'undefined') ? ALL_STOCKS : [];

    // 숫자지만 6자리 미만 → 코드 앞자리 매칭
    if (/^\d+$/.test(raw)) {
        const matches = stocks.filter(s => s.c.startsWith(raw));
        if (matches.length === 1) return { code: matches[0].code || matches[0].c };
        if (matches.length > 1) return { error: `'${raw}'으로 시작하는 종목이 ${matches.length}개 있습니다. 자동완성에서 선택해주세요.` };
        return { error: `'${raw}'으로 시작하는 종목코드를 찾을 수 없습니다.` };
    }

    // 초성 검색
    if (_isChosung(raw)) {
        const matches = stocks.filter(s => s.ch && s.ch.includes(raw));
        if (matches.length === 1) return { code: matches[0].c };
        if (matches.length > 1) return { error: `'${raw}' 초성에 해당하는 종목이 ${matches.length}개 있습니다. 자동완성에서 선택해주세요.` };
        return { error: `'${raw}' 초성에 해당하는 종목을 찾을 수 없습니다.` };
    }

    // 종목명 검색 — 정확히 일치하면 바로 사용, 아니면 부분 일치 시도
    const rawLower = raw.toLowerCase();
    const exact = stocks.find(s => s.n.toLowerCase() === rawLower);
    if (exact) return { code: exact.c };

    const partial = stocks.filter(s => s.n.toLowerCase().includes(rawLower));
    if (partial.length === 1) return { code: partial[0].c };
    if (partial.length > 1) return { error: `'${raw}'에 해당하는 종목이 ${partial.length}개 있습니다. 자동완성에서 선택해주세요.` };
    return { error: `'${raw}'에 해당하는 종목을 찾을 수 없습니다.` };
}

async function searchStock(codeOverride) {
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
        const res = await fetchWithTimeout(`/api/stock/${code}`);
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

        const styles = `
            <style>
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
