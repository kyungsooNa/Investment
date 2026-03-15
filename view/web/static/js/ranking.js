/* view/web/static/js/ranking.js — 랭킹 (상승/하락/거래량/순매수/순매도) */

let _rankingPollTimer = null;
let _rankingCurrentCategory = null;
let _rankingData = [];
let _rankingSortState = { key: null, dir: 'asc' };
let _rankingDirection = null;
let _rankingSelectedInvestors = new Set(['foreign']);

const _investorTypeFields = {
    'foreign': { pbmn: 'frgn_ntby_tr_pbmn', qty: 'frgn_ntby_qty', isMil: true, label: '외인' },
    'inst':    { pbmn: 'orgn_ntby_tr_pbmn', qty: 'orgn_ntby_qty', isMil: true, label: '기관' },
    'prsn':    { pbmn: 'prsn_ntby_tr_pbmn', qty: 'prsn_ntby_qty', isMil: true, label: '개인' },
    'program': { pbmn: 'whol_smtn_ntby_tr_pbmn', qty: 'whol_smtn_ntby_qty', isMil: false, label: '프로그램' },
};

let _rankingMarketFilter = 'ALL';

function setMarketFilter(market, btn) {
    _rankingMarketFilter = market;
    document.querySelectorAll('.market-filter').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    if (_rankingDirection) loadInvestorRanking();
    else if (_rankingCurrentCategory) loadRanking(_rankingCurrentCategory);
}

async function loadRanking(category) {
    if (_rankingPollTimer) {
        clearTimeout(_rankingPollTimer);
        _rankingPollTimer = null;
    }
    _rankingCurrentCategory = category;
    _rankingDirection = null;

    document.querySelectorAll('.ranking-tab').forEach(b => {
        b.classList.remove('active');
        if (b.dataset.cat === category) b.classList.add('active');
    });

    const invRow = document.getElementById('investor-type-row');
    if (invRow) invRow.style.display = 'none';

    const div = document.getElementById('ranking-result');
    showLoading(div, '랭킹 데이터 조회 중...');

    try {
        const res = await fetchWithTimeout(`/api/ranking/${category}`);
        const json = await res.json();

        if (json.rt_cd !== "0") {
            div.innerHTML = `<p class="error">실패: ${json.msg1}</p>`;
            return;
        }

        if (!json.data || json.data.length === 0) {
            const isPaperBlock = json.msg1 && json.msg1.includes('실전투자 전용');
            if (isPaperBlock) {
                div.innerHTML = `<div class="card" style="text-align:center; padding:40px;">
                    <p style="font-size:1.2em;">${json.msg1}</p>
                </div>`;
            } else {
                _startProgressPolling(category, div);
            }
            return;
        }

        _rankingData = json.data;
        _rankingSortState = { key: null, dir: 'asc' };
        renderRankingTable();

    } catch (e) {
        if (e.name === 'AbortError') {
            div.innerHTML = '<p class="error">요청 시간이 초과되었습니다. 다시 시도해주세요.</p>';
        } else {
            div.innerHTML = "오류: " + e;
        }
    }
}

function setRankingDirection(dir) {
    if (_rankingPollTimer) {
        clearTimeout(_rankingPollTimer);
        _rankingPollTimer = null;
    }
    _rankingDirection = dir;

    document.querySelectorAll('.ranking-tab').forEach(b => {
        b.classList.remove('active');
        if (b.dataset.cat === `net_${dir}`) b.classList.add('active');
    });

    const invRow = document.getElementById('investor-type-row');
    if (invRow) invRow.style.display = '';

    document.querySelectorAll('.investor-toggle').forEach(b => {
        b.classList.toggle('active', _rankingSelectedInvestors.has(b.dataset.inv));
    });

    loadInvestorRanking();
}

function toggleRankingInvestor(type) {
    if (_rankingSelectedInvestors.has(type)) {
        if (_rankingSelectedInvestors.size > 1) {
            _rankingSelectedInvestors.delete(type);
        }
    } else {
        _rankingSelectedInvestors.add(type);
    }

    document.querySelectorAll('.investor-toggle').forEach(b => {
        b.classList.toggle('active', _rankingSelectedInvestors.has(b.dataset.inv));
    });

    loadInvestorRanking();
}

