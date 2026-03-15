/* view/web/static/js/marketcap.js — 시가총액 랭킹 */

async function loadTopMarketCap(market = '0001') {
    document.querySelectorAll('#section-marketcap .ranking-tab').forEach(b => {
        b.classList.remove('active');
        if (b.dataset.market === market) b.classList.add('active');
    });

    const div = document.getElementById('marketcap-result');
    showLoading(div, '시가총액 랭킹 조회 중...');
    try {
        const res = await fetchWithTimeout(`/api/top-market-cap?limit=30&market=${market}`);
        const json = await res.json();
        if (json.rt_cd !== "0") {
            div.innerHTML = `<p class="error">실패: ${json.msg1}</p>`;
            return;
        }
        let html = `
            <div class="card">
            <table class="data-table">
            <thead><tr><th>순위</th><th>종목명</th><th>현재가</th><th>시가총액</th></tr></thead>
            <tbody>
        `;
        json.data.forEach((item, idx) => {
            const rate = parseFloat(item.change_rate || 0);
            const color = rate > 0 ? 'text-red' : (rate < 0 ? 'text-blue' : '');
            const rateStr = rate > 0 ? `+${rate}%` : `${rate}%`;
            html += `
                <tr>
                    <td>${item.rank || (idx+1)}</td>
                    <td><a href="/?code=${item.code}" target="_blank" class="stock-link">${item.name}(${item.code})</a></td>
                    <td>${parseInt(item.current_price).toLocaleString()} <small class="${color}">(${rateStr})</small></td>
                    <td>${formatMarketCap(item.market_cap)}</td>
                </tr>
            `;
        });
        html += "</tbody></table></div>";
        div.innerHTML = html;
    } catch(e) {
        if (e.name === 'AbortError') {
            div.innerHTML = '<p class="error">요청 시간이 초과되었습니다. 다시 시도해주세요.</p>';
        } else {
            div.innerHTML = "오류: " + e;
        }
    }
}
