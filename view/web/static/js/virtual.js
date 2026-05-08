/* view/web/static/js/virtual.js — 모의투자 (Virtual Trading) */

let allVirtualData = [];
let summaryAgg = {};
let cumulativeReturns = {};
let virtualCounts = {};
let dailyChanges = {};
let weeklyChanges = {};
let dailyRefDates = {};
let weeklyRefDates = {};
let firstDates = {};
let profitFactors = {};
let expectancies = {};
let virtualHoldSortState = { key: null, dir: 'asc' };
let virtualSoldSortState = { key: null, dir: 'asc' };
let selectedVirtualStrategies = new Set(['ALL']);
let allVirtualStrategies = [];
let currentVirtualHoldData = [];
let currentVirtualSoldData = [];
let virtualJournalMeta = { count: 0, total_count: 0 };
let virtualDivergenceReport = null;

// 모의투자 데이터 자동 갱신 (5분마다)
let _virtualRefreshInterval = null;
function _startVirtualRefresh() {
    if (_virtualRefreshInterval) clearInterval(_virtualRefreshInterval);
    _virtualRefreshInterval = setInterval(() => {
        const virtualSection = document.getElementById('section-virtual');
        if (virtualSection && virtualSection.classList.contains('active')) {
            loadVirtualHistory();
        }
    }, 300000);
}
document.addEventListener('DOMContentLoaded', _startVirtualRefresh);
document.addEventListener('pjax:ready', (e) => {
    if (e.detail?.path === '/virtual') _startVirtualRefresh();
});

function _showVirtualSkeleton() {
    const holdBody = document.getElementById('virtual-hold-body');
    const soldBody = document.getElementById('virtual-sold-body');
    const rows = Array(3).fill(0).map(() =>
        `<tr class="skeleton-row"><td colspan="5"><span class="skeleton-bar"></span></td></tr>`
    ).join('');
    if (holdBody) holdBody.innerHTML = rows;
    if (soldBody) soldBody.innerHTML = rows;
}

async function loadVirtualHistory(forceCode = null) {
    const summaryBox = document.getElementById('virtual-summary-box');
    const tabContainer = document.getElementById('virtual-strategy-tabs');

    if (!tabContainer) return;

    _showVirtualSkeleton();

    try {
        showLoading(summaryBox, '성과 데이터 로드 중...');

        const applyCostEl = document.getElementById('apply-cost-chk');
        const applyCost = applyCostEl ? applyCostEl.checked : false;

        let url = '/api/virtual/history';
        const params = [];
        if (forceCode) params.push(`force_code=${forceCode}`);
        params.push(`apply_cost=${applyCost ? 'true' : 'false'}`);

        if (params.length > 0) url += '?' + params.join('&');

        const selectedArray = selectedVirtualStrategies.has('ALL')
            ? ['ALL']
            : [...selectedVirtualStrategies];
        if (typeof invalidateVirtualChartCache === 'function') {
            invalidateVirtualChartCache();
        }
        if (typeof prefetchVirtualChart === 'function') {
            prefetchVirtualChart(selectedArray);
        }

        const listRes = await fetchWithTimeout(url, {}, 30000);
        console.log('[Virtual] response status:', listRes.status);
        if (listRes.ok) {
            const body = await listRes.json();
            allVirtualData = body.trades || [];
            summaryAgg = body.summary_agg || {};
            cumulativeReturns = body.cumulative_returns || {};
            virtualCounts = body.counts || {};
            dailyChanges = body.daily_changes || {};
            weeklyChanges = body.weekly_changes || {};
            dailyRefDates = body.daily_ref_dates || {};
            weeklyRefDates = body.weekly_ref_dates || {};
            firstDates = body.first_dates || {};
            profitFactors = body.profit_factors || {};
            expectancies = body.expectancies || {};
            console.log('[Virtual] data count:', allVirtualData.length, 'sample:', allVirtualData[0]);
        } else {
            const errText = await listRes.text();
            console.error('[Virtual] API error:', listRes.status, errText);
            allVirtualData = [];
            summaryAgg = {};
            cumulativeReturns = {};
            virtualCounts = {};
            dailyChanges = {};
            weeklyChanges = {};
            dailyRefDates = {};
            weeklyRefDates = {};
            firstDates = {};
            profitFactors = {};
            expectancies = {};
        }

        const defaultStrategies = ['수동매매'];
        const dataStrategies = allVirtualData.map(item => item.strategy);
        const individualStrategies = [...new Set([...defaultStrategies, ...dataStrategies])];
        allVirtualStrategies = individualStrategies;
        const strategies = ['ALL', ...individualStrategies];

        tabContainer.innerHTML = strategies.map(strat =>
            `<button class="sub-tab-btn" onclick="toggleVirtualStrategy('${strat}', this)">${strat}</button>`
        ).join('');

        const newButtons = tabContainer.querySelectorAll('.sub-tab-btn');
        if (selectedVirtualStrategies.has('ALL') || selectedVirtualStrategies.size === 0) {
            selectedVirtualStrategies = new Set(['ALL']);
        }
        newButtons.forEach(btn => {
            if (selectedVirtualStrategies.has(btn.innerText)) {
                btn.classList.add('active');
            }
        });
        applyVirtualFilter();
        loadVirtualDivergence({ silent: true });
        loadVirtualBacktestJournalRuns();

        const section = document.getElementById('section-virtual');
        if (section) section.querySelectorAll('table').forEach(ensureTableInCard);

        return allVirtualData.length > 0 ? { trades: allVirtualData } : null;

    } catch (e) {
        console.error("Virtual history error:", e);
        summaryBox.innerText = "데이터 로드 실패";
    }
    return null;
}

function escapeVirtualHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function formatVirtualPct(value, digits = 2) {
    if (value === null || value === undefined || value === '') return '-';
    const num = Number(value);
    if (!Number.isFinite(num)) return '-';
    return `${num > 0 ? '+' : ''}${num.toFixed(digits)}%`;
}

function formatVirtualWon(value) {
    if (value === null || value === undefined || value === '') return '-';
    const num = Number(value);
    if (!Number.isFinite(num)) return '-';
    return `${num > 0 ? '+' : ''}${Math.round(num).toLocaleString()}원`;
}

function divergenceColorClass(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) return '';
    return num > 0 ? 'text-positive' : (num < 0 ? 'text-negative' : '');
}

function renderVirtualDivergence(report = virtualDivergenceReport) {
    const summaryEl = document.getElementById('virtual-divergence-summary');
    const bodyEl = document.getElementById('virtual-divergence-body');
    if (!summaryEl || !bodyEl) return;

    const summary = report?.summary || {};
    const backtestCount = summary.backtest_count ?? 0;
    const liveCount = summary.live_count ?? virtualJournalMeta.total_count ?? 0;
    const matchedCount = summary.matched_count ?? 0;
    const unmatchedBacktestCount = summary.unmatched_backtest_count ?? 0;
    const unmatchedLiveCount = summary.unmatched_live_count ?? 0;

    summaryEl.innerHTML = `
        <div style="display:flex; justify-content:center; align-items:stretch; gap:10px; flex-wrap:wrap;">
            <div style="background-color:#000; color:#fff; padding:10px 14px; border:1px solid #30363d; border-radius:8px; min-width:115px;">
                <div style="font-size:0.8em; color:#a0a0b0; margin-bottom:4px;">실거래 journal</div>
                <strong>${liveCount}</strong>건
            </div>
            <div style="background-color:#000; color:#fff; padding:10px 14px; border:1px solid #30363d; border-radius:8px; min-width:115px;">
                <div style="font-size:0.8em; color:#a0a0b0; margin-bottom:4px;">백테스트 journal</div>
                <strong>${backtestCount}</strong>건
            </div>
            <div style="background-color:#000; color:#fff; padding:10px 14px; border:1px solid #30363d; border-radius:8px; min-width:115px;">
                <div style="font-size:0.8em; color:#a0a0b0; margin-bottom:4px;">매칭</div>
                <strong>${matchedCount}</strong>건
            </div>
            <div style="background-color:#000; color:#fff; padding:10px 14px; border:1px solid #30363d; border-radius:8px; min-width:130px;">
                <div style="font-size:0.8em; color:#a0a0b0; margin-bottom:4px;">평균 순수익률 차이</div>
                <strong class="${divergenceColorClass(summary.avg_net_return_diff)}">${formatVirtualPct(summary.avg_net_return_diff)}</strong>
            </div>
            <div style="background-color:#000; color:#fff; padding:10px 14px; border:1px solid #30363d; border-radius:8px; min-width:130px;">
                <div style="font-size:0.8em; color:#a0a0b0; margin-bottom:4px;">순손익 차이 합계</div>
                <strong class="${divergenceColorClass(summary.total_net_pnl_diff)}">${formatVirtualWon(summary.total_net_pnl_diff)}</strong>
            </div>
            <div style="background-color:#000; color:#fff; padding:10px 14px; border:1px solid #30363d; border-radius:8px; min-width:120px;">
                <div style="font-size:0.8em; color:#a0a0b0; margin-bottom:4px;">미매칭</div>
                <strong>${unmatchedBacktestCount + unmatchedLiveCount}</strong>건
            </div>
        </div>
    `;

    const rows = [];
    (report?.matches || []).forEach(row => {
        rows.push({
            status: 'MATCH',
            strategy: row.strategy,
            code: row.code,
            tradeDate: row.trade_date,
            backtestReturn: row.backtest_net_return,
            liveReturn: row.live_net_return,
            diff: row.net_return_diff,
            fillDiff: row.fill_price_diff_pct,
        });
    });
    (report?.unmatched_backtest || []).forEach(row => {
        rows.push({
            status: 'BACKTEST',
            strategy: row.strategy,
            code: row.code,
            tradeDate: String(row.signal_time || '').slice(0, 10),
            backtestReturn: row.net_return,
            liveReturn: null,
            diff: null,
            fillDiff: null,
        });
    });
    (report?.unmatched_live || []).forEach(row => {
        rows.push({
            status: 'LIVE',
            strategy: row.strategy,
            code: row.code,
            tradeDate: String(row.signal_time || '').slice(0, 10),
            backtestReturn: null,
            liveReturn: row.net_return,
            diff: null,
            fillDiff: null,
        });
    });

    if (rows.length === 0) {
        bodyEl.innerHTML = '<tr><td colspan="8" style="text-align:center; padding:15px;">비교 대기</td></tr>';
        return;
    }

    bodyEl.innerHTML = rows.slice(0, 100).map(row => `
        <tr>
            <td>${escapeVirtualHtml(row.status)}</td>
            <td>${escapeVirtualHtml(row.strategy)}</td>
            <td>${escapeVirtualHtml(row.code)}</td>
            <td>${escapeVirtualHtml(row.tradeDate)}</td>
            <td>${formatVirtualPct(row.backtestReturn)}</td>
            <td>${formatVirtualPct(row.liveReturn)}</td>
            <td class="${divergenceColorClass(row.diff)}"><strong>${formatVirtualPct(row.diff)}</strong></td>
            <td>${formatVirtualPct(row.fillDiff, 4)}</td>
        </tr>
    `).join('');
}

