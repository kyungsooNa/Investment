/**
 * VirtualTradeManager 데이터를 이용한 수익률 차트 관리
 */
let yieldChart = null;

async function initVirtualChart() {
    const canvas = document.getElementById('virtualYieldChart');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    yieldChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: '누적 수익률 (%)',
                data: [],
                borderColor: '#4dabf7',
                backgroundColor: 'rgba(77, 171, 247, 0.1)',
                borderWidth: 2,
                pointRadius: 3,
                fill: true,
                tension: 0.3
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: { grid: { display: false }, ticks: { color: '#868e96' } },
                y: { 
                    beginAtZero: true,
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: { 
                        color: '#868e96',
                        callback: value => value.toFixed(1) + '%' 
                    }
                }
            },
            plugins: {
                legend: { display: false },
                tooltip: { mode: 'index', intersect: false }
            }
        }
    });
    
    // 초기 데이터 로드
    await refreshVirtualChart();
}

async function refreshVirtualChart(strategyName = 'ALL') {
    try {
        const response = await fetch(`/api/virtual/chart/${strategyName}`);
        const history = await response.json();
        
        if (!yieldChart) return;
        
        yieldChart.data.labels = history.map(h => h.date.substring(5)); // MM-DD 포맷
        yieldChart.data.datasets[0].data = history.map(h => h.return_rate);
        yieldChart.data.datasets[0].label = `${strategyName} 누적 수익률 (%)`;
        
        yieldChart.update();
        await renderStrategyTabs();
    } catch (error) {
        console.error('Chart update failed:', error);
    }
}

async function renderStrategyTabs() {
    const container = document.getElementById('virtual-strategy-tabs');
    if (!container || container.children.length > 0) return; // 이미 생성됨
    
    const response = await fetch('/api/virtual/strategies');
    const strategies = await response.json();
    if (!strategies.includes('ALL')) strategies.unshift('ALL');
    
    container.innerHTML = strategies.map(s => `
        <button class="btn ranking-tab ${s === 'ALL' ? 'active' : ''}" 
                onclick="selectStrategyTab(this, '${s}')">${s}</button>
    `).join('');
}

function selectStrategyTab(btn, strategyName) {
    document.querySelectorAll('#virtual-strategy-tabs .ranking-tab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    refreshVirtualChart(strategyName);
}

// 페이지 로드 시 실행
document.addEventListener('DOMContentLoaded', initVirtualChart);