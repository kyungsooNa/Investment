/* view/web/static/js/order.js — 매수/매도 주문 */

function _isRealMode() {
    const badge = document.getElementById('status-env');
    return !!(badge && (
        badge.dataset.mode === 'real' || badge.classList.contains('real')
    ));
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
    const orderDvsn = document.getElementById('order-dvsn').value || null;

    if(!code || !qty || !price) {
        alert("모든 필드를 입력하세요.");
        return;
    }

    const sideKr = side === 'buy' ? '매수' : '매도';
    const isReal = _isRealMode();

    let realOrderConfirmation = null;
    if (isReal) {
        // 실전 모드: 2단계 확인
        const step1 = confirm(
            `⚠️ [실전투자] ${sideKr} 주문을 실행합니다.\n\n` +
            `종목: ${code}\n수량: ${qty}\n가격: ${price}\n\n` +
            `실제 자금이 사용됩니다. 계속하시겠습니까?`
        );
        if (!step1) return;

        realOrderConfirmation = prompt(`최종 확인을 위해 "REAL"을 입력하세요:`);
        if (realOrderConfirmation !== 'REAL') {
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
            body: JSON.stringify({
                code,
                qty,
                price,
                side,
                order_dvsn: orderDvsn,
                real_order_confirmation: realOrderConfirmation
            })
        });
        const json = await res.json();

        if (json.rt_cd === "0") {
            resDiv.innerHTML = `<p class="success">주문 접수! 체결 미확정 - 체결통보/조회로 별도 확인 (주문번호: ${json.data.ord_no})</p>`;
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

/* ── 국내 미체결 조회 / 취소 ── */

function _orderNumber(value) {
    const num = Number(String(value ?? '0').replace(/,/g, ''));
    return Number.isFinite(num) ? num : 0;
}

async function loadUnfilledOrders() {
    const resultDiv = document.getElementById('unfilled-orders-result');
    if (!resultDiv) return;
    showLoading(resultDiv, '미체결 조회 중...');
    try {
        const res = await fetchWithTimeout('/api/orders/unfilled', {}, 12000);
        const { json, error } = await readJsonResponse(res);
        if (error || json.rt_cd !== '0') {
            showError(resultDiv, `미체결 조회 실패: ${error || json.msg1 || res.status}`);
            return;
        }
        const data = json.data || {};
        const rows = Array.isArray(data) ? data : (Array.isArray(data.output) ? data.output : []);
        const body = rows.map(row => {
            const odno = String(row.odno || row.ODNO || '');
            const code = String(row.pdno || '');
            const orgno = String(row.ord_gno_brno || '');
            const remaining = _orderNumber(row.psbl_qty ?? (_orderNumber(row.ord_qty) - _orderNumber(row.tot_ccld_qty)));
            return `
                <tr>
                    <td>${escapeHtml(odno || '-')}</td>
                    <td>${escapeHtml(row.prdt_name || code || '-')}</td>
                    <td>${escapeHtml(row.sll_buy_dvsn_cd_name || '-')}</td>
                    <td>${_orderNumber(row.ord_qty).toLocaleString()}</td>
                    <td>${remaining.toLocaleString()}</td>
                    <td>${_orderNumber(row.ord_unpr).toLocaleString()}</td>
                    <td><button type="button" class="btn btn-sell" data-unfilled-cancel data-order-no="${escapeHtml(odno)}" data-orgno="${escapeHtml(orgno)}" data-code="${escapeHtml(code)}" data-qty="${remaining}">취소</button></td>
                </tr>
            `;
        }).join('');
        resultDiv.innerHTML = `
            <table class="data-table">
                <thead><tr><th>주문번호</th><th>종목</th><th>구분</th><th>주문수량</th><th>미체결</th><th>주문가</th><th></th></tr></thead>
                <tbody>${body || '<tr><td colspan="7">미체결 없음</td></tr>'}</tbody>
            </table>
        `;
        resultDiv.querySelectorAll('[data-unfilled-cancel]').forEach(button => {
            button.addEventListener('click', () => cancelDomesticOrder(
                button.dataset.orderNo || '',
                button.dataset.orgno || '',
                button.dataset.code || '',
                Number(button.dataset.qty || 0),
            ));
        });
    } catch (e) {
        showError(resultDiv, e.name === 'AbortError' ? '요청 시간이 초과되었습니다.' : '통신 오류가 발생했습니다.');
    }
}

async function cancelDomesticOrder(orderNo, orgno, code, qty) {
    if (!orderNo) {
        alert('주문번호가 없습니다.');
        return;
    }
    let realOrderConfirmation = null;
    if (_isRealMode()) {
        const step1 = confirm(
            `⚠️ [실전투자] 주문 취소\n\n종목: ${code}\n주문번호: ${orderNo}\n미체결 수량: ${qty}\n\n계속하시겠습니까?`
        );
        if (!step1) return;
        realOrderConfirmation = prompt('최종 확인을 위해 "REAL"을 입력하세요:');
        if (realOrderConfirmation !== 'REAL') {
            alert('확인 문자열이 일치하지 않아 취소 요청이 중단되었습니다.');
            return;
        }
    } else if (!confirm(`주문 취소\n종목: ${code}\n주문번호: ${orderNo}\n미체결 수량: ${qty}`)) {
        return;
    }

    try {
        const res = await fetchWithTimeout('/api/order/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                broker_order_no: orderNo,
                order_orgno: orgno || null,
                real_order_confirmation: realOrderConfirmation,
            }),
        }, 15000);
        const { json, error } = await readJsonResponse(res);
        if (error || json.rt_cd !== '0') {
            alert(`취소 실패: ${error || json.msg1 || res.status}`);
            return;
        }
        await loadUnfilledOrders();
    } catch (e) {
        alert(e.name === 'AbortError' ? '요청 시간이 초과되었습니다.' : '통신 오류가 발생했습니다.');
    }
}
