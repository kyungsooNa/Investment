let tradeTrendRows = [];
let tradeTrendFilter = 'all';

function tradeMoney(value) {
    if (value === null || value === undefined || value === '') return '-';
    return `${Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 })}억 달러`;
}

function tradePct(value) {
    if (value === null || value === undefined || value === '') return '-';
    const number = Number(value);
    return `${number > 0 ? '+' : ''}${number.toFixed(1)}%`;
}

function tradeBalance(row) {
    if (row.trade_balance_100m_usd === null || row.trade_balance_100m_usd === undefined) return '-';
    const label = row.trade_balance_label ? ` ${row.trade_balance_label}` : '';
    return `${Math.abs(Number(row.trade_balance_100m_usd)).toLocaleString(undefined, { maximumFractionDigits: 1 })}억 달러${label}`;
}

function phaseLabel(phase) {
    if (phase === 'customs_10d') return '1~10일';
    if (phase === 'customs_20d') return '1~20일';
    if (phase === 'customs_monthly_final') return '월간 확정';
    if (phase === 'customs_monthly') return '월간 잠정';
    if (phase === 'motie_monthly') return '산업부 월간';
    return phase || '-';
}

function sourceLabel(row) {
    if (row.source_type === 'sent') return '발송됨';
    return '공식 최신';
}

async function loadTradeTrendHistory() {
    const status = document.getElementById('trade-trend-status');
    if (status) status.textContent = '조회 중';
    try {
        const res = await fetch('/api/trade-trends/national/history?include_recent=true&limit=120');
        const payload = await res.json();
        if (!res.ok || !payload.success) {
            throw new Error(payload.detail || '수출입동향 조회 실패');
        }
        tradeTrendRows = payload.data.rows || [];
        renderTradeTrendSummary(payload.data.latest);
        renderTradeTrendChart(tradeTrendRows);
        renderTradeTrendHighlights(tradeTrendRows);
        renderTradeTrendTable();
        if (status) {
            const recentCount = payload.data.recent_count || 0;
            const storedCount = payload.data.stored_count || 0;
            status.textContent = `공식 후보 ${recentCount}건, 발송 이력 ${storedCount}건`;
        }
    } catch (error) {
        if (status) status.textContent = `조회 실패: ${error.message}`;
        tradeTrendRows = [];
        renderTradeTrendSummary(null);
        renderTradeTrendChart([]);
        renderTradeTrendHighlights([]);
        renderTradeTrendTable();
    }
}

function renderTradeTrendSummary(latest) {
    document.getElementById('trade-latest-period').textContent = latest ? latest.period_label : '-';
    document.getElementById('trade-latest-export').textContent = latest ? tradeMoney(latest.export_amount_100m_usd) : '-';
    document.getElementById('trade-latest-import').textContent = latest ? tradeMoney(latest.import_amount_100m_usd) : '-';
    document.getElementById('trade-latest-balance').textContent = latest ? tradeBalance(latest) : '-';
}

function filterTradeTrendRows(filter, button) {
    tradeTrendFilter = filter;
    const parent = button && button.closest('.sub-nav-container');
    if (parent) {
        parent.querySelectorAll('.sub-tab-btn').forEach((el) => el.classList.remove('active'));
        button.classList.add('active');
    }
    renderTradeTrendTable();
}

function filteredTradeTrendRows() {
    if (tradeTrendFilter === 'all') return tradeTrendRows;
    if (tradeTrendFilter === 'monthly') {
        return tradeTrendRows.filter((row) => String(row.phase || '').includes('monthly') || row.phase === 'motie_monthly');
    }
    return tradeTrendRows.filter((row) => row.phase === tradeTrendFilter);
}

function renderTradeTrendTable() {
    const tbody = document.getElementById('trade-trend-history-body');
    if (!tbody) return;
    const rows = filteredTradeTrendRows();
    if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:18px;">표시할 수출입동향이 없습니다.</td></tr>';
        return;
    }
    tbody.innerHTML = rows.map((row) => `
        <tr>
            <td>
                <strong>${escapeHtml(row.period_label || '-')}</strong>
                <div class="trade-trend-muted">${escapeHtml(phaseLabel(row.phase))}</div>
            </td>
            <td>${escapeHtml(row.published_at || '-')}</td>
            <td>${tradeMoney(row.export_amount_100m_usd)}</td>
            <td class="${Number(row.export_yoy_pct || 0) >= 0 ? 'text-red' : 'text-blue'}">${tradePct(row.export_yoy_pct)}</td>
            <td>${tradeMoney(row.import_amount_100m_usd)}</td>
            <td class="${Number(row.import_yoy_pct || 0) >= 0 ? 'text-red' : 'text-blue'}">${tradePct(row.import_yoy_pct)}</td>
            <td><strong>${tradeBalance(row)}</strong></td>
            <td><span class="badge ${row.source_type === 'sent' ? 'open' : 'paper'}">${sourceLabel(row)}</span></td>
            <td>${row.url ? `<a class="stock-link" target="_blank" rel="noopener" href="${escapeAttribute(row.url)}">보기</a>` : '-'}</td>
        </tr>
    `).join('');
}

function renderTradeTrendChart(rows) {
    const chart = document.getElementById('trade-trend-chart');
    if (!chart) return;
    const points = rows
        .filter((row) => row.export_amount_100m_usd !== null && row.export_amount_100m_usd !== undefined)
        .slice(0, 12)
        .reverse();
    if (!points.length) {
        chart.innerHTML = '<div class="trade-trend-empty">차트로 볼 숫자 데이터가 없습니다.</div>';
        return;
    }
    const maxValue = Math.max(...points.map((row) => Math.max(
        Number(row.export_amount_100m_usd || 0),
        Number(row.import_amount_100m_usd || 0),
    )), 1);
    chart.innerHTML = points.map((row) => {
        const exportHeight = Math.max(6, Number(row.export_amount_100m_usd || 0) / maxValue * 100);
        const importHeight = Math.max(6, Number(row.import_amount_100m_usd || 0) / maxValue * 100);
        return `
            <div class="trade-trend-chart-group" title="${escapeAttribute(row.period_label || '')}">
                <div class="trade-trend-bars">
                    <span class="trade-trend-bar export" style="height:${exportHeight}%"></span>
                    <span class="trade-trend-bar import" style="height:${importHeight}%"></span>
                </div>
                <div class="trade-trend-chart-label">${escapeHtml(row.period_label || '-')}</div>
            </div>
        `;
    }).join('');
}

function renderTradeTrendHighlights(rows) {
    const target = document.getElementById('trade-trend-highlights');
    if (!target) return;
    const highlights = [];
    rows.forEach((row) => {
        (row.highlights || []).forEach((item) => {
            if (item && !highlights.includes(item)) highlights.push(item);
        });
    });
    if (!highlights.length) {
        target.innerHTML = '<div class="trade-trend-muted">핵심 품목 메모가 없습니다.</div>';
        return;
    }
    target.innerHTML = highlights.slice(0, 6).map((item) =>
        `<div class="trade-trend-highlight">${escapeHtml(item)}</div>`
    ).join('');
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function escapeAttribute(value) {
    return escapeHtml(value);
}
