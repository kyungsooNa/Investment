// ── 캐시 상태 모니터링 ───────────────────────────────────────

const _cacheExpanded = { price: false, ohlcv: false };
const SYSTEM_POLL_TIMEOUT_MS = 4000;
let _systemPageActive = location.pathname === '/system';
let _systemPollTimers = {};
let _cacheStatusInFlight = false;
let _backgroundStatusInFlight = false;
let _operationsStatusInFlight = false;
let _subscriptionStatusInFlight = false;
let _positionSizingInFlight = false;

function _isSystemPageActive() {
    return _systemPageActive && !!document.getElementById('background-tasks-body');
}

async function _fetchWithTimeout(url, options = {}, timeoutMs = SYSTEM_POLL_TIMEOUT_MS) {
    return await fetch(url, options);
}

function _isPollAbortError(error) {
    if (typeof isAbortError === 'function') return isAbortError(error);
    return error && error.name === 'AbortError';
}

function _clearSystemTimer(name) {
    const timer = _systemPollTimers[name];
    if (timer) clearTimeout(timer);
    delete _systemPollTimers[name];
}

function _scheduleSystemPoll(name, fn, intervalMs, firstDelayMs = intervalMs) {
    if (!_systemPageActive) return;
    _clearSystemTimer(name);
    _systemPollTimers[name] = setTimeout(async () => {
        delete _systemPollTimers[name];
        if (!_isSystemPageActive()) return;
        await fn();
        _scheduleSystemPoll(name, fn, intervalMs);
    }, firstDelayMs);
}

function stopSystemPolling() {
    _systemPageActive = false;
    Object.values(_systemPollTimers).forEach(clearTimeout);
    _systemPollTimers = {};
}

