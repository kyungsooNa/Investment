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
// 페이지 전환 로딩 오버레이
// ==========================================
document.addEventListener('click', (e) => {
    const link = e.target.closest('nav.nav a');
    if (!link) return;
    const href = link.getAttribute('href');
    if (!href || href === '#' || href === window.location.pathname) return;

    const overlay = document.getElementById('page-loading-overlay');
    if (overlay) overlay.classList.add('active');
});

// bfcache 복원 시 오버레이 해제
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

async function updateStatus() {
    try {
        const res = await fetch('/api/status');
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
        } else if (data.env_type === "실전투자") {
            envBadge.className = "badge real clickable";
        } else {
            envBadge.className = "badge closed clickable";
        }

    } catch (e) {
        console.error("Status update failed:", e);
    }
}

async function toggleEnvironment() {
    if (!confirm("거래 환경을 전환하시겠습니까? (서버 재설정)")) return;

    const currentText = document.getElementById('status-env').innerText;
    const isCurrentlyPaper = (currentText === "모의투자");
    const targetIsPaper = !isCurrentlyPaper;

    try {
        const res = await fetch('/api/environment', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ is_paper: targetIsPaper })
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