async function loadInvestorRanking() {
    const dir = _rankingDirection;
    const investors = Array.from(_rankingSelectedInvestors);

    _rankingSortState = { key: null, dir: 'asc' };
    _rankingCurrentCategory = `investor_${dir}`;

    const div = document.getElementById('ranking-result');
    showLoading(div, '투자자별 랭킹 조회 중...');

    const categories = investors.map(inv => `${inv}_${dir}`);

    try {
        const results = await Promise.all(
            categories.map(cat => fetchWithTimeout(`/api/ranking/${cat}`).then(r => r.json()))
        );

        for (const json of results) {
            if (json.rt_cd !== "0") {
                div.innerHTML = `<p class="error">실패: ${json.msg1}</p>`;
                return;
            }
            if (!json.data || json.data.length === 0) {
                if (json.msg1 && json.msg1.includes('실전투자 전용')) {
                    div.innerHTML = `<div class="card" style="text-align:center; padding:40px;">
                        <p style="font-size:1.2em;">${json.msg1}</p></div>`;
                    return;
                }
                _startProgressPolling(categories[0], div);
                return;
            }
        }

        const stockMap = new Map();
        for (const json of results) {
            for (const item of json.data) {
                const code = item.stck_shrn_iscd || '';
                if (!stockMap.has(code)) {
                    stockMap.set(code, { ...item });
                }
            }
        }

        const merged = Array.from(stockMap.values());
        for (const item of merged) {
            let combinedPbmn = 0;
            let combinedQty = 0;
            for (const inv of investors) {
                const f = _investorTypeFields[inv];
                let pbmn = parseInt(item[f.pbmn] || 0);
                if (f.isMil) pbmn *= 1000000;
                combinedPbmn += pbmn;
                combinedQty += parseInt(item[f.qty] || 0);
            }
            item._combined_pbmn = combinedPbmn;
            item._combined_qty = combinedQty;
        }

        merged.sort((a, b) => dir === 'buy'
            ? b._combined_pbmn - a._combined_pbmn
            : a._combined_pbmn - b._combined_pbmn
        );

        const top = merged.slice(0, 30);
        top.forEach((item, i) => { item.data_rank = String(i + 1); });

        _rankingData = top;
        renderRankingTable();

    } catch (e) {
        if (e.name === 'AbortError') {
            div.innerHTML = '<p class="error">요청 시간이 초과되었습니다. 다시 시도해주세요.</p>';
        } else {
            div.innerHTML = "오류: " + e;
        }
    }
}

function _formatElapsed(sec) {
    if (sec < 60) return `${sec.toFixed(1)}s`;
    const m = Math.floor(sec / 60);
    const s = (sec % 60).toFixed(1);
    return `${m}m ${s}s`;
}

function _startProgressPolling(category, div) {
    div.innerHTML = `<div class="card" style="text-align:center; padding:40px;">
        <div class="loading-indicator" style="margin-bottom: 12px;"><span class="spinner"></span><span class="loading-text" style="font-size:1.2em;">데이터 수집 중...</span></div>
        <p id="ranking-progress-text" style="color:#888; margin-top:8px;">전체 종목을 순회하여 랭킹을 생성하고 있습니다. 잠시만 기다려주세요.</p>
    </div>`;

    const poll = async () => {
        if (_rankingCurrentCategory !== category
            && _rankingCurrentCategory !== 'investor_buy'
            && _rankingCurrentCategory !== 'investor_sell') return;
        try {
            const res = await fetch('/api/ranking/progress');
            const p = await res.json();
            const el = document.getElementById('ranking-progress-text');
            if (el && p.total > 0) {
                const pct = (p.processed / p.total * 100).toFixed(1);
                el.textContent = `${p.processed}/${p.total} — ${pct}% | 수집: ${p.collected} | 소요: ${_formatElapsed(p.elapsed)}`;
            }
            if (!p.running && p.processed > 0 && p.processed >= p.total) {
                if (_rankingDirection) loadInvestorRanking();
                else loadRanking(category);
                return;
            }
        } catch (_) { /* ignore */ }
        _rankingPollTimer = setTimeout(poll, 2000);
    };
    _rankingPollTimer = setTimeout(poll, 1000);
}

const _rankingPbmnField = {
    'foreign_buy': 'frgn_ntby_tr_pbmn', 'foreign_sell': 'frgn_ntby_tr_pbmn',
    'inst_buy': 'orgn_ntby_tr_pbmn', 'inst_sell': 'orgn_ntby_tr_pbmn',
    'prsn_buy': 'prsn_ntby_tr_pbmn', 'prsn_sell': 'prsn_ntby_tr_pbmn',
    'program_buy': 'whol_smtn_ntby_tr_pbmn', 'program_sell': 'whol_smtn_ntby_tr_pbmn',
};
const _rankingQtyField = {
    'foreign_buy': 'frgn_ntby_qty', 'foreign_sell': 'frgn_ntby_qty',
    'inst_buy': 'orgn_ntby_qty', 'inst_sell': 'orgn_ntby_qty',
    'prsn_buy': 'prsn_ntby_qty', 'prsn_sell': 'prsn_ntby_qty',
    'program_buy': 'whol_smtn_ntby_qty', 'program_sell': 'whol_smtn_ntby_qty',
};

