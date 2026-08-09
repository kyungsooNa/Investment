/* view/web/static/js/market_heatmap.js — 히트맵(트리맵) 렌더 라이브러리
 *
 * 화면 배선은 갖지 않는다. 소스({ targetId, captionId, url, toGroups, caption, ... })를 받아
 * 조회 → 트리맵 계산 → 렌더까지 하며, 어떤 소스를 언제 그릴지는 화면(heatmap_page.js)이 정한다.
 *
 * 미국: /api/overseas/top-market-cap 스냅샷을 쓴다. 미국 시가총액 화면과 같은 소스라
 *       (Yahoo 스냅샷 1회로 sector/change_rate/market_cap_usd 가 모두 오고) TTL 캐시를 공유한다.
 *       섹터 블록 → 종목 타일 2단으로 그린다.
 * 국내: /api/heatmap/domestic (장마감 후 daily_prices 스냅샷). 실시간 전종목 시세 소스가 없어
 *       종가 기준이며, 배타적 업종 분류도 아직 없어 섹터 블록 없이 시총순 타일만 그린다.
 *
 * 타일 좌표는 % 로만 계산해 컨테이너 크기와 무관하게 반응형으로 그린다.
 * 색상은 국내 화면 컨벤션(상승=빨강, 하락=파랑)을 따른다.
 */

const HEATMAP_SECTOR_HEADER_PX = 16;
// 타일이 이보다 작으면 글자가 넘쳐 겹치므로 텍스트를 생략한다(툴팁으로만 노출).
const HEATMAP_MIN_LABEL_WIDTH_PCT = 4.5;
const HEATMAP_MIN_LABEL_HEIGHT_PCT = 8;

const HEATMAP_UP_COLORS = [
    { min: 3, color: '#8f1a1a' },
    { min: 2, color: '#b52626' },
    { min: 1, color: '#d64545' },
    { min: 0, color: '#e58080' },
];
const HEATMAP_DOWN_COLORS = [
    { min: 3, color: '#12386f' },
    { min: 2, color: '#2b5cb8' },
    { min: 1, color: '#4a7fd4' },
    { min: 0, color: '#84aee6' },
];
const HEATMAP_FLAT_COLOR = '#6b7280';
const HEATMAP_UNKNOWN_COLOR = '#3f4650';
// 기간 등락률 응답의 캡션 표기. '1d' 는 스냅샷 일간 등락률이라 기간 표기를 붙이지 않는다.
const HEATMAP_PERIOD_LABELS = {
    '1w': '1주',
    '1m': '1개월',
    '3m': '3개월',
    '6m': '6개월',
    'ytd': '올해',
    '1y': '1년',
};

let _heatmapRequestSequence = 0;

