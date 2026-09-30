from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "view" / "web" / "static" / "js" / "overseas.js"
TEMPLATES = (
    ROOT / "view" / "web" / "templates" / "overseas.html",
    ROOT / "view" / "web" / "templates" / "overseas_virtual.html",
)


def test_overseas_ledger_renders_strategy_version_and_config_hash():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "trade.strategy_version" in script
    assert "trade.config_hash" in script
    assert "버전 / 설정" in script


def test_overseas_pages_use_metadata_aware_script_version():
    for template in TEMPLATES:
        assert "/static/js/overseas.js?v=14" in template.read_text(encoding="utf-8")
