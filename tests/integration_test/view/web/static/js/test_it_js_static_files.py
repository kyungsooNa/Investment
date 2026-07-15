from pathlib import Path

from fastapi.testclient import TestClient

import view.web.web_main as web_main


JS_ROOT = Path("view/web/static/js")


def test_real_web_app_serves_all_static_js_files():
    """실제 FastAPI static mount가 모든 JS 파일을 서빙해야 한다."""
    client = TestClient(web_main.app)

    for script_path in sorted(JS_ROOT.glob("*.js")):
        response = client.get(f"/static/js/{script_path.name}")

        assert response.status_code == 200
        assert response.text.strip()
        assert "javascript" in response.headers["content-type"]


def test_pjax_updates_market_navigation_and_view_context():
    source = (JS_ROOT / "common.js").read_text(encoding="utf-8")

    assert "nav.market-nav a" in source
    assert "data-view-market" in source
    assert "newMarketNav" in source
    assert "newFeatureNav" in source
