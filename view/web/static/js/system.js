// ── 캐시 상태 모니터링 ───────────────────────────────────────

const _cacheExpanded = { price: false, ohlcv: false };

function toggleCacheDetails(type) {
    _cacheExpanded[type] = !_cacheExpanded[type];
    const container = document.getElementById(`${type}-cache-details`);
    const btn = document.getElementById(`toggle-${type}-details-btn`);
    if (_cacheExpanded[type]) {
        container.style.display = 'block';
        btn.textContent = '상세 정보 닫기 ▲';
        updateCacheStatus();
    } else {
        container.style.display = 'none';
        btn.textContent = '상세 정보 보기 ▼';
    }
}

function formatTimestamp(ts) {
    if (!ts) return '-';
    const d = new Date(ts * 1000);
    return d.toLocaleString('ko-KR', { hour12: false });
}

function renderCacheSummary(type, stats, capacity) {
    const s = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    s(`${type}-cache-total`,  (stats.total_requests || 0).toLocaleString());
    s(`${type}-cache-hits`,   (stats.hits  || 0).toLocaleString());
    s(`${type}-cache-misses`, (stats.misses || 0).toLocaleString());
    s(`${type}-cache-size`,   `${stats.current_size || 0} / ${capacity}`);
    const rateEl = document.getElementById(`${type}-cache-rate`);
    if (rateEl) {
        const rate = stats.hit_rate || 0;
        rateEl.textContent = rate.toFixed(2);
        rateEl.style.color = rate >= 90 ? 'var(--success-color,#4CAF50)' : rate >= 50 ? 'orange' : 'var(--danger-color,#f44336)';
    }
    if (type === 'price') {
        const streamEl = document.getElementById('price-cache-streaming');
        if (streamEl) {
            const cnt = stats.streaming_count ?? 0;
            streamEl.textContent = cnt;
            streamEl.style.color = cnt > 0 ? 'var(--success-color,#4CAF50)' : '#888';
        }
    }
}

function renderCallersSection(innerId, callers) {
    if (!callers || !Object.keys(callers).length) return;
    const wrapId = `${innerId}-callers-wrap`;
    let section = document.getElementById(wrapId);
    if (!section) {
        const inner = document.getElementById(innerId);
        if (!inner) return;
        section = document.createElement('div');
        section.id = wrapId;
        section.innerHTML = `
            <h3 style="margin-top:10px;">🔍 서비스/전략별 호출 통계</h3>
            <table class="data-table" style="margin-bottom:25px;">
                <thead><tr>
                    <th>호출자 (Caller)</th><th>Hits</th><th>Misses</th>
                    <th>적중률 (%)</th><th>조회 유형</th><th>주요 대상 종목</th>
                </tr></thead>
                <tbody id="${wrapId}-body"></tbody>
            </table>
            <h3 style="margin-top:20px;">📋 종목별 상세 조회 통계</h3>
        `;
        inner.insertBefore(section, inner.firstChild);
    }
    const tbody = document.getElementById(`${wrapId}-body`);
    if (!tbody) return;
    const arr = Object.entries(callers)
        .map(([caller, data]) => ({ caller, ...data, total: data.hits + data.misses }))
        .sort((a, b) => b.total - a.total);
    tbody.innerHTML = arr.map(item => {
        const total = item.hits + item.misses;
        const hitRate = total > 0 ? ((item.hits / total) * 100).toFixed(2) : '0.00';
        const rateColor = hitRate >= 90 ? 'var(--success-color,#4CAF50)' : hitRate >= 50 ? 'orange' : 'var(--danger-color,#f44336)';
        let itemsStr = '-';
        if (item.items && Object.keys(item.items).length) {
            itemsStr = Object.entries(item.items)
                .map(([k, v]) => `<span style="display:inline-block;margin-right:4px;margin-bottom:2px;background:var(--bg-color,#f0f0f0);color:var(--text-color,#333);border:1px solid #ccc;padding:2px 6px;border-radius:10px;font-size:0.85em;">${k}: ${v.toLocaleString()}</span>`)
                .join('');
        }
        let keysStr = '-', keysTitle = '';
        if (item.keys && Object.keys(item.keys).length) {
            keysStr = Object.entries(item.keys).map(([k, v]) => `${k} <span style="color:#888;font-size:0.9em;">(${v})</span>`).join(', ');
            keysTitle = Object.entries(item.keys).map(([k, v]) => `${k}(${v})`).join(', ');
        }
        return `<tr>
            <td style="font-weight:bold;color:var(--text-color);">${item.caller}</td>
            <td>${item.hits.toLocaleString()}</td>
            <td>${item.misses.toLocaleString()}</td>
            <td style="color:${rateColor};font-weight:bold;">${hitRate}</td>
            <td>${itemsStr}</td>
            <td style="font-size:0.9em;max-width:300px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="${keysTitle}">${keysStr}</td>
        </tr>`;
    }).join('');
}

