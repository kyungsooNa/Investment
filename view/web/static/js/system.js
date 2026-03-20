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
            if (isCacheExpanded && stats.items) {
                const tbody = document.getElementById('cache-details-body');
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
    } catch (error) {
        console.error('캐시 상태를 가져오는 중 오류 발생:', error);
    }
}

document.addEventListener('DOMContentLoaded', updateCacheStatus);
setInterval(updateCacheStatus, 5000);