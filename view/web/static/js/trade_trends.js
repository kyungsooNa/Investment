let tradeTrendRows = [];
let jejuRegionRows = [];
let tradeTrendFilter = 'all';
let tradeTrendChartPhase = null;

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
    const model = buildTradeTrendChartModel(rows, tradeTrendChartPhase);
    if (!model.items.length) {
        chart.innerHTML = '<div class="trade-trend-empty">차트로 볼 숫자 데이터가 없습니다.</div>';
        return;
    }
    tradeTrendChartPhase = model.selectedPhase;

    chart.innerHTML = `
        <div class="trade-trend-chart-tabs">
            ${model.phases.map((phase) => `
                <button type="button"
                    class="sub-tab-btn ${phase.key === model.selectedPhase ? 'active' : ''}"
                    onclick="selectTradeTrendChartPhase('${escapeAttribute(phase.key)}')">
                    ${escapeHtml(phase.label)}
                </button>
            `).join('')}
        </div>
        <div class="trade-trend-chart-legend">
            <span><i class="legend-export"></i>수출 일평균</span>
            <span><i class="legend-chip"></i>반도체 일평균</span>
            <span class="trade-trend-muted">수입은 보조 지표로 표시</span>
        </div>
        <div class="trade-trend-comparison-grid">
            ${model.items.map((item) => renderTradeTrendComparisonCard(item, model.maxValue)).join('')}
        </div>
    `;
}

function selectTradeTrendChartPhase(phase) {
    tradeTrendChartPhase = phase;
    renderTradeTrendChart(tradeTrendRows);
}

function buildTradeTrendChartModel(rows, selectedPhase) {
    const phases = [
        { key: 'customs_10d', label: '1~10일' },
        { key: 'customs_20d', label: '1~20일' },
        { key: 'monthly', label: '월간' },
        { key: 'quarter', label: '분기' },
        { key: 'month_progress', label: '월 진행판' },
    ];
    const rowsWithMonth = rows
        .map((row) => ({ row, month: tradeMonthKey(row), phase: tradePhaseBucket(row) }))
        .filter((item) => item.month && item.phase);
    if (!rowsWithMonth.length) {
        return { phases, selectedPhase: selectedPhase || 'customs_10d', items: [], maxValue: 1 };
    }

    const availablePhases = new Set(rowsWithMonth.map((item) => item.phase));
    if (availablePhases.has('monthly')) availablePhases.add('quarter');
    availablePhases.add('month_progress');
    const latestPhase = latestTradeTrendPhase(rowsWithMonth);
    const selected = availablePhases.has(selectedPhase) ? selectedPhase : latestPhase;
    const items = selected === 'quarter'
        ? buildTradeTrendQuarterItems(rowsWithMonth)
        : selected === 'month_progress'
            ? buildTradeTrendMonthProgressItems(rowsWithMonth)
            : buildTradeTrendPhaseItems(rowsWithMonth, selected);
    const scaleItems = items.flatMap((item) => item.progressRows || [item]);
    const maxValue = Math.max(...scaleItems.map((item) => Math.max(
        Number(item.export_daily_avg_100m_usd || 0),
        Number(item.semiconductor_daily_avg_100m_usd || 0),
    )), 1);
    return {
        phases: phases.filter((phase) => availablePhases.has(phase.key)),
        selectedPhase: selected,
        items,
        maxValue,
    };
}

function latestTradeTrendPhase(rowsWithMonth) {
    const latest = rowsWithMonth.reduce((current, item) => {
        if (!current) return item;
        return tradeRowTime(item.row) >= tradeRowTime(current.row) ? item : current;
    }, null);
    return latest ? latest.phase : 'customs_10d';
}

function buildTradeTrendPhaseItems(rowsWithMonth, phase, monthLimit = 8) {
    const latestByMonth = new Map();
    rowsWithMonth
        .filter((item) => item.phase === phase)
        .forEach(({ row, month }) => {
            const current = latestByMonth.get(month);
            if (!current || tradeRowTime(row) >= tradeRowTime(current)) latestByMonth.set(month, row);
        });
    return Array.from(latestByMonth.entries())
        .sort(([left], [right]) => left.localeCompare(right))
        .slice(-monthLimit)
        .map(([month, row]) => tradeTrendComparisonItem(formatTradeMonth(month), row));
}

