/*
 * 유튜브 일일 다이제스트 화면 (youtube.html).
 *
 * 채널 관리 + 리포트 조회. 리포트 본문은 AI 가 만든 마크다운성 텍스트라
 * 그대로 escape 해서 <pre> 로 보여준다 (HTML 로 해석하지 않는다).
 */

let youtubeChannels = [];

async function loadYoutubeChannels() {
    const tbody = document.getElementById('youtube-channel-body');
    if (!tbody) return;
    try {
        const res = await fetchWithTimeout('/api/youtube/channels');
        const { json, error } = await readJsonResponse(res);
        if (error) throw new Error(error);
        youtubeChannels = Array.isArray(json) ? json : [];
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="4">채널 목록을 불러오지 못했습니다: ${escapeHtml(e.message)}</td></tr>`;
        return;
    }
    renderYoutubeChannels();
}

function renderYoutubeChannels() {
    const tbody = document.getElementById('youtube-channel-body');
    if (!tbody) return;
    if (!youtubeChannels.length) {
        tbody.innerHTML = '<tr><td colspan="4">등록된 채널이 없습니다.</td></tr>';
        return;
    }
    tbody.innerHTML = youtubeChannels.map((ch) => {
        const id = escapeHtml(ch.channel_id);
        const label = ch.enabled ? '수집 중' : '중지';
        return `<tr data-channel-id="${id}">
            <td>${escapeHtml(ch.title || '(제목 없음)')}</td>
            <td>@${escapeHtml(ch.handle || '')}</td>
            <td><button type="button" class="yt-toggle" onclick="toggleYoutubeChannel('${id}', ${ch.enabled ? 'false' : 'true'})">${label}</button></td>
            <td><button type="button" class="yt-remove" onclick="removeYoutubeChannel('${id}')">삭제</button></td>
        </tr>`;
    }).join('');
}

async function addYoutubeChannel() {
    const input = document.getElementById('youtube-channel-url');
    const status = document.getElementById('youtube-channel-status');
    if (!input) return;
    const url = (input.value || '').trim();
    if (!url) {
        if (status) status.textContent = '채널 URL 또는 핸들을 입력하세요.';
        return;
    }
    if (status) status.textContent = '채널을 확인하는 중...';
    try {
        const res = await fetchWithTimeout('/api/youtube/channels', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url }),
        });
        const { json, error } = await readJsonResponse(res);
        if (error) throw new Error(error);
        if (status) {
            status.textContent = json.added
                ? `추가됨: ${json.title || json.channel_id}`
                : `이미 등록된 채널입니다: ${json.title || json.channel_id}`;
        }
        input.value = '';
        await loadYoutubeChannels();
    } catch (e) {
        if (status) status.textContent = `추가 실패: ${e.message}`;
    }
}

async function removeYoutubeChannel(channelId) {
    try {
        const res = await fetchWithTimeout(`/api/youtube/channels/${encodeURIComponent(channelId)}`, {
            method: 'DELETE',
        });
        const { error } = await readJsonResponse(res);
        if (error) throw new Error(error);
        await loadYoutubeChannels();
    } catch (e) {
        const status = document.getElementById('youtube-channel-status');
        if (status) status.textContent = `삭제 실패: ${e.message}`;
    }
}

async function toggleYoutubeChannel(channelId, enabled) {
    try {
        const res = await fetchWithTimeout(`/api/youtube/channels/${encodeURIComponent(channelId)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled }),
        });
        const { error } = await readJsonResponse(res);
        if (error) throw new Error(error);
        await loadYoutubeChannels();
    } catch (e) {
        const status = document.getElementById('youtube-channel-status');
        if (status) status.textContent = `변경 실패: ${e.message}`;
    }
}

async function loadYoutubeReportDates() {
    const select = document.getElementById('youtube-report-date');
    if (!select) return;
    try {
        const res = await fetchWithTimeout('/api/youtube/reports');
        const { json, error } = await readJsonResponse(res);
        if (error) throw new Error(error);
        const rows = Array.isArray(json) ? json : [];
        select.innerHTML = rows.map((row) => {
            const date = escapeHtml(row.report_date);
            return `<option value="${date}">${date} (${row.video_count}편)</option>`;
        }).join('');
    } catch (e) {
        select.innerHTML = '';
    }
}

