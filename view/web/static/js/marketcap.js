/* view/web/static/js/marketcap.js — 시가총액 랭킹 */

async function loadTopMarketCap(market = '0001') {
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
                    <td><a href="/?code=${item.code}" style="color:var(--accent); text-decoration:none;">${item.name}</a></td>
                    <td><a href="/?code=${item.code}" style="color:var(--accent); text-decoration:none;">${item.code}</a></td>
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
