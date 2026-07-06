/**
 * VirtualTradeRepository 데이터를 이용한 전략별 수익률 미니 차트 관리
 * - 멀티셀렉트 전략 필터링 지원
 * - 전략별 고정 색상 매핑
 * - 각 전략 차트에 KOSPI200/KOSDAQ150 벤치마크 표시
 */
const yieldCharts = new Map();

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

const chartDataCache = new Map();
const chartDataPromiseCache = new Map();
const CHART_LIBRARY_WAIT_MS = 5000;
const CHART_LIBRARY_RETRY_MS = 50;

function getChartSelectionKey(selectedStrategies) {
    if (!selectedStrategies || selectedStrategies.includes('ALL')) {
        return 'ALL';
    }
    return [...selectedStrategies].sort().join('|');
}

function buildChartDataUrl(selectedStrategies) {
    if (!selectedStrategies || selectedStrategies.includes('ALL')) {
        return '/api/virtual/chart/ALL';
    }
    const sortedStrategies = [...selectedStrategies].sort();
    const params = new URLSearchParams({
        strategies: sortedStrategies.join(',')
    });
    return `/api/virtual/chart/ALL?${params.toString()}`;
}

async function getChartData(selectedStrategies) {
    const cacheKey = getChartSelectionKey(selectedStrategies);
    if (chartDataCache.has(cacheKey)) {
        return chartDataCache.get(cacheKey);
    }
    if (!chartDataPromiseCache.has(cacheKey)) {
        const pendingRequest = fetch(buildChartDataUrl(selectedStrategies))
            .then(async response => {
                if (!response.ok) {
                    throw new Error(`Chart API ${response.status}`);
                }
                return response.json();
            })
            .then(data => {
                chartDataCache.set(cacheKey, data);
                chartDataPromiseCache.delete(cacheKey);
                return data;
            })
            .catch(error => {
                chartDataPromiseCache.delete(cacheKey);
                throw error;
            });
        chartDataPromiseCache.set(cacheKey, pendingRequest);
    }
    return chartDataPromiseCache.get(cacheKey);
}

function isChartLibraryReady() {
    return typeof window.Chart === 'function';
}

async function waitForChartLibrary(timeoutMs = CHART_LIBRARY_WAIT_MS) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        if (isChartLibraryReady()) {
            return;
        }
        await new Promise(resolve => setTimeout(resolve, CHART_LIBRARY_RETRY_MS));
    }
    throw new Error('Chart.js library not loaded');
}

function unregisterVirtualChart(chart) {
    if (!window.currentCharts) return;
    window.currentCharts = window.currentCharts.filter(item => item !== chart);
}

function destroyVirtualCharts() {
    yieldCharts.forEach(chart => {
        unregisterVirtualChart(chart);
        try {
            chart.destroy();
        } catch (_) {}
    });
    yieldCharts.clear();
}

function setVirtualChartMessage(message) {
    const grid = document.getElementById('virtualYieldChartGrid');
    if (!grid) return;
    destroyVirtualCharts();
    grid.innerHTML = '';
    const empty = document.createElement('div');
    empty.className = 'virtual-chart-empty';
    empty.textContent = message;
    grid.appendChild(empty);
}

function formatVirtualChartTradeCounts(counts) {
    const buyCount = Number(counts?.buy ?? 0);
    const sellCount = Number(counts?.sell ?? 0);
    return `기간 매수 ${buyCount} / 매도 ${sellCount}`;
}

function createVirtualChartCard(strategyName, tradeCounts) {
    const card = document.createElement('div');
    card.className = 'virtual-mini-chart-card';

    const header = document.createElement('div');
    header.className = 'virtual-mini-chart-header';

    const title = document.createElement('h3');
    title.textContent = strategyName === 'ALL' ? '전체(ALL)' : strategyName;

    const legend = document.createElement('span');
    legend.textContent = formatVirtualChartTradeCounts(tradeCounts);

    header.appendChild(title);
    header.appendChild(legend);

    const canvasWrap = document.createElement('div');
    canvasWrap.className = 'virtual-mini-chart-canvas';

    const canvas = document.createElement('canvas');
    canvas.id = `virtualYieldChart-${yieldCharts.size}`;
    canvasWrap.appendChild(canvas);

    card.appendChild(header);
    card.appendChild(canvasWrap);
    return { card, canvas };
}