async function loadVirtualDivergence(options = {}) {
    const summaryEl = document.getElementById('virtual-divergence-summary');
    if (!summaryEl) return;

    if (!options.silent) {
        showLoading(summaryEl, '괴리 데이터 로드 중...');
    }

    try {
        const journalRes = await fetchWithTimeout('/api/virtual/journal?limit=500', {}, 15000);
        if (journalRes.ok) {
            const journalBody = await journalRes.json();
            virtualJournalMeta = {
                count: journalBody.count || 0,
                total_count: journalBody.total_count || 0,
            };
        }

        const input = document.getElementById('virtual-backtest-journal-input');
        if (input && input.value.trim()) {
            await compareVirtualDivergence();
        } else {
            virtualDivergenceReport = null;
            renderVirtualDivergence(null);
        }
    } catch (error) {
        console.error('[Virtual] divergence load failed:', error);
        summaryEl.innerText = '괴리 데이터 로드 실패';
    }
}

window.loadVirtualDivergence = loadVirtualDivergence;

async function loadVirtualBacktestJournalRuns() {
    const select = document.getElementById('virtual-backtest-run-select');
    if (!select) return;

    try {
        const response = await fetchWithTimeout('/api/virtual/backtest-journals?limit=50', {}, 15000);
        if (!response.ok) {
            throw new Error(`Backtest journal list API ${response.status}`);
        }
        const body = await response.json();
        const runs = body.runs || [];
        if (runs.length === 0) {
            select.innerHTML = '<option value="">저장된 백테스트 없음</option>';
            return;
        }
        select.innerHTML = [
            '<option value="">저장된 백테스트 선택</option>',
            ...runs.map(run => {
                const label = [
                    run.target_date || '날짜없음',
                    run.strategy || '전략없음',
                    `${run.record_count || 0}건`,
                ].join(' / ');
                return `<option value="${escapeVirtualHtml(run.run_id)}">${escapeVirtualHtml(label)}</option>`;
            }),
        ].join('');
    } catch (error) {
        console.error('[Virtual] backtest journal runs load failed:', error);
        select.innerHTML = '<option value="">백테스트 목록 로드 실패</option>';
    }
}

window.loadVirtualBacktestJournalRuns = loadVirtualBacktestJournalRuns;

window.loadVirtualBacktestJournalRun = async function() {
    const select = document.getElementById('virtual-backtest-run-select');
    const input = document.getElementById('virtual-backtest-journal-input');
    const summaryEl = document.getElementById('virtual-divergence-summary');
    if (!select || !input) return;

    const runId = select.value;
    if (!runId) return;

    try {
        if (summaryEl) showLoading(summaryEl, '백테스트 journal 불러오는 중...');
        const response = await fetchWithTimeout(`/api/virtual/backtest-journals/${encodeURIComponent(runId)}`, {}, 15000);
        if (!response.ok) {
            throw new Error(`Backtest journal records API ${response.status}`);
        }
        const body = await response.json();
        input.value = JSON.stringify(body.records || [], null, 2);
        await compareVirtualDivergence();
    } catch (error) {
        console.error('[Virtual] backtest journal run load failed:', error);
        if (summaryEl) summaryEl.innerText = '백테스트 journal 로드 실패';
    }
};

window.compareVirtualDivergence = async function() {
    const input = document.getElementById('virtual-backtest-journal-input');
    const summaryEl = document.getElementById('virtual-divergence-summary');
    if (!input || !summaryEl) return;

    let backtestRecords = [];
    try {
        const raw = input.value.trim();
        backtestRecords = raw ? JSON.parse(raw) : [];
        if (!Array.isArray(backtestRecords)) {
            throw new Error('journal payload must be an array');
        }
    } catch (error) {
        summaryEl.innerText = 'JSON 형식 오류';
        return;
    }

    showLoading(summaryEl, '괴리 비교 중...');
    try {
        const response = await fetchWithTimeout('/api/virtual/backtest-divergence', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(backtestRecords),
        }, 30000);
        if (!response.ok) {
            throw new Error(`Divergence API ${response.status}`);
        }
        virtualDivergenceReport = await response.json();
        renderVirtualDivergence(virtualDivergenceReport);
    } catch (error) {
        console.error('[Virtual] divergence compare failed:', error);
        summaryEl.innerText = '괴리 비교 실패';
    }
};

// 보유일 계산 유틸
function calcDaysHeld(buyDateStr, endDateStr) {
    if (!buyDateStr) return '-';
    const buy = new Date(buyDateStr.split(' ')[0]);
    const end = endDateStr ? new Date(endDateStr.split(' ')[0]) : new Date();
    const diff = Math.floor((end - buy) / (1000 * 60 * 60 * 24));
    return diff;
}

// 종목 표시명
function stockLabel(item) {
    const name = item.stock_name || '';
    return name ? `${name}(${item.code})` : item.code;
}

// 전략 멀티셀렉트 토글
window.toggleVirtualStrategy = function(strategyName, btnElement) {
    const buttons = document.querySelectorAll('#virtual-strategy-tabs .sub-tab-btn');

    if (strategyName === 'ALL') {
        if (selectedVirtualStrategies.has('ALL')) {
            return;
        }
        selectedVirtualStrategies = new Set(['ALL']);
        buttons.forEach(b => b.classList.remove('active'));
        if (btnElement) btnElement.classList.add('active');
    } else {
        if (selectedVirtualStrategies.has('ALL')) {
            selectedVirtualStrategies = new Set([strategyName]);
            buttons.forEach(b => b.classList.remove('active'));
            if (btnElement) btnElement.classList.add('active');
        } else if (selectedVirtualStrategies.has(strategyName)) {
            if (selectedVirtualStrategies.size <= 1) {
                selectedVirtualStrategies = new Set(['ALL']);
                buttons.forEach(b => b.classList.remove('active'));
                const allBtn = buttons[0];
                if (allBtn) allBtn.classList.add('active');
            } else {
                selectedVirtualStrategies.delete(strategyName);
                if (btnElement) btnElement.classList.remove('active');
            }
        } else {
            selectedVirtualStrategies.add(strategyName);
            if (btnElement) btnElement.classList.add('active');
        }

        if (allVirtualStrategies.length > 0 &&
            allVirtualStrategies.every(s => selectedVirtualStrategies.has(s))) {
            selectedVirtualStrategies = new Set(['ALL']);
            buttons.forEach(b => b.classList.remove('active'));
            const allBtn = buttons[0];
            if (allBtn) allBtn.classList.add('active');
        }
    }

    applyVirtualFilter();
};

