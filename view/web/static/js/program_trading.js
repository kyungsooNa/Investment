/* view/web/static/js/program_trading.js — 프로그램매매 실시간 + 데이터 영속성 */

let ptEventSource = null;
let ptChartData = {};

let ptRowCount = 0;
let ptSubscribedCodes = new Set();
let ptCodeNameMap = {};
let ptFilterCodes = new Set();
let ptDataDirty = false;
let ptTimeUnit = 1;
let ptResubscribing = false;

// ==========================================
// SSE 연결
// ==========================================
function connectPtEventSource() {
    if (ptEventSource) return;
    ptEventSource = new EventSource('/api/program-trading/stream');
    ptEventSource.onmessage = (event) => {
        const d = JSON.parse(event.data);
        handleProgramTradingData(d);
    };
    ptEventSource.onerror = () => {
        const statusDiv = document.getElementById('pt-status');
        if (statusDiv) statusDiv.innerHTML = '<span class="text-red">SSE 연결 끊김 — 재연결 시도 중...</span>';
    };
    ptEventSource.onopen = async () => {
        if (ptSubscribedCodes.size === 0 || ptResubscribing) return;
        ptResubscribing = true;
        try {
            for (const code of ptSubscribedCodes) {
                try {
                    await fetch('/api/program-trading/subscribe', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ code })
                    });
                } catch (e) {
                    console.error(`[PT] Resubscribe failed: ${code}`, e);
                }
            }
            const statusDiv = document.getElementById('pt-status');
            if (statusDiv) statusDiv.innerHTML = `<span class="text-green">구독 중: ${ptSubscribedCodes.size}개 종목</span>`;
        } finally {
            ptResubscribing = false;
        }
    };
}

// ==========================================
// 구독 관리
// ==========================================
async function addProgramTrading() {
    const input = document.getElementById('pt-code-input');
    const code = input.value.trim();
    if (!code) { alert('종목코드를 입력하세요.'); return; }
    if (ptSubscribedCodes.has(code)) { alert('이미 구독 중인 종목입니다.'); return; }

    const statusDiv = document.getElementById('pt-status');
    statusDiv.style.display = 'block';
    statusDiv.innerHTML = '<span>구독 요청 중...</span>';

    try {
        const res = await fetch('/api/program-trading/subscribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code })
        });
        const json = await res.json();
        if (!json.success) {
            statusDiv.innerHTML = '<span class="text-red">구독 실패</span>';
            return;
        }

        ptSubscribedCodes.add(code);
        if (json.stock_name) ptCodeNameMap[code] = json.stock_name;

        if (!ptChartData[code]) {
            ptChartData[code] = { totalValue: 0, totalVolume: 0, valueData: [], volumeData: [] };
        }

        ptDataDirty = true;
        initProgramChart(ptTimeUnit);
        updateProgramChart(ptChartData, ptSubscribedCodes, ptFilterCodes, ptCodeNameMap, ptTimeUnit);

        renderPtChips();
        input.value = '';

        connectPtEventSource();

        statusDiv.innerHTML = `<span class="text-green">구독 중: ${ptSubscribedCodes.size}개 종목</span>`;
    } catch (e) {
        statusDiv.innerHTML = '<span class="text-red">오류: ' + e + '</span>';
    }
}

async function removeProgramTrading(code) {
    try {
        await fetch('/api/program-trading/unsubscribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code })
        });
    } catch (e) { /* ignore */ }

    ptSubscribedCodes.delete(code);
    delete ptCodeNameMap[code];
    delete ptChartData[code];
    ptFilterCodes.delete(code);
    ptDataDirty = true;

    updateProgramChart(ptChartData, ptSubscribedCodes, ptFilterCodes, ptCodeNameMap, ptTimeUnit);
    renderPtChips();

    const statusDiv = document.getElementById('pt-status');
    if (ptSubscribedCodes.size === 0) {
        if (ptEventSource) { ptEventSource.close(); ptEventSource = null; }
        statusDiv.innerHTML = '<span>구독 중지됨</span>';
        destroyProgramChart();
    } else {
        statusDiv.innerHTML = `<span class="text-green">구독 중: ${ptSubscribedCodes.size}개 종목</span>`;
    }
}

