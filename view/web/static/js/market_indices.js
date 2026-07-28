// 홈 화면 지수 패널 (market_indices.js)
//
// 국장(코스피/코스닥)은 KIS API + Chart.js 로 직접 그린다.
//   - TradingView 무료 임베드는 KRX 심볼을 전부 차단한다("이 심볼은 트레이딩뷰에서만 쓸 수 있습니다").
// 미장/원자재는 TradingView mini-symbol-overview 위젯을 쓴다.
//   - KIS Open API 가 SOX/VIX/달러인덱스/금/유가 시세를 제공하지 않는다.
//   - 거래소 실지수(SP:SPX, NASDAQ:NDX, CBOE:VIX ...)도 임베드가 차단되므로
//     실제로 시세가 그려지는 CFD/ETF 심볼만 쓴다.

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
];

const MARKET_INDEX_WIDGET_SRC =
    'https://s3.tradingview.com/external-embedding/embed-widget-mini-symbol-overview.js';

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
        symbol: entry.symbol,
        width: '100%',
        height: 150,
        locale: 'kr',
        dateRange: '1D',
        colorTheme: 'light',
        isTransparent: true,
        autosize: false,
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

function _renderIndexSparkline(canvas, data) {
    if (typeof Chart === 'undefined' || !data.points.length) return;

    // 함께 표시하는 등락률과 색이 어긋나지 않도록 등락률 기준으로 칠한다.
    const color = Number.isFinite(data.changeRate) && data.changeRate < 0 ? '#3742fa' : '#ff4757';
    const chart = new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: {
            labels: data.points.map(p => p.date),
            datasets: [{
                data: data.points.map(p => p.close),
                borderColor: color,
                borderWidth: 1.5,
                pointRadius: 0,
                fill: false,
                tension: 0.15,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: { legend: { display: false }, tooltip: { enabled: false } },
            scales: { x: { display: false }, y: { display: false } },
        },
    });
    window.currentCharts = window.currentCharts || [];
    window.currentCharts.push(chart);
}

async function buildMarketIndexKisCard(doc, entry) {
    const card = _marketIndexCardShell(doc, 'kis', entry.code, entry.label);

    const body = doc.createElement('div');
    body.className = 'market-index-body';
    card.appendChild(body);

    const res = await fetchWithTimeout(`/api/market-index/${entry.code}`);
    const { json, error } = await readJsonResponse(res);
    const data = json && json.rt_cd === '0' ? json.data : null;
    if (error || !data) {
        body.appendChild(_marketIndexError(doc));
        return card;
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
        canvas.height = 70;
        body.appendChild(canvas);
        _renderIndexSparkline(canvas, { points, changeRate: data.change_rate });
    }

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
