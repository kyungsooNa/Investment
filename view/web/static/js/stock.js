/* view/web/static/js/stock.js — 종목 조회 (searchStock) */

async function searchStock(codeOverride) {
    const input = document.getElementById('stock-code-input');
    const code = codeOverride || (input ? input.value.trim() : '');
    if (!code) {
        alert("종목코드를 입력하세요.");
        return;
    }

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
        resultDiv.innerHTML = `<p class="error">오류 발생: ${e.message}</p>`;
        if(chartCard) chartCard.style.display = 'none';
    }
}
