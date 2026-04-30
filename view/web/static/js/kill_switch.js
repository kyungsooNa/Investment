/**
 * Kill Switch 상태 배지 + 모달 제어
 * base.html에서 로드되어 모든 페이지에서 동작한다.
 */

let _ksStatus = null;
let _ksStatusInFlight = false;

function _fmtNumber(n) {
    return n != null ? Number(n).toLocaleString() : '-';
}

async function loadKillSwitchStatus() {
    if (_ksStatusInFlight) return;
    _ksStatusInFlight = true;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 4000);
    try {
        const res = await fetch('/api/kill-switch/status', { signal: controller.signal });
        if (!res.ok) return;
        _ksStatus = await res.json();
        _updateBadge(_ksStatus);
    } catch (_) { /* silent */ }
    finally {
        clearTimeout(timer);
        _ksStatusInFlight = false;
    }
}

function _updateBadge(s) {
    const badge = document.getElementById('ks-badge');
    const text  = document.getElementById('ks-badge-text');
    if (!badge || !text) return;
    if (s.is_tripped) {
        badge.className = 'badge ks-tripped';
        text.textContent = '⚠ KS 차단';
    } else {
        badge.className = 'badge ks-ok';
        text.textContent = 'KS ✓';
    }
}

function openKillSwitchModal() {
    const overlay = document.getElementById('ks-modal-overlay');
    const modal   = document.getElementById('ks-modal');
    const body    = document.getElementById('ks-modal-body');
    const btnReset = document.getElementById('ks-btn-reset');
    if (!modal) return;

    const s = _ksStatus;
    if (!s) {
        body.innerHTML = '<span style="color:#f38ba8">상태를 불러오는 중...</span>';
        overlay.style.display = 'block';
        modal.style.display = 'block';
        loadKillSwitchStatus().then(() => openKillSwitchModal());
        return;
    }

    const thr = s.thresholds || {};
    const stateColor = s.is_tripped ? '#f38ba8' : '#a6e3a1';
    const stateText  = s.is_tripped
        ? `<b style="color:#f38ba8">⚠ 트립 (차단 중)</b>`
        : `<b style="color:#a6e3a1">✓ 정상</b>`;
    const tripInfo = s.is_tripped
        ? `<tr><td>트립 사유</td><td>${s.trip_reason || '-'}</td></tr>
           <tr><td>트립 시각</td><td>${s.trip_timestamp ? new Date(s.trip_timestamp).toLocaleString('ko-KR') : '-'}</td></tr>`
        : '';

    body.innerHTML = `
<table style="width:100%;border-collapse:collapse;font-size:0.85rem;">
  <colgroup><col style="width:45%"><col></colgroup>
  <tr><td style="padding:4px 0;color:#a6adc8;">상태</td><td>${stateText}</td></tr>
  ${tripInfo}
  <tr><td style="padding:4px 0;color:#a6adc8;">일 실현손실</td><td>${_fmtNumber(s.daily_realized_loss_won)}원 / 한도 ${_fmtNumber(thr.daily_loss_threshold_won)}원</td></tr>
  <tr><td style="padding:4px 0;color:#a6adc8;">연속 손실</td><td>${s.consecutive_losses}회 / 한도 ${thr.max_consecutive_losses}회</td></tr>
  <tr><td style="padding:4px 0;color:#a6adc8;">연속 API 오류</td><td>${s.consecutive_api_errors}회 / 한도 ${thr.max_consecutive_api_errors}회</td></tr>
  <tr><td style="padding:4px 0;color:#a6adc8;">체결 이탈 한도</td><td>±${thr.abnormal_fill_deviation_pct}%</td></tr>
</table>`;

    btnReset.style.display = s.is_tripped ? 'inline-block' : 'none';
    overlay.style.display = 'block';
    modal.style.display = 'block';
}

function closeKillSwitchModal() {
    document.getElementById('ks-modal-overlay').style.display = 'none';
    document.getElementById('ks-modal').style.display = 'none';
}

async function tripKillSwitch() {
    const reason = prompt('수동 트립 사유를 입력하세요:', '운영자 수동 트립');
    if (reason === null) return;
    try {
        const res = await fetch('/api/kill-switch/trip', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason }),
        });
        const data = await res.json();
        if (res.ok) {
            alert(data.message);
            await loadKillSwitchStatus();
            closeKillSwitchModal();
        } else {
            alert('트립 실패: ' + (data.detail || res.statusText));
        }
    } catch (e) {
        alert('오류: ' + e.message);
    }
}

async function resetKillSwitch() {
    if (!confirm('Kill Switch를 해제하시겠습니까?\n주문과 전략 실행이 재개됩니다.')) return;
    try {
        const res = await fetch('/api/kill-switch/reset', { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            alert(data.message);
            await loadKillSwitchStatus();
            closeKillSwitchModal();
        } else {
            alert('해제 실패: ' + (data.detail || res.statusText));
        }
    } catch (e) {
        alert('오류: ' + e.message);
    }
}

// 페이지 로드 + 5초 폴링
document.addEventListener('DOMContentLoaded', () => {
    loadKillSwitchStatus();
    setInterval(loadKillSwitchStatus, 5000);
});