function applyVirtualFilter() {
    if (window.Paginator) {
        window.Paginator.reset('virtual-hold');
        window.Paginator.reset('virtual-sold');
    }
    const isAll = selectedVirtualStrategies.has('ALL');
    const selectedArray = isAll ? ['ALL'] : [...selectedVirtualStrategies];
    const displayLabel = isAll ? 'ALL' : selectedArray.join(', ');

    let filteredData = allVirtualData;
    if (!isAll) {
        filteredData = allVirtualData.filter(item => selectedVirtualStrategies.has(item.strategy));
    }

    const holdData = filteredData.filter(item => item.status === 'HOLD');
    const soldData = filteredData.filter(item => item.status === 'SOLD');

    const totalTrades = filteredData.length;
    let cumulativeReturn;

    if (isAll) {
        cumulativeReturn = cumulativeReturns['ALL'] ?? 0;
    } else if (selectedArray.length === 1) {
        cumulativeReturn = cumulativeReturns[selectedArray[0]] ?? 0;
    } else {
        let totalBuyAmt = 0;
        let totalEvalAmt = 0;
        selectedArray.forEach(strat => {
            const agg = summaryAgg[strat];
            if (agg) {
                totalBuyAmt += agg.buy_sum || 0;
                totalEvalAmt += agg.eval_sum || 0;
            }
        });
        cumulativeReturn = totalBuyAmt > 0 ? ((totalEvalAmt - totalBuyAmt) / totalBuyAmt) * 100 : 0;
    }

    let dailyChange, weeklyChange, dailyRefDate, weeklyRefDate, firstDate;
    let profitFactor, expectancy;
    const toShortDate = (d) => d ? d.slice(2).replace(/-/g, '') : '';
    const todayShort = toShortDate(new Date().toISOString().slice(0, 10));

    if (isAll) {
        weeklyChange = weeklyChanges['ALL'];
        dailyRefDate = dailyRefDates['ALL'];
        weeklyRefDate = weeklyRefDates['ALL'];
        firstDate = firstDates['ALL'];
        profitFactor = profitFactors['ALL'];
        expectancy = expectancies['ALL'];
    } else if (selectedArray.length === 1) {
        dailyChange = dailyChanges[selectedArray[0]];
        weeklyChange = weeklyChanges[selectedArray[0]];
        dailyRefDate = dailyRefDates[selectedArray[0]];
        weeklyRefDate = weeklyRefDates[selectedArray[0]];
        firstDate = firstDates[selectedArray[0]];
        profitFactor = profitFactors[selectedArray[0]];
        expectancy = expectancies[selectedArray[0]];
    } else {
        // 다중 전략: 전일/전주대비는 누적수익률 기반으로 재계산
        // 각 전략의 buy_sum/eval_sum을 합산하여 누적수익률을 구한 뒤, 스냅샷 변동분과 비교
        // 단순 합산이 어려우므로 개별 전략 값의 가중평균 사용
        let totalBuyForWeight = 0;
        let weightedDaily = 0, weightedWeekly = 0;
        let hasDaily = false, hasWeekly = false;
        selectedArray.forEach(strat => {
            const agg = summaryAgg[strat];
            const buyAmt = agg ? (agg.buy_sum || 0) : 0;
            if (buyAmt <= 0) return;

            const dc = dailyChanges[strat];
            const wc = weeklyChanges[strat];
            if (dc != null) { weightedDaily += dc * buyAmt; hasDaily = true; }
            if (wc != null) { weightedWeekly += wc * buyAmt; hasWeekly = true; }
            totalBuyForWeight += buyAmt;

            // ref dates: 가장 이른 날짜
            const dr = dailyRefDates[strat];
            const wr = weeklyRefDates[strat];
            if (dr && (!dailyRefDate || dr < dailyRefDate)) dailyRefDate = dr;
            if (wr && (!weeklyRefDate || wr < weeklyRefDate)) weeklyRefDate = wr;
        });
        dailyChange = (hasDaily && totalBuyForWeight > 0) ? weightedDaily / totalBuyForWeight : null;
        weeklyChange = (hasWeekly && totalBuyForWeight > 0) ? weightedWeekly / totalBuyForWeight : null;

        const fDates = selectedArray.map(s => firstDates[s]).filter(Boolean).sort();
        firstDate = fDates[0];

        // 다중 전략: PF 합산 (총 수익금 합 / 총 손실금 합)
        let multiTotalGain = 0, multiTotalLoss = 0;
        selectedArray.forEach(strat => {
            const pf = profitFactors[strat];
            if (pf && typeof pf === 'object') {
                multiTotalGain += pf.total_gain || 0;
                multiTotalLoss += pf.total_loss || 0;
            }
        });
        if (multiTotalLoss > 0) {
            profitFactor = { value: Math.round((multiTotalGain / multiTotalLoss) * 100) / 100, total_gain: Math.round(multiTotalGain), total_loss: Math.round(multiTotalLoss) };
        } else if (multiTotalGain > 0) {
            profitFactor = { value: null, total_gain: Math.round(multiTotalGain), total_loss: 0 };
        } else {
            profitFactor = { value: 0, total_gain: 0, total_loss: 0 };
        }

        // 다중 전략: Expectancy 합산
        let multiWins = 0, multiLosses = 0, multiGainSum = 0, multiLossSum = 0;
        selectedArray.forEach(strat => {
            const exp = expectancies[strat];
            if (exp && typeof exp === 'object') {
                multiWins += exp.wins || 0;
                multiLosses += exp.losses || 0;
                multiGainSum += (exp.avg_gain || 0) * (exp.wins || 0);
                multiLossSum += (exp.avg_loss || 0) * (exp.losses || 0);
            }
        });
        const multiTotal = multiWins + multiLosses;
        if (multiTotal > 0) {
            const wr = multiWins / multiTotal;
            const lr = multiLosses / multiTotal;
            const ag = multiWins > 0 ? multiGainSum / multiWins : 0;
            const al = multiLosses > 0 ? multiLossSum / multiLosses : 0;
            expectancy = { value: Math.round((wr * ag) - (lr * al)), win_rate: Math.round(wr * 1000) / 10, avg_gain: Math.round(ag), avg_loss: Math.round(al), wins: multiWins, losses: multiLosses };
        } else {
            expectancy = { value: 0, win_rate: 0, avg_gain: 0, avg_loss: 0, wins: 0, losses: 0 };
        }
    }

    let holdCount = 0;
    let todayBuyCount = 0;
    let todaySellCount = 0;

    if (isAll) {
        const c = virtualCounts['ALL'] || {};
        holdCount = c.hold || 0;
        todayBuyCount = c.today_buy || 0;
        todaySellCount = c.today_sell || 0;
    } else if (selectedArray.length === 1) {
        const c = virtualCounts[selectedArray[0]] || {};
        holdCount = c.hold || 0;
        todayBuyCount = c.today_buy || 0;
        todaySellCount = c.today_sell || 0;
    } else {
        selectedArray.forEach(strat => {
            const c = virtualCounts[strat];
            if (c) {
                holdCount += c.hold || 0;
                todayBuyCount += c.today_buy || 0;
                todaySellCount += c.today_sell || 0;
            }
        });
    }

    const cumDateLabel = firstDate ? `${toShortDate(firstDate)}~${todayShort}` : '';
    const dailyDateLabel = dailyRefDate ? toShortDate(dailyRefDate) : '';
    const weeklyDateLabel = weeklyRefDate ? toShortDate(weeklyRefDate) : '';

    const colorClass = (val) => val > 0 ? 'text-positive' : (val < 0 ? 'text-negative' : '');
    const signPrefix = (val) => val > 0 ? '+' : '';

    const summaryBox = document.getElementById('virtual-summary-box');
    if (!summaryBox) { console.error('[Virtual] virtual-summary-box not found'); return; }
    summaryBox.innerHTML = `
        <div style="margin-bottom: 15px; margin-top: 5px;">
            <div style="background-color: #000000 !important; color: #ffffff !important; padding: 6px 18px; border-radius: 20px; border: 1.5px solid #e94560; display: inline-block; box-shadow: 0 2px 6px rgba(0,0,0,0.3);">
                <span style="color: #e94560; margin-right: 6px; font-size: 1.1em;">📊</span>
                <span style="font-size: 1.05em; font-weight: 700 !important; letter-spacing: 0.5px;">[ ${displayLabel} 성과 요약 ]</span>
            </div>
        </div>
        <div style="display: flex; justify-content: center; align-items: center; gap: 12px; flex-wrap: wrap;">
            <div style="background-color: #000000 !important; color: #ffffff !important; padding: 12px 18px; border-radius: 10px; border: 1px solid #30363d; min-width: 125px; box-shadow: 0 4px 8px rgba(0,0,0,0.4);">
                <div style="font-size: 0.85em; color: #a0a0b0 !important; margin-bottom: 4px; font-weight: 600;">총 거래</div>
                <div style="color: #ffffff !important;"><strong style="font-size: 1.35em;">${totalTrades}</strong> <span style="font-size: 1em;">건</span></div>
            </div>
            <div style="background-color: #000000 !important; color: #ffffff !important; padding: 12px 18px; border-radius: 10px; border: 1px solid #30363d; min-width: 160px; box-shadow: 0 4px 8px rgba(0,0,0,0.4);">
                <div style="font-size: 0.85em; color: #a0a0b0 !important; margin-bottom: 4px; font-weight: 600;">포지션 현황</div>
                <div style="color: #ffffff !important; font-size: 0.95em; line-height: 1.4;">
                    보유: <strong>${holdCount}</strong><br>
                    <span style="font-size:0.9em; color:#ccc;">(매수 <span style="color:#ff4d4d">+${todayBuyCount}</span> / 이탈 <span style="color:#4d94ff">-${todaySellCount}</span>)</span>
                </div>
            </div>
            <div style="background-color: #000000 !important; color: #ffffff !important; padding: 12px 18px; border-radius: 10px; border: 1px solid #30363d; min-width: 125px; box-shadow: 0 4px 8px rgba(0,0,0,0.4);">
                <div style="font-size: 0.85em; color: #a0a0b0 !important; margin-bottom: 4px; font-weight: 600;">누적 수익률 <span style="color:#707080; font-size:0.85em;">${cumDateLabel}</span></div>
                <strong class="${colorClass(cumulativeReturn)}" style="font-size: 1.35em; font-weight: 800 !important;">
                    ${signPrefix(cumulativeReturn)}${cumulativeReturn.toFixed(2)}%
                </strong>
            </div>
            <div style="background-color: #000000 !important; color: #ffffff !important; padding: 12px 18px; border-radius: 10px; border: 1px solid #30363d; min-width: 125px; box-shadow: 0 4px 8px rgba(0,0,0,0.4);">
                <div style="font-size: 0.85em; color: #a0a0b0 !important; margin-bottom: 4px; font-weight: 600;">전일대비 <span style="color:#707080; font-size:0.85em;">${dailyDateLabel}</span></div>
                <strong class="${dailyChange != null ? colorClass(dailyChange) : ''}" style="font-size: 1.35em; font-weight: 800 !important;">
                    ${dailyChange != null ? signPrefix(dailyChange) + dailyChange.toFixed(2) + '%' : '-'}
                </strong>
            </div>
            <div style="background-color: #000000 !important; color: #ffffff !important; padding: 12px 18px; border-radius: 10px; border: 1px solid #30363d; min-width: 125px; box-shadow: 0 4px 8px rgba(0,0,0,0.4);">
                <div style="font-size: 0.85em; color: #a0a0b0 !important; margin-bottom: 4px; font-weight: 600;">전주대비 <span style="color:#707080; font-size:0.85em;">${weeklyDateLabel}</span></div>
                <strong class="${weeklyChange != null ? colorClass(weeklyChange) : ''}" style="font-size: 1.35em; font-weight: 800 !important;">
                    ${weeklyChange != null ? signPrefix(weeklyChange) + weeklyChange.toFixed(2) + '%' : '-'}
                </strong>
            </div>
        </div>
        <div style="display: flex; justify-content: center; align-items: center; gap: 10px; flex-wrap: wrap; margin-top: 8px;">
            <div style="background-color: #000000 !important; color: #ffffff !important; padding: 8px 14px; border-radius: 8px; border: 1px solid #30363d; min-width: 100px; box-shadow: 0 2px 4px rgba(0,0,0,0.3); cursor: help;"
                 title="${(() => {
                    const pfData = profitFactor;
                    if (!pfData || typeof pfData !== 'object') return 'Profit Factor = 총 수익금 ÷ 총 손실금';
                    const g = Number(pfData.total_gain || 0).toLocaleString();
                    const l = Number(pfData.total_loss || 0).toLocaleString();
                    return 'Profit Factor = 총 수익금 ÷ 총 손실금\n\n총 수익금: ' + g + '원\n총 손실금: ' + l + '원\n\n< 1.0: 손실 시스템\n≥ 1.5: 우수\n≥ 2.0: 최우수';
                 })()}">
                <div style="font-size: 0.8em; color: #a0a0b0 !important; margin-bottom: 3px; font-weight: 600;">Profit Factor</div>
                ${(() => {
                    const pfData = profitFactor;
                    if (pfData == null) return '<strong style="font-size: 1.15em; font-weight: 800 !important;">-</strong>';
                    const pf = typeof pfData === 'object' ? pfData.value : pfData;
                    if (pf === null) return '<strong style="font-size: 1.15em; font-weight: 800 !important; color: #ffd700;">&infin;</strong>';
                    const pfNum = Number(pf) || 0;
                    const pfColor = pfNum >= 2.0 ? '#ffd700' : pfNum >= 1.5 ? '#4dff4d' : pfNum >= 1.0 ? '#ffffff' : '#ff4d4d';
                    const pfWeight = pfNum >= 2.0 ? '900' : pfNum >= 1.5 ? '800' : '600';
                    return '<strong style="font-size: 1.15em; font-weight: ' + pfWeight + ' !important; color: ' + pfColor + ';">' + pfNum.toFixed(2) + '</strong>';
                })()}
            </div>
            <div style="background-color: #000000 !important; color: #ffffff !important; padding: 8px 14px; border-radius: 8px; border: 1px solid #30363d; min-width: 100px; box-shadow: 0 2px 4px rgba(0,0,0,0.3); cursor: help;"
                 title="${(() => {
                    const expData = expectancy;
                    if (!expData || typeof expData !== 'object') return '1회 매매당 기대수익\n= (승률 × 평균수익금) - (패배율 × 평균손실금)';
                    const wr = expData.win_rate || 0;
                    const ag = Number(expData.avg_gain || 0).toLocaleString();
                    const al = Number(expData.avg_loss || 0).toLocaleString();
                    const w = expData.wins || 0;
                    const l = expData.losses || 0;
                    return '1회 매매당 기대수익 (평균 매수금 기준)\n= (승률 × 평균수익금) - (패배율 × 평균손실금)\n\n승률: ' + wr + '% (' + w + '승 ' + l + '패)\n평균 수익금: ' + ag + '원\n평균 손실금: ' + al + '원\n\n양수(+)면 반복할수록 수익이 기대되는 시스템';
                 })()}">
                <div style="font-size: 0.8em; color: #a0a0b0 !important; margin-bottom: 3px; font-weight: 600;">기대수익<span style="font-size:0.85em; color:#707080;"> /1회</span></div>
                ${(() => {
                    const expData = expectancy;
                    if (expData == null) return '<strong style="font-size: 1.15em; font-weight: 800 !important;">-</strong>';
                    const exp = typeof expData === 'object' ? Number(expData.value || 0) : Number(expData || 0);
                    const expColor = exp > 0 ? '#4dff4d' : exp < 0 ? '#ff4d4d' : '#ffffff';
                    const expWeight = exp > 0 ? '800' : '600';
                    return '<strong style="font-size: 1.15em; font-weight: ' + expWeight + ' !important; color: ' + expColor + ';">' + (exp > 0 ? '+' : '') + exp.toLocaleString() + '원</strong>';
                })()}
            </div>
        </div>
    `;

    currentVirtualHoldData = holdData;
    currentVirtualSoldData = soldData.slice().reverse();
    renderVirtualHoldTable();
    renderVirtualSoldTable();

    console.log('[applyVirtualFilter] refreshVirtualChart 호출 예정, selectedArray:', selectedArray,
        'refreshVirtualChart 존재:', typeof refreshVirtualChart);
    if (typeof refreshVirtualChart === 'function') {
        refreshVirtualChart(selectedArray);
    }
}