async function stopAllProgramTrading() {
    if (ptEventSource) {
        ptEventSource.close();
        ptEventSource = null;
    }
    try {
        await fetch('/api/program-trading/unsubscribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });
    } catch (e) { /* ignore */ }

    ptSubscribedCodes.clear();
    ptCodeNameMap = {};
    ptFilterCodes.clear();

    destroyProgramChart();

    ptChartData = {};
    ptDataDirty = true;

    renderPtChips();
    document.getElementById('pt-status').innerHTML = '<span>구독 중지됨</span>';
}

// ==========================================
// 필터 칩 UI
// ==========================================
function renderPtChips() {
    const container = document.getElementById('pt-subscribed-list');
    container.innerHTML = '';
    const showAll = ptFilterCodes.size === 0;

    const allChip = document.createElement('span');
    allChip.style.cssText = `display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:12px;font-size:0.8rem;font-weight:600;cursor:pointer;transition:all 0.2s;`
        + (showAll ? 'background:#ff6b35;color:#fff;box-shadow:0 0 0 2px #ff6b35;' : 'background:var(--neutral);');
    allChip.innerHTML = `<span onclick="togglePtFilter('ALL')">ALL</span>`;
    container.appendChild(allChip);

    for (const code of ptSubscribedCodes) {
        const chip = document.createElement('span');
        const isActive = ptFilterCodes.has(code);
        chip.style.cssText = `display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:12px;font-size:0.8rem;font-weight:600;cursor:pointer;transition:all 0.2s;`
            + (isActive ? 'background:#ff6b35;color:#fff;box-shadow:0 0 0 2px #ff6b35;' : 'background:var(--neutral);');
        const label = ptCodeNameMap[code] ? `${ptCodeNameMap[code]}(${code})` : code;
        chip.innerHTML = `<span onclick="togglePtFilter('${code}')">${label}</span> <span style="cursor:pointer;color:var(--negative);font-weight:bold;margin-left:2px;" onclick="event.stopPropagation();removeProgramTrading('${code}')">&times;</span>`;
        container.appendChild(chip);
    }
}

function togglePtFilter(code) {
    if (code === 'ALL') {
        ptFilterCodes.clear();
    } else {
        if (ptFilterCodes.has(code)) {
            ptFilterCodes.delete(code);
        } else {
            ptFilterCodes.add(code);
            if (ptFilterCodes.size === ptSubscribedCodes.size) {
                ptFilterCodes.clear();
            }
        }
    }
    applyPtFilter();
}

function applyPtFilter() {
    renderPtChips();
    _renderPtTable();
    updateProgramChart(ptChartData, ptSubscribedCodes, ptFilterCodes, ptCodeNameMap, ptTimeUnit);
}

// ==========================================
// 시간 단위 변경
// ==========================================
function setPtTimeUnit(minutes) {
    ptTimeUnit = minutes;

    document.querySelectorAll('.pt-interval-btn').forEach(btn => {
        if (parseInt(btn.dataset.interval) === minutes) btn.classList.add('active');
        else btn.classList.remove('active');
    });

    updateProgramChart(ptChartData, ptSubscribedCodes, ptFilterCodes, ptCodeNameMap, ptTimeUnit);
    _renderPtTable();
}

// ==========================================
// 테이블 렌더링
// ==========================================
function _appendProgramTradingTableRow(d) {
    const tbody = document.getElementById('pt-body');
    const time = d['주식체결시간'] || '';
    const fmtTime = time.length >= 6 ? time.slice(0,2)+':'+time.slice(2,4)+':'+time.slice(4,6) : time;
    const ntby = parseInt(d['순매수체결량'] || '0');
    const ntbyColor = ntby > 0 ? 'text-red' : (ntby < 0 ? 'text-blue' : '');

    const price = d['price'] ? parseInt(d['price']).toLocaleString() : '-';
    const rate = d['rate'] ? parseFloat(d['rate']).toFixed(2) + '%' : '';
    const sign = d['sign'] || '3';

    let priceClass = '';
    if (sign === '1' || sign === '2') priceClass = 'text-red';
    else if (sign === '4' || sign === '5') priceClass = 'text-blue';

    const stockCode = d['유가증권단축종목코드'] || '-';
    const hidden = (ptFilterCodes.size > 0 && !ptFilterCodes.has(stockCode)) ? ' style="display:none"' : '';
    const row = `<tr data-code="${stockCode}"${hidden}>
        <td><a href="/?code=${stockCode}" target="_blank" class="stock-link">${ptCodeNameMap[stockCode] ? ptCodeNameMap[stockCode] + '(' + stockCode + ')' : stockCode}</a></td>
        <td>${fmtTime}</td>
        <td class="${priceClass}">${price}<br><small>${rate}</small></td>
        <td>${parseInt(d['매도체결량'] || 0).toLocaleString()}</td>
        <td>${parseInt(d['매수2체결량'] || 0).toLocaleString()}</td>
        <td class="${ntbyColor}">${ntby.toLocaleString()}</td>
        <td>${formatTradingValue(d['순매수거래대금'])}</td>
        <td>${parseInt(d['매도호가잔량'] || 0).toLocaleString()}</td>
        <td>${parseInt(d['매수호가잔량'] || 0).toLocaleString()}</td>
    </tr>`;

    tbody.insertAdjacentHTML('afterbegin', row);
    ptRowCount++;
    if (ptRowCount > 200) {
        tbody.removeChild(tbody.lastElementChild);
        ptRowCount--;
    }
}

// ==========================================
// 데이터 수신 처리
// ==========================================
function handleProgramTradingData(d) {
    let code, netValue, netVolume, timeStr, price, rate, change, sign, sellVol, buyVol, sellRem, buyRem;

    if (Array.isArray(d)) {
        [code, timeStr, price, rate, change, sign, sellVol, buyVol, netVolume, netValue, sellRem, buyRem] = d;
        netValue = parseInt(netValue || 0);
        netVolume = parseInt(netVolume || 0);
    } else {
        code = d['유가증권단축종목코드'];
        netValue = parseInt(d['순매수거래대금'] || '0');
        netVolume = parseInt(d['순매수체결량'] || '0');
        timeStr = d['주식체결시간'];
        price = d.price;
        rate = d.rate;
        change = d.change;
        sign = d.sign;
        sellVol = d['매도체결량'];
        buyVol = d['매수2체결량'];
        sellRem = d['매도호가잔량'];
        buyRem = d['매수호가잔량'];
    }

    if (!ptChartData[code]) return;
    if (!timeStr || timeStr.length < 4) return;

    const now = new Date();
    now.setHours(parseInt(timeStr.slice(0, 2)));
    now.setMinutes(parseInt(timeStr.slice(2, 4)));
    now.setSeconds(0);
    now.setMilliseconds(0);
    const timestamp = now.getTime();

    const valueData = ptChartData[code].valueData;
    const volumeData = ptChartData[code].volumeData;

    const existingIdx = valueData.findIndex(item => item.x === timestamp);

    if (existingIdx >= 0) {
        valueData[existingIdx].y = netValue;
        valueData[existingIdx].price = price;
        valueData[existingIdx].rate = rate;
        valueData[existingIdx].change = change;
        valueData[existingIdx].sign = sign;
        valueData[existingIdx].netVolume = netVolume;
        valueData[existingIdx].sellVol = sellVol;
        valueData[existingIdx].buyVol = buyVol;
        valueData[existingIdx].sellRem = sellRem;
        valueData[existingIdx].buyRem = buyRem;

        if (volumeData[existingIdx]) {
            volumeData[existingIdx].y = netVolume;
            volumeData[existingIdx].price = price;
        }
    } else {
        const point = {
            x: timestamp, y: netValue,
            price: price, rate: rate, change: change, sign: sign,
            netVolume: netVolume, sellVol: sellVol, buyVol: buyVol,
            sellRem: sellRem, buyRem: buyRem
        };
        valueData.push(point);

        const volPoint = { x: timestamp, y: netVolume, price: price };
        volumeData.push(volPoint);
        valueData.sort((a, b) => a.x - b.x);
        volumeData.sort((a, b) => a.x - b.x);
    }

    if (valueData.length > 1000) {
        valueData.shift();
        volumeData.shift();
    }
    ptDataDirty = true;

    if (ptFilterCodes.size === 0 || ptFilterCodes.has(code)) {
        updateProgramChart(ptChartData, ptSubscribedCodes, ptFilterCodes, ptCodeNameMap, ptTimeUnit);
        _renderPtTable();
    }
}

// ==========================================
// 테이블 전체 렌더링 (집계 데이터 반영)
// ==========================================
function _fixProgramTradingTableHeader() {
    const tbody = document.getElementById('pt-body');
    if (!tbody) return;
    const table = tbody.closest('table');
    if (!table) return;
    const thead = table.querySelector('thead tr');
    if (!thead) return;

    const ths = Array.from(thead.querySelectorAll('th'));
    const hasPrice = ths.some(th => th.innerText.includes('현재가'));

    if (!hasPrice) {
        const timeTh = ths.find(th => th.innerText.includes('시간'));
        if (timeTh) {
            const priceTh = document.createElement('th');
            priceTh.innerText = '현재가';
            timeTh.after(priceTh);
        }
    }
}

function _renderPtTable() {
    const tbody = document.getElementById('pt-body');
    if (!tbody) return;
    tbody.innerHTML = '';
    ptRowCount = 0;

    let allRows = [];

    for (const code of ptSubscribedCodes) {
        if (ptFilterCodes.size > 0 && !ptFilterCodes.has(code)) continue;
        if (!ptChartData[code]) continue;

        const aggData = getAggregatedPtData(code, ptChartData, ptTimeUnit);
        const vData = aggData.value || [];
        const volData = aggData.volume || [];

        for (let i = 0; i < vData.length; i++) {
            const valItem = vData[i];
            const volItem = volData[i] || { y: 0 };

            allRows.push({
                code: code,
                timestamp: valItem.x,
                price: valItem.price,
                netValue: valItem.y,
                netVolume: valItem.netVolume || volItem.y,
                rate: valItem.rate,
                change: valItem.change,
                sign: valItem.sign,
                sellVol: valItem.sellVol,
                buyVol: valItem.buyVol,
                sellRem: valItem.sellRem,
                buyRem: valItem.buyRem
            });
        }
    }

    allRows.sort((a, b) => b.timestamp - a.timestamp);

    if (allRows.length > 200) allRows = allRows.slice(0, 200);

    for (const row of allRows) {
        const date = new Date(row.timestamp);
        const hh = String(date.getHours()).padStart(2, '0');
        const mm = String(date.getMinutes()).padStart(2, '0');
        const timeStr = `${hh}${mm}00`;

        const d = {
            '유가증권단축종목코드': row.code,
            '주식체결시간': timeStr,
            '순매수체결량': row.netVolume,
            '순매수거래대금': row.netValue,
            '매도체결량': row.sellVol || 0,
            '매수2체결량': row.buyVol || 0,
            '매도호가잔량': row.sellRem || 0,
            '매수호가잔량': row.buyRem || 0,
            'price': row.price,
            'rate': row.rate,
            'change': row.change,
            'sign': row.sign
        };

        const time = d['주식체결시간'] || '';
        const fmtTime = time.length >= 6 ? time.slice(0,2)+':'+time.slice(2,4)+':'+time.slice(4,6) : time;
        const ntby = parseInt(d['순매수체결량'] || '0');
        const ntbyColor = ntby > 0 ? 'text-red' : (ntby < 0 ? 'text-blue' : '');

        const price = d['price'] ? parseInt(d['price']).toLocaleString() : '-';
        const rate = d['rate'] ? parseFloat(d['rate']).toFixed(2) + '%' : '';
        const sign = d['sign'] || '3';

        let priceClass = '';
        if (sign === '1' || sign === '2') priceClass = 'text-red';
        else if (sign === '4' || sign === '5') priceClass = 'text-blue';

        const stockCode = d['유가증권단축종목코드'] || '-';
        const tr = document.createElement('tr');
        tr.dataset.code = stockCode;
        tr.innerHTML = `
            <td><a href="/?code=${stockCode}" target="_blank" class="stock-link">${ptCodeNameMap[stockCode] ? ptCodeNameMap[stockCode] + '(' + stockCode + ')' : stockCode}</a></td>
            <td>${fmtTime}</td>
            <td class="${priceClass}">${price}<br><small>${rate}</small></td>
            <td>${parseInt(d['매도체결량'] || 0).toLocaleString()}</td>
            <td>${parseInt(d['매수2체결량'] || 0).toLocaleString()}</td>
            <td class="${ntbyColor}">${ntby.toLocaleString()}</td>
            <td>${formatTradingValue(d['순매수거래대금'])}</td>
            <td>${parseInt(d['매도호가잔량'] || 0).toLocaleString()}</td>
            <td>${parseInt(d['매수호가잔량'] || 0).toLocaleString()}</td>
        `;
        tbody.appendChild(tr);
        ptRowCount++;
    }
}

// ==========================================
// 초기화
// ==========================================
async function initProgramTrading() {
    _fixProgramTradingTableHeader();

    const ptBody = document.getElementById('pt-body');
    if (ptBody) ensureTableInCard(ptBody.closest('table'));

    const ptHeader = document.querySelector('#section-program h2');
    if (ptHeader) {
        let backupGroup = ptHeader.querySelector('.pt-backup-group');
        if (!backupGroup) {
            backupGroup = document.createElement('span');
            backupGroup.className = 'pt-backup-group';
            backupGroup.style.cssText = 'font-size: 0.6em; margin-left: 15px; vertical-align: middle; font-weight: normal;';
            backupGroup.innerHTML = `
                <button onclick="exportPtDataToFile()" style="padding: 4px 8px; cursor: pointer; background: #333; color: #eee; border: 1px solid #555; border-radius: 4px; margin-right: 5px;">💾 백업 저장</button>
                <button onclick="importPtDataFromFile()" style="padding: 4px 8px; cursor: pointer; background: #333; color: #eee; border: 1px solid #555; border-radius: 4px;">📂 백업 불러오기</button>
            `;
            ptHeader.appendChild(backupGroup);
        }

        if (!ptHeader.querySelector('.pt-interval-group')) {
            const intervalGroup = document.createElement('span');
            intervalGroup.className = 'pt-interval-group';
            intervalGroup.style.cssText = 'font-size: 0.6em; margin-left: 15px; vertical-align: middle; font-weight: normal;';

            const intervals = [1, 3, 5, 10, 30, 60];
            let html = '<span style="margin-right:5px; color:#aaa;">|</span> ';
            intervals.forEach(min => {
                const activeClass = (min === ptTimeUnit) ? 'active' : '';
                html += `<button class="pt-interval-btn ${activeClass}" data-interval="${min}" onclick="setPtTimeUnit(${min})"
                    style="padding: 2px 6px; margin-right: 2px; cursor: pointer; background: #333; color: #eee; border: 1px solid #555; border-radius: 3px; font-size: 0.9em;">
                    ${min}분
                </button>`;
            });
            intervalGroup.innerHTML = html;

            if (backupGroup) {
                ptHeader.insertBefore(intervalGroup, backupGroup);
            } else {
                ptHeader.appendChild(intervalGroup);
            }

            if (!document.getElementById('pt-interval-style')) {
                const style = document.createElement('style');
                style.id = 'pt-interval-style';
                style.innerHTML = `
                    .pt-interval-btn.active { background-color: #e94560 !important; border-color: #e94560 !important; font-weight: bold; }
                    .pt-interval-btn:hover { background-color: #444; }
                `;
                document.head.appendChild(style);
            }
        }
    }

    setInterval(() => {
        if (ptDataDirty) {
            savePtData();
            ptDataDirty = false;
        }
    }, 5000);

    window.addEventListener('beforeunload', () => {
        if (ptDataDirty) {
            savePtData();
        }
    });

    if (await loadPtData()) {
        ptDataDirty = true;
        renderPtChips();
        initProgramChart(ptTimeUnit);
        updateProgramChart(ptChartData, ptSubscribedCodes, ptFilterCodes, ptCodeNameMap, ptTimeUnit);

        if (ptSubscribedCodes.size > 0) {
            const statusDiv = document.getElementById('pt-status');
            if (statusDiv) {
                statusDiv.style.display = 'block';
                statusDiv.innerHTML = '<span>이전 구독 복구 중...</span>';
            }
            connectPtEventSource();
        }
    } else {
        try {
            const res = await fetch('/api/program-trading/status');
            const status = await res.json();
            if (status.subscribed && status.codes && status.codes.length > 0) {
                for (const code of status.codes) {
                    ptSubscribedCodes.add(code);
                    if (!ptChartData[code]) {
                        ptChartData[code] = { totalValue: 0, totalVolume: 0, valueData: [], volumeData: [] };
                    }
                }
                renderPtChips();
                initProgramChart(ptTimeUnit);
                updateProgramChart(ptChartData, ptSubscribedCodes, ptFilterCodes, ptCodeNameMap, ptTimeUnit);

                const statusDiv = document.getElementById('pt-status');
                if (statusDiv) {
                    statusDiv.style.display = 'block';
                    statusDiv.innerHTML = '<span>서버 구독 상태에서 복구 중...</span>';
                }
                connectPtEventSource();
            }
        } catch (e) {
            console.warn('[PT] Server status check failed:', e);
        }
    }
}

// ==========================================
// 데이터 영속성 (LocalStorage + Server)
// ==========================================
async function savePtData() {
    const data = {
        chartData: ptChartData,
        subscribedCodes: Array.from(ptSubscribedCodes),
        codeNameMap: ptCodeNameMap,
        savedAt: new Date().toISOString()
    };

    localStorage.setItem('ptData', JSON.stringify(data));

    try {
        await fetch('/api/program-trading/save-data', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
            keepalive: true
        });
    } catch (e) {
        console.warn('Server save failed:', e);
    }
}

