/* view/web/static/js/notifications.js — 알림 센터 (Notification Center) */

let _notifEventSource = null;
let _notifications = [];
let _notifUnreadCount = 0;
let _notifCurrentFilter = 'all';
let _notifPanelOpen = false;
let _telegramNotifications = [];

document.addEventListener('DOMContentLoaded', () => {
    initNotifications();
});

function initNotifications() {
    if (typeof window.hasRequiredRole === 'function'
        && !window.hasRequiredRole('operator')) return;
    fetch('/api/notifications/recent?count=50')
        .then(r => r.json())
        .then(data => {
            if (data.notifications) {
                _notifications = data.notifications;
                renderNotifications();
            }
        })
        .catch(() => {});

    connectNotificationSSE();

    document.addEventListener('click', (e) => {
        const panel = document.getElementById('notification-panel');
        const wrapper = document.querySelector('.notification-wrapper');
        if (_notifPanelOpen && panel && wrapper &&
            !panel.contains(e.target) && !wrapper.contains(e.target)) {
            _notifPanelOpen = false;
            panel.style.display = 'none';
        }
    });
}

function connectNotificationSSE() {
    if (_notifEventSource) return;
    _notifEventSource = new EventSource('/api/notifications/stream');

    _notifEventSource.onmessage = function(event) {
        try {
            const notif = JSON.parse(event.data);
            _notifications.unshift(notif);
            if (_notifications.length > 200) _notifications = _notifications.slice(0, 200);
            _notifUnreadCount++;
            updateNotificationBadge();

            // 필터가 걸려있고 해당 카테고리가 아니면 전체 다시 그리기
            if (_notifCurrentFilter !== 'all' && notif.category !== _notifCurrentFilter) {
                // 데이터는 저장했지만 현재 필터에 안 맞으므로 DOM 업데이트 불필요
            } else {
                // 전체를 다시 그리지 않고 새 항목만 DOM 상단에 삽입
                _appendSingleNotification(notif);
            }

            if (notif.level === 'critical') {
                showToast(`[${notif.category}] ${notif.title}: ${notif.message}`, 'success');
            } else if (notif.level === 'error') {
                showToast(`${notif.title}: ${notif.message}`, 'error');
            } else if (notif.level === 'warning' && notif.metadata && notif.metadata.alert_type === 'execution_quality_candidate') {
                showToast(`${notif.title}: ${notif.message}`, 'error');
            }
        } catch (e) {
            console.warn('[Notification SSE] parse error', e);
        }
    };

    _notifEventSource.onerror = function() {
        _notifEventSource.close();
        _notifEventSource = null;
        setTimeout(connectNotificationSSE, 3000);
    };
}

function toggleNotificationPanel() {
    const panel = document.getElementById('notification-panel');
    if (!panel) return;
    _notifPanelOpen = !_notifPanelOpen;
    panel.style.display = _notifPanelOpen ? 'flex' : 'none';
    if (_notifPanelOpen) {
        _notifUnreadCount = 0;
        updateNotificationBadge();
        renderNotifications();
    }
}

function filterNotifications(category) {
    _notifCurrentFilter = category;
    document.querySelectorAll('.notif-filter').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.category === category);
    });
    if (category === 'TELEGRAM') {
        fetch('/api/notifications/telegram/today?count=200')
            .then(r => r.json())
            .then(data => {
                _telegramNotifications = data.notifications || [];
                renderNotifications();
            })
            .catch(() => {
                renderNotifications();
            });
        return;
    }
    renderNotifications();
}

function renderNotifications() {
    const list = document.getElementById('notification-list');
    if (!list) return;

    let items = _notifCurrentFilter === 'TELEGRAM' ? _telegramNotifications : _notifications;
    if (_notifCurrentFilter !== 'all' && _notifCurrentFilter !== 'TELEGRAM') {
        items = items.filter(n => n.category === _notifCurrentFilter);
    }

    if (items.length === 0) {
        const message = _notifCurrentFilter === 'TELEGRAM' ? '오늘 발송된 Telegram 알림이 없습니다.' : '알림이 없습니다.';
        list.innerHTML = `<div class="notification-empty">${message}</div>`;
        return;
    }

    list.innerHTML = items.map(n => {
        const time = formatNotifTime(n.timestamp);
        const levelIcon = n.level === 'critical' ? '🔴' : n.level === 'error' ? '❗' : n.level === 'warning' ? '⚠️' : '';
        return `
            <div class="notif-item category-${n.category}">
                <div class="notif-item-header">
                    <span class="notif-category cat-${n.category}">${n.category}</span>
                    <span class="notif-time">${time}</span>
                </div>
                <div class="notif-title">${levelIcon} ${escapeHtml(n.title)}</div>
                <div class="notif-message">${escapeHtml(n.message)}</div>
            </div>
        `;
    }).join('');
}

function _appendSingleNotification(n) {
    const list = document.getElementById('notification-list');
    if (!list) return;

    const emptyDiv = list.querySelector('.notification-empty');
    if (emptyDiv) emptyDiv.remove();

    const time = formatNotifTime(n.timestamp);
    const levelIcon = n.level === 'critical' ? '\uD83D\uDD34' : n.level === 'error' ? '\u2757' : n.level === 'warning' ? '\u26A0\uFE0F' : '';
    const html = `
        <div class="notif-item category-${n.category}">
            <div class="notif-item-header">
                <span class="notif-category cat-${n.category}">${n.category}</span>
                <span class="notif-time">${time}</span>
            </div>
            <div class="notif-title">${levelIcon} ${escapeHtml(n.title)}</div>
            <div class="notif-message">${escapeHtml(n.message)}</div>
        </div>
    `;

    list.insertAdjacentHTML('afterbegin', html);

    if (list.children.length > 200) {
        list.removeChild(list.lastElementChild);
    }
}

function updateNotificationBadge() {
    const badge = document.getElementById('notification-badge');
    if (!badge) return;
    if (_notifUnreadCount > 0) {
        badge.style.display = 'flex';
        badge.textContent = _notifUnreadCount > 99 ? '99+' : _notifUnreadCount;
    } else {
        badge.style.display = 'none';
    }
}

function clearNotifications() {
    _notifUnreadCount = 0;
    updateNotificationBadge();
}

function formatNotifTime(isoStr) {
    if (!isoStr) return '';
    try {
        const d = new Date(isoStr);
        const now = new Date();
        const diffMs = now - d;
        const diffMin = Math.floor(diffMs / 60000);
        if (diffMin < 1) return '방금';
        if (diffMin < 60) return `${diffMin}분 전`;
        const diffHr = Math.floor(diffMin / 60);
        if (diffHr < 24) return `${diffHr}시간 전`;
        return d.toLocaleDateString('ko-KR', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    } catch (e) {
        return isoStr;
    }
}
