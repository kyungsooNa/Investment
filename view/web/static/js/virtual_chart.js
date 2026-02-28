/**
 * VirtualTradeManager 데이터를 이용한 수익률 차트 관리
 * - 멀티셀렉트 전략 필터링 지원
 * - 전략별 고정 색상 매핑
 */
let yieldChart = null;

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

// 캐시: ALL 데이터를 한 번만 fetch
let cachedAllChartData = null;

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
    await refreshVirtualChart(['ALL']);
}

/**
 * @param {string[]} selectedStrategies - 선택된 전략명 배열 (e.g. ['ALL'] 또는 ['수동매매', '모멘텀'])
 */
window.refreshVirtualChart = async function(selectedStrategies) {
    if (!selectedStrategies) selectedStrategies = ['ALL'];

    // 차트 객체가 없으면 초기화를 먼저 시도
    if (!yieldChart) {
        console.log('[VirtualChart] 차트가 아직 준비되지 않아 초기화를 시도합니다.');
        await initVirtualChart();
        return; // initVirtualChart 내부에서 refreshVirtualChart를 호출하므로 중복 방지
    }

    try {
        // 항상 ALL 데이터를 fetch (캐시 활용)
        if (!cachedAllChartData) {
            const response = await fetch('/api/virtual/chart/ALL');
            cachedAllChartData = await response.json();
        }

        const data = cachedAllChartData;
        if (!yieldChart || !data.histories) return;

        const allHistories = data.histories;
        const benchmarks = data.benchmarks || {};
        const isAll = selectedStrategies.includes('ALL');

        // 1. 기준 날짜 목록 (ALL 전략 기준)
        const refHistory = allHistories['ALL'] || Object.values(allHistories)[0] || [];
        const globalDates = refHistory.map(h => h.date);
        yieldChart.data.labels = globalDates.map(d => d.substring(5));

        const newDatasets = [];

        // 2. 색상 맵 초기화 — ALL fetch 시 전체 전략 순서대로 색상 할당
        const allStrategyNames = Object.keys(allHistories).filter(n => n !== 'ALL');
        allStrategyNames.forEach(name => getStrategyColor(name));

        // 3. 표시할 전략 결정
        const displayStrategies = isAll
            ? Object.keys(allHistories)
            : selectedStrategies.filter(s => allHistories[s]);

        // ALL이 선택되면 ALL 라인도 포함, 아니면 개별 전략만
        displayStrategies.forEach((name) => {
            const isAllLine = (name === 'ALL');
            const strategyHistory = allHistories[name];
            if (!strategyHistory) return;

            const dateMap = {};
            strategyHistory.forEach(h => { dateMap[h.date] = h.return_rate; });

            const alignedData = globalDates.map(date => {
                const val = dateMap[date];
                return val !== undefined ? val : null;
            });

            newDatasets.push({
                label: isAllLine ? '전체(ALL) %' : `${name} %`,
                data: alignedData,
                borderColor: getStrategyColor(name),
                backgroundColor: isAllLine ? 'rgba(77, 171, 247, 0.1)' : 'transparent',
                borderWidth: isAllLine ? 3 : 1.5,
                pointRadius: isAllLine ? 3 : 0,
                fill: isAllLine,
                tension: 0.3
            });
        });

        // 4. 벤치마크 데이터셋 추가
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

// 차트 캐시 무효화 (새 거래 기록 시 호출)
window.invalidateVirtualChartCache = function() {
    cachedAllChartData = null;
}

// 페이지 로드 시 실행
document.addEventListener('DOMContentLoaded', initVirtualChart);
