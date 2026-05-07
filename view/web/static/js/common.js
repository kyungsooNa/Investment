/* view/web/static/js/common.js — 공통 유틸리티, 상태 갱신, 환경 전환 */

// ==========================================
// 유틸리티 함수
// ==========================================
function formatTradingValue(val, isMillion = false) {
    let num = parseInt(val || '0');
    if (isMillion) num *= 1000000;
    const abs = Math.abs(num);
    const sign = num < 0 ? '-' : '';
    if (abs >= 1e12) {
        const jo = Math.floor(abs / 1e12);
        const eok = Math.round((abs % 1e12) / 1e8);
        return sign + jo.toLocaleString() + '조' + (eok > 0 ? ' ' + eok.toLocaleString() + '억' : '');
    }
    if (abs >= 1e8) return sign + (abs / 1e8).toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '억';
    if (abs >= 1e4) return sign + (abs / 1e4).toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '만';
    return num.toLocaleString();
}

function formatMarketCap(val) {
    const num = parseInt(val || '0');
    if (num >= 10000) {
        const jo = Math.floor(num / 10000);
        const eok = num % 10000;
        return jo.toLocaleString() + '조' + (eok > 0 ? ' ' + eok.toLocaleString() + '억' : '');
    }
    return num.toLocaleString() + '억';
}

function ensureTableInCard(table) {
    if (table && !table.parentElement.classList.contains('card')) {
        const card = document.createElement('div');
        card.className = 'card';
        table.parentNode.insertBefore(card, table);
        card.appendChild(table);
    }
}

function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `<span>${type === 'success' ? '✅' : '❌'}</span> <span>${message}</span>`;

    container.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('fade-out');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ==========================================
// 로딩 표시 유틸리티
// ==========================================
function showLoading(targetEl, message = '로딩 중...') {
    if (!targetEl) return;
    targetEl.innerHTML = `<div class="loading-indicator"><span class="spinner"></span><span class="loading-text">${escapeHtml(message)}</span></div>`;
}

function fetchWithTimeout(url, options = {}, timeoutMs = 15000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    return fetch(url, { ...options, signal: controller.signal })
        .finally(() => clearTimeout(timer));
}

async function withLoading(btn, targetEl, message, asyncFn) {
    if (btn) btn.disabled = true;
    showLoading(targetEl, message);
    try {
        await asyncFn();
    } catch (e) {
        if (e.name === 'AbortError') {
            if (targetEl) targetEl.innerHTML = '<p class="error">요청 시간이 초과되었습니다. 다시 시도해주세요.</p>';
        } else {
            if (targetEl) targetEl.innerHTML = `<p class="error">오류: ${escapeHtml(String(e.message || e))}</p>`;
        }
    } finally {
        if (btn) btn.disabled = false;
    }
}

// ==========================================
// Pjax 비동기 라우팅
// ==========================================

// 이미 로드된 외부 스크립트 src URL 추적 (중복 로드 방지)
const _loadedScripts = new Set(
    Array.from(document.querySelectorAll('script[src]')).map(s => s.src)
);

// Chart.js 인스턴스 전역 레지스트리 — 페이지 전환 전 .destroy() 호출
window.currentCharts = window.currentCharts || [];

function loadScript(src) {
    return new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = src;
        script.onload = resolve;
        script.onerror = reject;
        document.head.appendChild(script);
    });
}

async function executePageScripts(container) {
    const scripts = Array.from(container.querySelectorAll('script'));
    for (const oldScript of scripts) {
        if (oldScript.src) {
            const normalizedSrc = new URL(oldScript.src, location.href).href;
            if (!_loadedScripts.has(normalizedSrc)) {
                _loadedScripts.add(normalizedSrc);
                try { await loadScript(oldScript.src); } catch (e) { console.error('Script load failed:', oldScript.src, e); }
            }
            // 이미 로드된 src 스크립트는 skip (중복 실행 방지)
        } else if (oldScript.textContent.trim()) {
            try { new Function(oldScript.textContent)(); } catch (e) { console.error('Inline script error:', e); }
        }
    }
}

function updateNavActive(pathname) {
    document.querySelectorAll('nav.nav a').forEach(a => {
        const href = a.getAttribute('href');
        a.classList.toggle('active', href === pathname || (pathname === '/' && href === '/'));
    });
}

async function navigatePjax(href) {
    const overlay = document.getElementById('page-loading-overlay');
    if (overlay) overlay.classList.add('active');
    const targetPath = new URL(href, location.href).pathname;

    document.dispatchEvent(new CustomEvent('pjax:before-change', {
        detail: { path: targetPath, from: location.pathname }
    }));

    // 페이지 이탈 전 SSE 정리 (장중 zombie 연결 방지)
    if (typeof unsubscribeRealtimePrice === 'function') { try { unsubscribeRealtimePrice(); } catch (_) {} }

    // 페이지 이탈 전 Chart.js 인스턴스 정리
    if (window.currentCharts && window.currentCharts.length > 0) {
        window.currentCharts.forEach(chart => { try { chart.destroy(); } catch (_) {} });
        window.currentCharts = [];
    }

    try {
        const response = await fetchWithTimeout(href, {}, 15000);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const html = await response.text();

        const parser = new DOMParser();
        const doc = parser.parseFromString(html, 'text/html');
        const newMain = doc.querySelector('#page-main');
        if (!newMain) throw new Error('page-main not found');

        const pageMain = document.querySelector('#page-main');
        pageMain.innerHTML = newMain.innerHTML;

        await executePageScripts(pageMain);

        window.history.pushState({path: href}, '', href);
        updateNavActive(targetPath);

        // 페이지별 외부 JS의 초기화 함수 트리거 (DOMContentLoaded 대체)
        document.dispatchEvent(new CustomEvent('pjax:ready', { detail: { path: targetPath } }));

    } catch (error) {
        console.error('Pjax 전환 실패, 일반 이동:', error);
        window.location.href = href;
    } finally {
        if (overlay) overlay.classList.remove('active');
    }
}