function buildTradeTrendQuarterItems(rowsWithMonth) {
    // 분기 카드 8개를 채우려면 24개월이 필요하다. 월간 탭의 8개월 상한과는 별개다.
    // 조업일수 없는 행(산업부 발표)은 일평균 분모에 못 들어가므로 합산에서 제외한다.
    const monthlyRows = buildTradeTrendPhaseItems(rowsWithMonth, 'monthly', 24)
        .map((item) => item.row)
        .filter((row) => row && Number(row.working_days_current) > 0);
    const quarters = new Map();
    monthlyRows.forEach((row) => {
        const month = tradeMonthKey(row);
        const [year, monthPart] = month.split('-');
        const quarter = Math.ceil(Number(monthPart) / 3);
        const key = `${year} Q${quarter}`;
        if (!quarters.has(key)) quarters.set(key, []);
        quarters.get(key).push(row);
    });
    return Array.from(quarters.entries())
        .sort(([left], [right]) => left.localeCompare(right))
        .slice(-8)
        .map(([label, quarterRows]) => tradeTrendQuarterItem(label, quarterRows));
}

function buildTradeTrendMonthProgressItems(rowsWithMonth) {
    const latestByMonthPhase = new Map();
    rowsWithMonth.forEach(({ row, month, phase }) => {
        const key = `${month}:${phase}`;
        const current = latestByMonthPhase.get(key);
        if (!current || tradeRowTime(row) >= tradeRowTime(current)) latestByMonthPhase.set(key, row);
    });
    const months = Array.from(new Set(rowsWithMonth.map((item) => item.month)))
        .sort()
        .slice(-6);
    return months.map((month) => ({
        label: formatTradeMonth(month),
        progressRows: ['customs_10d', 'customs_20d', 'monthly']
            .map((phase) => latestByMonthPhase.get(`${month}:${phase}`))
            .filter(Boolean)
            .map((row) => tradeTrendComparisonItem(phaseLabel(row.phase), row)),
    }));
}

function tradeTrendComparisonItem(label, row) {
    const workingDays = Number(row.working_days_current || 0);
    return {
        label,
        row,
        period_label: row.period_label,
        export_amount_100m_usd: row.export_amount_100m_usd,
        import_amount_100m_usd: row.import_amount_100m_usd,
        semiconductor_export_amount_100m_usd: row.semiconductor_export_amount_100m_usd,
        export_daily_avg_100m_usd: tradeDailyAverage(row.export_daily_avg_100m_usd, row.export_amount_100m_usd, workingDays),
        semiconductor_daily_avg_100m_usd: tradeDailyAverage(row.semiconductor_daily_avg_100m_usd, row.semiconductor_export_amount_100m_usd, workingDays),
        working_days_current: row.working_days_current,
        export_daily_avg_mom_pct: row.export_daily_avg_mom_pct,
        semiconductor_daily_avg_mom_pct: row.semiconductor_daily_avg_mom_pct,
    };
}

function tradeTrendQuarterItem(label, rows) {
    const exportAmount = tradeSum(rows, 'export_amount_100m_usd');
    const importAmount = tradeSum(rows, 'import_amount_100m_usd');
    const semiconductorAmount = tradeSum(rows, 'semiconductor_export_amount_100m_usd');
    const workingDays = tradeSum(rows, 'working_days_current');
    const monthCount = rows.length;
    const isPartial = monthCount < 3;
    return {
        label: isPartial ? `${label} (${monthCount}/3개월)` : label,
        period_label: isPartial
            ? `${label} 월간 확정치 누적 (3개월 중 ${monthCount}개월)`
            : `${label} 월간 확정치 누적`,
        month_count: monthCount,
        is_partial: isPartial,
        export_amount_100m_usd: exportAmount,
        import_amount_100m_usd: importAmount,
        semiconductor_export_amount_100m_usd: semiconductorAmount,
        export_daily_avg_100m_usd: tradeDailyAverage(null, exportAmount, workingDays),
        semiconductor_daily_avg_100m_usd: tradeDailyAverage(null, semiconductorAmount, workingDays),
        working_days_current: workingDays,
    };
}

function tradeDailyAverage(explicitValue, amount, workingDays) {
    if (explicitValue !== null && explicitValue !== undefined && explicitValue !== '') {
        return Number(explicitValue);
    }
    if (!workingDays || amount === null || amount === undefined || amount === '') return null;
    return Number(amount) / Number(workingDays);
}

function tradeSum(rows, field) {
    return rows.reduce((total, row) => total + Number(row[field] || 0), 0);
}

function tradeRowTime(row) {
    return `${row.published_at || ''} ${row.sent_at || ''}`;
}

