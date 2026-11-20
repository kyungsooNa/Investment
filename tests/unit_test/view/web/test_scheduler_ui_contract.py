from pathlib import Path


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
