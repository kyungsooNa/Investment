/* 운영자 대시보드 — Active 차단 + 전이 이력 */

const POLL_INTERVAL_MS = 5000;

function severityBadge(sev) {
    if (!sev) return '';
    const s = sev.toLowerCase();
    if (s === 'critical') return `<span class="badge-crit">${sev}</span>`;
    if (s === 'error')    return `<span class="badge-warn">${sev}</span>`;
    if (s === 'block')    return `<span class="badge-block">${sev}</span>`;
    return `<span class="badge-ok">${sev}</span>`;
}

function transitionLabel(t) {
    if (!t) return '';
    const cl = { NEW: 'transition-new', ESCALATED: 'transition-escalated', RESOLVED: 'transition-resolved' };
    return `<span class="${cl[t] || ''}">${t}</span>`;
}

function fmtTime(iso) {
    if (!iso) return '-';
    try {
        const d = new Date(iso);
        return d.toLocaleTimeString('ko-KR', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch { return iso; }
}

function shortKey(key) {
    if (!key) return '';
    return key.length > 40 ? key.slice(0, 38) + '…' : key;
}

async function resolveAlert(dedupKey) {
    if (!confirm(`차단 키 "${dedupKey}"를 수동 해제할까요?`)) return;
    try {
        const encoded = encodeURIComponent(dedupKey);
        const r = await fetch(`/api/operator/alerts/${encoded}/resolve`, { method: 'POST' });
        const data = await r.json();
        if (data.resolved) {
            showToast('해제 완료: ' + dedupKey, 'success');
            loadStatus();
        } else {
            showToast('해제 실패 (active에 없음)', 'warning');
        }
    } catch (e) {
        showToast('해제 요청 오류: ' + e, 'error');
    }
}

function renderActiveAlerts(alerts) {
    const tbody = document.getElementById('active-alerts-body');
    const badge = document.getElementById('active-count-badge');
    badge.textContent = alerts.length;
    badge.style.background = alerts.length > 0 ? '#e53935' : '#888';

    if (!alerts.length) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#888;">차단 없음 ✅</td></tr>';
        return;
    }
    tbody.innerHTML = alerts.map(a => `
        <tr>
            <td>${a.source || '-'}</td>
            <td title="${a.dedup_key || ''}">${shortKey(a.dedup_key)}</td>
            <td>${severityBadge(a.severity)}</td>
            <td>${a.title || '-'}</td>
            <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${a.message || ''}">${a.message || '-'}</td>
            <td>${fmtTime(a.first_seen)}</td>
            <td>${fmtTime(a.last_seen)}</td>
            <td><button class="btn btn-sm" style="background:#e53935;color:#fff;" onclick="resolveAlert('${(a.dedup_key||'').replace(/'/g,"\\'")}')">해제</button></td>
        </tr>
    `).join('');
}

function renderSubsystemCards(data) {
    const ks = data.kill_switch || {};
    const ksBadge = document.getElementById('ks-badge');
    const rgBadge = document.getElementById('rg-badge');

    // Kill Switch 카드
    if (ks.is_tripped) {
        ksBadge.innerHTML = '<span class="badge-crit">TRIPPED</span>';
        document.getElementById('card-kill-switch').style.borderLeft = '4px solid #e53935';
    } else {
        ksBadge.innerHTML = '<span class="badge-ok">정상</span>';
        document.getElementById('card-kill-switch').style.borderLeft = '';
    }

    // Risk Gate 카드 — active 중 RISK_GATE 소스 있으면 경고
    const hasRg = (data.active_alerts || []).some(a => a.source === 'RISK_GATE');
    if (hasRg) {
        rgBadge.innerHTML = '<span class="badge-warn">차단 중</span>';
        document.getElementById('card-risk-gate').style.borderLeft = '4px solid #fb8c00';
    } else {
        rgBadge.innerHTML = '<span class="badge-ok">정상</span>';
        document.getElementById('card-risk-gate').style.borderLeft = '';
    }
}

function renderHistory(alerts) {
    const tbody = document.getElementById('history-body');
    if (!alerts.length) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#888;">이력 없음</td></tr>';
        return;
    }
    tbody.innerHTML = alerts.slice(0, 50).map(h => `
        <tr>
            <td>${fmtTime(h.timestamp)}</td>
            <td>${transitionLabel(h.transition)}</td>
            <td>${h.source || '-'}</td>
            <td title="${h.dedup_key || ''}">${shortKey(h.dedup_key)}</td>
            <td>${severityBadge(h.severity)}</td>
            <td>${h.title || '-'}</td>
            <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${h.message || ''}">${h.message || '-'}</td>
        </tr>
    `).join('');
}

async function loadStatus() {
    try {
        const [statusRes, histRes] = await Promise.all([
            fetch('/api/operator/status'),
            fetch('/api/operator/alerts?limit=50'),
        ]);
        const status = await statusRes.json();
        const hist = await histRes.json();

        renderSubsystemCards(status);
        renderActiveAlerts(status.active_alerts || []);
        renderHistory(hist.alerts || []);
    } catch (e) {
        console.error('[OperatorDashboard] 조회 실패:', e);
    }
}

// SSE 구독 — transition 메타가 있는 이벤트는 토스트 표시
function subscribeSSE() {
    if (typeof EventSource === 'undefined') return;
    const es = new EventSource('/api/notifications/stream');
    es.onmessage = (ev) => {
        try {
            const data = JSON.parse(ev.data);
            const meta = data.metadata || {};
            if (meta.transition) {
                const color = { NEW: 'error', ESCALATED: 'warning', RESOLVED: 'success' }[meta.transition] || 'info';
                showToast(`[${meta.transition}] ${data.title}: ${data.message}`, color);
                loadStatus();
            }
        } catch {}
    };
    es.onerror = () => {};
}

function showToast(msg, type) {
    if (typeof window.showNotification === 'function') {
        window.showNotification(msg, type);
        return;
    }
    console.info('[Toast]', type, msg);
}

// 초기 로드 + 폴링
loadStatus();
subscribeSSE();
setInterval(loadStatus, POLL_INTERVAL_MS);
