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
 * 종목 자동완성 초기화.
 *
 * @param {object} config
 * @param {string}   config.inputId    - 검색 input 요소 ID
 * @param {string}   config.listId     - 드롭다운 목록 요소 ID
 * @param {function} config.onSelect   - 항목 선택 시 콜백: (code, name) => void
 * @param {function} [config.onConfirm] - 드롭다운 미선택 상태에서 Enter 시 콜백 (기본: noop)
 */
function StockAutocomplete({ inputId, listId, onSelect, onConfirm }) {
    let _stocks = [];
    let _activeIndex = -1;

    function _setupStocks(raw) {
        _stocks = raw || [];
        for (let i = 0; i < _stocks.length; i++) {
            if (!_stocks[i].ch) _stocks[i].ch = _getChosung(_stocks[i].n);
        }
    }

    function _search(q) {
        if (!q) return [];
        const results = [];
        const isDigit = /^\d+$/.test(q);
        for (let i = 0; i < _stocks.length && results.length < 20; i++) {
            const s = _stocks[i];
            if (isDigit) {
                if (s.c.startsWith(q)) results.push(s);
            } else {
                if (_matchMixed(q, s.n)) results.push(s);
            }
        }
        return results;
    }

    function _initDom() {
        const input = document.getElementById(inputId);
        const list = document.getElementById(listId);
        if (!input || !list) return;

        if (typeof ALL_STOCKS !== 'undefined' && ALL_STOCKS) _setupStocks(ALL_STOCKS);
        document.addEventListener('all-stocks-ready', function (e) { _setupStocks(e.detail); });

        function _close() {
            list.innerHTML = '';
            list.style.display = 'none';
            _activeIndex = -1;
        }

        function _select(code, name) {
            _close();
            onSelect(code, name);
        }

        function _render(results) {
            list.innerHTML = '';
            if (!results.length) { list.style.display = 'none'; return; }
            results.forEach(function (s) {
                const li = document.createElement('li');
                li.textContent = s.n + ' (' + s.c + ')';
                li.dataset.code = s.c;
                li.dataset.name = s.n;
                li.addEventListener('click', function () { _select(s.c, s.n); });
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
                if (_activeIndex >= 0 && items[_activeIndex]) {
                    const item = items[_activeIndex];
                    _select(item.dataset.code, item.dataset.name);
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
