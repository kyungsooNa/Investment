// 홈 화면 지수 패널 (market_indices.js)
//
// 국장(코스피/코스닥)은 KIS API + Chart.js 로 직접 그린다.
//   - TradingView 무료 임베드는 KRX 심볼을 전부 차단한다("이 심볼은 트레이딩뷰에서만 쓸 수 있습니다").
// 미장/원자재/가상자산/채권은 TradingView symbol-overview 위젯을 쓴다.
//   - KIS Open API 가 SOX/VIX/달러인덱스/금/유가 시세를 제공하지 않는다.
//   - 거래소 실지수(SP:SPX, NASDAQ:NDX, CBOE:VIX ...)도 임베드가 차단되므로
//     실제로 시세와 그래프가 그려지는 CFD/ETF 심볼만 쓴다.

const MARKET_INDEX_GROUPS = [
    {
        title: '국장',
        kind: 'kis',
        entries: [
            { label: '코스피', code: '0001' },
            { label: '코스닥', code: '1001' },
        ],
    },
    {
        title: '미장',
        kind: 'widget',
        entries: [
            { label: '나스닥100', symbol: 'CAPITALCOM:US100' },
            { label: 'S&P500', symbol: 'CAPITALCOM:US500' },
            { label: '반도체 ETF (SOX 추종)', symbol: 'NASDAQ:SOXX' },
            { label: 'VIX 변동성', symbol: 'CAPITALCOM:VIX' },
            { label: '달러인덱스', symbol: 'CAPITALCOM:DXY' },
        ],
    },
    {
        title: '원자재',
        kind: 'widget',
        entries: [
            { label: '금', symbol: 'TVC:GOLD' },
            { label: 'WTI 유가', symbol: 'TVC:USOIL' },
            { label: '메모리 반도체 ETF', symbol: 'CBOE:DRAM' },
        ],
    },
    {
        title: '가상자산',
        kind: 'widget',
        entries: [
            { label: '비트코인', symbol: 'BITSTAMP:BTCUSD' },
            { label: '이더리움', symbol: 'BITSTAMP:ETHUSD' },
        ],
    },
    {
        // 국채는 금리(%)를 그대로 보여준다. TVC 금리 지표(US02Y/US10Y)는 mini-symbol-overview·
        // symbol-overview·advanced-chart 세 위젯 타입 모두에서 차단되는 것을 2026-08-26 에
        // 재실측했고, 연준 공개데이터인 FRED 는 열린다(DGS10 4.7 · DGS2 4.24 렌더 확인).
        // FRED 는 일 1회 갱신이라 dateRange '1D' 로는 그릴 점이 없어 12M 을 준다.
        title: '채권',
        kind: 'widget',
        entries: [
            { label: '미국채 10년 금리', symbol: 'FRED:DGS10', dateRange: '12M' },
            { label: '미국채 2년 금리', symbol: 'FRED:DGS2', dateRange: '12M' },
        ],
    },
];

// 국장 카드 기간 선택. KIS 기간별 지수 API 가 범위와 무관하게 최대 50행만 주므로
// 1Y 는 서버에서 주봉으로 받는다. 1D 는 10분봉이라 x축이 시각으로 바뀐다.
const MARKET_INDEX_PERIODS = [
    { key: '1D', label: '1일' },
    { key: '1W', label: '1주' },
    { key: '1M', label: '1개월' },
    { key: '1Y', label: '1년' },
];

const MARKET_INDEX_DEFAULT_PERIOD = '1D';

const MARKET_INDEX_WIDGET_SRC =
    'https://s3.tradingview.com/external-embedding/embed-widget-symbol-overview.js';

const MARKET_INDEX_LOAD_ERROR = '지수 데이터를 불러오지 못했습니다.';

function _marketIndexCardShell(doc, kind, key, label) {
    const card = doc.createElement('div');
    card.className = 'market-index-card';
    card.dataset.kind = kind;
    card.dataset.key = key;

    const title = doc.createElement('div');
    title.className = 'market-index-label';
    title.textContent = label;
    card.appendChild(title);

    return card;
}

function _marketIndexError(doc) {
    const message = doc.createElement('p');
    message.className = 'market-index-error';
    message.textContent = MARKET_INDEX_LOAD_ERROR;
    return message;
}