function virtualSortCompare(data, key, dir) {
    const sorted = data.slice();
    const d = dir === 'asc' ? 1 : -1;
    sorted.sort((a, b) => {
        let va, vb;
        if (key === 'name') {
            va = (a.stock_name || a.code || '').toLowerCase();
            vb = (b.stock_name || b.code || '').toLowerCase();
            return d * va.localeCompare(vb);
        } else if (key === 'buy_price') {
            va = Number(a.buy_price || 0);
            vb = Number(b.buy_price || 0);
        } else if (key === 'current_price') {
            va = Number(a.current_price || 0);
            vb = Number(b.current_price || 0);
        } else if (key === 'sell_price') {
            va = Number(a.sell_price || 0);
            vb = Number(b.sell_price || 0);
        } else if (key === 'return_rate') {
            va = Number(a.return_rate || 0);
            vb = Number(b.return_rate || 0);
        } else if (key === 'days') {
            va = calcDaysHeld(a.buy_date, a.sell_date || null);
            vb = calcDaysHeld(b.buy_date, b.sell_date || null);
            va = typeof va === 'number' ? va : 0;
            vb = typeof vb === 'number' ? vb : 0;
        } else {
            return 0;
        }
        return d * (va - vb);
    });
    return sorted;
}

function virtualSortClass(table, key) {
    const state = table === 'hold' ? virtualHoldSortState : virtualSoldSortState;
    if (state.key !== key) return 'sortable';
    return `sortable sort-${state.dir}`;
}

