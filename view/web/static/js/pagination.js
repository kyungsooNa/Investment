// Generic client-side pagination utility shared by scheduler/virtual/system/ranking pages.
// Stores per-table page state in memory and exposes a small global Paginator API.
(function () {
    const _state = {};

    function _get(dataKey, pageSize) {
        if (!_state[dataKey]) {
            _state[dataKey] = { page: 1, pageSize: pageSize || 20 };
        } else if (pageSize) {
            _state[dataKey].pageSize = pageSize;
        }
        return _state[dataKey];
    }

    function _renderControls(containerId, dataKey, totalItems, pageSize, currentPage, onPageChange) {
        const el = document.getElementById(containerId);
        if (!el) return;
        const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
        if (totalPages <= 1) {
            el.innerHTML = '';
            return;
        }

        const startIdx = (currentPage - 1) * pageSize + 1;
        const endIdx = Math.min(totalItems, currentPage * pageSize);

        // Build a window of up to 5 page numbers around current page.
        const windowSize = 5;
        let winStart = Math.max(1, currentPage - Math.floor(windowSize / 2));
        let winEnd = Math.min(totalPages, winStart + windowSize - 1);
        winStart = Math.max(1, winEnd - windowSize + 1);

        const parts = [];
        parts.push(`<span class="pagination-info">총 ${totalItems.toLocaleString()}건 중 ${startIdx}-${endIdx}</span>`);
        parts.push(`<div class="pagination">`);

        const disabledFirst = currentPage === 1 ? ' disabled' : '';
        const disabledLast = currentPage === totalPages ? ' disabled' : '';

        parts.push(`<button type="button" class="pagination-btn${disabledFirst}" data-page="1" title="처음">«</button>`);
        parts.push(`<button type="button" class="pagination-btn${disabledFirst}" data-page="${currentPage - 1}" title="이전">‹</button>`);

        for (let p = winStart; p <= winEnd; p++) {
            const active = p === currentPage ? ' active' : '';
            parts.push(`<button type="button" class="pagination-btn${active}" data-page="${p}">${p}</button>`);
        }

        parts.push(`<button type="button" class="pagination-btn${disabledLast}" data-page="${currentPage + 1}" title="다음">›</button>`);
        parts.push(`<button type="button" class="pagination-btn${disabledLast}" data-page="${totalPages}" title="마지막">»</button>`);

        parts.push(`</div>`);

        el.innerHTML = parts.join('');

        el.querySelectorAll('.pagination-btn').forEach(btn => {
            btn.addEventListener('click', (ev) => {
                const target = parseInt(ev.currentTarget.dataset.page, 10);
                if (!Number.isFinite(target)) return;
                const clamped = Math.min(totalPages, Math.max(1, target));
                if (clamped === _state[dataKey].page) return;
                _state[dataKey].page = clamped;
                if (typeof onPageChange === 'function') onPageChange();
            });
        });
    }

    window.Paginator = {
        paginate(dataKey, data, controlsContainerId, onPageChange, pageSize = 20) {
            const items = Array.isArray(data) ? data : [];
            const state = _get(dataKey, pageSize);
            const total = items.length;
            const totalPages = Math.max(1, Math.ceil(total / state.pageSize));
            // Auto-clamp page if data shrank below current page.
            if (state.page > totalPages) state.page = totalPages;
            if (state.page < 1) state.page = 1;
            const start = (state.page - 1) * state.pageSize;
            const end = start + state.pageSize;
            _renderControls(controlsContainerId, dataKey, total, state.pageSize, state.page, onPageChange);
            return items.slice(start, end);
        },

        reset(dataKey) {
            if (_state[dataKey]) _state[dataKey].page = 1;
            else _state[dataKey] = { page: 1, pageSize: 20 };
        },

        getPage(dataKey) {
            return _state[dataKey] ? _state[dataKey].page : 1;
        },
    };
})();
