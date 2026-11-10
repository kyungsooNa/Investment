let backtestResults = [];

async function loadBacktestResults() {
    const body = document.getElementById('backtest-results-body');
    const summary = document.getElementById('backtest-results-summary');
    if (!body || !summary) return;
    body.innerHTML = '<tr><td colspan="15">불러오는 중...</td></tr>';
    summary.textContent = '백테스트 결과를 불러오는 중입니다.';
    try {
        const response = await fetchWithTimeout('/api/backtest/results?limit=200', {}, 30000);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        backtestResults = Array.isArray(payload.results) ? payload.results : [];
        populateBacktestStrategyFilter();
        renderBacktestResults();
    } catch (error) {
        console.error('[Backtest] result load failed:', error);
        summary.textContent = '백테스트 결과를 불러오지 못했습니다.';
        body.innerHTML = '<tr><td colspan="15">결과 로드 실패</td></tr>';
    }
}

function populateBacktestStrategyFilter() {
    const select = document.getElementById('backtest-strategy-filter');
    if (!select) return;
    const selected = select.value;
    const strategies = [...new Set(backtestResults.map(row => row.strategy).filter(Boolean))].sort();
    select.innerHTML = '<option value="">전체</option>' + strategies
        .map(strategy => `<option value="${escapeHtml(strategy)}">${escapeHtml(strategy)}</option>`)
        .join('');
    if (strategies.includes(selected)) select.value = selected;
}

function renderBacktestResults() {
    const body = document.getElementById('backtest-results-body');
    const summary = document.getElementById('backtest-results-summary');
    if (!body || !summary) return;
    const strategy = document.getElementById('backtest-strategy-filter')?.value || '';
    const validity = document.getElementById('backtest-validity-filter')?.value || '';
    const configCohort = document.getElementById('backtest-config-filter')?.value || '';
    const rows = backtestResults.filter(row =>
        (!strategy || row.strategy === strategy)
        && (!validity || row.validity?.status === validity)
        && (!configCohort || row.config_cohort === configCohort)
    );
    const validCount = rows.filter(row => row.validity?.status === 'VALID').length;
    summary.innerHTML = `<span>표시 <strong>${rows.length}</strong>건 · 유효 <strong>${validCount}</strong>건</span>`
        + '<span style="margin-left:12px;">유효 판정이 없는 수익률은 투자 근거로 확정하지 않습니다.</span>';
    if (!rows.length) {
        body.innerHTML = '<tr><td colspan="15">조건에 맞는 백테스트 결과가 없습니다.</td></tr>';
        return;
    }
    body.innerHTML = rows.map(row => {
        const status = row.validity || {};
        return `<tr title="${escapeHtml(status.reason || '')}">
            <td><strong>${escapeHtml(status.label || status.status || '-')}</strong></td>
            <td>${escapeHtml(row.strategy || '-')}</td>
            <td>${formatBacktestConfigCohort(row.config_cohort)}</td>
            <td><code>${escapeHtml(row.config_hash || '-')}</code></td>
            <td><code>${escapeHtml((row.git_commit || '').slice(0, 12) || '-')}</code></td>
            <td>${formatBacktestDate(row.start_date)} ~ ${formatBacktestDate(row.end_date)}</td>
            <td>${Number(row.date_count || 0).toLocaleString()}일</td>
            <td>${Number(row.completed_trade_count || 0).toLocaleString()}건</td>
            <td class="${returnClass(row.realized_return_pct)}">${formatBacktestPercent(row.realized_return_pct)}</td>
            <td>${formatBacktestMoney(row.realized_net_pnl)}</td>
            <td>${formatBacktestPercent(row.win_rate)}</td>
            <td>${formatBacktestPercent(row.avg_net_return)}</td>
            <td>${row.profit_factor == null ? '-' : Number(row.profit_factor).toFixed(2)}</td>
            <td>${Number(row.open_position_count || 0).toLocaleString()}건</td>
            <td>${formatBacktestSavedAt(row.saved_at)}</td>
        </tr>`;
    }).join('');
}

function formatBacktestConfigCohort(value) {
    return {
        LATEST: '최신 저장 설정',
        LEGACY: '과거 설정',
        UNKNOWN: '설정 미기록',
    }[value] || '-';
}

function formatBacktestDate(value) {
    const text = String(value || '');
    return text.length === 8 ? `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}` : escapeHtml(text || '-');
}

function formatBacktestPercent(value) {
    if (value == null) return '-';
    const number = Number(value);
    return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`;
}

function formatBacktestMoney(value) {
    const number = Number(value || 0);
    return `${number > 0 ? '+' : ''}${Math.round(number).toLocaleString()}원`;
}

function formatBacktestSavedAt(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? escapeHtml(value || '-') : date.toLocaleString('ko-KR');
}

function returnClass(value) {
    const number = Number(value || 0);
    return number > 0 ? 'positive' : number < 0 ? 'negative' : '';
}

window.loadBacktestResults = loadBacktestResults;
window.renderBacktestResults = renderBacktestResults;
document.addEventListener('DOMContentLoaded', loadBacktestResults);
document.addEventListener('pjax:ready', event => {
    if (event.detail?.path === '/backtest') loadBacktestResults();
});
