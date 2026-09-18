"""favorite.js의 stale 가격 유지 동작 테스트 (Node 실행).

30초 자동 갱신은 응답을 통째로 갈아끼우므로, 한 번의 일시적 조회 실패가
화면에 떠 있던 가격까지 지운다. 직전 값을 승계하는지 여기서 고정한다.
"""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


DRIVER = """
const fs = require('fs');
const vm = require('vm');

const SOURCE = process.argv[2];

const sandbox = {
    document: {
        getElementById: () => null,
        querySelectorAll: () => [],
        addEventListener: () => {},
        hidden: false,
    },
    window: { addEventListener: () => {} },
    navigator: { sendBeacon: () => {} },
    console,
    setInterval: () => 0,
    clearInterval: () => {},
    setTimeout: (fn) => { fn(); return 0; },
    fetch: async () => ({ ok: true, json: async () => [] }),
    StockAutocomplete: () => {},
    showToast: () => {},
    Date,
    Number,
    JSON,
    parseFloat,
    parseInt,
    isNaN,
    String,
    Object,
};
sandbox.globalThis = sandbox;
vm.runInNewContext(fs.readFileSync(SOURCE, 'utf-8'), sandbox, { filename: 'favorite.js' });

const NOW = '2026-09-18T14:33:00+09:00';

const fresh = { code: '005930', name: '삼성전자', price: '71000', rate: '1.2',
                price_stale: false, price_as_of: null };
const lost = { code: '005930', name: '삼성전자', price: null, rate: null,
               price_stale: false, price_as_of: null };
const neverHad = { code: '000660', name: 'SK하이닉스', price: null, rate: null,
                   price_stale: false, price_as_of: null };
const serverStale = { code: '005930', name: '삼성전자', price: '70500', rate: '-0.7',
                      price_stale: true, price_as_of: '2026-08-14' };

// 1) 신선한 값에는 수신 시각을 찍어 둔다 (다음 주기 승계 시 기준 시각이 되어야 한다)
const stamped = sandbox._mergeFavoriteItems([], [fresh], NOW);

// 2) 값을 잃으면 직전 값을 승계하고 stale 로 표시한다
const carried = sandbox._mergeFavoriteItems(stamped, [lost], NOW);

// 3) 직전에도 값이 없었으면 승계할 것이 없다
const empty = sandbox._mergeFavoriteItems([neverHad], [neverHad], NOW);

// 4) 서버가 이미 stale 로 준 값은 그대로 쓴다
const fromServer = sandbox._mergeFavoriteItems([], [serverStale], NOW);

const staleRow = sandbox._buildRow(carried[0]);
const freshRow = sandbox._buildRow(stamped[0]);

console.log(JSON.stringify({
    stampedAsOf: stamped[0].price_as_of,
    stampedStale: stamped[0].price_stale,
    carriedPrice: carried[0].price,
    carriedRate: carried[0].rate,
    carriedStale: carried[0].price_stale,
    carriedAsOf: carried[0].price_as_of,
    emptyPrice: empty[0].price,
    emptyStale: empty[0].price_stale,
    serverStalePrice: fromServer[0].price,
    serverStaleAsOf: fromServer[0].price_as_of,
    staleRowMarked: staleRow.includes('마지막으로 확인된'),
    staleRowDimmed: staleRow.includes('opacity'),
    staleRowShowsPrice: staleRow.includes('71,000'),
    freshRowMarked: freshRow.includes('마지막으로 확인된'),
    freshRowDimmed: freshRow.includes('opacity'),
}));
"""


def _run_driver(tmp_path: Path) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node executable is required for JavaScript behaviour checks")

    driver_path = tmp_path / "favorite_stale_driver.js"
    driver_path.write_text(DRIVER, encoding="utf-8")
    result = subprocess.run(
        [node, str(driver_path), str(Path("view/web/static/js/favorite.js").resolve())],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_a_lost_price_keeps_the_previous_value_as_stale(tmp_path):
    """조회가 한 번 밀려 price가 비어 와도 직전 값을 승계해 빈 칸을 만들지 않는다."""
    outcome = _run_driver(tmp_path)

    assert outcome["carriedPrice"] == "71000"
    assert outcome["carriedRate"] == "1.2"
    assert outcome["carriedStale"] is True
    assert outcome["carriedAsOf"] == "2026-09-18T14:33:00+09:00"


def test_a_fresh_price_is_stamped_but_not_marked_stale(tmp_path):
    """신선한 값은 stale 이 아니며, 승계 대비 수신 시각만 기록한다."""
    outcome = _run_driver(tmp_path)

    assert outcome["stampedAsOf"] == "2026-09-18T14:33:00+09:00"
    assert outcome["stampedStale"] is False
    assert outcome["freshRowMarked"] is False
    assert outcome["freshRowDimmed"] is False


def test_nothing_is_invented_when_there_is_no_previous_price(tmp_path):
    """직전에도 값이 없으면 빈 값 그대로 둔다."""
    outcome = _run_driver(tmp_path)

    assert outcome["emptyPrice"] is None
    assert outcome["emptyStale"] is False


def test_a_server_side_stale_price_is_passed_through(tmp_path):
    """서버가 기준일과 함께 stale 로 내려준 값은 덮어쓰지 않는다."""
    outcome = _run_driver(tmp_path)

    assert outcome["serverStalePrice"] == "70500"
    assert outcome["serverStaleAsOf"] == "2026-08-14"


def test_a_stale_row_is_visibly_distinguishable(tmp_path):
    """오래된 값은 현재가로 오해되지 않도록 흐리게 + 기준 시각 안내와 함께 렌더한다."""
    outcome = _run_driver(tmp_path)

    assert outcome["staleRowShowsPrice"] is True
    assert outcome["staleRowMarked"] is True
    assert outcome["staleRowDimmed"] is True