function renderTradeTrendComparisonCard(item, maxValue) {
    if (item.progressRows) {
        return renderTradeTrendProgressCard(item, maxValue);
    }
    const exportHeight = tradeBarHeight(item.export_daily_avg_100m_usd, maxValue);
    const semiconductorHeight = tradeBarHeight(item.semiconductor_daily_avg_100m_usd, maxValue);
    return `
        <div class="trade-trend-compare-card" title="${escapeAttribute(item.period_label || '')}">
            <div class="trade-trend-compare-head">
                <strong>${escapeHtml(item.label)}</strong>
                <span>조업 ${item.working_days_current ? `${Number(item.working_days_current).toFixed(1)}일` : '-'}</span>
            </div>
            <div class="trade-trend-bars compare">
                <span class="trade-trend-bar export" style="height:${exportHeight}%">
                    <em>${tradeMoney(item.export_daily_avg_100m_usd).replace(' 달러', '')}</em>
                </span>
                <span class="trade-trend-bar chip" style="height:${semiconductorHeight}%">
                    <em>${tradeMoney(item.semiconductor_daily_avg_100m_usd).replace(' 달러', '')}</em>
                </span>
            </div>
            <div class="trade-trend-cell-metrics">
                <strong>수출 총액 ${tradeMoney(item.export_amount_100m_usd)}</strong>
                <span>반도체 총액 ${tradeMoney(item.semiconductor_export_amount_100m_usd)}</span>
                <span>수입 ${tradeMoney(item.import_amount_100m_usd)}</span>
                <span>일평균 전월비 수출 ${tradePct(item.export_daily_avg_mom_pct)} · 반도체 ${tradePct(item.semiconductor_daily_avg_mom_pct)}</span>
            </div>
        </div>
    `;
}

function renderTradeTrendProgressCard(item, maxValue) {
    return `
        <div class="trade-trend-progress-card">
            <div class="trade-trend-month-title">${escapeHtml(item.label)}</div>
            <div class="trade-trend-progress-phases">
                ${item.progressRows.map((row) => renderTradeTrendComparisonCard(row, maxValue)).join('')}
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

if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        buildTradeTrendChartModel,
        latestTradeTrendPhase,
        buildTradeTrendPhaseItems,
        buildTradeTrendQuarterItems,
        tradeDailyAverage,
        formatJejuMillionUsd,
    };
}

function formatJejuMillionUsd(value) {
    if (value === null || value === undefined || value === '') return '-';
    return `${(Number(value) / 1000000).toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}백만 달러`;
}

async function loadJejuRegionTrade() {
    const status = document.getElementById('jeju-trade-status');
    if (status) status.textContent = '조회 중';
    try {
        const res = await fetch('/api/trade-trends/jeju/region?months=12');
        const payload = await res.json();
        if (!res.ok || !payload.success) {
            throw new Error(payload.detail || '제주지역 수출입동향 조회 실패');
        }
        jejuRegionRows = payload.data.rows || [];
        renderJejuRegionSummary(payload.data.latest);
        renderJejuRegionTable();
        if (status) {
            status.textContent = payload.data.available
                ? `최근 ${jejuRegionRows.length}개월`
                : (payload.data.message || '조회할 수 없습니다.');
        }
    } catch (error) {
        if (status) status.textContent = `조회 실패: ${error.message}`;
        jejuRegionRows = [];
        renderJejuRegionSummary(null);
        renderJejuRegionTable();
    }
}

function renderJejuRegionSummary(latest) {
    document.getElementById('jeju-latest-period').textContent = latest ? latest.period_label : '-';
    document.getElementById('jeju-latest-export').textContent = latest ? formatJejuMillionUsd(latest.export_amount_usd) : '-';
    document.getElementById('jeju-latest-import').textContent = latest ? formatJejuMillionUsd(latest.import_amount_usd) : '-';
    document.getElementById('jeju-latest-balance').textContent = latest ? formatJejuMillionUsd(latest.trade_balance_usd) : '-';
}

function renderJejuRegionTable() {
    const tbody = document.getElementById('jeju-trade-body');
    if (!tbody) return;
    if (!jejuRegionRows.length) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:18px;">표시할 제주지역 수출입동향이 없습니다.</td></tr>';
        return;
    }
    tbody.innerHTML = jejuRegionRows.map((row) => `
        <tr>
            <td><strong>${escapeHtml(row.period_label || row.period || '-')}</strong></td>
            <td>${formatJejuMillionUsd(row.export_amount_usd)}</td>
            <td class="${Number(row.export_mom_pct || 0) >= 0 ? 'text-red' : 'text-blue'}">${tradePct(row.export_mom_pct)}</td>
            <td class="${Number(row.export_yoy_pct || 0) >= 0 ? 'text-red' : 'text-blue'}">${tradePct(row.export_yoy_pct)}</td>
            <td>${formatJejuMillionUsd(row.import_amount_usd)}</td>
            <td class="${Number(row.import_yoy_pct || 0) >= 0 ? 'text-red' : 'text-blue'}">${tradePct(row.import_yoy_pct)}</td>
            <td><strong>${formatJejuMillionUsd(row.trade_balance_usd)}</strong></td>
        </tr>
    `).join('');
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
