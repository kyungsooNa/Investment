/**
 * VirtualTradeManager 데이터를 이용한 수익률 차트 관리
 */
let yieldChart = null;
let activeVirtualStrategy = 'ALL';

const STRATEGY_COLORS_PALETTE = [
    '#ff6b6b', '#51cf66', '#fcc419', '#ae3ec9',
    '#1098ad', '#f06595', '#845ef7', '#339af0', '#20c997'
];
const ALL_COLOR = '#4dabf7';

// 전략명 → 색상 고정 매핑 (전략 순서가 바뀌어도 동일 색상 유지)
const strategyColorMap = {};
function getStrategyColor(name) {
    if (name === 'ALL') return ALL_COLOR;
    if (!strategyColorMap[name]) {
        const idx = Object.keys(strategyColorMap).length;
        strategyColorMap[name] = STRATEGY_COLORS_PALETTE[idx % STRATEGY_COLORS_PALETTE.length];
    }
    return strategyColorMap[name];
}

async function initVirtualChart() {
    const canvas = document.getElementById('virtualYieldChart');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    yieldChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: []
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
                legend: { 
                    display: true,
                    labels: { 
                        color: '#a0a0b0', 
                        usePointStyle: true, 
                        pointStyle: 'line', 
                        boxWidth: 20, 
                        font: { size: 11 },
                        generateLabels: function(chart) {
                            const original = Chart.defaults.plugins.legend.labels.generateLabels(chart);
                            return original.map(item => {
                                const dataset = chart.data.datasets[item.datasetIndex];
                                item.pointStyle = 'line';
                                if (dataset.borderDash && dataset.borderDash.length > 0) {
                                    item.lineDash = dataset.borderDash;
                                }
                                return item;
                            });
                        }
                    }
                },
                tooltip: { mode: 'index', intersect: false }
            }
        }
    });
    
    // 초기 데이터 로드
    await refreshVirtualChart('ALL');
}

window.refreshVirtualChart = async function(strategyName) {
    if (strategyName) activeVirtualStrategy = strategyName;

    // 차트 객체가 없으면 초기화를 먼저 시도하거나 대기
    if (!yieldChart) {
        console.log('[VirtualChart] 차트가 아직 준비되지 않아 초기화를 시도합니다.');
        await initVirtualChart();
    }

    try {
        const response = await fetch(`/api/virtual/chart/${activeVirtualStrategy}`);
        const data = await response.json();
        
        if (!yieldChart || !data.histories) return;
        
        const histories = data.histories;
        const benchmarks = data.benchmarks || {};
        const strategyNames = Object.keys(histories);
        
        // 1. 기준이 되는 전체 날짜 목록 생성 (ALL 전략 기준)
        const refHistory = histories['ALL'] || Object.values(histories)[0] || [];
        const globalDates = refHistory.map(h => h.date);
        yieldChart.data.labels = globalDates.map(d => d.substring(5)); // MM-DD 포맷

        const newDatasets = [];

        // 2. 각 전략별 데이터셋 생성 및 날짜 정렬(Alignment)
        strategyNames.forEach((name) => {
            const isAll = (name === 'ALL');
            const strategyHistory = histories[name];
            
            // 날짜별 값을 매핑하여 정렬된 데이터 생성
            const dateMap = {};
            strategyHistory.forEach(h => { dateMap[h.date] = h.return_rate; });
            
            const alignedData = globalDates.map(date => {
                const val = dateMap[date];
                return val !== undefined ? val : null; // 데이터가 없는 날짜는 null 처리
            });

            newDatasets.push({
                label: isAll ? '전체(ALL) %' : `${name} %`,
                data: alignedData,
                borderColor: getStrategyColor(name),
                backgroundColor: isAll ? 'rgba(77, 171, 247, 0.1)' : 'transparent',
                borderWidth: isAll ? 3 : 1.5,
                pointRadius: isAll ? 3 : 0,
                fill: isAll,
                tension: 0.3
            });
        });

        // 3. 벤치마크 데이터셋 추가
        if (benchmarks.KOSPI200 && benchmarks.KOSPI200.length > 0) {
            newDatasets.push({
                label: '벤치마크(KOSPI200) %',
                data: benchmarks.KOSPI200.map(b => b.return_rate),
                borderColor: '#ff922b',
                borderWidth: 2,
                borderDash: [5, 5],
                fill: false,
                pointRadius: 0,
                tension: 0.3
            });
        }
        if (benchmarks.KOSDAQ150 && benchmarks.KOSDAQ150.length > 0) {
            newDatasets.push({
                label: '벤치마크(KOSDAQ150) %',
                data: benchmarks.KOSDAQ150.map(b => b.return_rate),
                borderColor: '#8ce99a',
                borderWidth: 2,
                borderDash: [2, 2],
                fill: false,
                pointRadius: 0,
                tension: 0.3
            });
        }

        yieldChart.data.datasets = newDatasets;
        yieldChart.update();
    } catch (error) {
        console.error('[VirtualChart] 업데이트 실패:', error);
    }
}

// 페이지 로드 시 실행
document.addEventListener('DOMContentLoaded', initVirtualChart);