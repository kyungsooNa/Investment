/* view/web/static/js/autocomplete.js — 종목 자동완성 공통 모듈 */

/* ── 초성 추출 유틸 (전역 공유) ── */
const _CHO = ['ㄱ','ㄲ','ㄴ','ㄷ','ㄸ','ㄹ','ㅁ','ㅂ','ㅃ','ㅅ','ㅆ','ㅇ','ㅈ','ㅉ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ'];
const _CHO_SET = new Set(_CHO);

function _getChosung(str) {
    let r = '';
    for (let i = 0; i < str.length; i++) {
        const c = str.charCodeAt(i);
        if (c >= 0xAC00 && c <= 0xD7A3) r += _CHO[Math.floor((c - 0xAC00) / 588)];
    }
    return r;
}

function _isChosung(str) {
    for (let i = 0; i < str.length; i++) { if (!_CHO_SET.has(str[i])) return false; }
    return str.length > 0;
}

/**
 * 혼합 쿼리 매칭: 일반 문자는 exact(대소문자 무시), 초성 자모는 해당 자음으로 시작하는 음절 매칭.
 * 예) "삼성ㅈㅈ" → "삼성전자" 매칭
 */
function _matchMixed(query, name) {
    const qLen = query.length;
    const nLen = name.length;
    if (qLen === 0) return true;
    for (let start = 0; start <= nLen - qLen; start++) {
        let matched = true;
        for (let i = 0; i < qLen; i++) {
            const qc = query[i];
            const nc = name[start + i];
            const nCode = name.charCodeAt(start + i);
            if (_CHO_SET.has(qc)) {
                // 초성 자모 → 해당 자음으로 시작하는 한글 음절인지 확인
                if (nCode < 0xAC00 || nCode > 0xD7A3) { matched = false; break; }
                if (_CHO[Math.floor((nCode - 0xAC00) / 588)] !== qc) { matched = false; break; }
            } else {
                // 일반 문자 → 대소문자 무시 exact 매칭
                if (qc.toLowerCase() !== nc.toLowerCase()) { matched = false; break; }
            }
        }
        if (matched) return true;
    }
    return false;
}

/**
 * 국내 종목 기본 검색: 숫자 → 종목코드 prefix, 그 외 → 종목명 혼합(초성+텍스트) 매칭.
 */
function _defaultStockSearch(q, stocks) {
    const results = [];
    const isDigit = /^\d+$/.test(q);
    for (let i = 0; i < stocks.length && results.length < 20; i++) {
        const s = stocks[i];
        if (isDigit) {
            if (s.c.startsWith(q)) results.push(s);
        } else {
            if (_matchMixed(q, s.n)) results.push(s);
        }
    }
    return results;
}

/**
 * 해외 심볼 검색: 심볼 prefix(대소문자 무시) 또는 영문명 부분 일치.
 * 항목 형태: { s: 심볼, n: 종목명, e: 거래소 }
 */
function _overseasStockSearch(q, stocks) {
    const upper = q.toUpperCase();
    const lower = q.toLowerCase();
    const results = [];
    for (let i = 0; i < stocks.length && results.length < 20; i++) {
        const s = stocks[i];
        const sym = String(s.s || '');
        const name = String(s.n || '');
        if (sym.toUpperCase().startsWith(upper) || name.toLowerCase().includes(lower)) {
            results.push(s);
        }
    }
    return results;
}

/**
 * 종목 자동완성 초기화.
 *
 * @param {object} config
 * @param {string}   config.inputId    - 검색 input 요소 ID
 * @param {string}   config.listId     - 드롭다운 목록 요소 ID
 * @param {function} config.onSelect   - 항목 선택 시 콜백: (value, name, item) => void
 * @param {function} [config.onConfirm] - 드롭다운 미선택 상태에서 Enter 시 콜백 (기본: noop)
 * @param {string}   [config.valueKey='c'] - onSelect 첫 인자로 넘길 항목 필드 (국내 'c' / 해외 's')
 * @param {function} [config.getInitial] - 초기 데이터 배열 반환 (기본: 전역 ALL_STOCKS)
 * @param {string}   [config.readyEvent='all-stocks-ready'] - 데이터 준비 완료 이벤트명
 * @param {function} [config.searchImpl] - 검색 구현 (q, stocks) => item[] (기본: 국내 검색)
 * @param {function} [config.formatItem] - 항목 표시 문자열 (기본: "name (value)")
 */
function StockAutocomplete(config) {
    const { inputId, listId, onSelect, onConfirm } = config;
    const valueKey = config.valueKey || 'c';
    const readyEvent = config.readyEvent || 'all-stocks-ready';
    const searchImpl = config.searchImpl || _defaultStockSearch;
    const formatItem = config.formatItem || function (s) { return s.n + ' (' + s[valueKey] + ')'; };
    const getInitial = config.getInitial || function () {
        return (typeof ALL_STOCKS !== 'undefined' && ALL_STOCKS) ? ALL_STOCKS : null;
    };

    let _stocks = [];
    let _activeIndex = -1;
    let _rendered = [];

    function _setupStocks(raw) {
        _stocks = raw || [];
        for (let i = 0; i < _stocks.length; i++) {
            if (!_stocks[i].ch) _stocks[i].ch = _getChosung(_stocks[i].n || '');
        }
    }

    function _search(q) {
        if (!q) return [];
        return searchImpl(q, _stocks);
    }

    function _initDom() {
        const input = document.getElementById(inputId);
        const list = document.getElementById(listId);
        if (!input || !list) return;

        const initial = getInitial();
        if (initial) _setupStocks(initial);
        document.addEventListener(readyEvent, function (e) { _setupStocks(e.detail); });

        function _close() {
            list.innerHTML = '';
            list.style.display = 'none';
            _activeIndex = -1;
            _rendered = [];
        }

        function _select(item) {
            _close();
            onSelect(item[valueKey], item.n, item);
        }

        function _render(results) {
            list.innerHTML = '';
            _rendered = results;
            if (!results.length) { list.style.display = 'none'; return; }
            results.forEach(function (s) {
                const li = document.createElement('li');
                li.textContent = formatItem(s);
                li.addEventListener('click', function () { _select(s); });
                list.appendChild(li);
            });
            list.style.display = 'block';
        }

        function _updateActive() {
            const items = list.querySelectorAll('li');
            items.forEach(function (li, i) { li.classList.toggle('active', i === _activeIndex); });
        }

        input.addEventListener('input', function () {
            const q = input.value.trim();
            _activeIndex = -1;
            _render(_search(q));
        });

        input.addEventListener('keydown', function (e) {
            const items = list.querySelectorAll('li');

            if (e.key === 'ArrowDown') {
                e.preventDefault();
                _activeIndex = Math.min(_activeIndex + 1, items.length - 1);
                _updateActive();
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                _activeIndex = Math.max(_activeIndex - 1, 0);
                _updateActive();
            } else if (e.key === 'Enter') {
                e.preventDefault();
                if (_activeIndex >= 0 && _rendered[_activeIndex]) {
                    _select(_rendered[_activeIndex]);
                } else if (typeof onConfirm === 'function') {
                    _close();
                    onConfirm();
                }
            } else if (e.key === 'Escape') {
                _close();
            }
        });

        document.addEventListener('click', function (e) {
            if (!input.contains(e.target) && !list.contains(e.target)) _close();
        });
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _initDom);
    } else {
        _initDom(); // Pjax 컨텍스트: DOM 이미 준비됨 → 즉시 실행
    }
}
