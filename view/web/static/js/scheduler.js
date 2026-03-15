/* view/web/static/js/scheduler.js — 전략 스케줄러 */

let schedulerPollingId = null;
let allSchedulerHistory = [];
let currentSchedulerFilter = '전체';
let schedulerEventSource = null;

async function loadSchedulerStatus() {
    try {
        const [statusRes, historyRes] = await Promise.all([
            fetch('/api/scheduler/status'),
            fetch('/api/scheduler/history'),
        ]);
        const statusData = await statusRes.json();
        const historyData = await historyRes.json();
        renderSchedulerStatus(statusData);

        allSchedulerHistory = historyData.history || [];
        buildSchedulerHistoryTabs(statusData.strategies || []);
        filterSchedulerHistory(currentSchedulerFilter);

        if (statusData.running && !schedulerPollingId) {
            startSchedulerPolling();
        }
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
        const positionBadge = `<span class="badge ${s.current_holds >= s.max_positions ? 'closed' : 'paper'}">포지션 ${s.current_holds}/${s.max_positions}</span>`;
        const toggleBtn = s.enabled
            ? `<button class="btn btn-sell" style="padding:4px 12px;font-size:0.85em;" onclick="stopStrategy('${s.name}')">정지</button>`
            : `<button class="btn btn-buy" style="padding:4px 12px;font-size:0.85em;" onclick="startStrategy('${s.name}')">시작</button>`;
        const poolABtn = s.name === '오닐스퀴즈돌파'
            ? `<button class="btn" style="padding:4px 12px;font-size:0.85em;background:var(--accent);" onclick="generatePoolA(this)">Pool A 생성</button>`
            : '';
        return `
        <div class="card" style="margin-bottom:8px;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <div style="display:flex;align-items:center;gap:8px;">
                    <h3 style="margin:0;color:var(--text-primary);">${s.name}</h3>
                    ${enabledBadge}
                </div>
                <div style="display:flex;align-items:center;gap:8px;">
                    ${poolABtn}
                    ${positionBadge}
                    ${toggleBtn}
                </div>
            </div>
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
            renderSchedulerStatus(data.status);
            startSchedulerPolling();
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
            renderSchedulerStatus(data.status);
            stopSchedulerPolling();
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
            renderSchedulerStatus(data.status);
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
            renderSchedulerStatus(data.status);
        } else {
            alert(data.detail || '전략 정지 실패');
        }
    } catch (e) {
        alert('전략 정지 실패');
    }
}

async function generatePoolA(btnEl) {
    const origText = btnEl.textContent;
    btnEl.disabled = true;
    btnEl.textContent = 'Pool A 생성 중...';
    try {
        const res = await fetch('/api/scheduler/strategy/%EC%98%A4%EB%8B%90%EC%8A%A4%ED%80%B4%EC%A6%88%EB%8F%8C%ED%8C%8C/generate-pool-a', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            const r = data.result;
            alert(`Pool A 생성 완료!\n\n스캔: ${r.scanned}종목\n1차 통과: ${r.first_filter_passed}종목\n2차 통과: ${r.second_filter_passed}종목\n\nKOSPI: ${r.kospi_count}종목\nKOSDAQ: ${r.kosdaq_count}종목\n시총 범위: ${r.market_cap_filter}`);
        } else {
            alert(data.detail || 'Pool A 생성 실패');
        }
    } catch (e) {
        alert('Pool A 생성 실패: ' + e.message);
    } finally {
        btnEl.disabled = false;
        btnEl.textContent = origText;
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
    renderSchedulerHistory(filtered);
}

function renderSchedulerHistory(history) {
    const tbody = document.getElementById('scheduler-history-body');
    if (!tbody) return;

    ensureTableInCard(tbody.closest('table'));

    if (!history || history.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;padding:15px;">실행 이력이 없습니다.</td></tr>';
        return;
    }

    tbody.innerHTML = history.map(h => {
        const actionClass = h.action === 'BUY' ? 'text-red' : 'text-blue';
        const actionLabel = h.action === 'BUY' ? '매수' : '매도';
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
            ${returnRateHtml}
            <td style="font-size:0.85em;">${h.reason}</td>
        </tr>`;
    }).join('');
}

function startSchedulerPolling() {
    stopSchedulerPolling();
    schedulerPollingId = setInterval(loadSchedulerStatus, 10000);
    connectSchedulerSSE();
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
                .then(data => renderSchedulerStatus(data))
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