async function loadYoutubeReport(reportDate) {
    const body = document.getElementById('youtube-report-body');
    if (!body) return;
    const url = reportDate
        ? `/api/youtube/reports/${encodeURIComponent(reportDate)}`
        : '/api/youtube/reports/latest';
    body.textContent = '불러오는 중...';
    try {
        const res = await fetchWithTimeout(url);
        const { json, error } = await readJsonResponse(res);
        if (error) throw new Error(error);
        // latest 는 { report: ... } 로 감싸져 오고, 날짜 지정 조회는 리포트 자체가 온다.
        renderYoutubeReport(reportDate ? json : json.report);
    } catch (e) {
        body.innerHTML = `<p class="error">리포트를 불러오지 못했습니다: ${escapeHtml(e.message)}</p>`;
    }
}

function renderYoutubeReport(report) {
    const body = document.getElementById('youtube-report-body');
    if (!body) return;
    if (!report) {
        body.innerHTML = '<p>아직 생성된 리포트가 없습니다. 매일 07:30에 자동 생성됩니다.</p>';
        return;
    }
    const mentions = (report.mentions || []).map((m, i) => `<tr>
        <td>${i + 1}</td>
        <td>${escapeHtml(m.name)}</td>
        <td>${escapeHtml(m.code || '')}</td>
        <td>${m.count}</td>
        <td>${m.video_count}</td>
    </tr>`).join('');
    const videos = (report.videos || []).map((v) => `<li>
        <a href="${escapeHtml(v.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(v.title)}</a>
        <span class="yt-channel">${escapeHtml(v.channel_title)}</span>
        <pre class="yt-video-summary">${escapeHtml(v.summary || '')}</pre>
    </li>`).join('');
    const failedNote = report.failed_summary_count
        ? `<p class="warn">요약 실패 ${report.failed_summary_count}편</p>`
        : '';

    body.innerHTML = `
        <h3>${escapeHtml(report.report_date)} · 영상 ${report.video_count}편</h3>
        ${failedNote}
        <pre id="youtube-digest-text" class="yt-digest">${escapeHtml(report.digest_text || '')}</pre>
        <h4>많이 언급된 종목</h4>
        <table class="yt-mentions"><thead><tr>
            <th>#</th><th>종목</th><th>코드</th><th>언급</th><th>영상 수</th>
        </tr></thead><tbody id="youtube-mention-body">${mentions}</tbody></table>
        <h4>영상별 요약</h4>
        <ul id="youtube-video-list">${videos}</ul>`;
}

async function runYoutubeDigestNow() {
    const status = document.getElementById('youtube-run-status');
    if (status) status.textContent = '생성 중... (수 분 걸릴 수 있습니다)';
    try {
        // 자막 수집 + 영상별 AI 요약이라 기본 타임아웃(15초)으로는 끊긴다.
        const res = await fetchWithTimeout('/api/youtube/run', { method: 'POST' }, 600000);
        const { json, error } = await readJsonResponse(res);
        if (error) throw new Error(error);
        if (status) status.textContent = '생성 완료';
        await loadYoutubeReportDates();
        await loadYoutubeReport('');
    } catch (e) {
        if (status) status.textContent = `생성 실패: ${e.message}`;
    }
}

async function loadYoutubeStatus() {
    const el = document.getElementById('youtube-status');
    if (!el) return;
    try {
        const res = await fetchWithTimeout('/api/youtube/status');
        const { json, error } = await readJsonResponse(res);
        if (error) throw new Error(error);
        if (!json.enabled) {
            el.textContent = 'AI 분석이 비활성화되어 자동 생성이 꺼져 있습니다.';
            return;
        }
        const progress = json.progress || {};
        const parts = [`마지막 리포트: ${progress.last_report_date || '없음'}`];
        if (progress.last_error) parts.push(`마지막 오류: ${progress.last_error}`);
        el.textContent = parts.join(' · ');
    } catch (e) {
        el.textContent = '';
    }
}

function initYoutubePage() {
    loadYoutubeStatus();
    loadYoutubeChannels();
    loadYoutubeReportDates().then(() => loadYoutubeReport(''));
}

if (typeof document !== 'undefined' && document.getElementById('youtube-page')) {
    initYoutubePage();
}
