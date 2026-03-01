// ==========================================
// 주식 캔들 차트 렌더링 (candle_chart.js)
// app.js에서 분리하여 재사용성 향상
// ==========================================

// 차트 인스턴스 (재사용 및 파괴용)
let stockChartInstance = null;

// 차트 데이터 캐싱 및 관리용 전역 변수
let g_chartRawData = null;
let g_chartIndicators = null;
let g_chartCode = null;

async function loadAndRenderStockChart(code) {
    const chartCard = document.getElementById('stock-chart-card');
    if (!chartCard) return;

    // 차트 컨트롤(기간 버튼)이 없으면 동적으로 추가
    // [수정] index.html에 추가된 'chart-controls-area'를 우선 사용
    const controlsArea = document.getElementById('chart-controls-area');

    if (controlsArea && !document.getElementById('chart-controls')) {
        const controls = document.createElement('div');
        controls.id = 'chart-controls';
        controls.className = 'chart-controls';
        controls.innerHTML = `
            <button class="btn-xs active" onclick="changeChartPeriod('3M', this)">3개월</button>
            <button class="btn-xs" onclick="changeChartPeriod('6M', this)">6개월</button>
            <button class="btn-xs" onclick="changeChartPeriod('1Y', this)">1년</button>
        `;
        controlsArea.appendChild(controls);

        // 버튼 스타일 추가
        if (!document.getElementById('chart-btn-style')) {
            const style = document.createElement('style');
            style.id = 'chart-btn-style';
            style.innerHTML = `
                .btn-xs { padding: 4px 8px; font-size: 12px; margin-left: 4px; background: var(--bg-primary); border: 1px solid var(--border); color: var(--text-secondary); border-radius: 4px; cursor: pointer; }
                .btn-xs.active { background: var(--accent); color: white; border-color: var(--accent); }
                .btn-xs:hover { background: var(--bg-card); }
            `;
            document.head.appendChild(style);
        }
    } else if (!controlsArea && !document.getElementById('chart-controls')) {
        // Fallback: chart-controls-area가 없는 경우 (기존 방식 유지)
        const controls = document.createElement('div');
        controls.id = 'chart-controls';
        controls.className = 'chart-controls';
        controls.style.cssText = 'text-align: right; margin-bottom: 10px;';
        controls.innerHTML = `
            <button class="btn-xs active" onclick="changeChartPeriod('3M', this)">3개월</button>
            <button class="btn-xs" onclick="changeChartPeriod('6M', this)">6개월</button>
            <button class="btn-xs" onclick="changeChartPeriod('1Y', this)">1년</button>
        `;
        const canvas = document.getElementById('stockChart');
        if(canvas) canvas.parentNode.insertBefore(controls, canvas);

        if (!document.getElementById('chart-btn-style')) {
            const style = document.createElement('style');
            style.id = 'chart-btn-style';
            style.innerHTML = `
                .btn-xs { padding: 4px 8px; font-size: 12px; margin-left: 4px; background: var(--bg-primary); border: 1px solid var(--border); color: var(--text-secondary); border-radius: 4px; cursor: pointer; }
                .btn-xs.active { background: var(--accent); color: white; border-color: var(--accent); }
                .btn-xs:hover { background: var(--bg-card); }
            `;
            document.head.appendChild(style);
        }
    }

    try {
        // [최적화] 7개 API 호출 → 1개 통합 API로 변경 (OHLCV + 지표 한번에 조회)
        const res = await fetch(`/api/chart/${code}?period=D&indicators=true`);
        const json = await res.json();

        if (json.rt_cd !== "0" || !json.data || !json.data.ohlcv || json.data.ohlcv.length === 0) {
            chartCard.style.display = 'none';
            return;
        }

        // 전역 변수에 데이터 저장
        g_chartRawData = json.data.ohlcv;
        g_chartIndicators = json.data.indicators || {};
        g_chartCode = code;

        chartCard.style.display = 'block';

        // 기본 3개월 렌더링
        renderStockChart('3M');

    } catch (e) {
        console.error("Chart rendering failed:", e);
        chartCard.style.display = 'none';
    }
}

function changeChartPeriod(period, btn) {
    if (btn) {
        document.querySelectorAll('#chart-controls .btn-xs').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
    }
    renderStockChart(period);
}