function renderRankingTable() {
    const div = document.getElementById('ranking-result');
    if (!div) return;
    const cat = _rankingCurrentCategory;
    const isInvestor = ['foreign_buy', 'foreign_sell', 'inst_buy', 'inst_sell', 'prsn_buy', 'prsn_sell'].includes(cat);
    const isProgram = ['program_buy', 'program_sell'].includes(cat);
    const isCombined = cat === 'investor_buy' || cat === 'investor_sell';
    const isTradingValue = cat === 'trading_value';
    const isSell = cat.endsWith('_sell') || cat === 'investor_sell';

    const sortCls = (key) => {
        if (_rankingSortState.key !== key) return 'sortable';
        return `sortable sort-${_rankingSortState.dir}`;
    };

    const investorLabel = isCombined
        ? Array.from(_rankingSelectedInvestors).map(inv => _investorTypeFields[inv].label).join('+')
        : '';

    let headerRow;
    if (isInvestor || isProgram || isCombined) {
        const pbmnLabel = isCombined
            ? `${investorLabel} ${isSell ? '순매도대금' : '순매수대금'}`
            : (isSell ? '순매도대금' : '순매수대금');
        const qtyLabel = isCombined
            ? `${investorLabel} ${isSell ? '순매도량' : '순매수량'}`
            : (isSell ? '순매도량' : '순매수량');
        headerRow = `<th class="${sortCls('rank')}" onclick="sortRanking('rank')">순위</th>`
            + `<th class="${sortCls('name')}" onclick="sortRanking('name')">종목명</th>`
            + `<th class="${sortCls('price')}" onclick="sortRanking('price')">현재가</th>`
            + `<th class="${sortCls('rate')}" onclick="sortRanking('rate')">등락률</th>`
            + `<th class="${sortCls('ntby_pbmn')}" onclick="sortRanking('ntby_pbmn')">${pbmnLabel}</th>`
            + `<th class="${sortCls('ntby_qty')}" onclick="sortRanking('ntby_qty')">${qtyLabel}</th>`
            + `<th class="${sortCls('ratio')}" onclick="sortRanking('ratio')">거래대금비(거래대금)</th>`;
    } else if (isTradingValue) {
        headerRow = `<th class="${sortCls('rank')}" onclick="sortRanking('rank')">순위</th>`
            + `<th class="${sortCls('name')}" onclick="sortRanking('name')">종목명</th>`
            + `<th class="${sortCls('price')}" onclick="sortRanking('price')">현재가</th>`
            + `<th class="${sortCls('rate')}" onclick="sortRanking('rate')">등락률</th>`
            + `<th class="${sortCls('tr_pbmn')}" onclick="sortRanking('tr_pbmn')">거래대금</th>`;
    } else {
        headerRow = `<th class="${sortCls('rank')}" onclick="sortRanking('rank')">순위</th>`
            + `<th class="${sortCls('name')}" onclick="sortRanking('name')">종목명</th>`
            + `<th class="${sortCls('price')}" onclick="sortRanking('price')">현재가</th>`
            + `<th class="${sortCls('rate')}" onclick="sortRanking('rate')">등락률</th>`
            + `<th class="${sortCls('volume')}" onclick="sortRanking('volume')">거래량</th>`;
    }

    let data = _rankingData.slice();
    if (_rankingSortState.key) {
        data = rankingSortCompare(data, _rankingSortState.key, _rankingSortState.dir);
    }

    let rows = '';
    data.forEach(item => {
        const rate = parseFloat(item.prdy_ctrt || 0);
        const color = rate > 0 ? 'text-red' : (rate < 0 ? 'text-blue' : '');
        const code = item.stck_shrn_iscd || item.iscd || item.mksc_shrn_iscd || item.code || '';
        let extraCols;
        if (isCombined) {
            const pbmnVal = formatTradingValue(String(item._combined_pbmn));
            const qtyVal = parseInt(item._combined_qty || 0).toLocaleString();
            const acmlTr = parseInt(item.acml_tr_pbmn || 0);
            const ratio = acmlTr ? ((item._combined_pbmn / acmlTr) * 100).toFixed(1) : '-';
            const acmlTrFmt = formatTradingValue(item.acml_tr_pbmn);
            extraCols = `<td>${pbmnVal}</td><td>${qtyVal}</td><td>${ratio}% (${acmlTrFmt})</td>`;
        } else if (isInvestor || isProgram) {
            const isMil = isInvestor;
            const pbmnVal = formatTradingValue(item[_rankingPbmnField[cat]], isMil);
            const qtyVal = parseInt(item[_rankingQtyField[cat]] || 0).toLocaleString();
            let rawNtby = parseInt(item[_rankingPbmnField[cat]] || 0);
            if (isMil) rawNtby *= 1000000;
            const acmlTr = parseInt(item.acml_tr_pbmn || 0);
            const ratio = acmlTr ? ((rawNtby / acmlTr) * 100).toFixed(1) : '-';
            const acmlTrFmt = formatTradingValue(item.acml_tr_pbmn);
            extraCols = `<td>${pbmnVal}</td><td>${qtyVal}</td><td>${ratio}% (${acmlTrFmt})</td>`;
        } else if (isTradingValue) {
            extraCols = `<td>${formatTradingValue(item.acml_tr_pbmn)}</td>`;
        } else {
            extraCols = `<td>${parseInt(item.acml_vol || 0).toLocaleString()}</td>`;
        }
        rows += `<tr>
            <td>${item.data_rank || item.rank || '-'}</td>
            <td><a href="/stock?code=${code}" target="_blank" class="stock-link">${item.hts_kor_isnm || item.name}</a></td>
            <td>${parseInt(item.stck_prpr || 0).toLocaleString()}</td>
            <td class="${color}">${rate}%</td>
            ${extraCols}
        </tr>`;
    });

    div.innerHTML = `<div class="card"><table class="data-table">
        <thead><tr>${headerRow}</tr></thead>
        <tbody>${rows}</tbody></table></div>`;
}

