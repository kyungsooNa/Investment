/* view/web/static/js/ranking.js — 랭킹 (상승/하락/거래량/순매수/순매도) */

let _rankingPollTimer = null;
let _rankingCurrentCategory = null;
let _rankingData = [];
let _rankingSortState = { key: null, dir: 'asc' };
let _rankingDirection = null;
let _rankingSelectedInvestors = new Set(['foreign']);
let _rankingAIAnalysisState = { loading: false, result: null, error: null };
let _rankingPeriodDays = 5;
let _rankingPeriodMetric = 'amount';

const _investorTypeFields = {
    'foreign': { pbmn: 'frgn_ntby_tr_pbmn', qty: 'frgn_ntby_qty', isMil: true, label: '외인' },
    'inst':    { pbmn: 'orgn_ntby_tr_pbmn', qty: 'orgn_ntby_qty', isMil: true, label: '기관' },
    'prsn':    { pbmn: 'prsn_ntby_tr_pbmn', qty: 'prsn_ntby_qty', isMil: true, label: '개인' },
    'program': { pbmn: 'whol_smtn_ntby_tr_pbmn', qty: 'whol_smtn_ntby_qty', isMil: false, label: '프로그램' },
};

let _rankingMarketFilter = 'ALL';
let _rankingYtdMarket = 'ALL';

function setMarketFilter(market, btn) {
    _rankingMarketFilter = market;
    _resetRankingAIAnalysis();
    document.querySelectorAll('.market-filter').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    if (_rankingCurrentCategory === 'investor_period') loadPeriodInvestorRanking();
    else if (_rankingDirection) loadInvestorRanking();
    else if (_rankingCurrentCategory) loadRanking(_rankingCurrentCategory);
}

function setYtdMarketFilter(market) {
    _rankingYtdMarket = market;
    document.querySelectorAll('.ytd-market-toggle').forEach(b => {
        b.classList.toggle('active', b.dataset.ytdMarket === market);
    });
    loadRanking('ytd');
}

function _resetRankingAIAnalysis() {
    _rankingAIAnalysisState = { loading: false, result: null, error: null };
}

function _showRankingSkeleton() {
    const div = document.getElementById('ranking-result');
    if (!div) return;
    const rows = Array(10).fill(0).map(() => `
        <tr class="skeleton-row">
            <td><span class="skeleton-bar"></span></td>
            <td><span class="skeleton-bar"></span></td>
            <td><span class="skeleton-bar"></span></td>
            <td><span class="skeleton-bar"></span></td>
            <td><span class="skeleton-bar"></span></td>
        </tr>`).join('');
    div.innerHTML = `<div class="card"><table class="data-table">
        <thead><tr>
            <th>순위</th><th>종목명</th><th>현재가</th><th>등락률</th><th>거래량</th>
        </tr></thead>
        <tbody>${rows}</tbody>
    </table></div>`;
}

