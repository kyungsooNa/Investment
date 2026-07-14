let _strategyReports = [];
let _selectedStrategyReportId = null;

document.addEventListener('DOMContentLoaded', loadStrategyReports);

async function loadStrategyReports() {
    const list = document.getElementById('strategy-report-list');
    try {
        const response = await fetch('/api/strategies/diagnostic-reports?limit=200');
        if (!response.ok) throw new Error('목록 조회 실패');
        const data = await response.json();
        _strategyReports = data.reports || [];
        renderStrategyReportList();
        if (_strategyReports.length > 0) {
            const selected = _strategyReports.some(item => item.id === _selectedStrategyReportId)
                ? _selectedStrategyReportId
                : _strategyReports[0].id;
            await selectStrategyReport(selected);
        } else {
            clearStrategyReportViewer('저장된 상세 리포트가 없습니다.');
        }
    } catch (_) {
        list.innerHTML = '<div class="strategy-report-empty">상세 리포트 목록을 불러오지 못했습니다.</div>';
        clearStrategyReportViewer('조회 상태를 확인해 주세요.');
    }
}

function renderStrategyReportList() {
    const list = document.getElementById('strategy-report-list');
    if (_strategyReports.length === 0) {
        list.innerHTML = '<div class="strategy-report-empty">저장된 상세 리포트가 없습니다.</div>';
        return;
    }
    list.innerHTML = _strategyReports.map(report => `
        <button type="button" class="strategy-report-item ${report.id === _selectedStrategyReportId ? 'active' : ''}"
                data-report-id="${escapeHtml(report.id)}"
                onclick="selectStrategyReport(this.dataset.reportId)">
            <strong>${formatReportDate(report.report_date)}</strong>
            <span>${formatCreatedAt(report.created_at)}</span>
        </button>
    `).join('');
}

async function selectStrategyReport(reportId) {
    _selectedStrategyReportId = reportId;
    renderStrategyReportList();
    const meta = document.getElementById('strategy-report-meta');
    const content = document.getElementById('strategy-report-content');
    meta.textContent = '리포트를 불러오는 중입니다.';
    content.replaceChildren();
    try {
        const response = await fetch(`/api/strategies/diagnostic-reports/${encodeURIComponent(reportId)}`);
        if (!response.ok) throw new Error('상세 조회 실패');
        const report = await response.json();
        meta.textContent = `${formatReportDate(report.report_date)} · ${formatCreatedAt(report.created_at)}`;
        content.replaceChildren(sanitizeStrategyReport(report.content || ''));
    } catch (_) {
        clearStrategyReportViewer('상세 리포트를 불러오지 못했습니다.');
    }
}

function sanitizeStrategyReport(source) {
    const template = document.createElement('template');
    template.innerHTML = source;
    const fragment = document.createDocumentFragment();

    function appendSafe(parent, node) {
        if (node.nodeType === Node.TEXT_NODE) {
            parent.appendChild(document.createTextNode(node.textContent));
            return;
        }
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        const target = node.tagName === 'B' ? document.createElement('strong') : parent;
        if (target !== parent) parent.appendChild(target);
        node.childNodes.forEach(child => appendSafe(target, child));
    }

    template.content.childNodes.forEach(node => appendSafe(fragment, node));
    return fragment;
}

function clearStrategyReportViewer(message) {
    document.getElementById('strategy-report-meta').textContent = message;
    document.getElementById('strategy-report-content').replaceChildren();
}

function formatReportDate(value) {
    const text = String(value || '');
    return text.length === 8 ? `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}` : text;
}

function formatCreatedAt(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value || '') : date.toLocaleString('ko-KR');
}
