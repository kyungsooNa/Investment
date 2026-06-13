"""웹 화면 JS 의 런타임 DOM 회귀를 jsdom 으로 검증한다 (pytest gate 편입).

해외(overseas) 기능이 기존 국내 차트/조회 경로를 깨거나, 해외 화면 자체가
KIS 응답 형태를 잘못 가정하는 회귀는 소스 문자열 검사로는 못 잡으므로,
tests/frontend/run_*_dom_tests.mjs 를 node 서브프로세스로 실행해 실제 DOM 행위를 확인한다.

node 또는 jsdom 미설치 시에는 skip 한다(설치: `cd tests/frontend && npm install`).
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_FRONTEND_DIR = _REPO_ROOT / "tests" / "frontend"
_RUNNERS = sorted(_FRONTEND_DIR.glob("run_*_dom_tests.mjs"))


def _skip_if_unavailable():
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — JS DOM 회귀 테스트 skip")
    if not (_FRONTEND_DIR / "node_modules" / "jsdom").exists():
        pytest.skip("jsdom 미설치 — `cd tests/frontend && npm install` 후 실행")


@pytest.mark.parametrize("runner", _RUNNERS, ids=lambda p: p.name)
def test_js_dom_regression_suite(runner):
    _skip_if_unavailable()

    result = subprocess.run(
        ["node", str(runner)],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert result.returncode == 0, (
        f"jsdom 회귀 테스트 실패 ({runner.name})\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