// 네비게이션 클릭 가로채기
document.addEventListener('click', (e) => {
    const link = e.target.closest('nav.nav a');
    if (!link) return;
    const href = link.getAttribute('href');
    if (!href || href === '#') return;
    if (new URL(href, location.href).pathname === location.pathname) return;

    e.preventDefault();
    navigatePjax(href);
});

// 뒤로가기/앞으로가기도 Pjax로 처리
window.addEventListener('popstate', () => {
    navigatePjax(location.pathname);
});

// bfcache 복원 시 오버레이 해제 (폴백)
window.addEventListener('pageshow', () => {
    const overlay = document.getElementById('page-loading-overlay');
    if (overlay) overlay.classList.remove('active');
});

// ==========================================
// 상태 갱신 & 환경 전환
// ==========================================
document.addEventListener('DOMContentLoaded', () => {
    document.body.classList.add('light-mode');

    // 주식 종목 링크 호버 스타일 추가
    if (!document.getElementById('stock-link-style')) {
        const style = document.createElement('style');
        style.id = 'stock-link-style';
        style.innerHTML = `
            .stock-link {
                color: var(--accent);
                text-decoration: none;
                transition: all 0.2s ease;
            }
            .stock-link:hover {
                font-weight: bold;
                text-decoration: underline;
                filter: brightness(1.2);
            }
        `;
        document.head.appendChild(style);
    }

    updateStatus();
    setInterval(updateStatus, 5000);
});

let _statusInFlight = false;

async function updateStatus() {
    if (_statusInFlight) return;
    _statusInFlight = true;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 4000);
    try {
        const res = await fetch('/api/status', { signal: controller.signal });
        const data = await res.json();

        document.getElementById('status-time').innerText = data.current_time || '--:--:--';

        const marketBadge = document.getElementById('status-market');
        if (data.market_open) {
            marketBadge.innerText = "장중";
            marketBadge.className = "badge open";
        } else {
            marketBadge.innerText = "장마감";
            marketBadge.className = "badge closed";
        }

        const envBadge = document.getElementById('status-env');
        envBadge.innerText = data.env_type || "Unknown";
        if (data.env_type === "모의투자") {
            envBadge.className = "badge paper clickable";
            envBadge.dataset.mode = "paper";
        } else if (data.env_type === "실전투자") {
            envBadge.className = "badge real clickable";
            envBadge.dataset.mode = "real";
        } else {
            envBadge.className = "badge closed clickable";
            envBadge.dataset.mode = "unknown";
        }
        if (typeof data.is_paper_trading === 'boolean') {
            envBadge.className = data.is_paper_trading
                ? "badge paper clickable"
                : "badge real clickable";
            envBadge.dataset.mode = data.is_paper_trading ? "paper" : "real";
        }

    } catch (e) {
        console.error("Status update failed:", e);
    } finally {
        clearTimeout(timer);
        _statusInFlight = false;
    }
}

async function toggleEnvironment() {
    if (!confirm("거래 환경을 전환하시겠습니까? (서버 재설정)")) return;

    const envBadge = document.getElementById('status-env');
    const currentText = envBadge.innerText;
    const isCurrentlyPaper = (currentText === "모의투자");
    const targetIsPaper = envBadge.dataset.mode
        ? envBadge.dataset.mode === "real"
        : !isCurrentlyPaper;
    let realModeConfirmation = null;

    if (!targetIsPaper) {
        const step1 = confirm(
            "실전투자 모드로 전환합니다.\n\n실제 계좌와 실전 주문 API가 사용됩니다. 계속하시겠습니까?"
        );
        if (!step1) return;
        realModeConfirmation = prompt('최종 확인을 위해 "REAL"을 입력하세요:');
        if (realModeConfirmation !== "REAL") {
            alert("확인 문자열이 일치하지 않아 실전 모드 전환이 취소되었습니다.");
            return;
        }
    }

    try {
        const res = await fetch('/api/environment', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                is_paper: targetIsPaper,
                real_mode_confirmation: realModeConfirmation
            })
        });
        const data = await res.json();

        if (data.success) {
            alert(`환경이 [${data.env_type}]로 전환되었습니다.`);
            updateStatus();
        } else {
            alert("환경 전환 실패: " + (data.detail || "알 수 없는 오류"));
        }
    } catch(e) {
        alert("요청 중 오류 발생: " + e);
    }
}