function _heatmapNumber(value) {
    if (value === null || value === undefined || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
}

function _heatmapRateText(rate) {
    if (rate === null) return '-';
    const sign = rate > 0 ? '+' : '';
    return `${sign}${rate.toFixed(2)}%`;
}

function _heatmapDirection(rate) {
    if (rate === null) return 'unknown';
    if (rate > 0) return 'up';
    if (rate < 0) return 'down';
    return 'flat';
}

function _heatmapColor(rate) {
    if (rate === null) return HEATMAP_UNKNOWN_COLOR;
    if (rate === 0) return HEATMAP_FLAT_COLOR;
    const scale = rate > 0 ? HEATMAP_UP_COLORS : HEATMAP_DOWN_COLORS;
    const magnitude = Math.abs(rate);
    return scale.find(step => magnitude >= step.min).color;
}

function _heatmapCapText(value) {
    const number = _heatmapNumber(value);
    if (number === null || number <= 0) return '-';
    if (number >= 1e12) return `$${(number / 1e12).toFixed(2)}T`;
    if (number >= 1e9) return `$${(number / 1e9).toFixed(2)}B`;
    if (number >= 1e6) return `$${(number / 1e6).toFixed(2)}M`;
    return `$${Math.round(number).toLocaleString()}`;
}

/**
 * squarified treemap. cells 는 { key, weight } 배열(내림차순), rect 는 % 단위 사각형.
 * 반환: [{ key, x, y, w, h }] (모두 % 단위)
 */
function _squarifyTreemap(cells, rect) {
    const placed = [];
    const remaining = cells.filter(cell => cell.weight > 0);
    let { x, y, w, h } = rect;

    while (remaining.length && w > 0 && h > 0) {
        const totalWeight = remaining.reduce((sum, cell) => sum + cell.weight, 0);
        const areaScale = (w * h) / totalWeight;
        const side = Math.min(w, h);
        const row = [];
        let rowArea = 0;
        let bestWorst = Infinity;

        while (remaining.length) {
            const area = remaining[0].weight * areaScale;
            const nextArea = rowArea + area;
            const areas = row.map(entry => entry.area).concat(area);
            const worst = Math.max(
                (nextArea * nextArea) / (side * side * Math.min(...areas)),
                (side * side * Math.max(...areas)) / (nextArea * nextArea),
            );
            if (row.length && worst > bestWorst) break;
            bestWorst = worst;
            row.push({ cell: remaining.shift(), area });
            rowArea = nextArea;
        }

        const thickness = rowArea / side;
        let offset = 0;
        const horizontal = w >= h;
        row.forEach(({ cell, area }) => {
            const length = area / thickness;
            placed.push(horizontal
                ? { key: cell.key, x, y: y + offset, w: thickness, h: length }
                : { key: cell.key, x: x + offset, y, w: length, h: thickness });
            offset += length;
        });

        if (horizontal) {
            x += thickness;
            w -= thickness;
        } else {
            y += thickness;
            h -= thickness;
        }
    }

    return placed;
}

function _overseasGroups(items) {
    const groups = new Map();
    items.forEach(raw => {
        const row = raw && typeof raw === 'object' ? raw : {};
        const cap = _heatmapNumber(row.market_cap_usd);
        if (cap === null || cap <= 0) return;
        const sector = String(row.sector || '기타');
        if (!groups.has(sector)) groups.set(sector, { sector, cap: 0, members: [] });
        const group = groups.get(sector);
        group.cap += cap;
        group.members.push({
            symbol: String(row.symbol ?? ''),
            label: String(row.symbol ?? ''),
            name: String(row.name ?? ''),
            rate: _heatmapNumber(row.change_rate),
            cap,
            capText: _heatmapCapText(cap),
        });
    });

    const groupList = [...groups.values()].sort((a, b) => b.cap - a.cap);
    groupList.forEach(group => group.members.sort((a, b) => b.cap - a.cap));
    return groupList;
}

// 국내는 배타적 업종 분류가 없어(네이버 테마는 중복 소속) 섹터 블록 없이 단일 그룹으로 그린다.
function _domesticGroups(items) {
    const members = [];
    items.forEach(raw => {
        const row = raw && typeof raw === 'object' ? raw : {};
        const cap = _heatmapNumber(row.market_cap);
        if (cap === null || cap <= 0) return;
        members.push({
            symbol: String(row.code ?? ''),
            label: String(row.name ?? ''),
            name: String(row.name ?? ''),
            rate: _heatmapNumber(row.change_rate),
            cap,
            capText: formatMarketCap(cap),
        });
    });
    if (!members.length) return [];

    members.sort((a, b) => b.cap - a.cap);
    return [{ sector: null, cap: members.reduce((sum, m) => sum + m.cap, 0), members }];
}

// zoom 은 캔버스를 몇 배로 늘려 그리는지(전용 페이지의 확대 배율). 타일이 그만큼 커지므로
// 라벨 생략 기준도 같이 완화해야 확대의 의미가 있다.
function _heatmapTileHtml(member, box, zoom) {
    const rateText = _heatmapRateText(member.rate);
    const showLabel = box.w * zoom >= HEATMAP_MIN_LABEL_WIDTH_PCT && box.h * zoom >= HEATMAP_MIN_LABEL_HEIGHT_PCT;
    const label = showLabel
        ? `<span class="heatmap-tile-symbol">${escapeHtml(member.label)}</span>`
          + `<span class="heatmap-tile-rate">${escapeHtml(rateText)}</span>`
        : '';
    const tooltip = `${member.symbol} ${member.name} | ${rateText} | ${member.capText}`;
    return `
        <div class="heatmap-tile"
             data-symbol="${escapeHtml(member.symbol)}"
             data-name="${escapeHtml(member.name)}"
             data-direction="${_heatmapDirection(member.rate)}"
             title="${escapeHtml(tooltip)}"
             style="left:${box.x.toFixed(4)}%; top:${box.y.toFixed(4)}%; width:${box.w.toFixed(4)}%; height:${box.h.toFixed(4)}%; background-color:${_heatmapColor(member.rate)};">
            ${label}
        </div>
    `;
}

function _heatmapSectorHtml(group, box, zoom) {
    const memberBoxes = _squarifyTreemap(
        group.members.map((member, index) => ({ key: index, weight: member.cap })),
        { x: 0, y: 0, w: 100, h: 100 },
    );
    const tiles = memberBoxes
        .map(memberBox => _heatmapTileHtml(group.members[memberBox.key], memberBox, zoom))
        .join('');

    // 섹터명이 없는 시장(국내)은 헤더 없이 타일이 블록 전체를 채운다.
    const titled = Boolean(group.sector);
    const sectorAttr = titled ? ` data-sector="${escapeHtml(group.sector)}"` : '';
    const title = titled ? `<div class="heatmap-sector-title">${escapeHtml(group.sector)}</div>` : '';
    const bodyTop = titled ? HEATMAP_SECTOR_HEADER_PX : 0;

    return `
        <div class="heatmap-sector"${sectorAttr}
             style="left:${box.x.toFixed(4)}%; top:${box.y.toFixed(4)}%; width:${box.w.toFixed(4)}%; height:${box.h.toFixed(4)}%;">
            ${title}
            <div class="heatmap-sector-body" style="top:${bodyTop}px;">${tiles}</div>
        </div>
    `;
}

function _overseasCaption(data) {
    const number = _heatmapNumber(data.updated_at);
    if (number === null || number <= 0) return '최신 업데이트: --';
    const date = new Date(number * 1000);
    return Number.isNaN(date.getTime())
        ? '최신 업데이트: --'
        : `최신 업데이트: ${date.toLocaleString('ko-KR')}`;
}

function _heatmapDateText(value) {
    const raw = String(value || '');
    return /^\d{8}$/.test(raw) ? `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}` : null;
}

// 장중에도 전일 종가가 보일 수 있으므로 기준일을 감추지 않는다.
// 기간(period)을 받은 응답은 색이 그 구간 등락률이므로 비교 구간을 함께 밝힌다.
function _domesticCaption(data) {
    const latest = _heatmapDateText(data.trade_date);
    const periodLabel = HEATMAP_PERIOD_LABELS[String(data.period || '')];
    if (!periodLabel) return latest ? `기준일: ${latest} 종가` : '기준일: --';

    const base = _heatmapDateText(data.base_date);
    if (!base) return `${periodLabel} 비교 데이터가 없습니다`;
    return `${periodLabel} 등락률: ${base} → ${latest || '--'} 종가`;
}

function _renderHeatmapCaption(elementId, text) {
    const el = document.getElementById(elementId);
    if (el) el.textContent = text;
}

// source.zoom 은 전용 페이지가 배율을 주입하는 훅. 홈 미리보기는 배율이 없어 1 이다.
function _renderHeatmap(div, source, data) {
    _renderHeatmapCaption(source.captionId, source.caption(data));

    const zoom = typeof source.zoom === 'function' ? source.zoom() : 1;
    const groups = source.toGroups(Array.isArray(data.items) ? data.items : []);
    if (!groups.length) {
        div.innerHTML = '<p class="empty">조회 결과가 없습니다.</p>';
        return;
    }

    const sectorBoxes = _squarifyTreemap(
        groups.map((group, index) => ({ key: index, weight: group.cap })),
        { x: 0, y: 0, w: 100, h: 100 },
    );
    div.innerHTML = `<div class="heatmap-canvas">${
        sectorBoxes.map(box => _heatmapSectorHtml(groups[box.key], box, zoom)).join('')
    }</div>`;
}

// 한 화면에 여러 맵이 있을 수 있으므로 시퀀스 가드는 소스별로 따로 센다.
async function _loadHeatmap(source, options = {}) {
    const requestSequence = ++source.sequence;
    const isLatestRequest = () => requestSequence === source.sequence;

    const div = document.getElementById(source.targetId);
    if (!div) return;
    if (options.showLoading !== false) showLoading(div, source.loadingText);

    // 조회 조건(기간 등)이 바뀌는 소스는 url 을 함수로 준다 — 호출 시점에 평가한다.
    const url = typeof source.url === 'function' ? source.url() : source.url;
    try {
        const res = await fetchWithTimeout(url, {}, 30000);
        const { json, error } = await readJsonResponse(res);
        if (!isLatestRequest()) return;

        if (error) {
            showError(div, `히트맵 조회 실패: ${error}`);
            return;
        }
        if (json.rt_cd !== '0') {
            showError(div, `실패: ${json.msg1 || '히트맵 조회에 실패했습니다.'}`);
            return;
        }
        // 배율을 바꿀 때 재조회 없이 다시 그릴 수 있도록 마지막 응답을 소스에 남긴다.
        source.lastData = json.data || {};
        _renderHeatmap(div, source, source.lastData);
    } catch (e) {
        console.error('[market-heatmap] 히트맵 조회 오류', e);
        if (!isLatestRequest()) return;
        showError(div, e.name === 'AbortError'
            ? '요청 시간이 초과되었습니다. 다시 시도해주세요.'
            : '히트맵을 불러오지 못했습니다. 다시 시도해주세요.');
    }
}

async function _heatmapOverseasEnabled() {
    try {
        const res = await fetchWithTimeout('/api/market-mode', {}, 5000);
        if (!res.ok) return false;
        const json = await res.json();
        return Array.isArray(json.enabled_market_modes) && json.enabled_market_modes.includes('overseas_us');
    } catch (_) {
        return false;
    }
}