function renderPriceCacheDetails(stats) {
    renderCallersSection('price-details-inner', stats.callers);
    const tbody = document.getElementById('price-items-body');
    if (!tbody || !stats.items) return;
    tbody.innerHTML = stats.items.map(item => {
        const displayName = item.name && item.name !== item.code ? `${item.name}(${item.code})` : (item.code || '-');
        const streamBadge = item.is_streaming
            ? `<span style="background:var(--success-color,#4CAF50);color:#fff;padding:1px 7px;border-radius:8px;font-size:0.82em;font-weight:bold;">실시간</span>`
            : `<span style="color:#bbb;font-size:0.82em;">-</span>`;
        return `<tr>
            <td style="font-weight:bold;color:var(--accent);">
                <a href="/stock?code=${item.code}" target="_blank" class="stock-link">${displayName}</a>
            </td>
            <td>${(item.hit_count || 0).toLocaleString()}</td>
            <td>${item.has_current_price ? 'O' : 'X'}</td>
            <td>${streamBadge}</td>
            <td>${formatTimestamp(item.price_updated_at || item.last_updated)}</td>
        </tr>`;
    }).join('');
}

function formatOhlcvDate(d) {
    // "YYYYMMDD" → "YY/MM/DD"
    if (!d || d.length !== 8) return d || '-';
    return `${d.slice(2, 4)}/${d.slice(4, 6)}/${d.slice(6, 8)}`;
}

function renderOhlcvCacheDetails(stats) {
    renderCallersSection('ohlcv-details-inner', stats.callers);
    const tbody = document.getElementById('ohlcv-items-body');
    if (!tbody || !stats.items) return;
    tbody.innerHTML = stats.items.map(item => {
        const displayName = item.name && item.name !== item.code ? `${item.name}(${item.code})` : (item.code || '-');
        const completeBadge = item.historical_complete
            ? `<span style="color:var(--success-color,#4CAF50);">완전</span>`
            : `<span style="color:orange;">미완</span>`;
        const dates = (item.recent_dates || []);
        const datesHtml = dates.length
            ? dates.map((d, i) =>
                `<span style="display:inline-block;margin-right:3px;margin-bottom:2px;background:${i === 0 ? 'var(--primary-color,#2196F3)' : 'var(--bg-color,#f0f0f0)'};color:${i === 0 ? '#fff' : 'var(--text-color,#333)'};border:1px solid #ccc;padding:1px 5px;border-radius:8px;font-size:0.8em;">${formatOhlcvDate(d)}</span>`
              ).join('')
            : '<span style="color:#aaa;">-</span>';
        return `<tr>
            <td style="font-weight:bold;color:var(--accent);">
                <a href="/stock?code=${item.code}" target="_blank" class="stock-link">${displayName}</a>
            </td>
            <td>${item.freq || 0}</td>
            <td>${item.ohlcv_count || 0}일</td>
            <td>${item.has_today_candle ? 'O' : 'X'}</td>
            <td>${completeBadge}</td>
            <td style="white-space:nowrap;">${datesHtml}</td>
        </tr>`;
    }).join('');
}

