/* view/web/static/js/overseas_ranking.js — 미국 상위 종목 랭킹 (S&P 500 유니버스) */

let _overseasRankRequestSequence = 0;
let _overseasRankCategory = 'rise';
let _overseasRankLimit = 30;

const OVERSEAS_RANK_LABELS = {
    rise: '상승률',
    fall: '하락률',
    volume: '거래량',
    trading_value: '거래대금',
};

function _rankNumber(value) {
    if (value === null || value === undefined || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
}

function _rankUsdPrice(value) {
    const number = _rankNumber(value);
    if (number === null) return '-';
    return '$' + number.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function _rankUsdAmount(value) {
    const number = _rankNumber(value);
    if (number === null || number <= 0) return '-';
    if (number >= 1e12) return `$${(number / 1e12).toFixed(2)}T`;
    if (number >= 1e9) return `$${(number / 1e9).toFixed(2)}B`;
    if (number >= 1e6) return `$${(number / 1e6).toFixed(2)}M`;
    return _rankUsdPrice(number);
}

function _rankCount(value) {
    const number = _rankNumber(value);
    return number === null ? '-' : Math.trunc(number).toLocaleString();
}

function _rankRate(value) {
    const rate = _rankNumber(value);
    if (rate === null) return { color: '', text: '-' };
    const normalized = Object.is(rate, -0) ? 0 : rate;
    const color = normalized > 0 ? 'text-red' : (normalized < 0 ? 'text-blue' : '');
    const sign = normalized > 0 ? '+' : '';
    return { color, text: `${sign}${normalized.toFixed(2)}%` };
}

function _renderOverseasRanking(div, data, category) {
    const items = Array.isArray(data.items) ? data.items : [];
    if (!items.length) {
        div.innerHTML = '<p class="empty">조회 결과가 없습니다.</p>';
        return;
    }

    const rows = items.map(item => {
        const row = item && typeof item === 'object' ? item : {};
        const rate = _rankRate(row.change_rate);
        return `
            <tr>
                <td>${escapeHtml(row.rank)}</td>
                <td><strong>${escapeHtml(row.symbol)}</strong> <span style="color:#888; font-size:0.85rem;">${escapeHtml(row.name)}</span></td>
                <td>${escapeHtml(row.sector || '-')}</td>
                <td>${_rankUsdPrice(row.price)}</td>
                <td class="${rate.color}">${rate.text}</td>
                <td>${_rankCount(row.volume)}</td>
                <td>${_rankUsdAmount(row.trading_value_usd)}</td>
            </tr>
        `;
    }).join('');

    div.innerHTML = `
        <div class="card">
            <h3>${escapeHtml(OVERSEAS_RANK_LABELS[category] || category)} 상위 ${items.length}종목</h3>
            <table class="data-table">
                <thead><tr><th>순위</th><th>심볼·종목명</th><th>섹터</th><th>현재가</th><th>등락률</th><th>거래량</th><th>거래대금</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    `;
}

async function loadOverseasRanking(category = _overseasRankCategory) {
    const requestSequence = ++_overseasRankRequestSequence;
    const isLatestRequest = () => requestSequence === _overseasRankRequestSequence;
    _overseasRankCategory = category;

    document.querySelectorAll('#section-overseas-ranking .ranking-tab').forEach(button => {
        button.classList.toggle('active', button.dataset.category === category);
    });
    document.querySelectorAll('#section-overseas-ranking .ranking-limit').forEach(button => {
        button.classList.toggle('active', Number(button.dataset.limit) === Number(_overseasRankLimit));
    });

    const div = document.getElementById('overseas-ranking-result');
    if (!div) return;
    showLoading(div, '미국 랭킹 조회 중...');

    try {
        const query = `limit=${encodeURIComponent(_overseasRankLimit)}`;
        const res = await fetchWithTimeout(
            `/api/overseas/ranking/${encodeURIComponent(category)}?${query}`, {}, 30000,
        );
        const { json, error } = await readJsonResponse(res);
        if (!isLatestRequest()) return;

        if (error) {
            showError(div, `미국 랭킹 조회 실패: ${error}`);
            return;
        }
        if (json.rt_cd !== '0') {
            showError(div, `실패: ${json.msg1 || '미국 랭킹 조회에 실패했습니다.'}`);
            return;
        }
        _renderOverseasRanking(div, json.data || {}, category);
    } catch (e) {
        console.error('[overseas-ranking] 랭킹 조회 오류', e);
        if (!isLatestRequest()) return;
        showError(div, e.name === 'AbortError'
            ? '요청 시간이 초과되었습니다. 다시 시도해주세요.'
            : '미국 랭킹을 불러오지 못했습니다. 다시 시도해주세요.');
    }
}

function setOverseasRankingLimit(limit) {
    _overseasRankLimit = Number(limit) || 30;
    void loadOverseasRanking(_overseasRankCategory);
}

document.addEventListener('DOMContentLoaded', () => {
    if (window.location.pathname !== '/overseas-ranking') return;
    void loadOverseasRanking('rise');
});

document.addEventListener('pjax:ready', (e) => {
    if (e.detail?.path !== '/overseas-ranking') return;
    void loadOverseasRanking('rise');
});