function renderStockChart(period) {
    if (!g_chartRawData) return;

    // 1. 데이터 슬라이싱 (기간 필터링)
    let sliceCount = 0;
    if (period === '1M') sliceCount = 22;      // 약 1개월 (영업일 기준)
    else if (period === '3M') sliceCount = 66; // 약 3개월
    else if (period === '6M') sliceCount = 130;// 약 6개월
    else if (period === '1Y') sliceCount = 260;// 약 1년
    else sliceCount = g_chartRawData.length;   // 전체

    const startIndex = Math.max(0, g_chartRawData.length - sliceCount);
    const slicedRaw = g_chartRawData.slice(startIndex);

    // 날짜 포맷팅 (YYYY-MM-DD)
    const formatDate = (str) => str.substring(0, 4) + '-' + str.substring(4, 6) + '-' + str.substring(6, 8);
    const labels = slicedRaw.map(d => formatDate(d.date));

    // 2. 데이터 매핑 (중요: x값은 0부터 시작하는 인덱스 사용)
    const candles = slicedRaw.map((d, i) => ({
        x: i, // Index 사용 (차트 깨짐 방지)
        o: d.open, h: d.high, l: d.low, c: d.close
    }));
    const volumes = slicedRaw.map((d, i) => ({ x: i, y: d.volume }));

    // 지표 데이터 매핑 (인덱스 기준)
    const sliceIndicator = (data) => data.slice(startIndex).map((d, i) => ({ x: i, y: d.ma }));
    const ma5 = sliceIndicator(g_chartIndicators.ma5);
    const ma10 = sliceIndicator(g_chartIndicators.ma10); // [추가]
    const ma20 = sliceIndicator(g_chartIndicators.ma20);
    const ma60 = sliceIndicator(g_chartIndicators.ma60);
    const ma120 = sliceIndicator(g_chartIndicators.ma120); // [추가]

    const bbUpper = g_chartIndicators.bb.slice(startIndex).map((d, i) => ({ x: i, y: d.upper }));
    const bbMiddle = g_chartIndicators.bb.slice(startIndex).map((d, i) => ({ x: i, y: d.middle }));
    const bbLower = g_chartIndicators.bb.slice(startIndex).map((d, i) => ({ x: i, y: d.lower }));

    // 3. 고가/저가 캔들 인덱스 및 등락률 계산
    const currentPrice = slicedRaw[slicedRaw.length - 1].close;
    let highestPrice = -Infinity, lowestPrice = Infinity;
    let highIdx = 0, lowIdx = 0;
    slicedRaw.forEach((d, i) => {
        if (d.high > highestPrice) { highestPrice = d.high; highIdx = i; }
        if (d.low < lowestPrice) { lowestPrice = d.low; lowIdx = i; }
    });
    const highPct = ((highestPrice - currentPrice) / currentPrice * 100).toFixed(1);
    const lowPct = ((lowestPrice - currentPrice) / currentPrice * 100).toFixed(1);

    // 고가/저가 마커 플러그인 (해당 캔들 위/아래에만 표기)
    const highLowPlugin = {
        id: 'highLowMarker',
        afterDatasetsDraw(chart) {
            const { ctx: c, scales: { y } } = chart;
            const meta = chart.getDatasetMeta(0); // 캔들스틱 데이터셋
            if (!meta || !meta.data) return;

            // 고가 표기: 가격 (날짜) 등락률 → ↓
            const highBar = meta.data[highIdx];
            if (highBar) {
                const hx = highBar.x;
                const hy = y.getPixelForValue(highestPrice);
                const highDate = labels[highIdx];
                const highSign = highPct > 0 ? '+' : '';
                c.save();
                c.font = 'bold 11px sans-serif';
                c.textAlign = 'center';
                c.fillStyle = '#ff4444';
                c.fillText(`${highestPrice.toLocaleString()} (${highDate}) ${highSign}${highPct}%`, hx, hy - 20);
                c.fillText('↓', hx, hy - 8);
                c.restore();
            }

            // 저가 표기: ↑ → 가격 (날짜) 등락률
            const lowBar = meta.data[lowIdx];
            if (lowBar) {
                const lx = lowBar.x;
                const ly = y.getPixelForValue(lowestPrice);
                const lowDate = labels[lowIdx];
                const lowSign = lowPct > 0 ? '+' : '';
                c.save();
                c.font = 'bold 11px sans-serif';
                c.textAlign = 'center';
                c.fillStyle = '#4488ff';
                c.fillText('↑', lx, ly + 14);
                c.fillText(`${lowestPrice.toLocaleString()} (${lowDate}) ${lowSign}${lowPct}%`, lx, ly + 26);
                c.restore();
            }
        }
    };

    // [추가] 차트 구분선 플러그인 (주가/거래량 사이)
    const splitLinePlugin = {
        id: 'splitLine',
        afterDraw: (chart) => {
            const { ctx, chartArea: { left, right }, scales: { y_spacer } } = chart;
            if (y_spacer) {
                const centerY = (y_spacer.top + y_spacer.bottom) / 2;
                ctx.save();
                ctx.beginPath();
                ctx.moveTo(left, centerY);
                ctx.lineTo(right, centerY);
                ctx.lineWidth = 2;
                ctx.strokeStyle = 'rgba(128, 128, 128, 0.5)';
                ctx.stroke();
                ctx.restore();
            }
        }
    };

    // 4. 차트 그리기
    const ctx = document.getElementById('stockChart').getContext('2d');
    if (stockChartInstance) stockChartInstance.destroy();

    stockChartInstance = new Chart(ctx, {
        type: 'candlestick',
        plugins: [highLowPlugin, splitLinePlugin],
        data: {
            labels: labels, // X축 라벨 (날짜)
            datasets: [
                {
                    label: '주가',
                    data: candles,
                    type: 'candlestick',
                    yAxisID: 'y',
                    // 한국식 캔들 색상 (상승: 빨강, 하락: 파랑)
                    backgroundColors: { up: '#ff0000', down: '#0000ff', unchanged: '#777777' },
                    borderColors: { up: '#ff0000', down: '#0000ff', unchanged: '#777777' },
                    order: 1
                },
                { label: 'MA5', data: ma5, type: 'line', borderColor: '#ff6b6b', borderWidth: 1, pointRadius: 0, yAxisID: 'y', order: 2 },
                { label: 'MA10', data: ma10, type: 'line', borderColor: '#51cf66', borderWidth: 1, pointRadius: 0, yAxisID: 'y', order: 2 }, // [추가] Green
                { label: 'MA20', data: ma20, type: 'line', borderColor: '#feca57', borderWidth: 1, pointRadius: 0, yAxisID: 'y', order: 2 },
                { label: 'MA60', data: ma60, type: 'line', borderColor: '#54a0ff', borderWidth: 1, pointRadius: 0, yAxisID: 'y', order: 2 },
                { label: 'MA120', data: ma120, type: 'line', borderColor: '#a29bfe', borderWidth: 1, pointRadius: 0, yAxisID: 'y', order: 2 }, // [추가] 보라색 계열
                { label: 'BB Upper', data: bbUpper, type: 'line', borderColor: 'rgba(78, 76, 76, 0.8)', borderWidth: 3, pointRadius: 0, yAxisID: 'y', fill: false, order: 3 },
                { label: 'BB Lower', data: bbLower, type: 'line', borderColor: 'rgba(78, 76, 76, 0.8)', borderWidth: 3, pointRadius: 0, yAxisID: 'y', fill: '-1', backgroundColor: 'rgba(200,200,200,0.1)', order: 3 },
                { label: 'BB Middle', data: bbMiddle, type: 'line', borderColor: 'rgba(255, 215, 0, 0.8)', borderWidth: 1.5, borderDash: [3, 3], pointRadius: 0, yAxisID: 'y', fill: false, order: 3, hidden: true }, // [수정] MA20과 중복되므로 기본 숨김
                { label: '거래량', data: volumes, type: 'bar', yAxisID: 'y1', backgroundColor: 'rgba(200, 200, 200, 0.2)', order: 4 }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: {
                    type: 'category', // [수정] 1970년 문제 해결을 위해 명시적 category 설정
                    ticks: { autoSkip: true, maxTicksLimit: 10 }
                },
                y: { type: 'linear', position: 'right', stack: 'stock', stackWeight: 4, grid: { color: 'rgba(255,255,255,0.1)' } },
                y_spacer: { // [추가] 주가/거래량 사이 간격
                    type: 'linear',
                    display: false,
                    position: 'right',
                    stack: 'stock',
                    stackWeight: 0.2,
                    grid: { drawOnChartArea: false }
                },
                y1: { type: 'linear', position: 'right', stack: 'stock', stackWeight: 1, grid: { drawOnChartArea: false }, ticks: { callback: v => (v/1000).toFixed(0)+'K' } }
            },
            plugins: {
                legend: {
                    display: true,
                    labels: {
                        color: '#a0a0b0',
                        usePointStyle: true,
                        pointStyle: 'line',
                        boxWidth: 20,
                        filter: function(item, chart) {
                            return item.text.includes('MA') || item.text.includes('BB');
                        },
                        generateLabels: function(chart) {
                            const original = Chart.defaults.plugins.legend.labels.generateLabels(chart);
                            return original.filter(item => item.text.includes('MA') || item.text.includes('BB')).map(item => {
                                item.pointStyle = 'line';
                                return item;
                            });
                        }
                    }
                },
                tooltip: {
                    callbacks: {
                        title: (items) => labels[items[0].dataIndex] // 툴팁에 날짜 표시
                    }
                }
            }
        }
    });
}