async function updateCacheStatus() {
    try {
        const needExpand = _cacheExpanded.price || _cacheExpanded.ohlcv;
        const url = needExpand ? '/api/cache/status?expand=true' : '/api/cache/status';
        const response = await fetch(url);
        if (!response.ok) throw new Error('네트워크 응답이 정상이 아닙니다.');
        const result = await response.json();
        if (!result.success || !result.data) return;
        const stats = result.data;
        renderCacheSummary('price', stats.price_cache || {}, 3000);
        renderCacheSummary('ohlcv', stats.ohlcv_cache || {}, 500);
        if (_cacheExpanded.price)  renderPriceCacheDetails(stats.price_cache || {});
        if (_cacheExpanded.ohlcv)  renderOhlcvCacheDetails(stats.ohlcv_cache || {});
    } catch (error) {
        console.error('캐시 상태를 가져오는 중 오류 발생:', error);
    }
}

document.addEventListener('DOMContentLoaded', updateCacheStatus);
setInterval(updateCacheStatus, 15000);  // 캐시 상태는 15초마다 갱신 (응답이 크므로 이벤트 루프 부하 분산)

// ── 백그라운드 태스크 모니터링 ──────────────────────────────

const STATE_BADGE = {
    running:   { label: 'RUNNING',   color: 'var(--success-color, #4CAF50)' },
    suspended: { label: 'SUSPENDED', color: 'orange' },
    stopped:   { label: 'STOPPED',   color: 'var(--danger-color, #f44336)' },
    idle:      { label: 'IDLE',      color: '#888' },
};

const SCHEDULE_TYPE_BADGE = {
    always_on:     { label: '실시간',     color: '#E64A19' },
    intraday:     { label: '장중 전용',   color: '#1976D2' },
    after_market: { label: '장마감 후',   color: '#6A1B9A' },
};

const PRIORITY_LABEL = {
    0:   'CRITICAL',
    10:  'HIGH',
    50:  'NORMAL',
    100: 'LOW',
    200: 'MAINTANCE',
};