function buildBaseChartOptions() {
    return {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: {
            mode: 'index',
            intersect: false
        },
        scales: {
            x: {
                grid: {
                    display: true,
                    drawOnChartArea: true,
                    color: 'rgba(255,255,255,0.12)',
                    lineWidth: 1,
                    drawTicks: true
                },
                ticks: {
                    color: '#868e96',
                    maxRotation: 0,
                    autoSkip: true,
                    maxTicksLimit: 8,
                    font: { size: 10 }
                }
            },
            y: {
                grid: {
                    display: true,
                    drawOnChartArea: true,
                    color: 'rgba(255,255,255,0.12)',
                    lineWidth: 1,
                    drawTicks: true
                },
                ticks: {
                    color: '#868e96',
                    callback: value => value.toFixed(1) + '%',
                    maxTicksLimit: 7
                }
            }
        },
        plugins: {
            legend: {
                display: true,
                position: 'top',
                labels: {
                    color: '#a0a0b0',
                    usePointStyle: true,
                    pointStyle: 'line',
                    boxWidth: 20,
                    font: { size: 10 },
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
    };
}

/**
 * 전일/전주 기준 annotation 생성
 * @param {string[]} labels - MM-DD 포맷 라벨 배열
 * @param {string[]} activeDates - YYYY-MM-DD 원본 날짜 배열
 * @param {Array<{date: string, return_rate: number}>} strategyHistory
 */
function buildAnnotations(labels, activeDates, strategyHistory) {
    const annotations = {
        zeroLine: {
            type: 'line',
            yMin: 0,
            yMax: 0,
            borderColor: 'rgba(255, 255, 255, 0.6)',
            borderWidth: 1.5,
            label: {
                display: true,
                content: '0%',
                position: 'end',
                backgroundColor: 'rgba(255, 255, 255, 0.15)',
                color: '#e9ecef',
                font: { size: 9, weight: 'bold' },
                padding: { top: 1, bottom: 1, left: 4, right: 4 },
                borderRadius: 3
            }
        }
    };
    if (activeDates.length < 2) return annotations;

    const todayIdx = activeDates.length - 1;
    const todayDate = activeDates[todayIdx];

    const prevDayIdx = todayIdx - 1;
    const todayMs = new Date(todayDate).getTime();
    let prevWeekIdx = -1;
    for (let i = todayIdx - 1; i >= 0; i--) {
        const diff = todayMs - new Date(activeDates[i]).getTime();
        if (diff >= 6 * 24 * 60 * 60 * 1000) {
            prevWeekIdx = i;
            break;
        }
    }

    const refDateMap = {};
    strategyHistory.forEach(h => { refDateMap[h.date] = h.return_rate; });

    const todayVal = refDateMap[todayDate];
    if (todayVal === undefined) return annotations;

    if (prevDayIdx >= 0) {
        const prevDate = activeDates[prevDayIdx];
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
                    content: `전일 ${sign}${change.toFixed(2)}%`,
                    position: 'start',
                    backgroundColor: 'rgba(255, 183, 77, 0.15)',
                    color: '#ffb74d',
                    font: { size: 9, weight: 'bold' },
                    padding: { top: 2, bottom: 2, left: 5, right: 5 },
                    borderRadius: 3
                }
            };
        }
    }

    if (prevWeekIdx >= 0) {
        const prevWeekDate = activeDates[prevWeekIdx];
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
                    content: `전주 ${sign}${change.toFixed(2)}%`,
                    position: 'start',
                    backgroundColor: 'rgba(129, 199, 132, 0.15)',
                    color: '#81c784',
                    font: { size: 9, weight: 'bold' },
                    padding: { top: 2, bottom: 2, left: 5, right: 5 },
                    borderRadius: 3,
                    yAdjust: -18
                }
            };
        }
    }

    return annotations;
}

function alignHistoryToDates(history, activeDates) {
    const dateMap = {};
    history.forEach(h => { dateMap[h.date] = h.return_rate; });
    return activeDates.map(date => {
        const val = dateMap[date];
        return val !== undefined ? val : null;
    });
}

