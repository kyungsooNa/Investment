/* view/web/static/js/order.js — 매수/매도 주문 */

async function placeOrder(side) {
    const code = document.getElementById('order-code').value;
    const qty = document.getElementById('order-qty').value;
    const price = document.getElementById('order-price').value;

    if(!code || !qty || !price) {
        alert("모든 필드를 입력하세요.");
        return;
    }
    if(!confirm(`${side === 'buy' ? '매수' : '매도'} 주문하시겠습니까?\n종목: ${code}\n수량: ${qty}\n가격: ${price}`)) {
        return;
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