function renderProgressCell(progress, taskName) {
    if (!progress) return '-';

    // ── 웹소켓 워치독: 장중 / 장 마감 표시 ──
    if (taskName === 'websocket_watchdog') {
        const isOpen = progress.market_open;
        let marketBadge;
        if (isOpen === null || isOpen === undefined) {
            marketBadge = '<span style="background:#888; color:#fff; padding:1px 7px; border-radius:8px; font-size:0.82em;">확인 중</span>';
        } else if (isOpen) {
            marketBadge = '<span style="background:var(--success-color,#4CAF50); color:#fff; padding:1px 7px; border-radius:8px; font-size:0.82em;">장중</span>';
        } else {
            marketBadge = '<span style="background:#888; color:#fff; padding:1px 7px; border-radius:8px; font-size:0.82em;">장 마감</span>';
        }
        const sub = progress.subscribed_codes ?? 0;
        const gap = (progress.data_gap_sec !== null && progress.data_gap_sec !== undefined)
            ? ` · 갭 ${progress.data_gap_sec}s` : '';
        return `${marketBadge} <span style="font-size:0.85em; color:#888;">구독 ${sub}종목${gap}</span>`;
    }

    // ── 전략 스케줄러: 활성 전략 수 표시 ──
    if (taskName === 'strategy_scheduler') {
        const active = progress.active_strategies ?? 0;
        const total = progress.total_strategies ?? 0;
        if (total === 0) return '<span style="color:#888; font-size:0.88em;">전략 없음</span>';
        const color = active > 0 ? 'var(--success-color,#4CAF50)' : '#888';
        return `<span style="font-size:0.88em; color:${color}; font-weight:bold;">활성 ${active} / ${total} 전략</span>`;
    }

    // ── 전일 기준 우량주 생성 ──
    if (taskName === '전일기준주도주_생성') {
        if (progress.running) {
            const phase = progress.phase || '진행 중';
            const total = progress.total ?? 0;
            const processed = progress.processed ?? 0;
            const pct = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0;
            const elapsed = progress.elapsed ? ` · ${progress.elapsed}s` : '';
            const sub = phase === '1차_필터(시총)'
                ? `통과 ${progress.passed ?? 0}`
                : phase === '2차_필터(지표)'
                ? `선정 ${progress.selected ?? 0}`
                : '';
            const barHtml = total > 0 ? `
                <div style="background:#e0e0e0; border-radius:4px; height:7px; width:100%; margin-top:4px;">
                    <div style="background:var(--primary-color,#2196F3); height:7px; border-radius:4px; width:${pct}%;"></div>
                </div>` : '';
            return `
                <div style="font-size:0.85em;">
                    <span style="background:var(--primary-color,#2196F3); color:#fff; padding:1px 7px; border-radius:8px; font-size:0.82em;">${phase}</span>
                    <span style="color:#888; margin-left:6px;">${total > 0 ? `${processed}/${total} (${pct}%)` : ''}${sub ? ' · ' + sub : ''}${elapsed}</span>
                </div>${barHtml}`;
        }
        if (progress.last_generated_date) {
            const r = progress.last_result || {};
            const detail = (r.kospi_count !== undefined)
                ? ` · KOSPI ${r.kospi_count} / KOSDAQ ${r.kosdaq_count}`
                : '';
            const elapsed = progress.elapsed ? ` · ${progress.elapsed}s` : '';
            return `<span style="background:var(--success-color,#4CAF50); color:#fff; padding:1px 7px; border-radius:8px; font-size:0.82em;">완료</span> <span style="font-size:0.85em; color:#888;">${progress.last_generated_date}${detail}${elapsed}</span>`;
        }
        return '<span style="color:#888; font-size:0.88em;">대기 중</span>';
    }

    // ── 캐시 웜업 태스크 ──
    if (taskName === 'cache_warmup') {
        const isRunning = progress.running;
        const total = progress.total ?? 0;
        const processed = progress.processed ?? 0;
        const cached = progress.cached ?? 0;
        const failed = progress.failed ?? 0;
        const elapsed = progress.elapsed ? ` · ${progress.elapsed}s` : '';
        if (isRunning) {
            const pct = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0;
            return `
                <div style="font-size:0.85em; margin-bottom:3px;">웜업 중 · ${processed.toLocaleString()} / ${total.toLocaleString()} (${pct}%)${elapsed}</div>
                <div style="background:#e0e0e0; border-radius:4px; height:8px; width:100%;">
                    <div style="background:var(--primary-color,#2196F3); height:8px; border-radius:4px; width:${pct}%;"></div>
                </div>
                <div style="font-size:0.8em; color:#888; margin-top:2px;">적재 ${cached.toLocaleString()} / 실패 ${failed.toLocaleString()}</div>
            `;
        }
        if (progress.last_warmed_date) {
            return `<span style="background:var(--success-color,#4CAF50); color:#fff; padding:1px 7px; border-radius:8px; font-size:0.82em;">완료</span> <span style="font-size:0.85em; color:#888;">${progress.last_warmed_date} · 적재 ${cached.toLocaleString()}${elapsed}</span>`;
        }
        return '<span style="color:#888; font-size:0.88em;">대기 중</span>';
    }

    // ── 배치 태스크: 수집 진행률 ──
    const total = progress.total ?? 0;
    const processed = progress.processed ?? 0;
    const isRunning = progress.running;

    if (total === 0) {
        return `<span style="color:#888; font-size:0.88em;">대기 중</span>`;
    }

    const pct = Math.min(100, Math.round((processed / total) * 100));
    const elapsed = progress.elapsed ? ` · ${progress.elapsed.toFixed(0)}s` : '';
    const detail = progress.updated !== undefined
        ? `갱신 ${(progress.updated || 0).toLocaleString()} / 스킵 ${(progress.skipped || 0).toLocaleString()}`
        : `수집 ${(progress.collected || 0).toLocaleString()}`;
    const barColor = isRunning ? 'var(--primary-color,#2196F3)' : 'var(--success-color,#4CAF50)';
    const statusLabel = isRunning ? '수집 중' : '완료';
    return `
        <div style="font-size:0.85em; margin-bottom:3px;">
            ${statusLabel} · ${processed.toLocaleString()} / ${total.toLocaleString()} (${pct}%)${elapsed}
        </div>
        <div style="background:#e0e0e0; border-radius:4px; height:8px; width:100%;">
            <div style="background:${barColor}; height:8px; border-radius:4px; width:${pct}%;"></div>
        </div>
        <div style="font-size:0.8em; color:#888; margin-top:2px;">${detail}</div>
    `;
}

