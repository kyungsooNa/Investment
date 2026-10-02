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

function escapeReadinessHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function readinessStatusBadge(status) {
    const labels = {
        pass: ['badge-ok', '통과'],
        fail: ['badge-crit', '실패'],
        insufficient_sample: ['badge-warn', '표본 부족'],
    };
    const [className, label] = labels[status] || ['badge-warn', status || '미확인'];
    return `<span class="${className}">${escapeReadinessHtml(label)}</span>`;
}

function renderStrategyReadiness(data) {
    const summary = data.summary || {};
    const strategies = data.strategies || [];
    const summaryEl = document.getElementById('readiness-summary');
    const body = document.getElementById('strategy-readiness-body');
    const cardBadge = document.getElementById('readiness-card-badge');
    const card = document.getElementById('card-strategy-readiness');

    summaryEl.textContent = `유효 캡처 ${summary.valid_capture_days || 0}일 · 최신 ${summary.latest_valid_capture_date || '-'}`;
    if ((summary.fail_count || 0) > 0) {
        cardBadge.innerHTML = '<span class="badge-crit">실패 전략 있음</span>';
        card.style.borderLeft = '4px solid #e53935';
    } else if ((summary.insufficient_sample_count || 0) > 0 || !strategies.length) {
        cardBadge.innerHTML = '<span class="badge-warn">검증 진행 중</span>';
        card.style.borderLeft = '4px solid #fb8c00';
    } else {
        cardBadge.innerHTML = '<span class="badge-ok">전략 통과</span>';
        card.style.borderLeft = '';
    }

    if (!strategies.length) {
        body.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#888;">표준 저널 데이터 없음</td></tr>';
        return;
    }
    body.innerHTML = strategies.map(item => {
        const cohorts = (item.config_cohorts || []).map(cohort => {
            const hash = cohort.config_hash === '<missing>' ? '미기록' : cohort.config_hash;
            return `${escapeReadinessHtml(hash)} (${cohort.sold_count})`;
        }).join(', ');
        const mixed = item.mixed_config ? ' ⚠️' : '';
        const reasons = (item.blocking_reasons || []).map(escapeReadinessHtml).join(', ') || '-';
        return `
            <tr>
                <td>${escapeReadinessHtml(item.strategy)}</td>
                <td>${readinessStatusBadge(item.status)}</td>
                <td>${item.sold_trades} / ${item.min_trades}</td>
                <td>${item.progress_pct}%</td>
                <td>${cohorts || '-'}${mixed}</td>
                <td>${reasons}</td>
            </tr>
        `;
    }).join('');
}

function renderBackupHealth(data) {
    const badge = document.getElementById('backup-health-badge');
    const card = document.getElementById('card-backup-health');
    const summary = document.getElementById('backup-health-summary');
    const body = document.getElementById('backup-history-body');
    const labels = {
        healthy: ['badge-ok', '정상'],
        stale: ['badge-warn', '오래됨'],
        failed: ['badge-crit', '검증 실패'],
        missing: ['badge-warn', '백업 없음'],
    };
    const [className, label] = labels[data.status] || ['badge-warn', '미확인'];
    badge.innerHTML = `<span class="${className}">${label}</span>`;
    card.style.borderLeft = data.status === 'healthy' ? '' : `4px solid ${data.status === 'failed' ? '#e53935' : '#fb8c00'}`;
    const latest = data.latest || {};
    summary.textContent = latest.backup_id ? `최신 ${latest.backup_id} · ${latest.verified_count || 0}개 검증` : '백업 이력 없음';
    const history = data.history || [];
    if (!history.length) {
        body.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#888;">백업 이력 없음</td></tr>';
        return;
    }
    body.innerHTML = history.map(item => `
        <tr>
            <td>${escapeReadinessHtml(item.backup_id)}</td>
            <td>${escapeReadinessHtml(item.created_at || '-')}</td>
            <td>${item.status === 'passed' ? '<span class="badge-ok">통과</span>' : '<span class="badge-crit">실패</span>'}</td>
            <td>${item.file_count || 0}</td>
            <td>${item.verified_count || 0}</td>
            <td>${escapeReadinessHtml((item.missing_sources || []).join(', ') || '-')}</td>
        </tr>
    `).join('');
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

    // Reconcile 카드
    const rcBadge = document.getElementById('rc-badge');
    const hasRc = (data.active_alerts || []).some(a => a.source === 'RECONCILE');
    if (rcBadge) {
        if (hasRc) {
            rcBadge.innerHTML = '<span class="badge-warn">불일치</span>';
            document.getElementById('card-reconcile').style.borderLeft = '4px solid #fb8c00';
        } else {
            rcBadge.innerHTML = '<span class="badge-ok">정상</span>';
            document.getElementById('card-reconcile').style.borderLeft = '';
        }
    }

    // WebSocket Watchdog 카드
    const wdBadge = document.getElementById('wd-badge');
    const hasWD = (data.active_alerts || []).some(a => a.source === 'WEBSOCKET_WATCHDOG');
    if (wdBadge) {
        if (hasWD) {
            wdBadge.innerHTML = '<span class="badge-warn">재연결 중</span>';
            document.getElementById('card-ws-watchdog').style.borderLeft = '4px solid #fb8c00';
        } else {
            wdBadge.innerHTML = '<span class="badge-ok">정상</span>';
            document.getElementById('card-ws-watchdog').style.borderLeft = '';
        }
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
        const [statusRes, histRes, readinessRes, backupRes] = await Promise.all([
            fetch('/api/operator/status'),
            fetch('/api/operator/alerts?limit=50'),
            fetch('/api/operator/strategy-readiness'),
            fetch('/api/operator/backup-health'),
        ]);
        const status = await statusRes.json();
        const hist = await histRes.json();
        const readiness = await readinessRes.json();
        const backupHealth = await backupRes.json();

        renderSubsystemCards(status);
        renderStrategyReadiness(readiness);
        renderBackupHealth(backupHealth);
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