function sortRanking(key) {
    if (_rankingSortState.key === key) {
        _rankingSortState.dir = _rankingSortState.dir === 'asc' ? 'desc' : 'asc';
    } else {
        _rankingSortState.key = key;
        _rankingSortState.dir = 'asc';
    }
    renderRankingTable();
}

function rankingSortCompare(data, key, dir) {
    const cat = _rankingCurrentCategory;
    const isCombined = cat === 'investor_buy' || cat === 'investor_sell';
    const isInvestor = ['foreign_buy', 'foreign_sell', 'inst_buy', 'inst_sell', 'prsn_buy', 'prsn_sell'].includes(cat);
    const sorted = data.slice();
    const d = dir === 'asc' ? 1 : -1;
    sorted.sort((a, b) => {
        let va, vb;
        if (key === 'rank') {
            va = parseInt(a.data_rank || a.rank || 0);
            vb = parseInt(b.data_rank || b.rank || 0);
        } else if (key === 'name') {
            va = (a.hts_kor_isnm || a.name || '').toLowerCase();
            vb = (b.hts_kor_isnm || b.name || '').toLowerCase();
            return d * va.localeCompare(vb);
        } else if (key === 'price') {
            va = parseInt(a.stck_prpr || 0);
            vb = parseInt(b.stck_prpr || 0);
        } else if (key === 'rate') {
            va = parseFloat(a.prdy_ctrt || 0);
            vb = parseFloat(b.prdy_ctrt || 0);
        } else if (key === 'volume') {
            va = parseInt(a.acml_vol || 0);
            vb = parseInt(b.acml_vol || 0);
        } else if (key === 'tr_pbmn') {
            va = parseInt(a.acml_tr_pbmn || 0);
            vb = parseInt(b.acml_tr_pbmn || 0);
        } else if (key === 'ntby_pbmn') {
            if (isCombined) {
                va = a._combined_pbmn || 0;
                vb = b._combined_pbmn || 0;
            } else {
                va = parseInt(a[_rankingPbmnField[cat]] || 0);
                vb = parseInt(b[_rankingPbmnField[cat]] || 0);
            }
        } else if (key === 'ntby_qty') {
            if (isCombined) {
                va = a._combined_qty || 0;
                vb = b._combined_qty || 0;
            } else {
                va = parseInt(a[_rankingQtyField[cat]] || 0);
                vb = parseInt(b[_rankingQtyField[cat]] || 0);
            }
        } else if (key === 'ratio') {
            if (isCombined) {
                const acmlA = parseInt(a.acml_tr_pbmn || 0);
                const acmlB = parseInt(b.acml_tr_pbmn || 0);
                va = acmlA ? ((a._combined_pbmn || 0) / acmlA) : 0;
                vb = acmlB ? ((b._combined_pbmn || 0) / acmlB) : 0;
            } else {
                const isMil = isInvestor;
                let rawA = parseInt(a[_rankingPbmnField[cat]] || 0);
                let rawB = parseInt(b[_rankingPbmnField[cat]] || 0);
                if (isMil) { rawA *= 1000000; rawB *= 1000000; }
                const acmlA = parseInt(a.acml_tr_pbmn || 0);
                const acmlB = parseInt(b.acml_tr_pbmn || 0);
                va = acmlA ? (rawA / acmlA) : 0;
                vb = acmlB ? (rawB / acmlB) : 0;
            }
        } else {
            return 0;
        }
        return d * (va - vb);
    });
    return sorted;
}