async function loadRanking(category) {
    if (_rankingPollTimer) {
        clearTimeout(_rankingPollTimer);
        _rankingPollTimer = null;
    }
    if (window.Paginator) window.Paginator.reset('ranking');
    _rankingCurrentCategory = category;
    _rankingDirection = null;
    _resetRankingAIAnalysis();

    document.querySelectorAll('.ranking-tab').forEach(b => {
        b.classList.remove('active');
        if (b.dataset.cat === category) b.classList.add('active');
    });

    const invRow = document.getElementById('investor-type-row');
    if (invRow) invRow.style.display = 'none';
    const periodRow = document.getElementById('period-investor-row');
    if (periodRow) periodRow.style.display = 'none';
    const ytdMarketRow = document.getElementById('ytd-market-row');
    if (ytdMarketRow) ytdMarketRow.style.display = category === 'ytd' ? '' : 'none';

    const div = document.getElementById('ranking-result');
    _showRankingSkeleton();

    try {
        let res;
        if (category === 'minervini_stage2') {
            res = await fetchWithTimeout(`/api/ranking/minervini_stage2`);
        } else if (category === 'newhigh') {
            res = await fetchWithTimeout(`/api/ranking/newhigh`);
        } else if (category === 'ytd') {
            res = await fetchWithTimeout(`/api/ranking/ytd?market=${_rankingYtdMarket}`);
        } else {
            res = await fetchWithTimeout(`/api/ranking/${category}`);
        }
        const json = await res.json();

        if (json.rt_cd !== "0") {
            div.innerHTML = `<p class="error">실패: ${json.msg1}</p>`;
            return;
        }

        if (!json.data || json.data.length === 0) {
            const isPaperBlock = json.msg1 && json.msg1.includes('실전투자 전용');
            if (isPaperBlock || category === 'ytd') {
                div.innerHTML = `<div class="card" style="text-align:center; padding:40px;">
                    <p style="font-size:1.2em;">${json.msg1}</p>
                </div>`;
            } else {
                _startProgressPolling(category, div);
            }
            return;
        }

        let data = json.data;
        if (category === 'minervini_stage2') {
            data = data.slice().sort((a, b) => parseInt(b.rs_rating || 0) - parseInt(a.rs_rating || 0));
            data.forEach((item, i) => { item.data_rank = String(i + 1); });
        } else if (category === 'newhigh') {
            data = data.slice().sort((a, b) => parseInt(b.market_cap || 0) - parseInt(a.market_cap || 0));
            data.forEach((item, i) => { item.data_rank = String(i + 1); });
        }
        _rankingData = data;
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
    _resetRankingAIAnalysis();

    document.querySelectorAll('.ranking-tab').forEach(b => {
        b.classList.remove('active');
        if (b.dataset.cat === `net_${dir}`) b.classList.add('active');
    });

    const invRow = document.getElementById('investor-type-row');
    if (invRow) invRow.style.display = '';
    const periodRow = document.getElementById('period-investor-row');
    if (periodRow) periodRow.style.display = 'none';

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

    _resetRankingAIAnalysis();
    loadInvestorRanking();
}

function setPeriodInvestorDays(days, btn) {
    _rankingPeriodDays = days;
    document.querySelectorAll('.period-days').forEach(b => {
        b.classList.toggle('active', parseInt(b.dataset.days || 0) === days);
    });
    loadPeriodInvestorRanking();
}

function setPeriodInvestorMetric(metric, btn) {
    _rankingPeriodMetric = metric;
    document.querySelectorAll('.period-metric').forEach(b => {
        b.classList.toggle('active', b.dataset.metric === metric);
    });
    loadPeriodInvestorRanking();
}

async function loadPeriodInvestorRanking() {
    if (_rankingPollTimer) {
        clearTimeout(_rankingPollTimer);
        _rankingPollTimer = null;
    }
    if (window.Paginator) window.Paginator.reset('ranking');
    _rankingCurrentCategory = 'investor_period';
    _rankingDirection = null;
    _rankingSortState = { key: null, dir: 'asc' };
    _resetRankingAIAnalysis();

    document.querySelectorAll('.ranking-tab').forEach(b => {
        b.classList.remove('active');
        if (b.dataset.cat === 'investor_period') b.classList.add('active');
    });

    const invRow = document.getElementById('investor-type-row');
    if (invRow) invRow.style.display = 'none';
    const periodRow = document.getElementById('period-investor-row');
    if (periodRow) periodRow.style.display = '';

    document.querySelectorAll('.period-days').forEach(b => {
        b.classList.toggle('active', parseInt(b.dataset.days || 0) === _rankingPeriodDays);
    });
    document.querySelectorAll('.period-metric').forEach(b => {
        b.classList.toggle('active', b.dataset.metric === _rankingPeriodMetric);
    });

    const div = document.getElementById('ranking-result');
    _showRankingSkeleton();

    try {
        const url = `/api/ranking/investor-period?days=${_rankingPeriodDays}&metric=${_rankingPeriodMetric}&limit=30`;
        const res = await fetchWithTimeout(url, {}, 300000);
        const json = await res.json();

        if (json.rt_cd !== "0") {
            div.innerHTML = `<p class="error">실패: ${json.msg1}</p>`;
            return;
        }

        _rankingData = json.data || [];
        if (_rankingData.length === 0 && (json.msg1 || '').includes('수집 중')) {
            div.innerHTML = `<div class="card" style="text-align:center; padding:40px;">
                <div class="loading-indicator" style="margin-bottom: 12px;"><span class="spinner"></span><span class="loading-text" style="font-size:1.2em;">기간수급 수집 중...</span></div>
                <p style="color:#888; margin-top:8px;">전체 종목을 순회하여 기간 순매수를 집계하고 있습니다. 완료되면 자동 표시됩니다.</p>
            </div>`;
            _rankingPollTimer = setTimeout(() => {
                if (_rankingCurrentCategory === 'investor_period') loadPeriodInvestorRanking();
            }, 10000);
            return;
        }
        renderRankingTable();
    } catch (e) {
        if (e.name === 'AbortError') {
            div.innerHTML = '<p class="error">요청 시간이 초과되었습니다. 기간수급 수집은 전체 종목을 순회하므로 잠시 후 다시 시도해주세요.</p>';
        } else {
            div.innerHTML = "오류: " + e;
        }
    }
}

async function loadInvestorRanking() {
    const dir = _rankingDirection;
    const investors = Array.from(_rankingSelectedInvestors);

    if (window.Paginator) window.Paginator.reset('ranking');
    _rankingSortState = { key: null, dir: 'asc' };
    _rankingCurrentCategory = `investor_${dir}`;

    const div = document.getElementById('ranking-result');
    _showRankingSkeleton();

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
        results.forEach((json, index) => {
            const inv = investors[index];
            const f = _investorTypeFields[inv];

            for (const item of json.data) {
                const code = item.stck_shrn_iscd || '';
                if (!stockMap.has(code)) {
                    stockMap.set(code, { ...item });
                } else {
                    // 동일 종목이 존재하면, 현재 주체의 대금/수량 필드를 기존 데이터에 병합하여 유실 방지
                    const existing = stockMap.get(code);
                    if (item[f.pbmn] !== undefined) existing[f.pbmn] = item[f.pbmn];
                    if (item[f.qty] !== undefined) existing[f.qty] = item[f.qty];
                }
            }
        });

        const merged = Array.from(stockMap.values());
        for (const item of merged) {
            let combinedPbmn = 0;
            let combinedQty = 0;
            let calcLogStr = `[ranking calc] ${item.hts_kor_isnm}(${item.stck_shrn_iscd}) `;
            
            for (const inv of investors) {
                const f = _investorTypeFields[inv];
                let rawPbmn = parseInt(item[f.pbmn] || 0);
                let pbmn = rawPbmn;
                if (f.isMil) pbmn *= 1000000;
                let qty = parseInt(item[f.qty] || 0);
                
                combinedPbmn += pbmn;
                combinedQty += qty;
                
                calcLogStr += `| ${f.label}: 대금(${rawPbmn}->${pbmn}), 수량(${qty}) `;
            }
            calcLogStr += `=> 합산대금=${combinedPbmn}, 합산수량=${combinedQty}`;
            
            item._combined_pbmn = combinedPbmn;
            item._combined_qty = combinedQty;
        }

        merged.sort((a, b) => dir === 'buy'
            ? b._combined_pbmn - a._combined_pbmn
            : a._combined_pbmn - b._combined_pbmn
        );

        const top = merged.slice(0, 30);
        console.log(`[ranking] 정렬 완료. 상위 3개 종목 확인:`, top.slice(0, 3).map(i => ({
            종목명: i.hts_kor_isnm,
            합산대금: i._combined_pbmn,
            합산수량: i._combined_qty
        })));

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

async function loadThemeLeaders() {
    if (_rankingPollTimer) {
        clearTimeout(_rankingPollTimer);
        _rankingPollTimer = null;
    }
    if (window.Paginator) window.Paginator.reset('ranking');
    _rankingCurrentCategory = 'theme_leaders';
    _rankingDirection = null;
    _resetRankingAIAnalysis();

    document.querySelectorAll('.ranking-tab').forEach(b => {
        b.classList.remove('active');
        if (b.dataset.cat === 'theme_leaders') b.classList.add('active');
    });
    const invRow = document.getElementById('investor-type-row');
    if (invRow) invRow.style.display = 'none';
    const periodRow = document.getElementById('period-investor-row');
    if (periodRow) periodRow.style.display = 'none';

    const div = document.getElementById('ranking-result');
    _showRankingSkeleton();

    try {
        const res = await fetchWithTimeout('/api/ranking/themes/intraday');
        const json = await res.json();

        if (json.rt_cd !== "0") {
            div.innerHTML = `<p class="error">실패: ${json.msg1}</p>`;
            return;
        }
        // 빈 데이터는 진행률 폴링이 아니라 전용 안내로 처리한다.
        if (!json.data || json.data.length === 0) {
            div.innerHTML = `<div class="card" style="text-align:center; padding:40px;">
                <p style="font-size:1.1em;">${json.msg1 || '테마 데이터가 아직 수집되지 않았습니다.'}</p>
                <p style="color:#888; margin-top:8px;">장중에는 매분 갱신되며, 최근 3분 값은 스냅샷이 쌓인 뒤 표시됩니다.</p>
            </div>`;
            return;
        }
        renderThemeLeaders(json.data, json.captured_at);
        _rankingPollTimer = setTimeout(() => {
            if (_rankingCurrentCategory === 'theme_leaders') loadThemeLeaders();
        }, 60000);
    } catch (e) {
        if (e.name === 'AbortError') {
            div.innerHTML = '<p class="error">요청 시간이 초과되었습니다. 다시 시도해주세요.</p>';
        } else {
            div.innerHTML = "오류: " + e;
        }
    }
}

function _sourceBadges(sources) {
    const label = { NAVER: '네이버', KIWOOM: '키움', WICS: 'WICS' };
    return (sources || []).map(s =>
        `<span style="display:inline-block; font-size:0.75em; padding:1px 6px; margin-left:4px;
            border-radius:8px; background:var(--accent-secondary,#3b82f6); color:#fff;">${label[s] || s}</span>`
    ).join('');
}

function renderThemeLeaders(groups, capturedAt) {
    const div = document.getElementById('ranking-result');
    const cards = groups.map((g, index) => {
        const recent = parseInt(g.recent_trading_value_won || 0);
        const delta = parseInt(g.recent_trading_value_change_won || 0);
        const deltaClass = delta > 0 ? 'text-red' : (delta < 0 ? 'text-blue' : '');
        const deltaPrefix = delta > 0 ? '+' : '';
        const coverageText = g.recent_coverage_count > 0
            ? `${g.recent_coverage_count}종목 측정`
            : '3분 스냅샷 수집 중';
        const leaders = (g.leaders || []).map(l => {
            const rate = parseFloat(l.change_rate || 0);
            const rateClass = rate > 0 ? 'text-red' : (rate < 0 ? 'text-blue' : '');
            return `<div style="display:grid; grid-template-columns:minmax(110px,1fr) auto auto; gap:12px; align-items:center; padding:9px 0; border-top:1px solid var(--border-color,#eee);">
                <a href="/stock?code=${encodeURIComponent(l.code || '')}" target="_blank" class="stock-link">${escapeHtml(l.name || l.code || '-')}</a>
                <span>${parseInt(l.current_price || 0).toLocaleString()}원</span>
                <span class="${rateClass}">${rate > 0 ? '+' : ''}${rate.toFixed(2)}%</span>
                <span style="grid-column:1 / -1; color:#888; font-size:.86em;">최근 3분 ${l.has_recent_trading_value ? formatTradingValue(l.recent_trading_value_won) : '수집 중'} · 누적 ${formatTradingValue(l.trading_value_won)}</span>
            </div>`;
        }).join('');
        return `<div class="card" style="margin-bottom:12px;">
            <div style="display:flex; gap:12px; align-items:flex-start;">
                <div style="font-size:1.1em; color:#888; min-width:24px;">${index + 1}</div>
                <div style="flex:1; min-width:0;">
                    <div style="display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap;">
                        <h3 style="margin:0;">${escapeHtml(g.normalized_name || '')}${_sourceBadges(g.sources)}</h3>
                        <strong>${g.recent_coverage_count > 0 ? formatTradingValue(recent) : '수집 중'}</strong>
                    </div>
                    <div style="margin:5px 0 10px; color:#888; font-size:.9em;">
                        최근 ${g.recent_window_minutes || 3}분 · ${coverageText}
                        <span class="${deltaClass}" style="margin-left:8px;">직전 구간 대비 ${deltaPrefix}${formatTradingValue(delta)}</span>
                        <span style="margin-left:8px;">상승 확산 ${Number(g.advancing_ratio || 0).toFixed(1)}%</span>
                    </div>
                    ${leaders}
                </div>
            </div>
        </div>`;
    }).join('');
    const updated = capturedAt ? `<div style="color:#888; margin:0 0 10px; text-align:right;">기준 ${escapeHtml(capturedAt)} · 1분 자동 갱신</div>` : '';
    div.innerHTML = updated + cards;
}

function _buildAIAnalysisCandidate(item) {
    const candidate = {
        code: item.stck_shrn_iscd || item.iscd || item.mksc_shrn_iscd || item.code || '',
        name: item.hts_kor_isnm || item.name || '',
        rank: item.data_rank || item.rank || '',
        current_price: item.stck_prpr || item.current_price || '',
        change_rate: item.prdy_ctrt || item.change_rate || '',
        volume: item.acml_vol || '',
        trading_value_won: item.acml_tr_pbmn || item.trading_value || '',
        market_cap: item.market_cap || '',
        rs_rating: item.rs_rating || item.rs || '',
        stage: item.stage || item.minervini_stage || '',
    };
    if (item._combined_pbmn !== undefined) candidate.combined_net_amount_won = item._combined_pbmn;
    if (item._combined_qty !== undefined) candidate.combined_net_qty = item._combined_qty;
    for (const key of [
        'frgn_ntby_tr_pbmn', 'frgn_ntby_qty',
        'orgn_ntby_tr_pbmn', 'orgn_ntby_qty',
        'prsn_ntby_tr_pbmn', 'prsn_ntby_qty',
        'whol_smtn_ntby_tr_pbmn', 'whol_smtn_ntby_qty',
    ]) {
        if (item[key] !== undefined) candidate[key] = item[key];
    }
    return candidate;
}

function _renderRankingAIAnalysisPanel(totalCount) {
    if (!totalCount || _rankingCurrentCategory === 'theme_leaders') return '';

    const disabled = _rankingAIAnalysisState.loading ? 'disabled' : '';
    const buttonText = _rankingAIAnalysisState.loading ? '분석 중...' : 'AI 분석';
    let body = '';
    if (_rankingAIAnalysisState.error) {
        body = `<div style="margin-top:10px; color:var(--text-red); white-space:pre-wrap;">${escapeHtml(_rankingAIAnalysisState.error)}</div>`;
    } else if (_rankingAIAnalysisState.result) {
        const result = _rankingAIAnalysisState.result;
        const meta = [result.provider, result.model].filter(Boolean).join(' · ');
        body = `<div style="margin-top:10px; white-space:pre-wrap; line-height:1.55;">${escapeHtml(result.analysis || '')}</div>`
            + (meta ? `<div style="margin-top:8px; color:#888; font-size:0.9em;">${escapeHtml(meta)}</div>` : '');
    }

    return `<div class="card" style="margin-bottom:12px;">
        <div style="display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap;">
            <div style="font-weight:600;">랭킹 후보 AI 분석</div>
            <button class="btn" onclick="runRankingAIAnalysis()" ${disabled}>${buttonText}</button>
        </div>
        ${body}
    </div>`;
}

async function runRankingAIAnalysis() {
    if (_rankingAIAnalysisState.loading || !_rankingData.length) return;

    _rankingAIAnalysisState = { loading: true, result: null, error: null };
    renderRankingTable();

    try {
        const candidates = _rankingData.slice(0, 20).map(_buildAIAnalysisCandidate);
        const res = await fetchWithTimeout('/api/ranking/ai-analysis', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                candidates,
                max_candidates: 20,
                market_context: {
                    category: _rankingCurrentCategory,
                    direction: _rankingDirection,
                    investors: Array.from(_rankingSelectedInvestors),
                    market_filter: _rankingMarketFilter,
                },
            }),
        }, 60000);
        const json = await res.json();
        if (json.rt_cd !== "0") {
            _rankingAIAnalysisState = { loading: false, result: null, error: json.msg1 || 'AI 분석 실패' };
            renderRankingTable();
            return;
        }
        _rankingAIAnalysisState = { loading: false, result: json.data, error: null };
        renderRankingTable();
    } catch (e) {
        const message = e.name === 'AbortError'
            ? 'AI 분석 요청 시간이 초과되었습니다.'
            : `AI 분석 오류: ${e}`;
        _rankingAIAnalysisState = { loading: false, result: null, error: message };
        renderRankingTable();
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
            const endpoint = category === 'newhigh' ? '/api/ranking/newhigh_progress' : '/api/ranking/progress';
            const res = await fetch(endpoint);
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
    const isPeriodInvestor = cat === 'investor_period';
    const isTradingValue = cat === 'trading_value';
    const isYtd = cat === 'ytd';
    const isSell = cat.endsWith('_sell') || cat === 'investor_sell';

    const sortCls = (key) => {
        if (_rankingSortState.key !== key) return 'sortable';
        return `sortable sort-${_rankingSortState.dir}`;
    };

    const investorLabel = isCombined
        ? Array.from(_rankingSelectedInvestors).map(inv => _investorTypeFields[inv].label).join('+')
        : '';

    let headerRow;
    if (isPeriodInvestor) {
        const unitLabel = _rankingPeriodMetric === 'amount' ? '순매수대금' : '순매수량';
        const totalLabel = _rankingPeriodMetric === 'amount' ? '매수금액(백만)' : '합산순매수량';
        headerRow = `<th class="${sortCls('rank')}" onclick="sortRanking('rank')">순위</th>`
            + `<th class="${sortCls('industry')}" onclick="sortRanking('industry')">업종</th>`
            + `<th class="${sortCls('name')}" onclick="sortRanking('name')">종목명</th>`
            + `<th class="${sortCls('period_foreign')}" onclick="sortRanking('period_foreign')">외국인 ${unitLabel}</th>`
            + `<th class="${sortCls('period_inst')}" onclick="sortRanking('period_inst')">기관 ${unitLabel}</th>`
            + `<th class="${sortCls('period_program')}" onclick="sortRanking('period_program')">프로그램 ${unitLabel}</th>`
            + `<th class="${sortCls('period_combined')}" onclick="sortRanking('period_combined')">${totalLabel}</th>`;
    } else if (isYtd) {
        headerRow = `<th class="${sortCls('rank')}" onclick="sortRanking('rank')">순위</th>`
            + `<th class="${sortCls('name')}" onclick="sortRanking('name')">종목명</th>`
            + `<th class="${sortCls('price')}" onclick="sortRanking('price')">현재가</th>`
            + `<th class="${sortCls('ytd_rate')}" onclick="sortRanking('ytd_rate')">YTD 상승률</th>`
            + `<th class="${sortCls('base_price')}" onclick="sortRanking('base_price')">연초 기준가</th>`
            + `<th class="${sortCls('market_cap')}" onclick="sortRanking('market_cap')">시가총액</th>`;
    } else if (isInvestor || isProgram || isCombined) {
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

    // Minervini Stage 2 전용 헤더
    const isMinervini = (cat === 'minervini_stage2');
    if (isMinervini) {
        headerRow = `<th class="${sortCls('rank')}" onclick="sortRanking('rank')">순위</th>`
            + `<th class="${sortCls('name')}" onclick="sortRanking('name')">종목명</th>`
            + `<th class="${sortCls('price')}" onclick="sortRanking('price')">현재가</th>`
            + `<th class="${sortCls('rate')}" onclick="sortRanking('rate')">등락률</th>`
            + `<th class="${sortCls('stage')}" onclick="sortRanking('stage')">Stage</th>`
            + `<th class="${sortCls('rs')}" onclick="sortRanking('rs')">RS</th>`
            + `<th class="${sortCls('market_cap')}" onclick="sortRanking('market_cap')">시가총액</th>`;
    }

    const isNewHigh = (cat === 'newhigh');
    if (isNewHigh) {
        headerRow = `<th class="${sortCls('rank')}" onclick="sortRanking('rank')">순위</th>`
            + `<th class="${sortCls('name')}" onclick="sortRanking('name')">종목명</th>`
            + `<th class="${sortCls('price')}" onclick="sortRanking('price')">현재가</th>`
            + `<th class="${sortCls('rate')}" onclick="sortRanking('rate')">등락률</th>`
            + `<th class="${sortCls('market_cap')}" onclick="sortRanking('market_cap')">시가총액</th>`
            + `<th class="${sortCls('trading_value')}" onclick="sortRanking('trading_value')">거래대금</th>`
            + `<th class="${sortCls('rs')}" onclick="sortRanking('rs')">RS</th>`
            + `<th class="${sortCls('is_historical')}" onclick="sortRanking('is_historical')">역사적신고가</th>`
            + `<th class="${sortCls('stage')}" onclick="sortRanking('stage')">Stage</th>`;
    }

    let data = _rankingData.slice();
    if (_rankingSortState.key) {
        if (isMinervini) data = _minerviniSortCompare(data, _rankingSortState.key, _rankingSortState.dir);
        else if (isNewHigh) data = rankingSortCompare(data, _rankingSortState.key, _rankingSortState.dir);
        else data = rankingSortCompare(data, _rankingSortState.key, _rankingSortState.dir);
    }

    const pageData = window.Paginator
        ? window.Paginator.paginate('ranking', data, 'ranking-pagination', renderRankingTable)
        : data;

    const formatMarketCap = (val) => {
        if (!val) return '-';
        let v = parseFloat(val);
        if (isNaN(v)) return '-';
        if (v > 100000000) v = v / 100000000; // 원 -> 억 단위 보정
        if (v >= 10000) {
            const jo = Math.floor(v / 10000);
            const uk = Math.floor(v % 10000);
            return jo.toLocaleString() + "조" + (uk > 0 ? " " + uk.toLocaleString() + "억" : "");
        }
        return v.toLocaleString() + "억";
    };
    const formatPeriodAmountMillion = (val) => {
        const n = parseInt(val || 0);
        return isNaN(n) ? '-' : n.toLocaleString();
    };
    const formatPeriodQty = (val) => {
        const n = parseInt(val || 0);
        return isNaN(n) ? '-' : n.toLocaleString();
    };

    const ytdMarketCapRank = isYtd ? _buildMarketCapRankMap(_rankingData) : null;

    let rows = '';
    pageData.forEach(item => {
        const rate = parseFloat(item.prdy_ctrt || 0);
        const color = rate > 0 ? 'text-red' : (rate < 0 ? 'text-blue' : '');
        const code = item.stck_shrn_iscd || item.iscd || item.mksc_shrn_iscd || item.code || '';
        if (isPeriodInvestor) {
            const foreignVal = _rankingPeriodMetric === 'amount'
                ? formatPeriodAmountMillion(item.frgn_period_ntby_tr_pbmn)
                : formatPeriodQty(item.frgn_period_ntby_qty);
            const instVal = _rankingPeriodMetric === 'amount'
                ? formatPeriodAmountMillion(item.orgn_period_ntby_tr_pbmn)
                : formatPeriodQty(item.orgn_period_ntby_qty);
            const programVal = _rankingPeriodMetric === 'amount'
                ? formatPeriodAmountMillion(item.program_period_ntby_tr_pbmn)
                : formatPeriodQty(item.program_period_ntby_qty);
            const combinedVal = _rankingPeriodMetric === 'amount'
                ? formatPeriodAmountMillion(item.combined_period_ntby_tr_pbmn)
                : formatPeriodQty(item.combined_period_ntby_qty);
            rows += `<tr>
                <td>${item.data_rank || item.rank || '-'}</td>
                <td>${escapeHtml(item.industry || '-')}</td>
                <td><a href="/stock?code=${code}" target="_blank" class="stock-link">${escapeHtml(item.hts_kor_isnm || item.name || code)}</a></td>
                <td>${foreignVal}</td>
                <td>${instVal}</td>
                <td>${programVal}</td>
                <td>${combinedVal}</td>
            </tr>`;
            return;
        }
        if (isYtd) {
            const ytdRate = parseFloat(item.ytd_return_rate || 0);
            const ytdColor = ytdRate > 0 ? 'text-red' : (ytdRate < 0 ? 'text-blue' : '');
            const mcapRank = ytdMarketCapRank ? ytdMarketCapRank.get(code) : null;
            rows += `<tr>
                <td>${item.data_rank || item.rank || '-'}</td>
                <td><a href="/stock?code=${code}" target="_blank" class="stock-link">${escapeHtml(item.name || code)}</a></td>
                <td>${parseInt(item.current_price || 0).toLocaleString()}</td>
                <td class="${ytdColor}">${ytdRate.toFixed(2)}%</td>
                <td>${parseInt(item.base_price || 0).toLocaleString()}</td>
                <td>${formatMarketCap(item.market_cap)}${mcapRank ? ` (${mcapRank}위)` : ''}</td>
            </tr>`;
            return;
        }
        let extraCols;
        if (isMinervini) {
            extraCols = `<td>S${item.stage}</td><td>${item.rs_rating || '-'}</td><td>${formatMarketCap(item.market_cap || 0)}</td>`;
        } else if (isNewHigh) {
            const isHist = item.is_historical_new_high || item.is_historical_newhigh;
            const histBadge = isHist ? '<span style="color:var(--text-red); font-weight:bold;">O</span>' : '<span style="color:#aaa;">X</span>';
            const tv = parseInt(item.trading_value || item.acml_tr_pbmn || 0);
            const tvFmt = tv > 0 ? formatTradingValue(tv) : '-';
            const stage = parseInt(item.minervini_stage || 0);
            const stageBadge = stage > 0 ? `<span style="color:${stage === 2 ? 'var(--text-red)' : '#aaa'};">S${stage}</span>` : '<span style="color:#aaa;">-</span>';
            extraCols = `<td>${formatMarketCap(item.market_cap || 0)}</td><td>${tvFmt}</td><td>${item.rs_rating || item.rs || '-'}</td><td>${histBadge}</td><td>${stageBadge}</td>`;
        } else
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

    const periodLabel = isPeriodInvestor ? _renderPeriodRankingLabel(data) : '';
    const ytdLabel = isYtd ? _renderYtdRankingLabel(data) : '';

    div.innerHTML = `${periodLabel}${ytdLabel}${_renderRankingAIAnalysisPanel(data.length)}<div class="card"><table class="data-table">
        <thead><tr>${headerRow}</tr></thead>
        <tbody>${rows}</tbody></table></div>`;
}

function _renderPeriodRankingLabel(data) {
    const raw = data[0] && data[0].latest_trading_date;
    const dateLabel = raw && raw.length === 8
        ? ` (~ ${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)})`
        : '';
    return `<p style="color:#888; margin: 4px 0 12px;">최근 ${_rankingPeriodDays}거래일 기준${dateLabel}</p>`;
}

function _buildMarketCapRankMap(list) {
    const map = new Map();
    const sorted = list.slice().sort((a, b) => parseInt(b.market_cap || 0) - parseInt(a.market_cap || 0));
    sorted.forEach((item, idx) => {
        const code = item.stck_shrn_iscd || item.iscd || item.mksc_shrn_iscd || item.code || '';
        if (code) map.set(code, idx + 1);
    });
    return map;
}

function _renderYtdRankingLabel(data) {
    const raw = data[0] && data[0].base_date;
    const dateLabel = raw && raw.length === 8
        ? `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}`
        : '-';
    return `<p style="color:#888; margin: 4px 0 12px;">연초 기준일: ${dateLabel}</p>`;
}

function sortRanking(key) {
    if (_rankingSortState.key === key) {
        _rankingSortState.dir = _rankingSortState.dir === 'asc' ? 'desc' : 'asc';
    } else {
        _rankingSortState.key = key;
        _rankingSortState.dir = 'asc';
    }
    if (window.Paginator) window.Paginator.reset('ranking');
    renderRankingTable();
}

function rankingSortCompare(data, key, dir) {
    const cat = _rankingCurrentCategory;
    const isCombined = cat === 'investor_buy' || cat === 'investor_sell';
    const isPeriodInvestor = cat === 'investor_period';
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
        } else if (key === 'industry') {
            va = (a.industry || '').toLowerCase();
            vb = (b.industry || '').toLowerCase();
            return d * va.localeCompare(vb);
        } else if (key === 'price') {
            va = parseInt(a.stck_prpr || a.current_price || 0);
            vb = parseInt(b.stck_prpr || b.current_price || 0);
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
        } else if (key === 'period_foreign' && isPeriodInvestor) {
            va = parseInt(_rankingPeriodMetric === 'amount' ? a.frgn_period_ntby_tr_pbmn_won || 0 : a.frgn_period_ntby_qty || 0);
            vb = parseInt(_rankingPeriodMetric === 'amount' ? b.frgn_period_ntby_tr_pbmn_won || 0 : b.frgn_period_ntby_qty || 0);
        } else if (key === 'period_inst' && isPeriodInvestor) {
            va = parseInt(_rankingPeriodMetric === 'amount' ? a.orgn_period_ntby_tr_pbmn_won || 0 : a.orgn_period_ntby_qty || 0);
            vb = parseInt(_rankingPeriodMetric === 'amount' ? b.orgn_period_ntby_tr_pbmn_won || 0 : b.orgn_period_ntby_qty || 0);
        } else if (key === 'period_program' && isPeriodInvestor) {
            va = parseInt(_rankingPeriodMetric === 'amount' ? a.program_period_ntby_tr_pbmn_won || 0 : a.program_period_ntby_qty || 0);
            vb = parseInt(_rankingPeriodMetric === 'amount' ? b.program_period_ntby_tr_pbmn_won || 0 : b.program_period_ntby_qty || 0);
        } else if (key === 'period_combined' && isPeriodInvestor) {
            va = parseInt(_rankingPeriodMetric === 'amount' ? a.combined_period_ntby_tr_pbmn_won || 0 : a.combined_period_ntby_qty || 0);
            vb = parseInt(_rankingPeriodMetric === 'amount' ? b.combined_period_ntby_tr_pbmn_won || 0 : b.combined_period_ntby_qty || 0);
        } else if (key === 'ytd_rate') {
            va = parseFloat(a.ytd_return_rate || 0);
            vb = parseFloat(b.ytd_return_rate || 0);
        } else if (key === 'base_price') {
            va = parseInt(a.base_price || 0);
            vb = parseInt(b.base_price || 0);
        } else if (key === 'market_cap') {
            va = parseInt(a.market_cap || 0);
            vb = parseInt(b.market_cap || 0);
        } else if (key === 'trading_value') {
            va = parseInt(a.trading_value || a.acml_tr_pbmn || 0);
            vb = parseInt(b.trading_value || b.acml_tr_pbmn || 0);
        } else if (key === 'rs') {
            va = parseInt(a.rs_rating || a.rs || 0);
            vb = parseInt(b.rs_rating || b.rs || 0);
        } else if (key === 'is_historical') {
            va = (a.is_historical_new_high || a.is_historical_newhigh) ? 1 : 0;
            vb = (b.is_historical_new_high || b.is_historical_newhigh) ? 1 : 0;
        } else if (key === 'stage') {
            va = parseInt(a.minervini_stage || 0);
            vb = parseInt(b.minervini_stage || 0);
        } else {
            return 0;
        }
        return d * (va - vb);
    });
    return sorted;
}

    // 추가: Minervini 전용 정렬 지원
    function _minerviniSortCompare(data, key, dir) {
        const sorted = data.slice();
        const d = dir === 'asc' ? 1 : -1;
        sorted.sort((a, b) => {
            let va = 0, vb = 0;
            if (key === 'name') {
                va = (a.name || '').toLowerCase();
                vb = (b.name || '').toLowerCase();
                return d * va.localeCompare(vb);
            } else if (key === 'price') {
                va = parseInt(a.stck_prpr || 0);
                vb = parseInt(b.stck_prpr || 0);
            } else if (key === 'rate') {
                va = parseFloat(a.prdy_ctrt || 0);
                vb = parseFloat(b.prdy_ctrt || 0);
            } else if (key === 'stage') {
                va = parseInt(a.stage || 0);
                vb = parseInt(b.stage || 0);
            } else if (key === 'rs') {
                va = parseInt(a.rs_rating || 0);
                vb = parseInt(b.rs_rating || 0);
            } else if (key === 'market_cap') {
                va = parseInt(a.market_cap || 0);
                vb = parseInt(b.market_cap || 0);
            } else if (key === 'rank') {
                va = parseInt(a.data_rank || a.rank || 0);
                vb = parseInt(b.data_rank || b.rank || 0);
            } else {
                return 0;
            }
            return d * (va - vb);
        });
        return sorted;
    }