async function loadPtData() {
    try {
        const res = await fetch('/api/program-trading/load-data');
        const json = await res.json();
        if (json.success && json.data) {
            console.log("[PT] Loaded data from Server file");
            return applyPtData(json.data);
        }
    } catch (e) {
        console.warn("[PT] Server load failed, trying LocalStorage...", e);
    }

    const raw = localStorage.getItem('ptData');
    if (raw) {
        try {
            const data = JSON.parse(raw);
            console.log("[PT] Loaded data from LocalStorage");
            return applyPtData(data);
        } catch (e) {
            console.error('Failed to parse LocalStorage data', e);
        }
    }
    return false;
}

function applyPtData(data) {
    try {
        const todayStart = new Date();
        todayStart.setHours(0, 0, 0, 0);
        const todayTs = todayStart.getTime();

        const rawChartData = data.chartData || {};
        ptChartData = {};

        Object.keys(rawChartData).forEach(code => {
            const entry = rawChartData[code];
            ptChartData[code] = {
                totalValue: 0,
                totalVolume: 0,
                valueData: (entry.valueData || []).filter(d => d.x >= todayTs),
                volumeData: (entry.volumeData || []).filter(d => d.x >= todayTs)
            };
        });

        ptSubscribedCodes = new Set(data.subscribedCodes || []);
        ptCodeNameMap = data.codeNameMap || {};

        _renderPtTable();

        return true;
    } catch (e) {
        console.error('Failed to apply PT data', e);
        return false;
    }
}

