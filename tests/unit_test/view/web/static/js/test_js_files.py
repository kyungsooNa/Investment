from pathlib import Path
import shutil
import subprocess

import pytest


JS_ROOT = Path("view/web/static/js")
JS_FILES = sorted(JS_ROOT.glob("*.js"))


@pytest.mark.parametrize("script_path", JS_FILES, ids=lambda p: p.name)
def test_static_js_files_are_valid_javascript(script_path):
    """정적 JS 파일이 Node 문법 검사에서 통과해야 한다."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node executable is required for JavaScript syntax checks")

    result = subprocess.run(
        [node, "--check", str(script_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