// 태스크별 강제 수집 설정 (task_name → {endpoint, label})
const FORCE_UPDATE_CONFIG = {
    ohlcv_update:         { endpoint: '/api/ohlcv/force-update',                       label: '강제 수집' },
    ranking_refresh:      { endpoint: '/api/background/ranking/force-update',          label: '강제 수집' },
    daily_price_collector:{ endpoint: '/api/background/daily-price/force-update',      label: '강제 수집' },
    '전일기준주도주_생성': { endpoint: '/api/background/watchlist/force-update',        label: '강제 생성' },
    cache_warmup:         { endpoint: '/api/background/cache-warmup/force-update',     label: '강제 웜업' },
};

function renderActionCell(task) {
    const config = FORCE_UPDATE_CONFIG[task.name];
    if (!config) return '-';
    const isRunning = task.progress && task.progress.running;
    const disabled = isRunning ? 'disabled' : '';
    const label = isRunning ? '진행 중...' : config.label;
    return `<button class="btn btn-sm" ${disabled} onclick="forceTaskUpdate(this, '${task.name}')" style="font-size:0.82em; padding:3px 10px;">${label}</button>`;
}

async function forceTaskUpdate(btn, taskName) {
    if (btn.disabled) return;
    const config = FORCE_UPDATE_CONFIG[taskName];
    if (!config) return;
    btn.disabled = true;
    btn.textContent = '요청 중...';
    try {
        const res = await fetch(config.endpoint, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            btn.textContent = '진행 중...';
            alert(data.message || `${config.label}이 시작되었습니다.`);
        } else {
            btn.disabled = false;
            btn.textContent = config.label;
            alert(data.detail || '요청 실패');
        }
    } catch (e) {
        btn.disabled = false;
        btn.textContent = config.label;
        alert('네트워크 오류: ' + e.message);
    }
}

async function updateBackgroundStatus() {
    try {
        const response = await fetch('/api/background/status');
        if (!response.ok) return;
        const result = await response.json();
        if (!result.success || !result.data) return;

        // ForegroundScheduler 상태 표시 (백그라운드 태스크 중단 여부)
        const fgEl = document.getElementById('foreground-status');
        if (fgEl && result.foreground) {
            const { is_blocking_background, active_count } = result.foreground;
            if (is_blocking_background) {
                fgEl.innerHTML = `<span style="background:orange;color:#fff;padding:2px 8px;border-radius:10px;font-size:0.82em;font-weight:bold;">BG 중단 중</span> <span style="color:#888;font-size:0.85em;">포어그라운드 요청 ${active_count}개 처리 중</span>`;
            } else {
                fgEl.innerHTML = `<span style="background:var(--success-color,#4CAF50);color:#fff;padding:2px 8px;border-radius:10px;font-size:0.82em;">정상</span>`;
            }
        }

        const tbody = document.getElementById('background-tasks-body');
        if (!tbody) return;

        if (result.data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:#888;">등록된 태스크 없음</td></tr>';
            return;
        }

        tbody.innerHTML = result.data.map(task => {
            const badge = STATE_BADGE[task.state] || { label: task.state.toUpperCase(), color: '#888' };
            const priorityLabel = PRIORITY_LABEL[task.priority] ?? task.priority;
            const progressHtml = renderProgressCell(task.progress, task.name);
            const actionHtml = renderActionCell(task);
            const schBadge = SCHEDULE_TYPE_BADGE[task.schedule_type];
            const schHtml = schBadge
                ? `<span style="background:${schBadge.color}; color:#fff; padding:1px 6px; border-radius:8px; font-size:0.75em; margin-left:6px; vertical-align:middle;">${schBadge.label}</span>`
                : '';
            return `
                <tr>
                    <td style="font-weight:bold; color:var(--text-color);">${task.name}${schHtml}</td>
                    <td><span style="background:${badge.color}; color:#fff; padding:2px 8px; border-radius:10px; font-size:0.82em; font-weight:bold;">${badge.label}</span></td>
                    <td style="font-size:0.88em; color:#888;">${priorityLabel}</td>
                    <td>${progressHtml}</td>
                    <td>${actionHtml}</td>
                </tr>
            `;
        }).join('');
    } catch (e) {
        console.error('백그라운드 태스크 상태 조회 오류:', e);
    }
}