// ==========================================
// 데이터 파일 백업 (Export/Import)
// ==========================================
function exportPtDataToFile() {
    const data = {
        chartData: ptChartData,
        subscribedCodes: Array.from(ptSubscribedCodes),
        codeNameMap: ptCodeNameMap,
        savedAt: new Date().toLocaleString()
    };
    const jsonStr = JSON.stringify(data, null, 2);
    const blob = new Blob([jsonStr], { type: "application/json" });
    const url = URL.createObjectURL(blob);

    const a = document.createElement('a');
    a.href = url;
    a.download = `program_trading_backup_${new Date().toISOString().slice(0,10)}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function importPtDataFromFile() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json';
    input.onchange = e => {
        const file = e.target.files[0];
        if (!file) return;

        const reader = new FileReader();
        reader.onload = event => {
            try {
                const data = JSON.parse(event.target.result);
                if (data.chartData && data.subscribedCodes) {
                    if(!confirm(`[${data.savedAt}] 시점의 데이터를 불러오시겠습니까?\n현재 데이터는 덮어씌워집니다.`)) return;

                    ptChartData = data.chartData;
                    ptSubscribedCodes = new Set(data.subscribedCodes);
                    ptCodeNameMap = data.codeNameMap || {};

                    savePtData();
                    location.reload();
                } else {
                    alert('올바르지 않은 데이터 파일입니다.');
                }
            } catch (err) {
                alert('파일 읽기 오류: ' + err);
            }
        };
        reader.readAsText(file);
    };
    input.click();
}
