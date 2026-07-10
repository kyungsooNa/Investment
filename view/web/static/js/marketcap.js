/* view/web/static/js/marketcap.js — 시가총액 랭킹 */

let _marketCapRequestSequence = 0;

function _escapeMarketCapHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
    })[char]);
}

function _toFiniteMarketCapNumber(value) {
    if (value === null || value === undefined || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
}

function _formatMarketCapPrice(value) {
    const number = _toFiniteMarketCapNumber(value);
    return number === null ? '-' : Math.trunc(number).toLocaleString();
}

function _formatMarketCapRate(value) {
    const rate = _toFiniteMarketCapNumber(value);
    if (rate === null) return { color: '', text: '-' };

    const normalizedRate = Object.is(rate, -0) ? 0 : rate;
    const color = normalizedRate > 0 ? 'text-red' : (normalizedRate < 0 ? 'text-blue' : '');
    const sign = normalizedRate > 0 ? '+' : '';
    return { color, text: `${sign}${normalizedRate}%` };
}

function _formatMarketCapValue(value) {
    const number = _toFiniteMarketCapNumber(value);
    return number === null ? '-' : formatMarketCap(Math.trunc(number));
}

function _showMarketCapError(div, message) {
    div.innerHTML = `<p class="error">${_escapeMarketCapHtml(message)}</p>`;
}

function _showMarketCapEmpty(div) {
    div.innerHTML = '<p class="empty">조회 결과가 없습니다.</p>';
}

async function loadTopMarketCap(market = '0001') {
    const requestSequence = ++_marketCapRequestSequence;
    const isLatestRequest = () => requestSequence === _marketCapRequestSequence;

    document.querySelectorAll('#section-marketcap .ranking-tab').forEach(b => {
        b.classList.remove('active');
        if (b.dataset.market === market) b.classList.add('active');
    });

    const div = document.getElementById('marketcap-result');
    showLoading(div, '시가총액 랭킹 조회 중...');
    try {
        const query = new URLSearchParams({ limit: '30', market }).toString();
        const res = await fetchWithTimeout(`/api/top-market-cap?${query}`);
        if (!isLatestRequest()) return;

        if (!res.ok) {
            _showMarketCapError(div, `요청에 실패했습니다. (HTTP ${res.status})`);
            return;
        }

        let json;
        try {
            json = await res.json();
        } catch (_) {
            if (!isLatestRequest()) return;
            _showMarketCapError(div, '응답을 처리하지 못했습니다. 다시 시도해주세요.');
            return;
        }
        if (!isLatestRequest()) return;

        if (!json || typeof json !== 'object') {
            _showMarketCapError(div, '응답 형식이 올바르지 않습니다.');
            return;
        }
        if (json.rt_cd !== '0') {
            _showMarketCapError(div, `실패: ${json.msg1 || '시가총액 랭킹 조회에 실패했습니다.'}`);
            return;
        }
        if (!Array.isArray(json.data)) {
            _showMarketCapError(div, '응답 형식이 올바르지 않습니다.');
            return;
        }
        if (json.data.length === 0) {
            _showMarketCapEmpty(div);
            return;
        }

        let html = `
            <div class="card">
            <table class="data-table">
            <thead><tr><th>순위</th><th>종목명</th><th>현재가</th><th>시가총액</th></tr></thead>
            <tbody>
        `;
        json.data.forEach((item, idx) => {
            const row = item && typeof item === 'object' ? item : {};
            const rate = _formatMarketCapRate(row.change_rate);
            const code = String(row.code ?? '');
            html += `
                <tr>
                    <td>${_escapeMarketCapHtml(row.rank || (idx + 1))}</td>
                    <td><a href="/stock?code=${encodeURIComponent(code)}" target="_blank" class="stock-link">${_escapeMarketCapHtml(row.name)}(${_escapeMarketCapHtml(code)})</a></td>
                    <td>${_formatMarketCapPrice(row.current_price)} <small class="${rate.color}">(${rate.text})</small></td>
                    <td>${_formatMarketCapValue(row.market_cap)}</td>
                </tr>
            `;
        });
        html += '</tbody></table></div>';
        div.innerHTML = html;
    } catch (e) {
        if (!isLatestRequest()) return;
        if (e.name === 'AbortError') {
            _showMarketCapError(div, '요청 시간이 초과되었습니다. 다시 시도해주세요.');
        } else {
            _showMarketCapError(div, '시가총액 랭킹을 불러오지 못했습니다. 다시 시도해주세요.');
        }
    }
}
