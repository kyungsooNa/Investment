/**
 * VirtualTradeManager 데이터를 이용한 수익률 차트 관리
 * - 멀티셀렉트 전략 필터링 지원
 * - 전략별 고정 색상 매핑
 * - 전일/전주 기준선 annotation
 */
let yieldChart = null;
let chartInitPromise = null;

const STRATEGY_COLORS_PALETTE = [
    '#ff6b6b', '#51cf66', '#fcc419', '#ae3ec9',
    '#1098ad', '#f06595', '#845ef7', '#339af0', '#20c997'
];
const ALL_COLOR = '#4dabf7';

const strategyColorMap = {};
function getStrategyColor(name) {
    if (name === 'ALL') return ALL_COLOR;
    if (!strategyColorMap[name]) {
        const idx = Object.keys(strategyColorMap).length;
        strategyColorMap[name] = STRATEGY_COLORS_PALETTE[idx % STRATEGY_COLORS_PALETTE.length];
    }
    return strategyColorMap[name];
}

let cachedAllChartData = null;

async function initVirtualChart() {
    const canvas = document.getElementById('virtualYieldChart');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    yieldChart = new Chart(ctx, {
        type: 'line',
        data: { labels: [], datasets: [] },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    grid: {
                        display: true,
                        drawOnChartArea: true,
                        color: 'rgba(255,255,255,0.15)',
                        lineWidth: 1,
                        drawTicks: true
                    },
                    ticks: {
                        color: '#868e96',
                        maxRotation: 45,
                        autoSkip: false,
                        font: { size: 10 }
                    }
                },
                y: {
                    grid: {
                        display: true,
                        drawOnChartArea: true,
                        color: 'rgba(255,255,255,0.15)',
                        lineWidth: 1,
                        drawTicks: true
                    },
                    ticks: {
                        color: '#868e96',
                        callback: value => value.toFixed(1) + '%',
                        stepSize: 5
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
                tooltip: { mode: 'index', intersect: false },
                annotation: { annotations: {} }
            }
        }
    });
}

/**
 * 전일/전주 기준 annotation 생성
 * @param {string[]} labels - MM-DD 포맷 라벨 배열
 * @param {string[]} globalDates - YYYY-MM-DD 원본 날짜 배열
 * @param {object} allHistories - { strategyName: [{date, return_rate}, ...] }
 * @param {string[]} displayStrategies - 현재 표시 중인 전략 목록
 */
function buildAnnotations(labels, globalDates, allHistories, displayStrategies) {
    const annotations = {};
    if (globalDates.length < 2) return annotations;

    const todayIdx = globalDates.length - 1;
    const todayDate = globalDates[todayIdx];

    // 전일: 오늘 바로 이전 데이터 포인트
    const prevDayIdx = todayIdx - 1;
    // 전주: 7일 이상 전인 가장 가까운 데이터 포인트
    const todayMs = new Date(todayDate).getTime();
    let prevWeekIdx = -1;
    for (let i = todayIdx - 1; i >= 0; i--) {
        const diff = todayMs - new Date(globalDates[i]).getTime();
        if (diff >= 6 * 24 * 60 * 60 * 1000) { // 6일 이상 (전주)
            prevWeekIdx = i;
            break;
        }
    }

    // ALL 전략 기준 수익률 변동 계산
    const refName = displayStrategies.includes('ALL') ? 'ALL' : displayStrategies[0];
    const refHistory = allHistories[refName] || [];
    const refDateMap = {};
    refHistory.forEach(h => { refDateMap[h.date] = h.return_rate; });

    const todayVal = refDateMap[todayDate];
    if (todayVal === undefined) return annotations;

    // 전일 annotation (세로선 + 라벨)
    if (prevDayIdx >= 0) {
        const prevDate = globalDates[prevDayIdx];
        const prevVal = refDateMap[prevDate];
        if (prevVal !== undefined) {
            const change = todayVal - prevVal;
            const sign = change >= 0 ? '+' : '';
            annotations.prevDayLine = {
                type: 'line',
                xMin: labels[prevDayIdx],
                xMax: labels[prevDayIdx],
                borderColor: 'rgba(255, 183, 77, 0.6)',
                borderWidth: 2,
                label: {
                    display: true,
                    content: `전일 (${prevDate.substring(5)}) ${sign}${change.toFixed(2)}%`,
                    position: 'start',
                    backgroundColor: 'rgba(255, 183, 77, 0.15)',
                    color: '#ffb74d',
                    font: { size: 10, weight: 'bold' },
                    padding: { top: 3, bottom: 3, left: 6, right: 6 },
                    borderRadius: 3
                }
            };
        }
    }

    // 전주 annotation (세로선 + 라벨)
    if (prevWeekIdx >= 0) {
        const prevWeekDate = globalDates[prevWeekIdx];
        const prevWeekVal = refDateMap[prevWeekDate];
        if (prevWeekVal !== undefined) {
            const change = todayVal - prevWeekVal;
            const sign = change >= 0 ? '+' : '';
            annotations.prevWeekLine = {
                type: 'line',
                xMin: labels[prevWeekIdx],
                xMax: labels[prevWeekIdx],
                borderColor: 'rgba(129, 199, 132, 0.6)',
                borderWidth: 2,
                label: {
                    display: true,
                    content: `전주 (${prevWeekDate.substring(5)}) ${sign}${change.toFixed(2)}%`,
                    position: 'start',
                    backgroundColor: 'rgba(129, 199, 132, 0.15)',
                    color: '#81c784',
                    font: { size: 10, weight: 'bold' },
                    padding: { top: 3, bottom: 3, left: 6, right: 6 },
                    borderRadius: 3,
                    yAdjust: -20
                }
            };
        }
    }

    // 0% 기준 수평선
    annotations.zeroLine = {
        type: 'line',
        yMin: 0,
        yMax: 0,
        borderColor: 'rgba(255, 255, 255, 0.25)',
        borderWidth: 1,
        borderDash: [3, 3]
    };

    return annotations;
}

