from pathlib import Path
import re
from urllib.parse import urlsplit

import pytest
from jinja2 import Environment, FileSystemLoader


TEMPLATE_ROOT = Path("view/web/templates")
STATIC_ROOT = Path("view/web/static")
TEMPLATES = sorted(TEMPLATE_ROOT.glob("*.html"))
LOCAL_STATIC_SRC_RE = re.compile(
    r"""<(?:script|link)\b[^>]*(?:src|href)=["']([^"']+)["']""",
    re.IGNORECASE,
)


@pytest.mark.parametrize("template_path", TEMPLATES, ids=lambda p: p.name)
def test_templates_are_valid_jinja_syntax(template_path):
    """HTML 템플릿이 Jinja 문법으로 파싱되어야 한다."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_ROOT)))

    env.parse(template_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("template_path", TEMPLATES, ids=lambda p: p.name)
def test_template_local_static_references_exist(template_path):
    """템플릿에서 참조하는 로컬 static 파일이 실제 파일로 존재해야 한다."""
    html = template_path.read_text(encoding="utf-8")
    refs = [
        urlsplit(match.group(1)).path
        for match in LOCAL_STATIC_SRC_RE.finditer(html)
        if urlsplit(match.group(1)).path.startswith("/static/")
    ]

    for ref in refs:
        static_path = STATIC_ROOT / ref.removeprefix("/static/")
        assert static_path.is_file(), f"{template_path} references missing static asset: {ref}"


@pytest.mark.parametrize("template_path", TEMPLATES, ids=lambda p: p.name)
def test_common_js_loads_before_page_static_scripts(template_path):
    """페이지별 JS가 common.js 전역 헬퍼(showLoading 등)를 사용할 수 있어야 한다."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_ROOT)))
    html = env.get_template(template_path.name).render(active_page="")

    refs = [
        urlsplit(match.group(1)).path
        for match in LOCAL_STATIC_SRC_RE.finditer(html)
        if urlsplit(match.group(1)).path.startswith("/static/js/")
    ]
    page_refs = [
        ref for ref in refs
        if ref not in {
            "/static/js/common.js",
            "/static/js/notifications.js",
            "/static/js/kill_switch.js",
            "/static/js/pagination.js",
        }
    ]
    if not page_refs:
        return

    common_idx = html.find("/static/js/common.js")
    assert common_idx != -1, f"{template_path} rendered page scripts without common.js"

    for ref in page_refs:
        assert common_idx < html.find(ref), f"{template_path} loads {ref} before common.js"
