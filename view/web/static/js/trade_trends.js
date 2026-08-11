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

function tradeDelta(value) {
    if (value === null || value === undefined || value === '') return '-';
    const number = Number(value);
    return `${number > 0 ? '+' : ''}${number.toLocaleString(undefined, { maximumFractionDigits: 1 })}억`;
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
    document.getElementById('trade-latest-semiconductor').textContent = latest ? tradeMoney(latest.semiconductor_export_amount_100m_usd) : '-';
    document.getElementById('trade-latest-working-days').textContent = latest && latest.working_days_current ? `${Number(latest.working_days_current).toFixed(1)}일` : '-';
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
        tbody.innerHTML = '<tr><td colspan="12" style="text-align:center;padding:18px;">표시할 수출입동향이 없습니다.</td></tr>';
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
            <td>
                ${tradeMoney(row.export_daily_avg_100m_usd)}
                <div class="trade-trend-muted">${tradePct(row.export_daily_avg_mom_pct)} · 조업 ${row.working_days_current ? `${Number(row.working_days_current).toFixed(1)}일` : '-'}</div>
            </td>
            <td>
                ${tradeMoney(row.semiconductor_export_amount_100m_usd)}
                <div class="${Number(row.semiconductor_mom_pct || 0) >= 0 ? 'text-red' : 'text-blue'} trade-trend-muted">MoM ${tradeDelta(row.semiconductor_mom_change_100m_usd)} / ${tradePct(row.semiconductor_mom_pct)}</div>
                <div class="${Number(row.semiconductor_yoy_pct || 0) >= 0 ? 'text-red' : 'text-blue'} trade-trend-muted">YoY ${tradePct(row.semiconductor_yoy_pct)}</div>
            </td>
            <td>
                ${tradeMoney(row.semiconductor_daily_avg_100m_usd)}
                <div class="${Number(row.semiconductor_daily_avg_mom_pct || 0) >= 0 ? 'text-red' : 'text-blue'} trade-trend-muted">MoM ${tradePct(row.semiconductor_daily_avg_mom_pct)}</div>
            </td>
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
    const phaseOrder = [
        { key: 'customs_10d', label: '1~10일' },
        { key: 'customs_20d', label: '1~20일' },
        { key: 'monthly', label: '월간' },
    ];
    const rowsWithMonth = rows
        .map((row) => ({ row, month: tradeMonthKey(row), phase: tradePhaseBucket(row) }))
        .filter((item) => item.month && item.phase);
    if (!rowsWithMonth.length) {
        chart.innerHTML = '<div class="trade-trend-empty">차트로 볼 숫자 데이터가 없습니다.</div>';
        return;
    }

    const latestByMonthPhase = new Map();
    rowsWithMonth.forEach(({ row, month, phase }) => {
        const key = `${month}:${phase}`;
        const current = latestByMonthPhase.get(key);
        const rowTime = `${row.published_at || ''} ${row.sent_at || ''}`;
        const currentTime = current ? `${current.published_at || ''} ${current.sent_at || ''}` : '';
        if (!current || rowTime >= currentTime) latestByMonthPhase.set(key, row);
    });

    const months = Array.from(new Set(rowsWithMonth.map((item) => item.month)))
        .sort()
        .slice(-8);
    const visibleRows = [];
    months.forEach((month) => {
        phaseOrder.forEach(({ key }) => {
            const row = latestByMonthPhase.get(`${month}:${key}`);
            if (row) visibleRows.push(row);
        });
    });
    const maxValue = Math.max(...visibleRows.map((row) => Math.max(
        Number(row.export_amount_100m_usd || 0),
        Number(row.import_amount_100m_usd || 0),
        Number(row.semiconductor_export_amount_100m_usd || 0),
    )), 1);

    chart.innerHTML = `
        <div class="trade-trend-chart-legend">
            <span><i class="legend-export"></i>수출</span>
            <span><i class="legend-import"></i>수입</span>
            <span><i class="legend-chip"></i>반도체</span>
        </div>
        <div class="trade-trend-month-grid">
            ${months.map((month) => renderTradeTrendMonth(month, phaseOrder, latestByMonthPhase, maxValue)).join('')}
        </div>
    `;
}

function renderTradeTrendMonth(month, phaseOrder, latestByMonthPhase, maxValue) {
    return `
        <div class="trade-trend-month-card">
            <div class="trade-trend-month-title">${escapeHtml(formatTradeMonth(month))}</div>
            <div class="trade-trend-phase-row">
                ${phaseOrder.map(({ key, label }) => renderTradeTrendPhaseCell(label, latestByMonthPhase.get(`${month}:${key}`), maxValue)).join('')}
            </div>
        </div>
    `;
}

function renderTradeTrendPhaseCell(label, row, maxValue) {
    if (!row) {
        return `
            <div class="trade-trend-phase-cell empty">
                <div class="trade-trend-phase-label">${escapeHtml(label)}</div>
                <div class="trade-trend-empty-mini">-</div>
            </div>
        `;
    }
    const exportHeight = tradeBarHeight(row.export_amount_100m_usd, maxValue);
    const importHeight = tradeBarHeight(row.import_amount_100m_usd, maxValue);
    const semiconductorHeight = tradeBarHeight(row.semiconductor_export_amount_100m_usd, maxValue);
    return `
        <div class="trade-trend-phase-cell" title="${escapeAttribute(row.period_label || '')}">
            <div class="trade-trend-phase-label">${escapeHtml(label)}</div>
            <div class="trade-trend-bars">
                <span class="trade-trend-bar export" style="height:${exportHeight}%"></span>
                <span class="trade-trend-bar import" style="height:${importHeight}%"></span>
                <span class="trade-trend-bar chip" style="height:${semiconductorHeight}%"></span>
            </div>
            <div class="trade-trend-cell-metrics">
                <strong>${tradeMoney(row.export_amount_100m_usd)}</strong>
                <span>반도체 ${tradeMoney(row.semiconductor_export_amount_100m_usd)}</span>
                <span>일평균 ${tradeMoney(row.export_daily_avg_100m_usd)}</span>
                <span>조업 ${row.working_days_current ? `${Number(row.working_days_current).toFixed(1)}일` : '-'}</span>
            </div>
        </div>
    `;
}

function tradeBarHeight(value, maxValue) {
    if (value === null || value === undefined || value === '') return 0;
    return Math.max(8, Number(value || 0) / maxValue * 100);
}

function tradeMonthKey(row) {
    const match = String(row.period_label || '').match(/(\d{4})년\s*(\d{1,2})월/);
    if (!match) return '';
    return `${match[1]}-${String(match[2]).padStart(2, '0')}`;
}

function tradePhaseBucket(row) {
    const phase = String(row.phase || '');
    if (phase === 'customs_10d') return 'customs_10d';
    if (phase === 'customs_20d') return 'customs_20d';
    if (phase.includes('monthly') || phase === 'motie_monthly') return 'monthly';
    return '';
}

function formatTradeMonth(month) {
    const [year, monthPart] = String(month || '').split('-');
    if (!year || !monthPart) return month || '-';
    return `${year}.${monthPart}`;
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
