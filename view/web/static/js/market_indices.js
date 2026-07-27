// 홈 화면 글로벌 지수 패널 (market_indices.js)
// 지수별 TradingView mini-symbol-overview 위젯을 임베드한다.
// KIS Open API 로는 SOX/VIX/달러인덱스/금/유가/미국채 금리를 조달할 수 없어 위젯을 사용한다.

const MARKET_INDEX_WIDGETS = [
    { label: '나스닥100', symbol: 'NASDAQ:NDX' },
    { label: '필라델피아 반도체', symbol: 'NASDAQ:SOX' },
    { label: 'VIX 변동성', symbol: 'CBOE:VIX' },
    { label: '달러인덱스', symbol: 'TVC:DXY' },
    { label: '금', symbol: 'TVC:GOLD' },
    { label: 'WTI 유가', symbol: 'TVC:USOIL' },
    { label: '미국 10년물', symbol: 'TVC:US10Y' },
    { label: '미국 2년물', symbol: 'TVC:US02Y' },
];

const MARKET_INDEX_WIDGET_SRC =
    'https://s3.tradingview.com/external-embedding/embed-widget-mini-symbol-overview.js';

function buildMarketIndexCard(doc, entry) {
    const card = doc.createElement('div');
    card.className = 'market-index-card';
    card.dataset.symbol = entry.symbol;

    const label = doc.createElement('div');
    label.className = 'market-index-label';
    label.textContent = entry.label;
    card.appendChild(label);

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
        const fallback = doc.createElement('p');
        fallback.className = 'market-index-error';
        fallback.textContent = '지수 데이터를 불러오지 못했습니다.';
        container.appendChild(fallback);
    });
    container.appendChild(script);

    card.appendChild(container);
    return card;
}

function renderMarketIndices() {
    const target = document.getElementById('market-indices');
    if (!target) return;

    target.innerHTML = '';
    MARKET_INDEX_WIDGETS.forEach(entry => {
        target.appendChild(buildMarketIndexCard(document, entry));
    });
}

document.addEventListener('DOMContentLoaded', renderMarketIndices);
