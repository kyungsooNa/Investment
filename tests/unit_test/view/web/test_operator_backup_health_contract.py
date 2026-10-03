from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
TEMPLATE = ROOT / "view" / "web" / "templates" / "operator_dashboard.html"
SCRIPT = ROOT / "view" / "web" / "static" / "js" / "operator_dashboard.js"


def test_operator_dashboard_contains_backup_health_surface():
    template = TEMPLATE.read_text(encoding="utf-8")

    assert 'id="backup-health-badge"' in template
    assert 'id="backup-history-body"' in template
    assert "/static/js/operator_dashboard.js?v=4" in template


def test_operator_dashboard_fetches_and_renders_backup_health():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "fetch('/api/operator/backup-health')" in script
    assert "function renderBackupHealth" in script