function updateVirtualSortHeaders(table) {
    const section = document.getElementById('section-virtual');
    const tables = section.querySelectorAll('.data-table');
    const target = table === 'hold' ? tables[0] : tables[1];
    if (!target) return;
    const ths = target.querySelectorAll('thead th');
    const keys = table === 'hold'
        ? ['name', 'buy_price', 'current_price', 'return_rate', 'days']
        : ['name', 'buy_price', 'sell_price', 'return_rate', 'days'];
    ths.forEach((th, i) => {
        if (keys[i]) th.className = virtualSortClass(table, keys[i]);
    });
}

function renderVirtualHoldTable() {
    const holdBody = document.getElementById('virtual-hold-body');
    if (!holdBody) return;
    holdBody.innerHTML = '';
    let data = currentVirtualHoldData;
    if (virtualHoldSortState.key) {
        data = virtualSortCompare(data, virtualHoldSortState.key, virtualHoldSortState.dir);
    }

    updateVirtualSortHeaders('hold');

    const applyCostEl = document.getElementById('apply-cost-chk');
    const showCost = applyCostEl ? applyCostEl.checked : false;

    if (data.length === 0) {
        holdBody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:15px;">보유 종목이 없습니다.</td></tr>';
        const ctrl = document.getElementById('virtual-hold-pagination');
        if (ctrl) ctrl.innerHTML = '';
        return;
    }
    const pageData = window.Paginator
        ? window.Paginator.paginate('virtual-hold', data, 'virtual-hold-pagination', renderVirtualHoldTable)
        : data;
    pageData.forEach(item => {
        const ror = item.return_rate || 0;
        const rorClass = ror > 0 ? 'text-positive' : (ror < 0 ? 'text-negative' : '');
        const buyDate = item.buy_date ? item.buy_date.split(' ')[0] : '-';
        const buyPrice = Number(item.buy_price).toLocaleString();
        const curPrice = item.current_price ? Number(item.current_price).toLocaleString() : '-';
        const days = calcDaysHeld(item.buy_date, null);

        const cacheAge = item.cache_ts ? Math.floor(Date.now() / 1000 - item.cache_ts) : 0;
        const isOldCache = item.is_cached && cacheAge > 60;
        const cacheStyle = isOldCache ? 'color: #ff4d4d; opacity: 1; font-weight: bold;' : 'opacity: 0.6;';
        const cacheLabel = item.is_cached ? `<span title="API 호출 실패로 인한 캐시 데이터 (경과: ${Math.floor(cacheAge/60)}분)" style="cursor:help; margin-left:4px; ${cacheStyle}">🕒</span>` : '';
        const forceBtn = `<span onclick="forceUpdateStock('${item.code}', event)" title="강제 업데이트" style="cursor:pointer; margin-left:6px; opacity:0.5; transition: transform 0.3s;">🔄</span>`;

        const strategyLabel = item.strategy ? `<div style="font-size:0.75em; color:#888; margin-bottom:2px;">${item.strategy}</div>` : '';

        let costHtml = '';
        if (showCost) {
            const qty = Number(item.qty) || 1;
            const bp = Number(item.buy_price || 0);
            const cp = Number(item.current_price || 0);
            if (bp > 0 && cp > 0) {
                const cost = Math.floor((bp * qty * 0.000140527) + (cp * qty * (0.000140527 + 0.002)));
                costHtml = ` <span style="font-size:0.75em; color:#999; font-weight:normal;">(-${cost.toLocaleString()}원)</span>`;
            }
        }

        holdBody.insertAdjacentHTML('beforeend', `
            <tr>
                <td>
                    ${strategyLabel}
                    <a href="/stock?code=${item.code}" target="_blank" class="stock-link">${stockLabel(item)}</a>
                </td>
                <td>${buyPrice}</td>
                <td>${curPrice}${cacheLabel}${forceBtn}</td>
                <td class="${rorClass}"><strong>${ror.toFixed(2)}%</strong>${costHtml}</td>
                <td>${days}일<div style="font-size:0.8em; color:var(--text-secondary);">${buyDate}</div></td>
            </tr>
        `);
    });
}