function buildMarketIndexWidgetCard(doc, entry) {
    const card = _marketIndexCardShell(doc, 'widget', entry.symbol, entry.label);

    const container = doc.createElement('div');
    container.className = 'tradingview-widget-container';
    const slot = doc.createElement('div');
    slot.className = 'tradingview-widget-container__widget';
    container.appendChild(slot);

    const script = doc.createElement('script');
    script.async = true;
    script.src = MARKET_INDEX_WIDGET_SRC;
    script.textContent = JSON.stringify({
        symbols: [[entry.label, `${entry.symbol}|${entry.dateRange || '1D'}`]],
        chartOnly: false,
        width: '100%',
        height: 180,
        locale: 'kr',
        colorTheme: 'light',
        isTransparent: true,
        autosize: false,
        showVolume: false,
        showMA: false,
        hideDateRanges: true,
        hideMarketStatus: false,
        hideSymbolLogo: false,
        scalePosition: 'right',
        scaleMode: 'Normal',
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        fontSize: '10',
        noTimeScale: false,
        valuesTracking: '1',
        changeMode: 'price-and-percent',
        chartType: 'area',
        maLineColor: '#2962FF',
        maLineWidth: 1,
        maLength: 9,
        lineWidth: 1.5,
        lineType: 0,
        largeChartUrl: '',
    });
    script.addEventListener('error', () => {
        container.innerHTML = '';
        container.appendChild(_marketIndexError(doc));
    });
    container.appendChild(script);

    card.appendChild(container);
    return card;
}

function _formatIndexValue(value) {
    return Number.isFinite(value)
        ? value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
        : '-';
}

function _formatIndexChange(change, rate) {
    if (!Number.isFinite(rate)) return { text: '-', className: '' };
    const sign = rate > 0 ? '+' : '';
    const delta = Number.isFinite(change) ? `${sign}${_formatIndexValue(change)} ` : '';
    const className = rate > 0 ? 'text-red' : (rate < 0 ? 'text-blue' : '');
    return { text: `${delta}(${sign}${rate.toFixed(2)}%)`.replace(' (', ' ('), className };
}

// 분봉 응답에만 time 이 담기므로 라벨 형식은 요청한 기간이 아니라 받은 데이터를 보고 정한다.
function _formatIndexPointLabel(point) {
    // 서버가 1D 앞에 붙이는 전일 종가 기준점. 날짜가 없으므로 라벨을 따로 준다.
    if (point.prev) return '전일';
    const time = String(point.time || '');
    if (time.length >= 4) return `${time.slice(0, 2)}:${time.slice(2, 4)}`;
    const date = String(point.date || '');
    return date.length === 8 ? `${date.slice(4, 6)}/${date.slice(6, 8)}` : date;
}

// 좁은 카드라 눈금을 솎아내되, 뒤에서부터 세어 장 마감(마지막 봉) 라벨은 항상 남긴다.
// (Chart.js autoSkip 은 앞에서부터 세기 때문에 하루치 차트의 끝이 잘려나갔다)
const MARKET_INDEX_MAX_TICKS = 6;

function _indexTickCallback(labels) {
    const step = Math.max(1, Math.ceil((labels.length - 1) / (MARKET_INDEX_MAX_TICKS - 1)));
    return function (_value, index) {
        return (labels.length - 1 - index) % step === 0 ? labels[index] : '';
    };
}

// 기준선 위는 상승색, 아래는 하락색. 영역은 같은 색을 옅게 깐다.
const MARKET_INDEX_UP_COLOR = '#ff4757';
const MARKET_INDEX_DOWN_COLOR = '#3742fa';
const MARKET_INDEX_UP_FILL = 'rgba(255, 71, 87, 0.14)';
const MARKET_INDEX_DOWN_FILL = 'rgba(55, 66, 250, 0.14)';

function _indexBaselineColor(value, baseline) {
    return value < baseline ? MARKET_INDEX_DOWN_COLOR : MARKET_INDEX_UP_COLOR;
}

