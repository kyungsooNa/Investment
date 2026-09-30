from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
TEMPLATE = ROOT / "view" / "web" / "templates" / "operator_dashboard.html"
SCRIPT = ROOT / "view" / "web" / "static" / "js" / "operator_dashboard.js"


def test_operator_dashboard_contains_strategy_readiness_surface():
    template = TEMPLATE.read_text(encoding="utf-8")

    assert 'id="readiness-summary"' in template
    assert 'id="strategy-readiness-body"' in template
    assert "/static/js/operator_dashboard.js?v=2" in template


def test_operator_dashboard_fetches_and_renders_strategy_readiness():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "fetch('/api/operator/strategy-readiness')" in script
    assert "function renderStrategyReadiness" in script
    assert "config_cohorts" in script
