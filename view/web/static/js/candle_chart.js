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

// 실시간 현재가 SSE 연결 객체
let priceEventSource = null;

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
        const t0 = performance.now();
        const exchParam = (typeof _currentExchange !== 'undefined') ? _currentExchange : 'KRX';
        const res = await fetch(`/api/chart/${code}?period=D&indicators=true&exchange=${exchParam}`);
        const json = await res.json();
        const tFetch = performance.now();

        if (json.rt_cd !== "0" || !json.data || !json.data.ohlcv || json.data.ohlcv.length === 0) {
            chartCard.style.display = 'none';
            return;
        }

        // 전역 변수에 데이터 저장
        g_chartRawData = json.data.ohlcv;
        g_chartIndicators = json.data.indicators || {};

        // [최적화 2] API 수신 직후 날짜 포맷팅 1회 사전 처리
        const formatYYYYMMDD = (str) => str.substring(0, 4) + '-' + str.substring(4, 6) + '-' + str.substring(6, 8);
        g_chartRawData.forEach(d => { d.formattedDate = formatYYYYMMDD(d.date); });
        g_chartCode = code;

        chartCard.style.display = 'block';

        // 기본 3개월 렌더링
        renderStockChart('3M');
        const tRender = performance.now();

        // [성능 측정] API fetch + 차트 렌더링 시간 로깅
        console.log(`[Perf] 차트 데이터 조회(${code}): ${(tFetch - t0).toFixed(1)}ms | 렌더링: ${(tRender - tFetch).toFixed(1)}ms | 합계: ${(tRender - t0).toFixed(1)}ms`);

        subscribeRealtimePrice(code);

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
    const tRenderStart = performance.now();

    // 1. 데이터 슬라이싱 (기간 필터링)
    let sliceCount = 0;
    if (period === '1M') sliceCount = 22;      // 약 1개월 (영업일 기준)
    else if (period === '3M') sliceCount = 66; // 약 3개월
    else if (period === '6M') sliceCount = 130;// 약 6개월
    else if (period === '1Y') sliceCount = 260;// 약 1년
    else sliceCount = g_chartRawData.length;   // 전체

    const startIndex = Math.max(0, g_chartRawData.length - sliceCount);
    const slicedRaw = g_chartRawData.slice(startIndex);

    // [최적화 1] 다중 .map()/.forEach() → 단일 for 루프 통합 (GC 부하 감소)
    const len = slicedRaw.length;
    const labels = new Array(len);
    const candles = new Array(len);
    const volumes = new Array(len);
    const volumeColors = new Array(len);
    const ma5 = new Array(len), ma10 = new Array(len), ma20 = new Array(len);
    const ma60 = new Array(len), ma120 = new Array(len);
    const bbUpper = new Array(len), bbMiddle = new Array(len), bbLower = new Array(len);

    const currentPrice = slicedRaw[len - 1].close;
    let highestPrice = -Infinity, lowestPrice = Infinity;
    let highIdx = 0, lowIdx = 0;

    for (let i = 0; i < len; i++) {
        const d = slicedRaw[i];
        const prevVol = i === 0 ? d.volume : slicedRaw[i - 1].volume;

        labels[i] = d.formattedDate;
        candles[i] = { x: i, o: d.open, h: d.high, l: d.low, c: d.close };
        volumes[i] = { x: i, y: d.volume };
        volumeColors[i] = i === 0 || d.volume === prevVol ? 'rgba(200, 200, 200, 0.4)'
            : d.volume > prevVol ? 'rgba(255, 0, 0, 0.5)' : 'rgba(0, 0, 255, 0.5)';

        if (d.high > highestPrice) { highestPrice = d.high; highIdx = i; }
        if (d.low < lowestPrice)   { lowestPrice  = d.low;  lowIdx  = i; }

        const rawIdx = startIndex + i;
        const ind = g_chartIndicators;
        if (ind.ma5[rawIdx])   ma5[i]   = { x: i, y: ind.ma5[rawIdx].ma };
        if (ind.ma10[rawIdx])  ma10[i]  = { x: i, y: ind.ma10[rawIdx].ma };
        if (ind.ma20[rawIdx])  ma20[i]  = { x: i, y: ind.ma20[rawIdx].ma };
        if (ind.ma60[rawIdx])  ma60[i]  = { x: i, y: ind.ma60[rawIdx].ma };
        if (ind.ma120[rawIdx]) ma120[i] = { x: i, y: ind.ma120[rawIdx].ma };
        if (ind.bb[rawIdx]) {
            bbUpper[i]  = { x: i, y: ind.bb[rawIdx].upper };
            bbMiddle[i] = { x: i, y: ind.bb[rawIdx].middle };
            bbLower[i]  = { x: i, y: ind.bb[rawIdx].lower };
        }
    }

    const highPct = ((highestPrice - currentPrice) / currentPrice * 100).toFixed(1);
    const lowPct  = ((lowestPrice  - currentPrice) / currentPrice * 100).toFixed(1);

    // [최적화 3] 고가/저가 레이블 문자열을 플러그인 외부에서 1회 생성
    const highLabel = `${highestPrice.toLocaleString()} (${labels[highIdx]}) ${highPct > 0 ? '+' : ''}${highPct}%`;
    const lowLabel  = `${lowestPrice.toLocaleString()} (${labels[lowIdx]}) ${lowPct > 0 ? '+' : ''}${lowPct}%`;

    const highLowPlugin = {
        id: 'highLowMarker',
        afterDatasetsDraw(chart) {
            const { ctx: c, scales: { y } } = chart;
            const meta = chart.getDatasetMeta(0);
            if (!meta || !meta.data) return;

            const highBar = meta.data[highIdx];
            const lowBar  = meta.data[lowIdx];
            if (!highBar && !lowBar) return;

            // save/restore 1쌍 + font/textAlign 1회 설정으로 중복 제거
            c.save();
            c.font = 'bold 11px sans-serif';
            c.textAlign = 'center';

            if (highBar) {
                const hx = highBar.x;
                const hy = y.getPixelForValue(highestPrice);
                c.fillStyle = '#ff4444';
                c.fillText(highLabel, hx, hy - 20);
                c.fillText('↓', hx, hy - 8);
            }
            if (lowBar) {
                const lx = lowBar.x;
                const ly = y.getPixelForValue(lowestPrice);
                c.fillStyle = '#4488ff';
                c.fillText('↑', lx, ly + 14);
                c.fillText(lowLabel, lx, ly + 26);
            }
            c.restore();
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
    const _prevChart = stockChartInstance;
    if (_prevChart) { try { _prevChart.destroy(); } catch (_) {} }
    stockChartInstance = null;

    const tChartStart = performance.now();
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
                { label: '거래량', data: volumes, type: 'bar', yAxisID: 'y1', backgroundColor: volumeColors, order: 4 }
            ]
        },
        options: {
            animation: false, // [최적화] 애니메이션 비활성화로 즉시 렌더링
            responsive: true,
            maintainAspectRatio: false,
            elements: {
                line: { tension: 0, borderJoinStyle: 'round' } // [최적화 4] 베지어 곡선 비활성화 → MA 선 렌더링 속도 향상
            },
            interaction: { mode: 'index', intersect: false },
            parsing: false, // [최적화] 데이터 파싱 비활성화 (이미 포맷에 맞춤)
            normalized: true, // [최적화] 데이터가 이미 정렬되어 있음을 명시
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
    // 길이 비교
    console.log("OHLCV:", g_chartRawData.length, "MA5:", g_chartIndicators.ma5.length)

    // 마지막 날짜 비교 (정렬 여부)
    const last = g_chartRawData.length - 1;
    console.log("ohlcv last date:", g_chartRawData[last].date, "close:", g_chartRawData[last].close)
    console.log("ma5 last date:", g_chartIndicators.ma5[last]?.date, "ma:", g_chartIndicators.ma5[last]?.ma)

    window.currentCharts = window.currentCharts || [];
    window.currentCharts = window.currentCharts.filter(c => c !== _prevChart);
    window.currentCharts.push(stockChartInstance);

    // [성능 측정] 데이터 가공 + Chart.js 생성 시간 로깅
    const tRenderEnd = performance.now();
    console.log(`[Perf] 차트 렌더링(${period}, ${slicedRaw.length}건): 데이터 가공 ${(tChartStart - tRenderStart).toFixed(1)}ms | Chart.js 생성 ${(tRenderEnd - tChartStart).toFixed(1)}ms | 합계 ${(tRenderEnd - tRenderStart).toFixed(1)}ms`);
}