function _renderIndexSparkline(canvas, data) {
    if (typeof Chart === 'undefined' || !data.points.length) return;

    // 함께 표시하는 등락률과 색이 어긋나지 않도록 등락률 기준으로 칠한다.
    const color = Number.isFinite(data.changeRate) && data.changeRate < 0
        ? MARKET_INDEX_DOWN_COLOR
        : MARKET_INDEX_UP_COLOR;
    // 시가 기준선: 1D 는 서버가 앞에 붙인 전일 종가, 그 밖의 기간은 구간 첫 종가.
    // 구간이 기준선을 넘나들면 선과 영역 색도 그 지점에서 갈린다.
    const baseline = data.points[0].close;
    const labels = data.points.map(_formatIndexPointLabel);
    const chart = new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: {
            labels,
            datasets: [{
                data: data.points.map(p => p.close),
                borderColor: color,
                // Chart.js 는 구간 단위로만 색을 바꾸므로 구간이 도착한 값으로 판단한다.
                segment: {
                    borderColor: ctx => _indexBaselineColor(ctx.p1.parsed.y, baseline),
                },
                borderWidth: 1.5,
                pointRadius: 0,
                fill: {
                    target: { value: baseline },
                    above: MARKET_INDEX_UP_FILL,
                    below: MARKET_INDEX_DOWN_FILL,
                },
                tension: 0.15,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: { legend: { display: false }, tooltip: { enabled: false } },
            scales: {
                // 눈금은 _indexTickCallback 이 직접 고르므로 autoSkip 이 또 솎아내면 안 된다.
                x: {
                    display: true,
                    grid: { display: false },
                    border: { display: false },
                    ticks: {
                        autoSkip: false,
                        callback: _indexTickCallback(labels),
                        maxRotation: 0,
                        minRotation: 0,
                        font: { size: 9 },
                    },
                },
                y: { display: false },
            },
        },
    });
    window.currentCharts = window.currentCharts || [];
    window.currentCharts.push(chart);
    return chart;
}

function _buildMarketIndexPeriodBar(doc, card, code) {
    const bar = doc.createElement('div');
    bar.className = 'market-index-period-bar';

    MARKET_INDEX_PERIODS.forEach(period => {
        const button = doc.createElement('button');
        button.type = 'button';
        button.className = 'market-index-period';
        button.dataset.period = period.key;
        button.textContent = period.label;
        if (period.key === MARKET_INDEX_DEFAULT_PERIOD) button.classList.add('active');
        button.addEventListener('click', () => selectMarketIndexPeriod(card, code, period.key));
        bar.appendChild(button);
    });

    return bar;
}

function _destroyMarketIndexChart(card) {
    if (!card._indexChart) return;
    try { card._indexChart.destroy(); } catch (_) { /* 이미 파기된 차트 */ }
    if (window.currentCharts) {
        window.currentCharts = window.currentCharts.filter(chart => chart !== card._indexChart);
    }
    card._indexChart = null;
}

async function selectMarketIndexPeriod(card, code, period) {
    const doc = card.ownerDocument;
    card.querySelectorAll('.market-index-period').forEach(button => {
        button.classList.toggle('active', button.dataset.period === period);
    });

    const body = card.querySelector('.market-index-body');
    const res = await fetchWithTimeout(`/api/market-index/${code}?period=${period}`);
    const { json, error } = await readJsonResponse(res);
    const data = json && json.rt_cd === '0' ? json.data : null;

    _destroyMarketIndexChart(card);
    body.innerHTML = '';
    if (error || !data) {
        body.appendChild(_marketIndexError(doc));
        return;
    }

    const value = doc.createElement('div');
    value.className = 'market-index-value';
    value.textContent = _formatIndexValue(data.current);
    body.appendChild(value);

    const change = _formatIndexChange(data.change, data.change_rate);
    const changeEl = doc.createElement('div');
    changeEl.className = `market-index-change ${change.className}`.trim();
    changeEl.textContent = change.text;
    body.appendChild(changeEl);

    const points = Array.isArray(data.points) ? data.points : [];
    if (points.length) {
        const canvas = doc.createElement('canvas');
        canvas.className = 'market-index-spark';
        canvas.height = 60;
        body.appendChild(canvas);
        card._indexChart = _renderIndexSparkline(canvas, { points, changeRate: data.change_rate });
    }
}

// 투자자 순매수(억원) 3주체 + 등락 종목수. 네이버 지수 카드와 같은 2열 배치다.
const MARKET_INDEX_INVESTORS = [
    { key: 'individual', label: '개인' },
    { key: 'foreign', label: '외국인' },
    { key: 'institution', label: '기관' },
];

function _formatIndexNetBuy(amount) {
    if (!Number.isFinite(amount)) return { text: '-', className: '' };
    const sign = amount > 0 ? '+' : (amount < 0 ? '-' : '');
    const className = amount > 0 ? 'text-red' : (amount < 0 ? 'text-blue' : '');
    return { text: `${sign}${Math.abs(amount).toLocaleString()}`, className };
}