document.addEventListener('DOMContentLoaded', () => setTimeout(updateBackgroundStatus, 1500));  // 캐시 폴링과 시차 분산
setInterval(updateBackgroundStatus, 5000);

// ── 실시간 현재가 구독 현황 ──────────────────────────────────

let _subData = null;
let _subTab = 'HIGH';

function selectSubTab(btn, priority) {
    _subTab = priority;
    document.querySelectorAll('.sub-tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    renderSubTable();
}

function renderSubTable() {
    const tbody = document.getElementById('sub-table-body');
    if (!tbody || !_subData) return;

    const rows = _subData.pending_by_priority ? _subData.pending_by_priority[_subTab] || [] : [];
    if (rows.length === 0) {
        tbody.innerHTML = `<tr><td colspan="3" style="text-align:center; color:#888;">구독 종목 없음</td></tr>`;
        return;
    }

    tbody.innerHTML = rows.map(item => {
        const displayName = item.name && item.name !== item.code ? `${item.name}(${item.code})` : item.code;
        const activeBadge = item.active
            ? `<span style="background:var(--success-color,#4CAF50); color:#fff; padding:2px 8px; border-radius:10px; font-size:0.82em; font-weight:bold;">구독 중</span>`
            : `<span style="background:#888; color:#fff; padding:2px 8px; border-radius:10px; font-size:0.82em;">대기</span>`;
        const received = item.received_at
            ? formatTimestamp(item.received_at)
            : '<span style="color:#aaa;">-</span>';
        return `
            <tr>
                <td style="font-weight:bold; color:var(--accent);">
                    <a href="/stock?code=${item.code}" target="_blank" class="stock-link">${displayName}</a>
                </td>
                <td>${activeBadge}</td>
                <td style="font-size:0.9em; color:#888;">${received}</td>
            </tr>
        `;
    }).join('');
}

async function updateSubscriptionStatus() {
    try {
        const res = await fetch('/api/subscriptions/status');
        if (!res.ok) return;
        const result = await res.json();
        if (!result.success || !result.data) return;

        const d = result.data;
        _subData = d;

        const activeEl = document.getElementById('sub-active-count');
        const maxEl    = document.getElementById('sub-max');
        const pendEl   = document.getElementById('sub-pending-count');
        if (activeEl) activeEl.textContent = d.active_count;
        if (maxEl)    maxEl.textContent    = d.max_subscriptions;
        if (pendEl)   pendEl.textContent   = d.pending_count;

        // 각 우선순위별 탭에 종목 개수 업데이트
        if (d.pending_by_priority) {
            document.querySelectorAll('.sub-tab-btn').forEach(btn => {
                const priorityMatch = btn.getAttribute('onclick').match(/'([^']+)'/);
                const countSpan = btn.querySelector('.tab-count');
                if (priorityMatch && countSpan) {
                    const priority = priorityMatch[1];
                    const count = d.pending_by_priority[priority] ? d.pending_by_priority[priority].length : 0;
                    countSpan.textContent = `(${count})`;
                }
            });
        }

        renderSubTable();
    } catch (e) {
        console.error('구독 현황 조회 오류:', e);
    }
}

document.addEventListener('DOMContentLoaded', () => setTimeout(updateSubscriptionStatus, 3000));  // 다른 폴링과 시차 분산
setInterval(updateSubscriptionStatus, 5000);