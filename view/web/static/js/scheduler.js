/* view/web/static/js/scheduler.js — 전략 스케줄러 */

let schedulerPollingId = null;
let allSchedulerHistory = [];
let currentSchedulerFilter = '전체';
let schedulerEventSource = null;
let schedulerStatusInFlight = false;

/** 페이지가 담당하는 시장. 상단 탭(한국장/미국장)별로 페이지가 분리되어 있다. */
function schedulerMarket() {
    return window.SCHEDULER_MARKET || 'domestic';
}

function schedulerUrl(path) {
    const sep = path.includes('?') ? '&' : '?';
    return `${path}${sep}market=${encodeURIComponent(schedulerMarket())}`;
}

function fetchSchedulerJson(url, options = {}, timeoutMs = 8000) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    return fetch(url, { ...options, signal: controller.signal })
        .then(async (res) => {
            const data = await res.json().catch(() => ({}));
            if (!res.ok) {
                throw new Error(data.detail || `HTTP ${res.status}`);
            }
            return data;
        })
        .finally(() => window.clearTimeout(timer));
}

function syncSchedulerRealtimeState(data) {
    const marketTasks = (data && data.market_tasks) || [];
    const hasRunningScheduler = Boolean(data && data.running);
    const hasRunningMarketTask = marketTasks.some(task => task.running || task.state === 'running');
    if (hasRunningScheduler || hasRunningMarketTask) {
        if (!schedulerPollingId) {
            startSchedulerPolling();
        }
        return;
    }
    stopSchedulerPolling();
}

function refreshSchedulerStatusSoon(delayMs = 500) {
    window.setTimeout(() => {
        loadSchedulerStatus().catch(() => {});
    }, delayMs);
}

async function loadSchedulerStatus() {
    if (schedulerStatusInFlight) return;
    schedulerStatusInFlight = true;
    let statusData = null;
    try {
        // 두 요청을 동시에 시작 (병렬)
        const statusPromise  = fetchSchedulerJson(schedulerUrl('/api/scheduler/status'));
        const historyPromise = fetchSchedulerJson(schedulerUrl('/api/scheduler/history'));

        // status가 도착하는 즉시 렌더링 — history를 기다리지 않음
        statusData = await statusPromise;
        renderSchedulerStatus(statusData);
        syncSchedulerRealtimeState(statusData);

        // history가 도착하면 이력 테이블 렌더링
        try {
            const historyData = await historyPromise;
            allSchedulerHistory = historyData.history || [];
            buildSchedulerHistoryTabs(statusData.strategies || []);
            filterSchedulerHistory(currentSchedulerFilter);
        } catch (historyError) {
            console.warn('[Scheduler] history fetch failed:', historyError);
        }
    } catch (e) {
        const info = document.getElementById('scheduler-info');
        if (info && !statusData) info.innerHTML = '<span>스케줄러 상태 조회 실패</span>';
    } finally {
        schedulerStatusInFlight = false;
    }
}

function renderSchedulerStatus(data) {
    const badge = document.getElementById('scheduler-status-badge');
    const info = document.getElementById('scheduler-info');
    const startBtn = document.getElementById('scheduler-start-btn');
    const stopBtn = document.getElementById('scheduler-stop-btn');

    const schedulers = [data];
    const marketTasks = data.market_tasks || [];
    const hasRunningScheduler = schedulers.some(item => item.running);
    const hasRunningMarketTask = marketTasks.some(task => task.running || task.state === 'running');

    if (hasRunningScheduler) {
        badge.textContent = '실행 중';
        badge.className = 'badge open';
    } else if (hasRunningMarketTask) {
        badge.textContent = '시장 태스크 실행';
        badge.className = 'badge open';
    } else {
        badge.textContent = '정지';
        badge.className = 'badge closed';
    }

    const activeSchedulers = schedulers.filter(item => item.running).length;
    const activeMarketTasks = marketTasks.filter(task => task.running || task.state === 'running').length;
    const canControl = data.can_control_scheduler !== false;
    if (startBtn) startBtn.style.display = canControl ? '' : 'none';
    if (stopBtn) stopBtn.style.display = canControl ? '' : 'none';

    if (data.status_note) {
        const kind = data.scheduler_kind === 'market_tasks' ? '태스크 기반' : '미구성';
        info.textContent = `${kind} | ${data.status_note} | 시장 태스크 ${activeMarketTasks}/${marketTasks.length} 실행`;
    } else {
        info.textContent = `전략 스케줄러 ${activeSchedulers}/${schedulers.length} 실행 | 시장 태스크 ${activeMarketTasks}/${marketTasks.length} 실행`;
    }
    renderSchedulerSections(schedulers);
    renderMarketTasks(marketTasks);
}

