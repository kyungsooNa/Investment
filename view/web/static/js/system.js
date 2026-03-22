let isCacheExpanded = false;

function toggleCacheDetails() {
    isCacheExpanded = !isCacheExpanded;
    const container = document.getElementById('cache-details-container');
    const btn = document.getElementById('toggle-details-btn');
    
    if (isCacheExpanded) {
        container.style.display = 'block';
        btn.textContent = '상세 정보 닫기 ▲';
        updateCacheStatus(); // 열자마자 즉시 업데이트 수행
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

async function updateCacheStatus() {
    try {
        // 펼쳐져 있을 때만 expand=true로 호출하여 API 부하 및 응답 사이즈를 최소화
        const url = isCacheExpanded ? '/api/cache/status?expand=true' : '/api/cache/status';
        const response = await fetch(url);
        if (!response.ok) throw new Error('네트워크 응답이 정상이 아닙니다.');
        
        const result = await response.json();
        
        if (result.success && result.data) {
            const stats = result.data;
            document.getElementById('cache-total').textContent = stats.total_requests.toLocaleString();
            document.getElementById('cache-hits').textContent = stats.hits.toLocaleString();
            document.getElementById('cache-misses').textContent = stats.misses.toLocaleString();
            document.getElementById('cache-rate').textContent = stats.hit_rate.toFixed(2);
            document.getElementById('cache-size').textContent = stats.current_size;
            
            const rateElement = document.getElementById('cache-rate');
            rateElement.style.color = stats.hit_rate >= 90 ? 'var(--success-color, #4CAF50)' : 
                                      stats.hit_rate >= 50 ? 'orange' : 'var(--danger-color, #f44336)';
                                      
            // 상세 정보 영역이 열려 있을 때만 테이블 렌더링
            if (isCacheExpanded) {
                // 1. 서비스별 호출자(Caller) 통계 테이블 동적 생성
                let callersSection = document.getElementById('cache-callers-section');
                if (!callersSection && stats.callers) {
                    const container = document.getElementById('cache-details-container');
                    callersSection = document.createElement('div');
                    callersSection.id = 'cache-callers-section';
                    callersSection.innerHTML = `
                        <h3 style="margin-top: 10px;">🔍 서비스/전략별 호출 통계</h3>
                        <table class="data-table" style="margin-bottom: 25px;">
                            <thead>
                                <tr>
                                    <th>호출자 (Caller)</th>
                                    <th>Hits</th>
                                    <th>Misses</th>
                                    <th>적중률 (%)</th>
                                    <th>조회 데이터 (Items)</th>
                                    <th>주요 대상 종목</th>
                                </tr>
                            </thead>
                            <tbody id="cache-callers-body"></tbody>
                        </table>
                        <h3 style="margin-top: 20px;">📋 종목별 상세 조회 통계</h3>
                    `;
                    container.insertBefore(callersSection, container.firstChild);
                }
                
                // 호출자 데이터 채우기 (호출 많은 순 정렬)
                if (stats.callers) {
                    const tbodyCallers = document.getElementById('cache-callers-body');
                    const callersArray = Object.entries(stats.callers).map(([caller, data]) => {
                        return { caller, ...data, total: data.hits + data.misses };
                    }).sort((a, b) => b.total - a.total);

                    tbodyCallers.innerHTML = callersArray.map(item => {
                        const hitRate = item.total > 0 ? ((item.hits / item.total) * 100).toFixed(2) : '0.00';
                        const rateColor = hitRate >= 90 ? 'var(--success-color, #4CAF50)' : hitRate >= 50 ? 'orange' : 'var(--danger-color, #f44336)';
                        
                        let itemsStr = '-';
                        if (item.items && Object.keys(item.items).length > 0) {
                            itemsStr = Object.entries(item.items)
                                .map(([k, v]) => `<span style="display:inline-block; margin-right:4px; margin-bottom:2px; background:var(--bg-color, #f0f0f0); color:var(--text-color, #333); border:1px solid #ccc; padding:2px 6px; border-radius:10px; font-size:0.85em;">${k}: ${v.toLocaleString()}</span>`)
                                .join('');
                        }
                        
                        let keysStr = '-';
                        let keysTitle = '';
                        if (item.keys && Object.keys(item.keys).length > 0) {
                            keysStr = Object.entries(item.keys).map(([k, v]) => `${k} <span style="color:#888; font-size:0.9em;">(${v})</span>`).join(', ');
                            keysTitle = Object.entries(item.keys).map(([k, v]) => `${k}(${v})`).join(', ');
                        }

                        return `
                            <tr>
                                <td style="font-weight: bold; color: var(--text-color);">${item.caller}</td>
                                <td>${item.hits.toLocaleString()}</td>
                                <td>${item.misses.toLocaleString()}</td>
                                <td style="color: ${rateColor}; font-weight: bold;">${hitRate}</td>
                                <td>${itemsStr}</td>
                                <td style="font-size: 0.9em; max-width: 300px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${keysTitle}">${keysStr}</td>
                            </tr>
                        `;
                    }).join('');
                }

                // 2. 종목별 상세 통계 채우기
                if (stats.items) {
                    const tbody = document.getElementById('cache-details-body');
                    if (tbody) {
                        tbody.innerHTML = stats.items.map(item => {
                            const displayName = item.name && item.name !== item.code ? `${item.name}(${item.code})` : item.code;
                            return `
                                <tr>
                                    <td style="font-weight: bold; color: var(--accent);"><a href="/stock?code=${item.code}" target="_blank" class="stock-link">${displayName}</a></td>
                                    <td>${item.hit_count.toLocaleString()}</td>
                                    <td>${item.has_ohlcv ? `O (${item.ohlcv_length}일)` : 'X'}</td>
                                    <td>${item.has_current_price ? 'O' : 'X'}</td>
                                    <td>${formatTimestamp(item.price_updated_at || item.last_updated)}</td>
                                </tr>
                            `;
                        }).join('');
                    }
                }
            }
        }
    } catch (error) {
        console.error('캐시 상태를 가져오는 중 오류 발생:', error);
    }
}

document.addEventListener('DOMContentLoaded', updateCacheStatus);
setInterval(updateCacheStatus, 5000);

// ── 백그라운드 태스크 모니터링 ──────────────────────────────

const STATE_BADGE = {
    running:   { label: 'RUNNING',   color: 'var(--success-color, #4CAF50)' },
    suspended: { label: 'SUSPENDED', color: 'orange' },
    stopped:   { label: 'STOPPED',   color: 'var(--danger-color, #f44336)' },
    idle:      { label: 'IDLE',      color: '#888' },
};

const PRIORITY_LABEL = {
    0:   'CRITICAL',
    10:  'HIGH',
    50:  'NORMAL',
    100: 'LOW',
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

async function updateBackgroundStatus() {
    try {
        const response = await fetch('/api/background/status');
        if (!response.ok) return;
        const result = await response.json();
        if (!result.success || !result.data) return;

        const tbody = document.getElementById('background-tasks-body');
        if (!tbody) return;

        if (result.data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:#888;">등록된 태스크 없음</td></tr>';
            return;
        }

        tbody.innerHTML = result.data.map(task => {
            const badge = STATE_BADGE[task.state] || { label: task.state.toUpperCase(), color: '#888' };
            const priorityLabel = PRIORITY_LABEL[task.priority] ?? task.priority;
            const progressHtml = renderProgressCell(task.progress, task.name);
            return `
                <tr>
                    <td style="font-weight:bold; color:var(--text-color);">${task.name}</td>
                    <td><span style="background:${badge.color}; color:#fff; padding:2px 8px; border-radius:10px; font-size:0.82em; font-weight:bold;">${badge.label}</span></td>
                    <td style="font-size:0.88em; color:#888;">${priorityLabel}</td>
                    <td>${progressHtml}</td>
                </tr>
            `;
        }).join('');
    } catch (e) {
        console.error('백그라운드 태스크 상태 조회 오류:', e);
    }
}

document.addEventListener('DOMContentLoaded', updateBackgroundStatus);
setInterval(updateBackgroundStatus, 5000);