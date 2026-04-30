/* view/web/static/js/scheduler.js — 전략 스케줄러 */

let schedulerPollingId = null;
let allSchedulerHistory = [];
let currentSchedulerFilter = '전체';
let schedulerEventSource = null;

function syncSchedulerRealtimeState(data) {
    if (data && data.running) {
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
    try {
        // 두 요청을 동시에 시작 (병렬)
        const statusPromise  = fetch('/api/scheduler/status');
        const historyPromise = fetch('/api/scheduler/history');

        // status가 도착하는 즉시 렌더링 — history를 기다리지 않음
        const statusData = await statusPromise.then(r => r.json());
        renderSchedulerStatus(statusData);
        syncSchedulerRealtimeState(statusData);

        // history가 도착하면 이력 테이블 렌더링
        const historyData = await historyPromise.then(r => r.json());
        allSchedulerHistory = historyData.history || [];
        buildSchedulerHistoryTabs(statusData.strategies || []);
        filterSchedulerHistory(currentSchedulerFilter);
    } catch (e) {
        const info = document.getElementById('scheduler-info');
        if (info) info.innerHTML = '<span>스케줄러 상태 조회 실패</span>';
    }
}

function renderSchedulerStatus(data) {
    const badge = document.getElementById('scheduler-status-badge');
    const info = document.getElementById('scheduler-info');
    const strategiesDiv = document.getElementById('scheduler-strategies');

    if (data.running) {
        badge.textContent = '실행 중';
        badge.className = 'badge open';
    } else {
        badge.textContent = '정지';
        badge.className = 'badge closed';
    }

    const dryLabel = data.dry_run ? 'dry-run: CSV만 기록' : '실제 주문 실행';
    info.textContent = dryLabel;

    if (!data.strategies || data.strategies.length === 0) {
        strategiesDiv.innerHTML = '<div class="card"><span>등록된 전략이 없습니다.</span></div>';
        return;
    }

    strategiesDiv.innerHTML = data.strategies.map(s => {
        const enabledBadge = s.enabled
            ? '<span class="badge open">활성</span>'
            : '<span class="badge closed">비활성</span>';
        const positionBadge = `<span class="badge ${s.current_holds >= s.max_positions ? 'closed' : 'paper'}" style="cursor:pointer;" onclick="updateMaxPositions('${s.name}', ${s.max_positions})" title="클릭하여 최대 포지션 수 변경">포지션 ${s.current_holds}/${s.max_positions} ✏️</span>`;
        const toggleBtn = s.enabled
            ? `<button class="btn btn-sell" style="padding:4px 12px;font-size:0.85em;" onclick="stopStrategy('${s.name}')">정지</button>`
            : `<button class="btn btn-buy" style="padding:4px 12px;font-size:0.85em;" onclick="startStrategy('${s.name}')">시작</button>`;
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
                    <h3 style="margin:0;color:var(--text-primary);">${s.name}</h3>
                    ${enabledBadge}
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
    }).join('');
}

async function startScheduler() {
    try {
        const res = await fetch('/api/scheduler/start', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            refreshSchedulerStatusSoon();
        }
    } catch (e) {
        alert('스케줄러 시작 실패');
    }
}

async function stopScheduler() {
    try {
        const res = await fetch('/api/scheduler/stop', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            refreshSchedulerStatusSoon();
        }
    } catch (e) {
        alert('스케줄러 정지 실패');
    }
}

async function startStrategy(name) {
    try {
        const res = await fetch(`/api/scheduler/strategy/${encodeURIComponent(name)}/start`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            refreshSchedulerStatusSoon();
        } else {
            alert(data.detail || '전략 시작 실패');
        }
    } catch (e) {
        alert('전략 시작 실패');
    }
}

async function stopStrategy(name) {
    try {
        const res = await fetch(`/api/scheduler/strategy/${encodeURIComponent(name)}/stop`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            refreshSchedulerStatusSoon();
        } else {
            alert(data.detail || '전략 정지 실패');
        }
    } catch (e) {
        alert('전략 정지 실패');
    }
}

async function updateMaxPositions(name, currentMax) {
    const newVal = prompt(`'${name}' 전략의 최대 보유 포지션 수를 입력하세요:`, currentMax);
    if (newVal === null) return; // Cancelled
    
    const parsed = parseInt(newVal, 10);
    if (isNaN(parsed) || parsed < 1) {
        alert('1 이상의 올바른 숫자를 입력하세요.');
        return;
    }

    try {
        const res = await fetch(`/api/scheduler/strategy/${encodeURIComponent(name)}/max-positions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ max_positions: parsed })
        });
        const data = await res.json();
        if (data.success) {
            refreshSchedulerStatusSoon();
        } else {
            alert(data.detail || '포지션 수 변경 실패');
        }
    } catch (e) {
        alert('포지션 수 변경 중 오류가 발생했습니다.');
    }
}


function buildSchedulerHistoryTabs(strategies) {
    const tabContainer = document.getElementById('scheduler-history-tabs');
    if (!tabContainer) return;

    const names = ['전체', ...strategies.map(s => s.name)];
    tabContainer.innerHTML = names.map(name =>
        `<button class="sub-tab-btn${name === currentSchedulerFilter ? ' active' : ''}" onclick="filterSchedulerHistory('${name}', this)">${name}</button>`
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
            const match = Array.from(tabContainer.querySelectorAll('.sub-tab-btn')).find(b => b.textContent === strategyName);
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
    schedulerEventSource = new EventSource('/api/scheduler/stream');
    schedulerEventSource.onmessage = function(event) {
        try {
            const signal = JSON.parse(event.data);
            allSchedulerHistory.unshift(signal);
            if (allSchedulerHistory.length > 200) {
                allSchedulerHistory = allSchedulerHistory.slice(0, 200);
            }
            filterSchedulerHistory(currentSchedulerFilter);

            fetch('/api/scheduler/status')
                .then(res => res.json())
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
