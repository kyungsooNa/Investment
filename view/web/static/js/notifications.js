/* view/web/static/js/notifications.js — 알림 센터 (Notification Center) */

let _notifEventSource = null;
let _notifications = [];
let _notifUnreadCount = 0;
let _notifCurrentFilter = 'all';
let _notifPanelOpen = false;

document.addEventListener('DOMContentLoaded', () => {
    initNotifications();
});

function initNotifications() {
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
            renderNotifications();

            if (notif.level === 'critical') {
                showToast(`[${notif.category}] ${notif.title}: ${notif.message}`, 'success');
            } else if (notif.level === 'error') {
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
    renderNotifications();
}

function renderNotifications() {
    const list = document.getElementById('notification-list');
    if (!list) return;

    let items = _notifications;
    if (_notifCurrentFilter !== 'all') {
        items = items.filter(n => n.category === _notifCurrentFilter);
    }

    if (items.length === 0) {
        list.innerHTML = '<div class="notification-empty">알림이 없습니다.</div>';
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
