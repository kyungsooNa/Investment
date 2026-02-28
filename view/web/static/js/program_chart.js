/**
 * 프로그램매매 차트 관리 (program_chart.js)
 * - app.js에서 차트 초기화/업데이트 로직 분리
 * - 멀티셀렉트 필터링 지원
 * - 종목별 고정 색상 매핑
 */
let ptChart = null;
const PT_CHART_COLORS = ['#4BC0C0', '#FFB347', '#FF6384', '#36A2EB', '#9966FF', '#F2B1D0'];

/**
 * 데이터 집계 함수 (차트/테이블 공용)
 * @param {string} code - 종목코드
 * @param {object} chartData - ptChartData 객체
 * @param {number} timeUnit - 시간 단위 (분)
 */
function getAggregatedPtData(code, chartData, timeUnit) {
    if (!chartData[code]) return { value: [], volume: [] };

    const rawValue = chartData[code].valueData;
    const rawVolume = chartData[code].volumeData;

    if (timeUnit === 1) return { value: rawValue, volume: rawVolume };

    const aggValue = [];
    const aggVolume = [];
    const intervalMs = timeUnit * 60 * 1000;

    const lastItem = rawValue[rawValue.length - 1];
    const lastBucketStartTime = lastItem ? Math.floor(lastItem.x / intervalMs) * intervalMs : 0;

    let currentBucketStart = -1;
    let currentValItem = null;
    let currentVolItem = null;

    let i = 0;
    for (; i < rawValue.length; i++) {
        const item = rawValue[i];
        if (item.x >= lastBucketStartTime) break;
        const volItem = rawVolume[i];
        const bucketStart = Math.floor(item.x / intervalMs) * intervalMs;

        if (bucketStart !== currentBucketStart) {
            if (currentValItem) {
                aggValue.push(currentValItem);
                aggVolume.push(currentVolItem);
            }
            currentBucketStart = bucketStart;
        }
        currentValItem = { ...item, x: bucketStart };
        currentVolItem = { ...volItem, x: bucketStart };
    }

    if (currentValItem) {
        aggValue.push(currentValItem);
        aggVolume.push(currentVolItem);
    }

    // 마지막 버킷: 시작점과 끝점만 표시
    if (i < rawValue.length) {
        const firstIdx = i;
        const lastIdx = rawValue.length - 1;

        const firstVal = { ...rawValue[firstIdx], x: lastBucketStartTime };
        const firstVol = { ...rawVolume[firstIdx], x: lastBucketStartTime };
        aggValue.push(firstVal);
        aggVolume.push(firstVol);

        if (rawValue[lastIdx].x > lastBucketStartTime) {
            aggValue.push(rawValue[lastIdx]);
            aggVolume.push(rawVolume[lastIdx]);
        }
    }

    return { value: aggValue, volume: aggVolume };
}

/**
 * 차트 초기화
 */
window.initProgramChart = function(timeUnit) {
    if (ptChart) return;
    const canvas = document.getElementById('pt-chart');
    if (!canvas) return;

    const now = new Date();
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 9, 0, 0);
    const end = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 15, 30, 0);

    let maxTime = now.getTime();
    if (now > end) maxTime = end.getTime();
    if (maxTime < start.getTime()) maxTime = start.getTime();

    const ctx = canvas.getContext('2d');
    ptChart = new Chart(ctx, {
        type: 'line',
        data: { datasets: [] },
        plugins: [{
            id: 'chartBackground',
            beforeDraw: (chart) => {
                if (!chart.chartArea) return;
                const { ctx, chartArea: { left, width }, scales } = chart;

                ['y', 'y1'].forEach(axisId => {
                    const scale = scales[axisId];
                    if (!scale) return;

                    const top = scale.top;
                    const bottom = scale.bottom;
                    const zeroPixel = scale.getPixelForValue(0);

                    ctx.save();
                    ctx.beginPath();
                    ctx.rect(left, top, width, bottom - top);
                    ctx.clip();

                    if (zeroPixel > top) {
                        ctx.fillStyle = 'rgba(255, 99, 132, 0.05)';
                        const rectBottom = Math.min(zeroPixel, bottom);
                        ctx.fillRect(left, top, width, rectBottom - top);
                    }

                    if (zeroPixel < bottom) {
                        ctx.fillStyle = 'rgba(54, 162, 235, 0.05)';
                        const rectTop = Math.max(zeroPixel, top);
                        ctx.fillRect(left, rectTop, width, bottom - rectTop);
                    }

                    ctx.restore();
                });
            }
        }, {
            id: 'splitLine',
            afterDraw: (chart) => {
                const { ctx, chartArea: { left, right }, scales: { y_spacer } } = chart;
                if (y_spacer) {
                    const centerY = (y_spacer.top + y_spacer.bottom) / 2;
                    ctx.save();
                    ctx.beginPath();
                    ctx.moveTo(left, centerY);
                    ctx.lineTo(right, centerY);
                    ctx.lineWidth = 4;
                    ctx.strokeStyle = 'rgba(200, 200, 200, 1.0)';
                    ctx.stroke();
                    ctx.restore();
                }
            }
        }],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                mode: 'index',
                intersect: false,
            },
            scales: {
                x: {
                    type: 'linear',
                    position: 'bottom',
                    min: start.getTime(),
                    max: maxTime,
                    ticks: {
                        stepSize: (timeUnit || 1) * 60 * 1000,
                        callback: function(value) {
                            const d = new Date(value);
                            return d.toTimeString().slice(0, 5);
                        }
                    },
                    title: { display: false }
                },
                y: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    stack: 'demo',
                    stackWeight: 2,
                    title: { display: true, text: '순매수대금' },
                    grid: {
                        drawOnChartArea: true,
                        color: (context) => {
                            if (context.tick.value === 0) return 'rgba(200, 200, 200, 0.5)';
                            return 'rgba(255, 255, 255, 0.1)';
                        }
                    },
                    ticks: { callback: function(value) { return formatTradingValue(value); } }
                },
                y_spacer: {
                    type: 'linear',
                    display: false,
                    position: 'left',
                    stack: 'demo',
                    stackWeight: 0.2,
                    grid: { drawOnChartArea: false }
                },
                y1: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    stack: 'demo',
                    stackWeight: 1,
                    title: { display: true, text: '순매수량' },
                    grid: {
                        drawOnChartArea: true,
                        color: (context) => {
                            if (context.tick.value === 0) return 'rgba(200, 200, 200, 0.5)';
                            return 'rgba(255, 255, 255, 0.1)';
                        }
                    },
                    ticks: { callback: function(value) { return value.toLocaleString(); } }
                }
            },
            plugins: {
                legend: {
                    position: 'top',
                    labels: {
                        usePointStyle: true,
                        generateLabels: (chart) => {
                            const labels = Chart.defaults.plugins.legend.labels.generateLabels(chart);
                            labels.forEach(label => {
                                const dataset = chart.data.datasets[label.datasetIndex];
                                label.pointStyle = (dataset.type === 'line') ? 'line' : 'rect';
                            });
                            return labels;
                        }
                    }
                },
                tooltip: {
                    callbacks: {
                        title: function(tooltipItems) {
                            const d = new Date(tooltipItems[0].parsed.x);
                            return d.toTimeString().slice(0, 5);
                        },
                        footer: function(tooltipItems) {
                            if (tooltipItems.length > 0) {
                                const item = tooltipItems[0];
                                const price = item.raw.price;
                                if (price) return `주가: ${parseInt(price).toLocaleString()}원`;
                            }
                            return '';
                        }
                    }
                }
            }
        }
    });
};

