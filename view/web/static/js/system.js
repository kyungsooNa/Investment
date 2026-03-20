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
                        return `
                            <tr>
                                <td style="font-weight: bold; color: var(--text-color);">${item.caller}</td>
                                <td>${item.hits.toLocaleString()}</td>
                                <td>${item.misses.toLocaleString()}</td>
                                <td style="color: ${rateColor}; font-weight: bold;">${hitRate}</td>
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