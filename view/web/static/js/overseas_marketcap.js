/* view/web/static/js/overseas_marketcap.js — 미국 시가총액 상위 종목 (S&P 500 유니버스) */

let _overseasCapRequestSequence = 0;
let _overseasCapCurrentLimit = 30;
let _overseasCapRefreshTimer = null;
const OVERSEAS_MARKETCAP_REFRESH_MS = 5 * 60 * 1000;

function _capNumber(value) {
    if (value === null || value === undefined || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
}

function _capUsdPrice(value) {
    const number = _capNumber(value);
    if (number === null) return '-';
    return '$' + number.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function _capUsdAmount(value) {
    const number = _capNumber(value);
    if (number === null || number <= 0) return '-';
    if (number >= 1e12) return `$${(number / 1e12).toFixed(2)}T`;
    if (number >= 1e9) return `$${(number / 1e9).toFixed(2)}B`;
    if (number >= 1e6) return `$${(number / 1e6).toFixed(2)}M`;
    return _capUsdPrice(number);
}

function _capKrwAmount(value) {
    const number = _capNumber(value);
    if (number === null || number <= 0) return '-';
    // 국내 화면과 동일한 조/억 표기를 위해 공용 formatTradingValue 를 쓴다.
    return formatTradingValue(number);
}

function _capRate(value) {
    const rate = _capNumber(value);
    if (rate === null) return { color: '', text: '-' };
    const normalized = Object.is(rate, -0) ? 0 : rate;
    const color = normalized > 0 ? 'text-red' : (normalized < 0 ? 'text-blue' : '');
    const sign = normalized > 0 ? '+' : '';
    return { color, text: `${sign}${normalized.toFixed(2)}%` };
}

function _formatCapUpdatedAt(value) {
    const number = _capNumber(value);
    if (number === null || number <= 0) return '최신 업데이트: --';
    const date = new Date(number * 1000);
    if (Number.isNaN(date.getTime())) return '최신 업데이트: --';
    return `최신 업데이트: ${date.toLocaleString('ko-KR')}`;
}

function _renderCapUpdatedAt(value) {
    const el = document.getElementById('overseas-marketcap-updated-at');
    if (el) el.textContent = _formatCapUpdatedAt(value);
}

function _renderOverseasMarketCap(div, data) {
    const items = Array.isArray(data.items) ? data.items : [];
    _renderCapUpdatedAt(data.updated_at);
    if (!items.length) {
        div.innerHTML = '<p class="empty">조회 결과가 없습니다.</p>';
        return;
    }

    const fxRate = _capNumber(data.fx_rate);
    const fxText = fxRate === null ? '환율 정보 없음 (원화 환산 생략)' : `USD/KRW ${fxRate.toLocaleString()}`;
    const rows = items.map(item => {
        const row = item && typeof item === 'object' ? item : {};
        const rate = _capRate(row.change_rate);
        return `
            <tr>
                <td>${escapeHtml(row.rank)}</td>
                <td><strong>${escapeHtml(row.symbol)}</strong> <span style="color:#888; font-size:0.85rem;">${escapeHtml(row.name)}</span></td>
                <td>${escapeHtml(row.sector || '-')}</td>
                <td>${_capUsdPrice(row.price)} <small class="${rate.color}">(${rate.text})</small></td>
                <td>${_capUsdAmount(row.market_cap_usd)}</td>
                <td>${_capKrwAmount(row.market_cap_krw)}</td>
            </tr>
        `;
    }).join('');

    div.innerHTML = `
        <div class="card">
            <p class="small">${escapeHtml(fxText)}</p>
            <table class="data-table">
                <thead><tr><th>순위</th><th>심볼·종목명</th><th>섹터</th><th>현재가</th><th>시가총액(USD)</th><th>원화 환산</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    `;
}

async function loadOverseasTopMarketCap(limit = 30, options = {}) {
    const requestSequence = ++_overseasCapRequestSequence;
    const isLatestRequest = () => requestSequence === _overseasCapRequestSequence;
    const shouldShowLoading = options.showLoading !== false;
    _overseasCapCurrentLimit = Number(limit) || 30;

    document.querySelectorAll('#section-overseas-marketcap .ranking-tab').forEach(button => {
        button.classList.toggle('active', Number(button.dataset.limit) === Number(limit));
    });

    const div = document.getElementById('overseas-marketcap-result');
    if (!div) return;
    if (shouldShowLoading) showLoading(div, '미국 시가총액 조회 중...');

    try {
        const res = await fetchWithTimeout(`/api/overseas/top-market-cap?limit=${encodeURIComponent(limit)}`, {}, 30000);
        const { json, error } = await readJsonResponse(res);
        if (!isLatestRequest()) return;

        if (error) {
            showError(div, `미국 시가총액 조회 실패: ${error}`);
            return;
        }
        if (json.rt_cd !== '0') {
            showError(div, `실패: ${json.msg1 || '미국 시가총액 조회에 실패했습니다.'}`);
            return;
        }
        _renderOverseasMarketCap(div, json.data || {});
    } catch (e) {
        console.error('[overseas-marketcap] 시가총액 조회 오류', e);
        if (!isLatestRequest()) return;
        showError(div, e.name === 'AbortError'
            ? '요청 시간이 초과되었습니다. 다시 시도해주세요.'
            : '미국 시가총액을 불러오지 못했습니다. 다시 시도해주세요.');
    }
}

function initOverseasMarketCapPage() {
    void loadOverseasTopMarketCap(_overseasCapCurrentLimit);
    if (_overseasCapRefreshTimer !== null) return;
    _overseasCapRefreshTimer = setInterval(() => {
        void loadOverseasTopMarketCap(_overseasCapCurrentLimit, { showLoading: false });
    }, OVERSEAS_MARKETCAP_REFRESH_MS);
}

document.addEventListener('DOMContentLoaded', () => {
    if (window.location.pathname !== '/overseas-marketcap') return;
    initOverseasMarketCapPage();
});

document.addEventListener('pjax:ready', (e) => {
    if (e.detail?.path !== '/overseas-marketcap') return;
    initOverseasMarketCapPage();
});