function buildStrategyDatasets(strategyName, strategyHistory, benchmarks, activeDates) {
    const isAllLine = strategyName === 'ALL';
    const isSparse = activeDates.length <= 2;
    const datasets = [{
        label: isAllLine ? '전체(ALL) %' : `${strategyName} %`,
        data: alignHistoryToDates(strategyHistory, activeDates),
        borderColor: getStrategyColor(strategyName),
        backgroundColor: isAllLine ? 'rgba(77, 171, 247, 0.1)' : 'transparent',
        borderWidth: isAllLine ? 3 : 2,
        pointRadius: isSparse ? 4 : (isAllLine ? 3 : 0),
        fill: isAllLine,
        tension: 0.3
    }];

    if (benchmarks.KOSPI200 && benchmarks.KOSPI200.length > 0) {
        datasets.push({
            label: 'KOSPI200 %',
            data: alignHistoryToDates(benchmarks.KOSPI200, activeDates),
            borderColor: '#ff922b',
            borderWidth: 1.8,
            borderDash: [5, 5],
            fill: false,
            pointRadius: 0,
            tension: 0.3
        });
    }
    if (benchmarks.KOSDAQ150 && benchmarks.KOSDAQ150.length > 0) {
        datasets.push({
            label: 'KOSDAQ150 %',
            data: alignHistoryToDates(benchmarks.KOSDAQ150, activeDates),
            borderColor: '#8ce99a',
            borderWidth: 1.8,
            borderDash: [2, 2],
            fill: false,
            pointRadius: 0,
            tension: 0.3
        });
    }

    return datasets;
}

function getDisplayStrategies(selectedStrategies, allHistories) {
    const isAll = !selectedStrategies || selectedStrategies.includes('ALL');
    if (isAll) {
        return Object.keys(allHistories).filter(name => name !== 'ALL' && allHistories[name]?.length > 0);
    }
    return selectedStrategies.filter(name => allHistories[name]?.length > 0);
}

function renderMiniChart(grid, strategyName, strategyHistory, benchmarks, tradeCounts) {
    const activeDates = strategyHistory.map(h => h.date).filter(Boolean);
    if (activeDates.length === 0) return;

    const labels = activeDates.map(d => d.substring(5));
    const { card, canvas } = createVirtualChartCard(strategyName, tradeCounts);
    grid.appendChild(card);

    const chart = new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: {
            labels,
            datasets: buildStrategyDatasets(strategyName, strategyHistory, benchmarks, activeDates)
        },
        options: buildBaseChartOptions()
    });

    chart.options.plugins.annotation.annotations = buildAnnotations(labels, activeDates, strategyHistory);
    chart.update();

    yieldCharts.set(strategyName, chart);
    window.currentCharts = window.currentCharts || [];
    window.currentCharts.push(chart);
}

window.refreshVirtualChart = async function(selectedStrategies) {
    if (!selectedStrategies) selectedStrategies = ['ALL'];

    const grid = document.getElementById('virtualYieldChartGrid');
    if (!grid) return;

    try {
        await waitForChartLibrary();
        const data = await getChartData(selectedStrategies);
        if (!data.histories || Object.keys(data.histories).length === 0) {
            setVirtualChartMessage('표시할 차트 데이터가 없습니다.');
            return;
        }

        const allHistories = data.histories;
        const benchmarks = data.benchmarks || {};
        const chartCounts = data.chart_counts || {};
        const allStrategyNames = Object.keys(allHistories).filter(name => name !== 'ALL');
        allStrategyNames.forEach(name => getStrategyColor(name));

        const displayStrategies = getDisplayStrategies(selectedStrategies, allHistories);
        if (displayStrategies.length === 0) {
            setVirtualChartMessage('표시할 전략 차트가 없습니다.');
            return;
        }

        destroyVirtualCharts();
        grid.innerHTML = '';
        displayStrategies.forEach(name => {
            renderMiniChart(grid, name, allHistories[name], benchmarks, chartCounts[name]);
        });
    } catch (error) {
        console.error('[VirtualChart] 업데이트 실패:', error);
        setVirtualChartMessage('차트 업데이트 실패');
    }
};

window.invalidateVirtualChartCache = function() {
    chartDataCache.clear();
    chartDataPromiseCache.clear();
};

window.prefetchVirtualChart = function(selectedStrategies) {
    return getChartData(selectedStrategies).catch(error => {
        console.error('[VirtualChart] prefetch failed:', error);
    });
};

document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('virtualYieldChartGrid')) {
        waitForChartLibrary().catch(error => {
            console.error('[VirtualChart] init failed:', error);
        });
    }
});

/* ── Pjax 재방문 시 차트 재초기화 ── */
document.addEventListener('pjax:ready', (e) => {
    if (e.detail?.path !== '/virtual') return;
    destroyVirtualCharts();
    window.invalidateVirtualChartCache();
});
