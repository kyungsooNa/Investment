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