function jsString(value) {
    return String(value ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

function renderSchedulerSections(schedulers) {
    const strategiesDiv = document.getElementById('scheduler-strategies');
    if (!strategiesDiv) return;

    if (!schedulers || schedulers.length === 0) {
        strategiesDiv.innerHTML = '<div class="card"><span>등록된 전략 스케줄러가 없습니다.</span></div>';
        return;
    }

    strategiesDiv.innerHTML = schedulers.map(section => renderSchedulerSection(section)).join('');
}

function renderSchedulerSection(section) {
    const market = section.market || 'domestic';
    const marketLabel = section.market_label || market;
    const runningBadge = section.running
        ? '<span class="badge open">실행 중</span>'
        : '<span class="badge closed">정지</span>';
    const modeText = section.has_scheduler
        ? (section.dry_run ? 'dry-run: CSV만 기록' : '실제 주문 실행')
        : (section.scheduler_kind === 'market_tasks' ? '태스크 기반' : '스케줄러 미구성');
    const strategies = section.strategies || [];

    let bodyHtml = '';
    if (!section.has_scheduler) {
        const message = section.status_note
            || '이 시장의 StrategyScheduler가 아직 구성되지 않았습니다.';
        bodyHtml = `<div class="card"><span>${escapeHtml(message)}</span></div>`;
    } else if (strategies.length === 0) {
        bodyHtml = '<div class="card"><span>등록된 전략이 없습니다.</span></div>';
    } else {
        bodyHtml = strategies.map(s => renderStrategyCard(s, market)).join('');
    }

    return `
    <section style="margin-bottom:16px;">
        <div style="display:flex;align-items:center;gap:8px;margin:8px 0;">
            <h4 style="margin:0;color:var(--text-primary);">${escapeHtml(marketLabel)}</h4>
            ${runningBadge}
            <span class="badge paper">${escapeHtml(modeText)}</span>
        </div>
        ${bodyHtml}
    </section>`;
}

function renderStrategyCard(s, market) {
        const displayName = s.display_name || s.name;
        const enabledBadge = s.enabled
            ? '<span class="badge open">활성</span>'
            : '<span class="badge closed">비활성</span>';
        const dayTradeBadge = s.force_exit_on_close
            ? '<span class="badge" style="background:#f59e0b;color:#fff;font-size:0.8em;">당일청산</span>'
            : '';
        const adminAllowed = typeof window.hasRequiredRole !== 'function'
            || window.hasRequiredRole('admin');
        const positionBadge = `<span class="badge ${s.current_holds >= s.max_positions ? 'closed' : 'paper'}" style="cursor:${adminAllowed ? 'pointer' : 'default'};" ${adminAllowed ? `onclick="updateMaxPositions('${jsString(s.name)}', ${s.max_positions}, '${jsString(market)}')"` : ''} title="${adminAllowed ? '클릭하여 최대 포지션 수 변경' : 'admin 권한 필요'}">포지션 ${s.current_holds}/${s.max_positions} ✏️</span>`;
        const toggleBtn = s.enabled
            ? `<button class="btn btn-sell" data-required-role="admin" ${adminAllowed ? '' : 'disabled'} style="padding:4px 12px;font-size:0.85em;" onclick="stopStrategy('${jsString(s.name)}', '${jsString(market)}')">정지</button>`
            : `<button class="btn btn-buy" data-required-role="admin" ${adminAllowed ? '' : 'disabled'} style="padding:4px 12px;font-size:0.85em;" onclick="startStrategy('${jsString(s.name)}', '${jsString(market)}')">시작</button>`;
        // 보유 종목 리스트 렌더링
        let holdingsHtml = '';
        if (s.holdings && s.holdings.length > 0) {
            const list = s.holdings.map(h => 
                `<a href="/stock?code=${h.code}" target="_blank" class="stock-link" style="font-size:0.9em; padding:2px 6px; background:var(--bg-secondary); border-radius:4px;">${h.name || h.code}</a>`
            ).join(' ');
            holdingsHtml = `<div style="margin-top:8px; display:flex; flex-wrap:wrap; gap:6px; align-items:center;">
                <span style="font-size:0.85em; color:var(--text-secondary);">보유:</span> ${list}
            </div>`;
        } else {
            holdingsHtml = `<div style="margin-top:8px; font-size:0.85em; color:var(--text-secondary);">보유 종목 없음</div>`;
        }

        return `
        <div class="card" style="margin-bottom:8px;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <div style="display:flex;align-items:center;gap:8px;">
                    <h3 style="margin:0;color:var(--text-primary);">${displayName}</h3>
                    ${enabledBadge}
                    ${dayTradeBadge}
                </div>
                <div style="display:flex;align-items:center;gap:8px;">
                    ${positionBadge}
                    ${toggleBtn}
                </div>
            </div>
            ${holdingsHtml}
            <div style="margin-top:8px;color:var(--text-secondary);font-size:0.9em;">
                실행 주기: ${s.interval_minutes}분 | 마지막 실행: ${s.last_run || '-'}
            </div>
        </div>`;
}

function renderMarketTasks(tasks) {
    const marketTasksDiv = document.getElementById('scheduler-market-tasks');
    if (!marketTasksDiv) return;

    if (!tasks || tasks.length === 0) {
        marketTasksDiv.innerHTML = '<div class="card"><span>등록된 시장별 백그라운드 전략이 없습니다.</span></div>';
        return;
    }

    marketTasksDiv.innerHTML = tasks.map(task => {
        const running = task.running || task.state === 'running';
        const stateBadge = running
            ? '<span class="badge open">실행 중</span>'
            : '<span class="badge closed">대기</span>';
        const modeBadge = task.live_trading
            ? '<span class="badge closed">실주문</span>'
            : '<span class="badge paper">관찰/Paper</span>';
        const progress = task.progress || {};
        const watchCount = progress.watch_count ?? progress.watch ?? progress.candidates;
        const progressText = watchCount != null
            ? `감시 ${Number(watchCount).toLocaleString()}개`
            : `상태 ${escapeHtml(task.state || '-')}`;
        // 태스크는 폴링 사이에 대부분 idle 로 돌아온다 — 상태값만으로는 장 마감·휴장·
        // 감시목록 0개가 전부 똑같이 보이므로, 마지막 패스가 무엇을 했는지 함께 적는다.
        const phaseDetail = progress.phase_detail
            ? ` | ${escapeHtml(progress.phase_detail)}`
            : '';

        return `
        <div class="card" style="margin-bottom:8px;">
            <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;">
                <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                    <h3 style="margin:0;color:var(--text-primary);">${escapeHtml(task.display_name || task.name)}</h3>
                    <span class="badge paper">${escapeHtml(task.market_label || task.market || '시장')}</span>
                    ${stateBadge}
                    ${modeBadge}
                </div>
                <span class="badge paper">${escapeHtml(task.mode || 'background')}</span>
            </div>
            <div style="margin-top:8px;color:var(--text-secondary);font-size:0.9em;">
                ${progressText} | 실주문 자동매매: ${task.live_trading ? '허용' : '잠금'}${phaseDetail}
            </div>
        </div>`;
    }).join('');
}

async function startScheduler() {
    try {
        const data = await fetchSchedulerJson(schedulerUrl('/api/scheduler/start'), { method: 'POST' });
        if (data.success) {
            refreshSchedulerStatusSoon();
        }
    } catch (e) {
        alert(`스케줄러 시작 실패: ${e.message}`);
    }
}

async function stopScheduler() {
    try {
        const data = await fetchSchedulerJson(schedulerUrl('/api/scheduler/stop'), { method: 'POST' });
        if (data.success) {
            refreshSchedulerStatusSoon();
        }
    } catch (e) {
        alert(`스케줄러 정지 실패: ${e.message}`);
    }
}

async function startStrategy(name, market = schedulerMarket()) {
    try {
        const data = await fetchSchedulerJson(`/api/scheduler/strategy/${encodeURIComponent(name)}/start?market=${encodeURIComponent(market)}`, { method: 'POST' });
        if (data.success) {
            refreshSchedulerStatusSoon();
        }
    } catch (e) {
        alert(`전략 시작 실패: ${e.message}`);
    }
}

async function stopStrategy(name, market = schedulerMarket()) {
    try {
        const data = await fetchSchedulerJson(`/api/scheduler/strategy/${encodeURIComponent(name)}/stop?market=${encodeURIComponent(market)}`, { method: 'POST' });
        if (data.success) {
            refreshSchedulerStatusSoon();
        }
    } catch (e) {
        alert(`전략 정지 실패: ${e.message}`);
    }
}

async function updateMaxPositions(name, currentMax, market = schedulerMarket()) {
    const newVal = prompt(`'${name}' 전략의 최대 보유 포지션 수를 입력하세요:`, currentMax);
    if (newVal === null) return; // Cancelled
    
    const parsed = parseInt(newVal, 10);
    if (isNaN(parsed) || parsed < 1) {
        alert('1 이상의 올바른 숫자를 입력하세요.');
        return;
    }

    try {
        const data = await fetchSchedulerJson(`/api/scheduler/strategy/${encodeURIComponent(name)}/max-positions?market=${encodeURIComponent(market)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ max_positions: parsed })
        });
        if (data.success) {
            refreshSchedulerStatusSoon();
        }
    } catch (e) {
        alert(`포지션 수 변경 실패: ${e.message}`);
    }
}


function buildSchedulerHistoryTabs(strategies) {
    const tabContainer = document.getElementById('scheduler-history-tabs');
    if (!tabContainer) return;

    const items = [
        { key: '전체', label: '전체' },
        ...strategies.map(s => ({ key: s.name, label: s.display_name || s.name })),
    ];
    tabContainer.innerHTML = items.map(item =>
        `<button class="sub-tab-btn${item.key === currentSchedulerFilter ? ' active' : ''}" onclick="filterSchedulerHistory('${item.key}', this)">${item.label}</button>`
    ).join('');
}

function filterSchedulerHistory(strategyName, btnElement) {
    currentSchedulerFilter = strategyName;

    const tabContainer = document.getElementById('scheduler-history-tabs');
    if (tabContainer) {
        tabContainer.querySelectorAll('.sub-tab-btn').forEach(b => b.classList.remove('active'));
        if (btnElement) {
            btnElement.classList.add('active');
        } else {
            const match = Array.from(tabContainer.querySelectorAll('.sub-tab-btn')).find(
                b => b.getAttribute('onclick') && b.getAttribute('onclick').includes(`'${strategyName}'`)
            );
            if (match) match.classList.add('active');
        }
    }

    const filtered = strategyName === '전체'
        ? allSchedulerHistory
        : allSchedulerHistory.filter(h => h.strategy_name === strategyName);
    if (window.Paginator) window.Paginator.reset('scheduler-history');
    renderSchedulerHistory(filtered);
}

function renderSchedulerHistory(history) {
    const tbody = document.getElementById('scheduler-history-body');
    if (!tbody) return;

    ensureTableInCard(tbody.closest('table'));

    if (!history || history.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:15px;">실행 이력이 없습니다.</td></tr>';
        const ctrl = document.getElementById('scheduler-history-pagination');
        if (ctrl) ctrl.innerHTML = '';
        return;
    }

    const pageData = window.Paginator
        ? window.Paginator.paginate('scheduler-history', history, 'scheduler-history-pagination',
            () => renderSchedulerHistory(history))
        : history;
    tbody.innerHTML = pageData.map(h => {
        const isSizingSkip = h.action === 'BUY' && Number(h.qty || 0) <= 0 && String(h.reason || '').startsWith('sizing_skip:');
        const actionClass = isSizingSkip ? '' : (h.action === 'BUY' ? 'text-red' : 'text-blue');
        const actionLabel = isSizingSkip ? '스킵' : (h.action === 'BUY' ? '매수' : '매도');
        const statusIcon = h.api_success ? '' : ' <span title="API 주문 실패" style="color:orange;">⚠</span>';

        let returnRateHtml = '<td>-</td>';
        if (h.action === 'SELL' && h.return_rate != null) {
            const ror = parseFloat(h.return_rate);
            const rorClass = ror > 0 ? 'text-red' : (ror < 0 ? 'text-blue' : '');
            const sign = ror > 0 ? '+' : '';
            returnRateHtml = `<td class="${rorClass}"><strong>${sign}${ror.toFixed(2)}%</strong></td>`;
        }

        return `<tr>
            <td style="white-space:nowrap;">${h.timestamp}</td>
            <td>${h.strategy_name}</td>
            <td><a href="/stock?code=${h.code}" target="_blank" class="stock-link">${h.name}(${h.code})</a></td>
            <td class="${actionClass}"><strong>${actionLabel}</strong>${statusIcon}</td>
            <td>${Number(h.price).toLocaleString()}</td>
            <td>${h.qty ?? 1}</td>
            ${returnRateHtml}
            <td style="font-size:0.85em;">${h.reason}</td>
        </tr>`;
    }).join('');
}

function startSchedulerPolling() {
    stopSchedulerPolling();
    schedulerPollingId = setInterval(loadSchedulerStatus, 10000);
    disconnectSchedulerSSE();
}

function stopSchedulerPolling() {
    if (schedulerPollingId) {
        clearInterval(schedulerPollingId);
        schedulerPollingId = null;
    }
    disconnectSchedulerSSE();
}

function connectSchedulerSSE() {
    if (schedulerEventSource) return;
    schedulerEventSource = new EventSource(schedulerUrl('/api/scheduler/stream'));
    schedulerEventSource.onmessage = function(event) {
        try {
            const signal = JSON.parse(event.data);
            allSchedulerHistory.unshift(signal);
            if (allSchedulerHistory.length > 200) {
                allSchedulerHistory = allSchedulerHistory.slice(0, 200);
            }
            filterSchedulerHistory(currentSchedulerFilter);

            fetchSchedulerJson(schedulerUrl('/api/scheduler/status'))
                .then(data => {
                    renderSchedulerStatus(data);
                    syncSchedulerRealtimeState(data);
                })
                .catch(() => {});
        } catch (e) {
            console.error('[Scheduler SSE] parse error:', e);
        }
    };
    schedulerEventSource.onerror = function() {
        console.warn('[Scheduler SSE] connection error, will auto-reconnect');
    };
}

function disconnectSchedulerSSE() {
    if (schedulerEventSource) {
        schedulerEventSource.close();
        schedulerEventSource = null;
    }
}
