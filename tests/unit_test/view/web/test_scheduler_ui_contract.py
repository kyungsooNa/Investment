from pathlib import Path
import shutil
import subprocess

import pytest


SCHEDULER_JS = Path("view/web/static/js/scheduler.js")


def test_armed_market_task_is_presented_as_normally_running():
    """폴링 사이 idle 상태가 사용자에게 정지로 보이면 안 된다."""
    script = SCHEDULER_JS.read_text(encoding="utf-8")

    assert "정상 가동" in script
    assert "가동 중(대기)" not in script
    assert "실행 중 ${active}" not in script


def test_market_task_card_does_not_expose_idle_as_primary_status():
    """armed 태스크의 내부 TaskState 대신 실제 단계 설명을 우선 표시한다."""
    script = SCHEDULER_JS.read_text(encoding="utf-8")

    assert "상태 ${escapeHtml(task.state || '-')}" not in script
    assert "phaseLabel" in script


def test_overseas_task_card_shows_each_running_strategy():
    """미국장 장중 태스크에 포함된 전략을 화면에서 확인할 수 있어야 한다."""
    if shutil.which("node") is None:
        pytest.skip("Node.js is needed to render the scheduler UI")
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const elements = { 'scheduler-market-tasks': { innerHTML: '' } };
const context = {
    document: { getElementById: id => elements[id] },
    escapeHtml: value => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;'),
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
context.renderMarketTasks([{
    name: 'overseas_intraday', display_name: '미국장 장중 전략',
    market: 'overseas_us', armed: true, state: 'idle', live_trading: false,
    progress: { phase: 'polling', watch_count: 17, strategies: [
        'MinerviniStage2Transition_overseas_intraday',
        'LarryWilliamsVBO_overseas_intraday',
        '<unsafe>_overseas_intraday',
    ] },
}]);
const html = elements['scheduler-market-tasks'].innerHTML;
assert.match(html, /MinerviniStage2Transition/);
assert.match(html, /LarryWilliamsVBO/);
assert.match(html, /&lt;unsafe>/);
assert.doesNotMatch(html, /<unsafe>/);
"""
    result = subprocess.run(
        ["node", "-", str(SCHEDULER_JS.resolve())],
        input=script, text=True, encoding="utf-8", capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
