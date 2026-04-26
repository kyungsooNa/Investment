/* view/web/static/js/order.js — 매수/매도 주문 */

function _isRealMode() {
    const badge = document.getElementById('status-env');
    return !!(badge && badge.innerText.trim() === '실전투자');
}

function _refreshOrderRealBanner() {
    const isReal = _isRealMode();
    const banner = document.getElementById('order-real-banner');
    const label = document.getElementById('order-mode-label');
    if (banner) banner.style.display = isReal ? 'block' : 'none';
    if (label) {
        label.innerText = isReal ? '[실전]' : '';
        label.style.color = isReal ? '#ff4d4d' : '#aaa';
    }
    const buyBtn = document.querySelector('.btn-buy');
    const sellBtn = document.querySelector('.btn-sell');
    [buyBtn, sellBtn].forEach((btn) => {
        if (!btn) return;
        if (isReal) {
            btn.classList.add('real-mode');
            btn.style.outline = '2px solid #ff4d4d';
        } else {
            btn.classList.remove('real-mode');
            btn.style.outline = '';
        }
    });
}

document.addEventListener('DOMContentLoaded', () => {
    _refreshOrderRealBanner();
    // 모드 변경(updateStatus 폴링)에 맞춰 주기적으로 동기화
    setInterval(_refreshOrderRealBanner, 3000);
});

async function placeOrder(side) {
    const code = document.getElementById('order-code').value;
    const qty = document.getElementById('order-qty').value;
    const price = document.getElementById('order-price').value;

    if(!code || !qty || !price) {
        alert("모든 필드를 입력하세요.");
        return;
    }

    const sideKr = side === 'buy' ? '매수' : '매도';
    const isReal = _isRealMode();

    if (isReal) {
        // 실전 모드: 2단계 확인
        const step1 = confirm(
            `⚠️ [실전투자] ${sideKr} 주문을 실행합니다.\n\n` +
            `종목: ${code}\n수량: ${qty}\n가격: ${price}\n\n` +
            `실제 자금이 사용됩니다. 계속하시겠습니까?`
        );
        if (!step1) return;

        const typed = prompt(`최종 확인을 위해 "REAL"을 입력하세요:`);
        if (typed !== 'REAL') {
            alert('확인 문자열이 일치하지 않아 주문이 취소되었습니다.');
            return;
        }
    } else {
        if (!confirm(`${sideKr} 주문하시겠습니까?\n종목: ${code}\n수량: ${qty}\n가격: ${price}`)) {
            return;
        }
    }

    const resDiv = document.getElementById('order-result');
    const buyBtn = document.querySelector('.btn-buy');
    const sellBtn = document.querySelector('.btn-sell');
    showLoading(resDiv, '주문 전송 중...');
    if (buyBtn) buyBtn.disabled = true;
    if (sellBtn) sellBtn.disabled = true;

    try {
        const res = await fetchWithTimeout('/api/order', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ code, qty, price, side })
        });
        const json = await res.json();

        if (json.rt_cd === "0") {
            resDiv.innerHTML = `<p class="success">주문 성공! (주문번호: ${json.data.ord_no})</p>`;
            if (typeof invalidateVirtualChartCache === 'function') invalidateVirtualChartCache();
        } else {
            resDiv.innerHTML = `<p class="error">주문 실패: ${json.msg1}</p>`;
        }
    } catch (e) {
        if (e.name === 'AbortError') {
            resDiv.innerHTML = `<p class="error">주문 요청 시간이 초과되었습니다. 다시 시도해주세요.</p>`;
        } else {
            resDiv.innerHTML = `<p class="error">통신 오류: ${e}</p>`;
        }
    } finally {
        if (buyBtn) buyBtn.disabled = false;
        if (sellBtn) sellBtn.disabled = false;
    }
}