function startSystemPolling() {
    _systemPageActive = true;
    loadPositionSizingLimits();

    _scheduleSystemPoll('cache', updateCacheStatus, 20000, 0);
    _scheduleSystemPoll('operations', updateOperationsStatus, 8000, 900);
    _scheduleSystemPoll('background', updateBackgroundStatus, 9000, 2200);
    _scheduleSystemPoll('subscriptions', updateSubscriptionStatus, 10000, 3600);
}

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
    const _items = stats.items;
    const pageData = window.Paginator
        ? window.Paginator.paginate('price-items', _items, 'price-items-pagination',
            () => renderPriceCacheDetails(stats))
        : _items;
    tbody.innerHTML = pageData.map(item => {
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
    const _items = stats.items;
    const pageData = window.Paginator
        ? window.Paginator.paginate('ohlcv-items', _items, 'ohlcv-items-pagination',
            () => renderOhlcvCacheDetails(stats))
        : _items;
    tbody.innerHTML = pageData.map(item => {
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
    if (!_isSystemPageActive() || _cacheStatusInFlight) return;
    _cacheStatusInFlight = true;
    try {
        const needExpand = _cacheExpanded.price || _cacheExpanded.ohlcv;
        const url = needExpand ? '/api/cache/status?expand=true' : '/api/cache/status';
        const response = await _fetchWithTimeout(url);
        if (!response.ok) throw new Error('네트워크 응답이 정상이 아닙니다.');
        const result = await response.json();
        if (!result.success || !result.data) return;
        const stats = result.data;
        renderCacheSummary('price', stats.price_cache || {}, 3000);
        renderCacheSummary('ohlcv', stats.ohlcv_cache || {}, 500);
        if (_cacheExpanded.price)  renderPriceCacheDetails(stats.price_cache || {});
        if (_cacheExpanded.ohlcv)  renderOhlcvCacheDetails(stats.ohlcv_cache || {});
    } catch (error) {
        if (_isPollAbortError(error)) return;
        console.error('캐시 상태를 가져오는 중 오류 발생:', error);
    } finally {
        _cacheStatusInFlight = false;
    }
}

// ── 백그라운드 태스크 모니터링 ──────────────────────────────

let _timeDispatcherInfo = null;

const STATE_BADGE = {
    running:   { label: 'RUNNING',   color: 'var(--success-color, #4CAF50)' },
    suspended: { label: 'SUSPENDED', color: 'orange' },
    stopped:   { label: 'STOPPED',   color: 'var(--danger-color, #f44336)' },
    idle:      { label: 'IDLE',      color: '#888' },
};

const SCHEDULE_TYPE_BADGE = {
    always_on:     { label: '실시간',     color: '#E64A19' },
    pre_market:    { label: '장전 점검',   color: '#00897B' },
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

// 티켓 상태 3단계:
//   "before"   — 장중, 아직 오늘 티켓 미발행
//   "issued"   — 발행됨, delay_sec 대기 중
//   "consumed" — delay_sec 경과, 태스크 실행됨
//   "pending"  — 장 마감 후지만 아직 미발행 (폴링 대기)
function _ticketState(issued, marketIsOpen, dispatchedAt, delaySec) {
    if (!issued) return marketIsOpen ? 'before' : 'pending';
    const now = Date.now() / 1000;
    return (dispatchedAt && (dispatchedAt + delaySec) > now) ? 'issued' : 'consumed';
}

function renderTicketStatusSuffix(taskName) {
    if (!_timeDispatcherInfo) return '';
    const tasks = _timeDispatcherInfo.registered_tasks || [];
    const taskInfo = tasks.find(t => t.name === taskName);
    if (!taskInfo) return '';

    const { ticket_issued_today, last_dispatched_at, last_dispatched_date, market_is_open } = _timeDispatcherInfo;
    const state = _ticketState(ticket_issued_today, market_is_open, last_dispatched_at, taskInfo.delay_sec);

    let html;
    if (state === 'before') {
        html = `<span style="background:#E3F2FD;color:#1565C0;border:1px solid #90CAF9;padding:1px 7px;border-radius:8px;font-size:0.8em;">티켓 발행 전</span>`;
        html += ` <span style="font-size:0.8em;color:#888;">장 마감 후 발행 예정</span>`;
    } else if (state === 'pending') {
        html = `<span style="background:#FFF3E0;color:#E65100;border:1px solid #FFCC80;padding:1px 7px;border-radius:8px;font-size:0.8em;">티켓 발행 전</span>`;
        html += ` <span style="font-size:0.8em;color:#888;">장 마감 감지 대기 중</span>`;
        if (last_dispatched_date) html += ` <span style="font-size:0.8em;color:#aaa;">(마지막: ${last_dispatched_date})</span>`;
    } else if (state === 'issued') {
        const remaining = (last_dispatched_at + taskInfo.delay_sec) - Date.now() / 1000;
        const mins = remaining > 60 ? `${Math.round(remaining / 60)}분 후 실행 예정` : '곧 실행 예정';
        html = `<span style="background:#E8F5E9;color:#2E7D32;border:1px solid #A5D6A7;padding:1px 7px;border-radius:8px;font-size:0.8em;font-weight:bold;">티켓 발행됨</span>`;
        html += ` <span style="font-size:0.8em;color:orange;">${mins}</span>`;
    } else { // consumed
        html = `<span style="background:var(--success-color,#4CAF50);color:#fff;padding:1px 7px;border-radius:8px;font-size:0.8em;font-weight:bold;">티켓 사용됨</span>`;
        if (last_dispatched_at && taskInfo.delay_sec > 0) {
            const elapsed = Math.round((Date.now() / 1000 - last_dispatched_at - taskInfo.delay_sec) / 60);
            html += ` <span style="font-size:0.8em;color:#888;">${elapsed}분 전 실행됨</span>`;
        } else if (last_dispatched_date) {
            html += ` <span style="font-size:0.8em;color:#888;">${last_dispatched_date}</span>`;
        }
    }
    return `<div style="margin-top:5px;">${html}</div>`;
}

function renderTimeDispatcherStatus(td) {
    const el = document.getElementById('time-dispatcher-status');
    if (!el) return;
    if (!td) { el.innerHTML = ''; return; }

    const { ticket_issued_today, last_dispatched_date, last_dispatched_at, market_is_open, registered_tasks } = td;
    const taskCount = (registered_tasks || []).length;
    // 대표 state: delay_sec=0 기준으로 전체 상태 판단
    const overallState = _ticketState(ticket_issued_today, market_is_open, last_dispatched_at, 0);

    let badge, detail;
    if (overallState === 'before') {
        badge = `<span style="background:#1976D2;color:#fff;padding:2px 8px;border-radius:10px;font-size:0.82em;font-weight:bold;">발행 전 (장중)</span>`;
        detail = '장 마감 후 자동 발행 예정';
        if (last_dispatched_date) detail += ` · 마지막: ${last_dispatched_date}`;
    } else if (overallState === 'pending') {
        badge = `<span style="background:orange;color:#fff;padding:2px 8px;border-radius:10px;font-size:0.82em;font-weight:bold;">발행 전 (폴링 대기)</span>`;
        detail = last_dispatched_date ? `마지막: ${last_dispatched_date} · ` : '첫 실행 전 · ';
        detail += '장 마감 감지 대기 중 (1분 폴링)';
    } else { // issued or consumed (ticket_issued_today=true)
        badge = `<span style="background:var(--success-color,#4CAF50);color:#fff;padding:2px 8px;border-radius:10px;font-size:0.82em;font-weight:bold;">발행됨</span>`;
        detail = `거래일 ${last_dispatched_date}`;
        if (last_dispatched_at) detail += ` · 발행 ${new Date(last_dispatched_at * 1000).toLocaleTimeString('ko-KR', { hour12: false })}`;
    }

    el.innerHTML = `
        <div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:8px 12px;background:var(--bg-color,#f9f9f9);border:1px solid #ddd;border-radius:8px;font-size:0.88em;">
            <span style="font-weight:bold;color:var(--text-color);">📅 장 마감 티켓</span>
            ${badge}
            <span style="color:#888;">${detail}</span>
            <span style="color:#aaa;margin-left:auto;">등록 ${taskCount}개 태스크</span>
        </div>`;
}

function renderProgressCell(progress, taskName) {
    const main = _renderProgressBody(progress, taskName);
    if (main === '-') return main;
    const suffix = renderTicketStatusSuffix(taskName);
    return suffix ? main + suffix : main;
}

function _renderProgressBody(progress, taskName) {
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
        const subPt = progress.subscribed_pt_codes ?? 0;
        const subPrice = progress.subscribed_price_codes ?? 0;
        const gap = (progress.data_gap_sec !== null && progress.data_gap_sec !== undefined)
            ? ` · 갭 ${progress.data_gap_sec}s` : '';
        return `${marketBadge} <span style="font-size:0.85em; color:#888;">구독 PT ${subPt} · Price ${subPrice}종목${gap}</span>`;
    }

    // ── 전략 스케줄러: 활성 전략 수 표시 ──
    if (taskName === 'strategy_scheduler') {
        const strategies = progress.strategies || [];
        
        let active = progress.active_strategies ?? 0;
        let total = progress.total_strategies ?? 0;
        
        // strategies 데이터가 있으면 이를 기반으로 정확히 카운트
        if (strategies.length > 0) {
            total = strategies.length;
            active = strategies.filter(s => s.enabled).length;
        }

        if (total === 0) return '<span style="color:#888; font-size:0.88em;">전략 없음</span>';
        
        const color = active > 0 ? 'var(--success-color,#4CAF50)' : '#888';
        let html = `<span style="font-size:0.88em; color:${color}; font-weight:bold;">활성 ${active} / ${total} 전략</span>`;
        
        // IDLE, RUNNING 상태 관계없이 활성화(enabled=true)된 전략의 이름을 뱃지로 렌더링
        if (active > 0 && strategies.length > 0) {
            const activeNames = strategies.filter(s => s.enabled).map(s => s.name);
            const badgeHtml = activeNames.map(name => 
                `<span style="display:inline-block; background:var(--bg-color,#f0f0f0); border:1px solid #ccc; padding:2px 6px; border-radius:6px; font-size:0.82em; margin-right:4px; margin-top:4px; color:var(--text-color,#333); white-space:nowrap;">${name}</span>`
            ).join('');
            html += `<div style="margin-top:4px; display:flex; flex-wrap:wrap;">${badgeHtml}</div>`;
        }
        
        return html;
    }

    // ── 장전 헬스 체크 ──
    if (taskName === 'pre_market_health_check') {
        if (progress.running) {
            return '<span style="background:var(--primary-color,#2196F3); color:#fff; padding:1px 7px; border-radius:8px; font-size:0.82em;">점검 중</span>';
        }
        const result = progress.last_result || {};
        const issues = result.issues || [];
        if (progress.last_checked_date) {
            if (result.ok === false || issues.length > 0) {
                const detail = issues.length ? ` · ${issues.join(', ')}` : '';
                return `<span style="background:var(--danger-color,#f44336); color:#fff; padding:1px 7px; border-radius:8px; font-size:0.82em;">점검 실패</span> <span style="font-size:0.85em; color:#888;">${progress.last_checked_date}${detail}</span>`;
            }
            return `<span style="background:var(--success-color,#4CAF50); color:#fff; padding:1px 7px; border-radius:8px; font-size:0.82em;">완료</span> <span style="font-size:0.85em; color:#888;">${progress.last_checked_date} · 이상 없음</span>`;
        }
        return '<span style="color:#888; font-size:0.88em;">대기 중</span>';
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

    // ── 52주 신고가 태스크 ──
    if (taskName === 'newhigh') {
        if (progress.status) {
            return `<span style="background:orange; color:#fff; padding:1px 7px; border-radius:8px; font-size:0.82em;">${progress.status}</span>`;
        }
        if (progress.running) {
            return `<span style="background:var(--primary-color,#2196F3); color:#fff; padding:1px 7px; border-radius:8px; font-size:0.82em;">탐색 중...</span>`;
        }
        if (progress.last_date) {
            return `<span style="background:var(--success-color,#4CAF50); color:#fff; padding:1px 7px; border-radius:8px; font-size:0.82em;">완료</span> <span style="font-size:0.85em; color:#888;">${progress.last_date} · 신고가 ${progress.newhigh_count ?? 0}종목</span>`;
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
    after_market_reconcile:{ endpoint: '/api/background/reconcile/force-update',     label: '강제 검증' },
    ohlcv_update:         { endpoint: '/api/ohlcv/force-update',                       label: '강제 수집' },
    ranking_refresh:      { endpoint: '/api/background/ranking/force-update',          label: '강제 수집' },
    daily_price_collector:{ endpoint: '/api/background/daily-price/force-update',      label: '강제 수집' },
    '전일기준주도주_생성': { endpoint: '/api/background/watchlist/force-update',        label: '강제 생성' },
    cache_warmup:         { endpoint: '/api/background/cache-warmup/force-update',     label: '강제 웜업' },
    newhigh:              { endpoint: '/api/background/newhigh/force-update',          label: '강제 수집' },
    newhigh_strategy_coverage_backtest: { endpoint: '/api/background/newhigh-strategy-coverage/force-update', label: '강제 실행' },
    theme_classification:  { endpoint: '/api/background/theme-classification/force-update', label: '강제 수집' },
    daily_theme_leader_report: { endpoint: '/api/background/daily-theme-leader-report/force-update', label: '강제 발송' },
    minervini_update:     { endpoint: '/api/background/minervini/force-update',         label: '강제 수집' },
    strategy_log_report:  { endpoint: '/api/background/strategy-log-report/force-update', label: '강제 발송' },
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
    if (!_isSystemPageActive() || _backgroundStatusInFlight) return;
    _backgroundStatusInFlight = true;
    try {
        const response = await _fetchWithTimeout('/api/background/status');
        if (!response.ok) return;
        const result = await response.json();
        if (!result.success || !result.data) return;

        _timeDispatcherInfo = result.time_dispatcher || null;
        renderTimeDispatcherStatus(_timeDispatcherInfo);

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
            const ctrl = document.getElementById('background-tasks-pagination');
            if (ctrl) ctrl.innerHTML = '';
            return;
        }

        const _tasks = result.data;
        const pageData = window.Paginator
            ? window.Paginator.paginate('background-tasks', _tasks, 'background-tasks-pagination', updateBackgroundStatus)
            : _tasks;
        tbody.innerHTML = pageData.map(task => {
            const badge = STATE_BADGE[task.state] || { label: task.state.toUpperCase(), color: '#888' };
            const priorityLabel = PRIORITY_LABEL[task.priority] ?? task.priority;
            const progressHtml = renderProgressCell(task.progress, task.name);
            const actionHtml = renderActionCell(task);
            const schBadge = SCHEDULE_TYPE_BADGE[task.schedule_type];
            const schHtml = schBadge
                ? `<span style="background:${schBadge.color}; color:#fff; padding:1px 6px; border-radius:8px; font-size:0.75em; margin-left:6px; vertical-align:middle;">${schBadge.label}</span>`
                : '';
            const delayMin = task.delay_sec ? Math.round(task.delay_sec / 60) : 0;
            const delayHtml = task.schedule_type === 'after_market'
                ? `<span style="color:#aaa; font-size:0.75em; margin-left:5px; vertical-align:middle;">${delayMin > 0 ? `+${delayMin}m` : '즉시'}</span>`
                : '';
            return `
                <tr>
                    <td style="font-weight:bold; color:var(--text-color);">${task.name}${schHtml}${delayHtml}</td>
                    <td><span style="background:${badge.color}; color:#fff; padding:2px 8px; border-radius:10px; font-size:0.82em; font-weight:bold;">${badge.label}</span></td>
                    <td style="font-size:0.88em; color:#888;">${priorityLabel}</td>
                    <td>${progressHtml}</td>
                    <td>${actionHtml}</td>
                </tr>
            `;
        }).join('');
    } catch (e) {
        if (_isPollAbortError(e)) return;
        console.error('백그라운드 태스크 상태 조회 오류:', e);
    } finally {
        _backgroundStatusInFlight = false;
    }
}

function renderOpsMetric(label, value, tone = 'normal') {
    const color = tone === 'bad' ? 'var(--danger-color,#f44336)' : (tone === 'warn' ? 'orange' : 'var(--text-primary,#eee)');
    return `<div style="border:1px solid var(--border,#444);border-radius:6px;padding:10px;background:var(--bg-secondary,#222);">
        <div style="font-size:0.8em;color:var(--text-secondary,#aaa);">${label}</div>
        <div style="font-size:1.2em;font-weight:bold;color:${color};">${value}</div>
    </div>`;
}

async function updateOperationsStatus() {
    if (!_isSystemPageActive() || _operationsStatusInFlight) return;
    _operationsStatusInFlight = true;
    try {
        const response = await _fetchWithTimeout('/api/system/operations/status');
        if (!response.ok) return;
        const body = await response.json();
        const data = body.data || {};
        const el = document.getElementById('operations-summary');
        if (!el) return;
        const dq = data.data_quality || {};
        const dqResult = dq.last_result || {};
        const ws = data.websocket || {};
        const orders = data.orders || {};
        const ks = data.kill_switch || {};
        const pnl = data.pnl || {};
        const realized = pnl.realized || {};
        const evaluation = pnl.evaluation || {};
        const day = pnl.day || {};
        const fmtWon = (value) => value == null ? '-' : `${Number(value).toLocaleString()}원`;
        const fmtPct = (value) => value == null ? '-' : `${Number(value).toFixed(2)}%`;
        const pl = data.price_lookup || {};
        const plHit  = pl.snapshot_hit    ?? 0;
        const plRest = pl.rest_fallback   ?? 0;
        const plNoTick = pl.no_tick_fallback ?? 0;
        const plStale  = pl.stale_fallback   ?? 0;
        const plForceFresh = pl.force_fresh_bypass ?? 0;
        const plFullOutput = pl.full_output_required ?? 0;
        const plStreamUnavailable = pl.stream_unavailable_fallback ?? 0;
        const plEligibleTotal = plHit + plNoTick + plStale;
        const plMeasuredTotal = plEligibleTotal + plForceFresh + plFullOutput + plStreamUnavailable;
        const plRate  = plEligibleTotal > 0 ? ((plHit / plEligibleTotal) * 100).toFixed(1) : null;
        const plRateTone = plRate === null ? 'normal' : (parseFloat(plRate) >= 70 ? 'normal' : parseFloat(plRate) >= 30 ? 'warn' : 'bad');
        el.innerHTML = [
            renderOpsMetric('활성 전략', data.active_strategy_count ?? 0),
            renderOpsMetric('현재 포지션', data.position_count ?? 0),
            renderOpsMetric('활성 주문', orders.active_order_count ?? 0, (orders.active_order_count || 0) > 0 ? 'warn' : 'normal'),
            renderOpsMetric('미체결 주문', orders.unfilled_order_count ?? 0, (orders.unfilled_order_count || 0) > 0 ? 'warn' : 'normal'),
            renderOpsMetric('실현손익', fmtWon(realized.realized_pnl_won), (realized.realized_pnl_won || 0) < 0 ? 'warn' : 'normal'),
            renderOpsMetric('평가손익', fmtWon(evaluation.estimated_unrealized_pnl_won), (evaluation.estimated_unrealized_pnl_won || 0) < 0 ? 'warn' : 'normal'),
            renderOpsMetric('일중수익률', fmtPct(day.daily_change_pct), (day.daily_change_pct || 0) < 0 ? 'warn' : 'normal'),
            renderOpsMetric('Data Quality', dq.enabled ? (dqResult.reason || 'ok') : 'disabled', dqResult.ok === false ? 'bad' : 'normal'),
            renderOpsMetric('WebSocket', ws.receive_alive ? 'alive' : 'down', ws.receive_alive ? 'normal' : 'warn'),
            renderOpsMetric('알림 큐', data.notification_queue_depth ?? 0),
            renderOpsMetric('Kill Switch', ks && ks.is_tripped ? 'TRIPPED' : 'normal', ks && ks.is_tripped ? 'bad' : 'normal'),
            plEligibleTotal > 0 ? renderOpsMetric('현가 Snap율', `${plRate}%`, plRateTone) : '',
            plMeasuredTotal > 0 ? renderOpsMetric('Snap Hit', plHit.toLocaleString()) : '',
            plMeasuredTotal > 0 ? renderOpsMetric('Fallback', `${plRest.toLocaleString()} (NoTick ${plNoTick} / Stale ${plStale})`, (plNoTick + plStale) > plHit && plEligibleTotal > 20 ? 'warn' : 'normal') : '',
            plMeasuredTotal > 0 ? renderOpsMetric('Snap 우회', `Fresh ${plForceFresh} / 상세 ${plFullOutput} / 연결 ${plStreamUnavailable}`) : '',
        ].join('');
    } catch (e) {
        if (_isPollAbortError(e)) return;
        console.error('운영 요약 조회 오류:', e);
    } finally {
        _operationsStatusInFlight = false;
    }
}

// ── 실시간 현재가 구독 현황 ──────────────────────────────────

let _subData = null;
let _subTab = 'CRITICAL';

function selectSubTab(btn, priority) {
    _subTab = priority;
    document.querySelectorAll('.sub-tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    if (window.Paginator) window.Paginator.reset('sub-table');
    renderSubTable();
}

function renderSubTable() {
    const tbody = document.getElementById('sub-table-body');
    if (!tbody || !_subData) return;

    const rows = _subData.pending_by_priority ? _subData.pending_by_priority[_subTab] || [] : [];
    if (rows.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" style="text-align:center; color:#888;">구독 종목 없음</td></tr>`;
        const ctrl = document.getElementById('sub-table-pagination');
        if (ctrl) ctrl.innerHTML = '';
        return;
    }

    const pageData = window.Paginator
        ? window.Paginator.paginate('sub-table', rows, 'sub-table-pagination', renderSubTable)
        : rows;
    tbody.innerHTML = pageData.map(item => {
        const displayName = item.name && item.name !== item.code ? `${item.name}(${item.code})` : item.code;
        const activeBadge = item.active
            ? `<span style="background:var(--success-color,#4CAF50); color:#fff; padding:2px 8px; border-radius:10px; font-size:0.82em; font-weight:bold;">구독 중</span>`
            : `<span style="background:#888; color:#fff; padding:2px 8px; border-radius:10px; font-size:0.82em;">대기</span>`;
        const priceHtml = item.price != null
            ? `<span style="font-weight:bold;">${Number(item.price).toLocaleString()}원</span>`
            : '<span style="color:#aaa;">-</span>';
        const received = item.received_at
            ? formatTimestamp(item.received_at)
            : '<span style="color:#aaa;">-</span>';
        return `
            <tr>
                <td style="font-weight:bold; color:var(--accent);">
                    <a href="/stock?code=${item.code}" target="_blank" class="stock-link">${displayName}</a>
                </td>
                <td>${activeBadge}</td>
                <td style="font-size:0.9em;">${priceHtml}</td>
                <td style="font-size:0.9em; color:#888;">${received}</td>
            </tr>
        `;
    }).join('');
}

async function updateSubscriptionStatus() {
    if (!_isSystemPageActive() || _subscriptionStatusInFlight) return;
    _subscriptionStatusInFlight = true;
    try {
        const res = await _fetchWithTimeout('/api/subscriptions/status');
        if (!res.ok) return;
        const result = await res.json();
        if (!result.success || !result.data) return;

        const d = result.data;
        _subData = d;

        const activeEl = document.getElementById('sub-active-count');
        const activePtEl = document.getElementById('sub-active-pt-count');
        const activePriceEl = document.getElementById('sub-active-price-count');
        const maxEl    = document.getElementById('sub-max');
        const pendEl   = document.getElementById('sub-pending-count');
        if (activeEl) activeEl.textContent = d.active_count;
        if (activePtEl) activePtEl.textContent = d.active_codes_pt ? d.active_codes_pt.length : 0;
        if (activePriceEl) activePriceEl.textContent = d.active_codes_price ? d.active_codes_price.length : 0;
        if (maxEl)    maxEl.textContent    = d.max_subscriptions;
        if (pendEl)   pendEl.textContent   = d.pending_count;

        // 각 우선순위별 탭에 종목 개수 업데이트
        if (d.pending_by_priority) {
            document.querySelectorAll('.sub-tab-btn').forEach(btn => {
                const priority = btn.getAttribute('data-priority');
                const countSpan = btn.querySelector('.tab-count');
                if (priority && countSpan) {
                    const count = d.pending_by_priority[priority] ? d.pending_by_priority[priority].length : 0;
                    countSpan.textContent = `(${count})`;
                }
            });
        }

        renderSubTable();
    } catch (e) {
        if (_isPollAbortError(e)) return;
        console.error('구독 현황 조회 오류:', e);
    } finally {
        _subscriptionStatusInFlight = false;
    }
}

/* ── Pjax 재방문 시 상태 즉시 갱신 ── */
document.addEventListener('pjax:ready', (e) => {
    if (e.detail?.path !== '/system') return;
    startSystemPolling();
});

document.addEventListener('pjax:before-change', (e) => {
    if (e.detail?.from !== '/system') return;
    stopSystemPolling();
});

/* ── 자금 한도 설정 ── */
async function loadPositionSizingLimits() {
    if (!_isSystemPageActive() || _positionSizingInFlight) return;
    _positionSizingInFlight = true;
    try {
        const res = await _fetchWithTimeout('/api/position-sizing/limits');
        if (!res.ok) return;
        const data = await res.json();
        const amtEl = document.getElementById('input-max-order-amount');
        const pctEl = document.getElementById('input-max-position-pct');
        if (amtEl && data.max_order_amount_won != null) amtEl.value = Number(data.max_order_amount_won).toLocaleString('ko-KR');
        if (pctEl && data.max_per_position_pct != null) pctEl.value = data.max_per_position_pct;
        const dAmt = document.getElementById('default-max-order-amount');
        const dPct = document.getElementById('default-max-position-pct');
        if (dAmt && data.defaults?.max_order_amount_won != null)
            dAmt.textContent = `기본값: ${Number(data.defaults.max_order_amount_won).toLocaleString('ko-KR')}원`;
        if (dPct && data.defaults?.max_per_position_pct != null)
            dPct.textContent = `기본값: ${data.defaults.max_per_position_pct}%`;
    } catch (e) { /* 무시 */ }
    finally {
        _positionSizingInFlight = false;
    }
}

async function savePositionSizingLimits() {
    const amtEl = document.getElementById('input-max-order-amount');
    const pctEl = document.getElementById('input-max-position-pct');
    const msgEl = document.getElementById('position-sizing-msg');
    const body = {};
    if (amtEl && amtEl.value !== '') body.max_order_amount_won = parseInt(amtEl.value.replace(/,/g, ''), 10);
    if (pctEl && pctEl.value !== '') body.max_per_position_pct = parseFloat(pctEl.value);
    if (Object.keys(body).length === 0) return;
    try {
        const res = await fetch('/api/position-sizing/limits', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (res.ok && data.success) {
            if (msgEl) {
                msgEl.textContent = `저장 완료 — 주문 한도: ${(data.max_order_amount_won ?? 0).toLocaleString()}원 / 비중 상한: ${data.max_per_position_pct}%`;
                msgEl.style.color = 'var(--success-color, #4caf50)';
            }
        } else {
            if (msgEl) { msgEl.textContent = '저장 실패: ' + (data.detail || JSON.stringify(data)); msgEl.style.color = 'var(--danger-color, #f44336)'; }
        }
    } catch (e) {
        if (msgEl) { msgEl.textContent = '오류: ' + e.message; msgEl.style.color = 'var(--danger-color, #f44336)'; }
    }
}

// ── 서버 프로세스 종료 ───────────────────────────────────────
async function shutdownServer(btn) {
    if (!confirm('웹 서버 프로세스를 종료합니다.\n종료 후에는 터미널에서 다시 실행해야 합니다.\n\n계속하시겠습니까?')) return;
    const msgEl = document.getElementById('shutdown-msg');
    if (btn) btn.disabled = true;
    if (msgEl) { msgEl.textContent = '종료 요청 중...'; msgEl.style.color = 'var(--text-secondary, #888)'; }
    try {
        const res = await fetch('/api/system/shutdown', { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.success) {
            if (msgEl) { msgEl.textContent = '서버를 종료합니다. 잠시 후 연결이 끊어집니다.'; msgEl.style.color = 'var(--danger-color, #f44336)'; }
            if (typeof showToast === 'function') showToast('서버 종료 요청을 전송했습니다.', 'success');
        } else {
            if (btn) btn.disabled = false;
            if (msgEl) { msgEl.textContent = '종료 실패: ' + (data.detail || JSON.stringify(data)); msgEl.style.color = 'var(--danger-color, #f44336)'; }
        }
    } catch (e) {
        // 서버가 즉시 종료되면 fetch 가 네트워크 에러로 끝날 수 있다 — 정상 종료로 간주.
        if (msgEl) { msgEl.textContent = '서버 연결이 종료되었습니다.'; msgEl.style.color = 'var(--danger-color, #f44336)'; }
        if (typeof showToast === 'function') showToast('서버가 종료되었습니다.', 'success');
    }
}

document.addEventListener('DOMContentLoaded', startSystemPolling);