function _marketIndexFlowRow(doc, label, text, className) {
    const row = doc.createElement('div');
    row.className = 'market-index-flow-row';

    const name = doc.createElement('span');
    name.className = 'market-index-flow-label';
    name.textContent = label;
    row.appendChild(name);

    const value = doc.createElement('span');
    value.className = `market-index-flow-value ${className}`.trim();
    value.textContent = text;
    row.appendChild(value);

    return row;
}

function _marketIndexFlowColumn(doc, rows) {
    const column = doc.createElement('div');
    column.className = 'market-index-flow-col';
    rows.forEach(row => column.appendChild(row));
    return column;
}

// 상·하한 종목수는 네이버처럼 괄호로 덧붙인다: 380(3)
function _formatIndexBreadthCount(count, limitCount) {
    return Number.isFinite(limitCount) ? `${count.toLocaleString()}(${limitCount})` : count.toLocaleString();
}

function _buildMarketIndexFlow(doc, data) {
    const investors = data.investors;
    const breadth = data.breadth;
    if (!investors && !breadth) return null;

    const flow = doc.createElement('div');
    flow.className = 'market-index-flow';

    if (investors) {
        flow.appendChild(_marketIndexFlowColumn(doc, MARKET_INDEX_INVESTORS.map(investor => {
            const amount = _formatIndexNetBuy(investors[investor.key]);
            return _marketIndexFlowRow(doc, investor.label, amount.text, amount.className);
        })));
    }

    if (breadth) {
        flow.appendChild(_marketIndexFlowColumn(doc, [
            _marketIndexFlowRow(doc, '상승', _formatIndexBreadthCount(breadth.up, breadth.upper_limit), 'text-red'),
            _marketIndexFlowRow(doc, '보합', _formatIndexBreadthCount(breadth.unchanged), ''),
            _marketIndexFlowRow(doc, '하락', _formatIndexBreadthCount(breadth.down, breadth.lower_limit), 'text-blue'),
        ]));
    }

    return flow;
}

// 수급은 기간과 무관하므로 카드를 만들 때 한 번만 조회한다.
// 실전 전용 TR 이라 모의투자에서는 막힐 수 있고, 그때는 안내 없이 영역을 통째로 뺀다.
async function appendMarketIndexFlow(doc, card, code) {
    const res = await fetchWithTimeout(`/api/market-index/${code}/flow`);
    const { json, error } = await readJsonResponse(res);
    const data = json && json.rt_cd === '0' ? json.data : null;
    if (error || !data) return;

    const flow = _buildMarketIndexFlow(doc, data);
    if (flow) card.appendChild(flow);
}

async function buildMarketIndexKisCard(doc, entry) {
    const card = _marketIndexCardShell(doc, 'kis', entry.code, entry.label);
    card.appendChild(_buildMarketIndexPeriodBar(doc, card, entry.code));

    const body = doc.createElement('div');
    body.className = 'market-index-body';
    card.appendChild(body);

    await selectMarketIndexPeriod(card, entry.code, MARKET_INDEX_DEFAULT_PERIOD);
    appendMarketIndexFlow(doc, card, entry.code).catch(() => {});
    return card;
}

async function buildMarketIndexGroup(doc, group) {
    const section = doc.createElement('section');
    section.className = 'market-index-group';

    const title = doc.createElement('h3');
    title.className = 'market-index-group-title';
    title.textContent = group.title;
    section.appendChild(title);

    const grid = doc.createElement('div');
    grid.className = 'market-indices-grid';
    const cards = group.kind === 'kis'
        ? await Promise.all(group.entries.map(entry => buildMarketIndexKisCard(doc, entry)))
        : group.entries.map(entry => buildMarketIndexWidgetCard(doc, entry));
    cards.forEach(card => grid.appendChild(card));
    section.appendChild(grid);

    return section;
}

async function renderMarketIndices() {
    const target = document.getElementById('market-indices');
    if (!target) return;

    const sections = await Promise.all(
        MARKET_INDEX_GROUPS.map(group => buildMarketIndexGroup(document, group))
    );

    target.innerHTML = '';
    sections.forEach(section => target.appendChild(section));
}

document.addEventListener('DOMContentLoaded', renderMarketIndices);
document.addEventListener('pjax:ready', renderMarketIndices);