function subscribeRealtimePrice(code) {
    if (priceEventSource) {
        priceEventSource.close();
        priceEventSource = null;
    }

    priceEventSource = new EventSource(`/api/streaming/price/${code}`);

    priceEventSource.onmessage = function (event) {
        const tick = JSON.parse(event.data);

        // stock.js UI(가격·전일대비·당일시세) 업데이트
        document.dispatchEvent(new CustomEvent('stock-price-tick', { detail: tick }));

        if (!stockChartInstance || !g_chartRawData || g_chartRawData.length === 0) return;

        const lastRawIdx = g_chartRawData.length - 1;
        const raw = g_chartRawData[lastRawIdx];

        // g_chartRawData 갱신
        raw.close = tick.price;
        if (!raw.open && tick.open > 0) raw.open = tick.open;  // 시가 초기화 (최초 1회)
        if (tick.high > 0) raw.high = tick.high;               // 누적 최고가 직접 적용
        if (tick.low  > 0) raw.low  = tick.low;                // 누적 최저가 직접 적용
        raw.volume = tick.volume;

        // 현재 차트에 표시된 슬라이스의 마지막 인덱스 (기간 변경과 무관)
        const candleData = stockChartInstance.data.datasets[0].data;
        const lastChartIdx = candleData.length - 1;

        if (lastChartIdx >= 0 && candleData[lastChartIdx]) {
            candleData[lastChartIdx].o = raw.open;
            candleData[lastChartIdx].c = raw.close;
            candleData[lastChartIdx].h = raw.high;
            candleData[lastChartIdx].l = raw.low;
        }

        // 거래량 데이터셋은 label로 탐색 (인덱스 하드코딩 불필요)
        const volDataset = stockChartInstance.data.datasets.find(d => d.label === '거래량');
        if (volDataset && volDataset.data[lastChartIdx]) {
            volDataset.data[lastChartIdx].y = raw.volume;
        }

        stockChartInstance.update('none');
    };

    priceEventSource.onerror = function () {
        // EventSource 스펙상 브라우저가 자동 재연결 처리
    };
}

function unsubscribeRealtimePrice() {
    if (priceEventSource) {
        priceEventSource.close();
        priceEventSource = null;
    }
}

window.addEventListener('beforeunload', unsubscribeRealtimePrice);