window.refreshVirtualChart = async function(selectedStrategies) {
    if (!selectedStrategies) selectedStrategies = ['ALL'];

    if (!yieldChart) {
        if (!chartInitPromise) {
            chartInitPromise = initVirtualChart();
        }
        await chartInitPromise;
        if (!yieldChart) return;
    }

    try {
        if (!cachedAllChartData) {
            const response = await fetch('/api/virtual/chart/ALL');
            cachedAllChartData = await response.json();
        }

        const data = cachedAllChartData;
        if (!data.histories || Object.keys(data.histories).length === 0) return;

        const allHistories = data.histories;
        const benchmarks = data.benchmarks || {};
        const isAll = selectedStrategies.includes('ALL');

        // 색상 맵 초기화 — 전체 전략 순서대로 색상 할당 (필터 전에 호출)
        const allStrategyNames = Object.keys(allHistories).filter(n => n !== 'ALL');
        allStrategyNames.forEach(name => getStrategyColor(name));

        const displayStrategies = isAll
            ? Object.keys(allHistories)
            : selectedStrategies.filter(s => allHistories[s]);

        // X축 날짜: 선택된 전략들의 실제 날짜 합집합 (빈 날짜 제거)
        let activeDates;
        if (isAll) {
            const refHistory = allHistories['ALL'] || Object.values(allHistories)[0] || [];
            activeDates = refHistory.map(h => h.date);
        } else {
            const dateSet = new Set();
            displayStrategies.forEach(name => {
                (allHistories[name] || []).forEach(h => dateSet.add(h.date));
            });
            activeDates = [...dateSet].sort();
        }
        if (activeDates.length === 0) return;

        const labels = activeDates.map(d => d.substring(5));
        yieldChart.data.labels = labels;

        const newDatasets = [];
        const isSparse = activeDates.length <= 2;

        displayStrategies.forEach((name) => {
            const isAllLine = (name === 'ALL');
            const strategyHistory = allHistories[name];
            if (!strategyHistory) return;

            const dateMap = {};
            strategyHistory.forEach(h => { dateMap[h.date] = h.return_rate; });

            const alignedData = activeDates.map(date => {
                const val = dateMap[date];
                return val !== undefined ? val : null;
            });

            newDatasets.push({
                label: isAllLine ? '전체(ALL) %' : `${name} %`,
                data: alignedData,
                borderColor: getStrategyColor(name),
                backgroundColor: isAllLine ? 'rgba(77, 171, 247, 0.1)' : 'transparent',
                borderWidth: isAllLine ? 3 : 1.5,
                pointRadius: isSparse ? 4 : (isAllLine ? 3 : 0),
                fill: isAllLine,
                tension: 0.3
            });
        });

        // 벤치마크를 activeDates에 맞춰 align
        function alignBenchmark(bmData) {
            const bmMap = {};
            bmData.forEach(b => { bmMap[b.date] = b.return_rate; });
            return activeDates.map(date => {
                const val = bmMap[date];
                return val !== undefined ? val : null;
            });
        }

        if (benchmarks.KOSPI200 && benchmarks.KOSPI200.length > 0) {
            newDatasets.push({
                label: '벤치마크(KOSPI200) %',
                data: alignBenchmark(benchmarks.KOSPI200),
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
                data: alignBenchmark(benchmarks.KOSDAQ150),
                borderColor: '#8ce99a',
                borderWidth: 2,
                borderDash: [2, 2],
                fill: false,
                pointRadius: 0,
                tension: 0.3
            });
        }

        yieldChart.data.datasets = newDatasets;

        // annotation 업데이트
        const annotations = buildAnnotations(labels, activeDates, allHistories, displayStrategies);
        yieldChart.options.plugins.annotation.annotations = annotations;

        yieldChart.update();
    } catch (error) {
        console.error('[VirtualChart] 업데이트 실패:', error);
    }
}

window.invalidateVirtualChartCache = function() {
    cachedAllChartData = null;
}

document.addEventListener('DOMContentLoaded', initVirtualChart);