function renderVirtualSoldTable() {
    const soldBody = document.getElementById('virtual-sold-body');
    if (!soldBody) return;
    soldBody.innerHTML = '';

    let data = currentVirtualSoldData;
    if (virtualSoldSortState.key) {
        data = virtualSortCompare(data, virtualSoldSortState.key, virtualSoldSortState.dir);
    }

    updateVirtualSortHeaders('sold');

    const applyCostEl = document.getElementById('apply-cost-chk');
    const showCost = applyCostEl ? applyCostEl.checked : false;

    if (data.length === 0) {
        soldBody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:15px;">매도 기록이 없습니다.</td></tr>';
        const ctrl = document.getElementById('virtual-sold-pagination');
        if (ctrl) ctrl.innerHTML = '';
        return;
    }
    const pageData = window.Paginator
        ? window.Paginator.paginate('virtual-sold', data, 'virtual-sold-pagination', renderVirtualSoldTable)
        : data;
    pageData.forEach(item => {
        const ror = item.return_rate || 0;
        const rorClass = ror > 0 ? 'text-positive' : (ror < 0 ? 'text-negative' : '');
        const buyDate = item.buy_date ? item.buy_date.split(' ')[0] : '-';
        const sellDate = item.sell_date ? item.sell_date.split(' ')[0] : '-';
        const buyPrice = Number(item.buy_price).toLocaleString();
        const sellPrice = (item.sell_price != null && item.sell_price > 0) ? Number(item.sell_price).toLocaleString() : '-';
        const curPrice = item.current_price ? Number(item.current_price).toLocaleString() : '';
        const days = calcDaysHeld(item.buy_date, item.sell_date);

        const cacheAge = item.cache_ts ? Math.floor(Date.now() / 1000 - item.cache_ts) : 0;
        const isOldCache = item.is_cached && cacheAge > 60;
        const cacheStyle = isOldCache ? 'color: #ff4d4d; opacity: 1; font-weight: bold;' : 'opacity: 0.6;';
        const cacheLabel = item.is_cached ? `<span title="API 호출 실패로 인한 캐시 데이터 (경과: ${Math.floor(cacheAge/60)}분)" style="cursor:help; margin-left:4px; ${cacheStyle}">🕒</span>` : '';
        const forceBtn = `<span onclick="forceUpdateStock('${item.code}', event)" title="강제 업데이트" style="cursor:pointer; margin-left:6px; opacity:0.5; transition: transform 0.3s;">🔄</span>`;

        const strategyLabel = item.strategy ? `<div style="font-size:0.75em; color:#888; margin-bottom:2px;">${item.strategy}</div>` : '';

        let costHtml = '';
        if (showCost) {
            const qty = Number(item.qty) || 1;
            const bp = Number(item.buy_price || 0);
            let sp = Number(item.sell_price || 0);
            if (sp === 0 && item.current_price) sp = Number(item.current_price);
            if (bp > 0 && sp > 0) {
                const cost = Math.floor((bp * qty * 0.000140527) + (sp * qty * (0.000140527 + 0.002)));
                costHtml = ` <span style="font-size:0.75em; color:#999; font-weight:normal;">(-${cost.toLocaleString()}원)</span>`;
            }
        }

        soldBody.insertAdjacentHTML('beforeend', `
            <tr>
                <td>
                    ${strategyLabel}
                    <a href="/stock?code=${item.code}" target="_blank" class="stock-link">${stockLabel(item)}</a>
                </td>
                <td>${buyPrice}</td>
                <td>${sellPrice}<div style="font-size:0.8em; color:var(--text-secondary);">${curPrice}${cacheLabel}${forceBtn}</div></td>
                <td class="${rorClass}"><strong>${ror.toFixed(2)}%</strong>${costHtml}</td>
                <td>${days}일<div style="font-size:0.8em; color:var(--text-secondary);">${buyDate} ~ ${sellDate}</div></td>
            </tr>
        `);
    });
}