/**
 * 차트 업데이트
 * @param {object} chartData - ptChartData
 * @param {Set} subscribedCodes - ptSubscribedCodes
 * @param {Set} filterCodes - 필터링할 종목코드 Set (비어있으면 전체 표시)
 * @param {object} codeNameMap - ptCodeNameMap
 * @param {number} timeUnit - 시간 단위 (분)
 */
window.updateProgramChart = function(chartData, subscribedCodes, filterCodes, codeNameMap, timeUnit) {
    if (!ptChart) return;

    // X축 max 갱신
    const now = new Date();
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 9, 0, 0);
    const end = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 15, 30, 0);

    let maxTime = now.getTime();
    if (now > end) maxTime = end.getTime();
    if (maxTime < start.getTime()) maxTime = start.getTime();

    ptChart.options.scales.x.ticks.stepSize = timeUnit * 60 * 1000;
    if (ptChart.options.scales.x.max < maxTime) ptChart.options.scales.x.max = maxTime;

    const datasets = [];
    let colorIndex = 0;
    const showAll = filterCodes.size === 0;

    for (const code of subscribedCodes) {
        if (!showAll && !filterCodes.has(code)) {
            colorIndex++;
            continue;
        }

        if (chartData[code]) {
            const aggData = getAggregatedPtData(code, chartData, timeUnit);
            const color = PT_CHART_COLORS[colorIndex % PT_CHART_COLORS.length];

            // 대금 (Line + Area)
            datasets.push({
                type: 'line',
                label: `${codeNameMap[code] || code} (대금)`,
                data: aggData.value,
                backgroundColor: color + '20',
                borderColor: color,
                borderWidth: 2,
                pointRadius: 0,
                fill: true,
                yAxisID: 'y'
            });

            // 수량 (Line + Area)
            datasets.push({
                type: 'line',
                label: `${codeNameMap[code] || code} (수량)`,
                data: aggData.volume,
                borderColor: color,
                backgroundColor: color + '20',
                borderWidth: 2,
                pointRadius: 0,
                fill: true,
                pointHoverRadius: 4,
                tension: 0.1,
                yAxisID: 'y1'
            });

            colorIndex++;
        }
    }

    ptChart.data.datasets = datasets;
    ptChart.update();
};

/**
 * 차트 시간 단위 변경 시 X축 stepSize만 업데이트
 */
window.setPtChartTimeUnit = function(minutes) {
    if (!ptChart) return;
    ptChart.options.scales.x.ticks.stepSize = minutes * 60 * 1000;
};

/**
 * 차트 인스턴스 파괴 (구독 전체 중지 시)
 */
window.destroyProgramChart = function() {
    if (ptChart) {
        ptChart.destroy();
        ptChart = null;
    }
};

/**
 * 테이블용 집계 데이터 접근 (app.js에서 호출)
 */
window.getAggregatedPtData = function(code, chartData, timeUnit) {
    return getAggregatedPtData(code, chartData, timeUnit);
};
