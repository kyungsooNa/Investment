"""common.js의 세션 만료(401) 처리 동작 테스트 (Node 실행)."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


DRIVER = """
const fs = require('fs');
const vm = require('vm');

function run({ userRole, status, path }) {
    const reloads = [];
    const doc = {
        body: { dataset: { userRole }, classList: { add() {} }, setAttribute() {} },
        cookie: '',
        addEventListener: () => {},
        querySelectorAll: () => [],
        querySelector: () => null,
        getElementById: () => null,
        createElement: () => ({ style: {}, classList: { add() {} }, setAttribute() {}, appendChild() {} }),
        dispatchEvent: () => {},
    };
    const win = {
        fetch: async () => ({ status, ok: status < 400 }),
        location: {
            href: 'http://localhost:8000/favorite',
            origin: 'http://localhost:8000',
            reload: () => reloads.push(1),
        },
        addEventListener: () => {},
        history: { pushState() {} },
    };
    const sandbox = {
        window: win,
        document: doc,
        console,
        setTimeout: (fn) => { fn(); return 0; },
        clearTimeout: () => {},
        setInterval: () => 0,
        URL,
        Headers: class { has() { return false; } set() {} },
        navigator: { sendBeacon: () => {} },
    };
    sandbox.globalThis = sandbox;
    vm.runInNewContext(fs.readFileSync(SOURCE, 'utf-8'), sandbox, { filename: 'common.js' });
    return sandbox.window.fetch(path).then(() => reloads.length);
}

const SOURCE = process.argv[2];

Promise.all([
    run({ userRole: 'admin', status: 401, path: '/api/favorite' }),
    run({ userRole: '', status: 401, path: '/api/favorite' }),
    run({ userRole: 'admin', status: 200, path: '/api/favorite' }),
    run({ userRole: 'admin', status: 401, path: '/favorite' }),
]).then(([expired, loginPage, ok, pageRequest]) => {
    console.log(JSON.stringify({ expired, loginPage, ok, pageRequest }));
});
"""


def _run_driver(tmp_path: Path) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node executable is required for JavaScript behaviour checks")

    driver_path = tmp_path / "session_expiry_driver.js"
    driver_path.write_text(DRIVER, encoding="utf-8")
    result = subprocess.run(
        [node, str(driver_path), str(Path("view/web/static/js/common.js").resolve())],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_expired_session_triggers_relogin_prompt(tmp_path):
    """401 응답을 받으면 로그인 화면으로 되돌려 재로그인을 안내한다."""
    assert _run_driver(tmp_path)["expired"] == 1


def test_unauthorized_is_ignored_outside_authenticated_page(tmp_path):
    """로그인 화면(역할 없음)·정상 응답·페이지 요청에서는 새로고침하지 않는다."""
    outcome = _run_driver(tmp_path)

    assert outcome["loginPage"] == 0
    assert outcome["ok"] == 0
    assert outcome["pageRequest"] == 0