function sortVirtual(table, key) {
    const state = table === 'hold' ? virtualHoldSortState : virtualSoldSortState;
    if (state.key === key) {
        state.dir = state.dir === 'asc' ? 'desc' : 'asc';
    } else {
        state.key = key;
        state.dir = 'asc';
    }

    if (window.Paginator) {
        window.Paginator.reset(table === 'hold' ? 'virtual-hold' : 'virtual-sold');
    }
    if (table === 'hold') renderVirtualHoldTable();
    else renderVirtualSoldTable();
}

// 특정 종목 강제 업데이트 함수
window.forceUpdateStock = async function(code, event) {
    console.log(`[Virtual] 종목 강제 업데이트 시도: ${code}`);
    if (event && event.currentTarget) {
        event.currentTarget.classList.add('spinning');
    }

    const data = await loadVirtualHistory(code);

    if (event && event.currentTarget) {
        event.currentTarget.classList.remove('spinning');
    }

    if (data && data.trades) {
        const item = data.trades.find(t => t.code === code);
        if (item) {
            if (item.is_cached) {
                showToast(`${stockLabel(item)} 업데이트 실패 (캐시 데이터 사용)`, 'error');
            } else {
                showToast(`${stockLabel(item)} 최신가 업데이트 완료`, 'success');
            }
        }
    } else {
        showToast('네트워크 오류로 업데이트에 실패했습니다.', 'error');
    }
};
