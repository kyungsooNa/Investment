/* view/web/static/js/market_heatmap.js — 홈 화면 S&P 500 섹터 히트맵(트리맵)
 *
 * 데이터는 미국 시가총액 화면과 같은 /api/overseas/top-market-cap 스냅샷을 쓴다.
 * (Yahoo 스냅샷 1회로 sector/change_rate/market_cap_usd 가 모두 오고, 서비스 TTL 캐시를 공유한다.)
 * 타일 좌표는 % 로만 계산해 컨테이너 크기와 무관하게 반응형으로 그린다.
 *
 * 색상은 국내 화면 컨벤션(상승=빨강, 하락=파랑)을 따른다.
 */

const HEATMAP_LIMIT = 500;
const HEATMAP_REFRESH_MS = 5 * 60 * 1000;
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

let _heatmapRequestSequence = 0;
let _heatmapRefreshTimer = null;

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

function _groupBySector(items) {
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
            name: String(row.name ?? ''),
            rate: _heatmapNumber(row.change_rate),
            cap,
        });
    });

    const groupList = [...groups.values()].sort((a, b) => b.cap - a.cap);
    groupList.forEach(group => group.members.sort((a, b) => b.cap - a.cap));
    return groupList;
}

function _heatmapTileHtml(member, box) {
    const rateText = _heatmapRateText(member.rate);
    const showLabel = box.w >= HEATMAP_MIN_LABEL_WIDTH_PCT && box.h >= HEATMAP_MIN_LABEL_HEIGHT_PCT;
    const label = showLabel
        ? `<span class="heatmap-tile-symbol">${escapeHtml(member.symbol)}</span>`
          + `<span class="heatmap-tile-rate">${escapeHtml(rateText)}</span>`
        : '';
    const tooltip = `${member.symbol} ${member.name} | ${rateText} | ${_heatmapCapText(member.cap)}`;
    return `
        <div class="heatmap-tile"
             data-symbol="${escapeHtml(member.symbol)}"
             data-direction="${_heatmapDirection(member.rate)}"
             title="${escapeHtml(tooltip)}"
             style="left:${box.x.toFixed(4)}%; top:${box.y.toFixed(4)}%; width:${box.w.toFixed(4)}%; height:${box.h.toFixed(4)}%; background-color:${_heatmapColor(member.rate)};">
            ${label}
        </div>
    `;
}

function _heatmapSectorHtml(group, box) {
    const memberBoxes = _squarifyTreemap(
        group.members.map((member, index) => ({ key: index, weight: member.cap })),
        { x: 0, y: 0, w: 100, h: 100 },
    );
    const tiles = memberBoxes
        .map(memberBox => _heatmapTileHtml(group.members[memberBox.key], memberBox))
        .join('');

    return `
        <div class="heatmap-sector" data-sector="${escapeHtml(group.sector)}"
             style="left:${box.x.toFixed(4)}%; top:${box.y.toFixed(4)}%; width:${box.w.toFixed(4)}%; height:${box.h.toFixed(4)}%;">
            <div class="heatmap-sector-title">${escapeHtml(group.sector)}</div>
            <div class="heatmap-sector-body" style="top:${HEATMAP_SECTOR_HEADER_PX}px;">${tiles}</div>
        </div>
    `;
}

function _renderHeatmapUpdatedAt(value) {
    const el = document.getElementById('market-heatmap-updated-at');
    if (!el) return;
    const number = _heatmapNumber(value);
    if (number === null || number <= 0) {
        el.textContent = '최신 업데이트: --';
        return;
    }
    const date = new Date(number * 1000);
    el.textContent = Number.isNaN(date.getTime())
        ? '최신 업데이트: --'
        : `최신 업데이트: ${date.toLocaleString('ko-KR')}`;
}

function _renderMarketHeatmap(div, data) {
    _renderHeatmapUpdatedAt(data.updated_at);

    const groups = _groupBySector(Array.isArray(data.items) ? data.items : []);
    if (!groups.length) {
        div.innerHTML = '<p class="empty">조회 결과가 없습니다.</p>';
        return;
    }

    const sectorBoxes = _squarifyTreemap(
        groups.map((group, index) => ({ key: index, weight: group.cap })),
        { x: 0, y: 0, w: 100, h: 100 },
    );
    div.innerHTML = `<div class="heatmap-canvas">${
        sectorBoxes.map(box => _heatmapSectorHtml(groups[box.key], box)).join('')
    }</div>`;
}

async function loadMarketHeatmap(options = {}) {
    const requestSequence = ++_heatmapRequestSequence;
    const isLatestRequest = () => requestSequence === _heatmapRequestSequence;

    const div = document.getElementById('market-heatmap');
    if (!div) return;
    if (options.showLoading !== false) showLoading(div, 'S&P 500 히트맵 조회 중...');

    try {
        const res = await fetchWithTimeout(`/api/overseas/top-market-cap?limit=${HEATMAP_LIMIT}`, {}, 30000);
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
        _renderMarketHeatmap(div, json.data || {});
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

async function initMarketHeatmap() {
    const card = document.getElementById('market-heatmap-card');
    if (!card) return;

    // 미국장이 꺼진 run 에서는 API 가 400 을 내므로, 오류 대신 카드를 감춘다.
    if (!await _heatmapOverseasEnabled()) {
        card.style.display = 'none';
        return;
    }
    card.style.display = '';

    await loadMarketHeatmap();
    if (_heatmapRefreshTimer !== null) return;
    _heatmapRefreshTimer = setInterval(() => {
        void loadMarketHeatmap({ showLoading: false });
    }, HEATMAP_REFRESH_MS);
}

document.addEventListener('DOMContentLoaded', () => {
    if (window.location.pathname !== '/') return;
    void initMarketHeatmap();
});

document.addEventListener('pjax:ready', (e) => {
    if (e.detail?.path !== '/') return;
    void initMarketHeatmap();
});
