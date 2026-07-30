/*
 * jsdom 기반 홈 화면 S&P500 히트맵(트리맵) 회귀 테스트.
 *
 * 실행: node run_market_heatmap_dom_tests.mjs
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { applyCommonStubs, test, assert, run } from "./harness.mjs";

const HEATMAP_JS = process.env.MARKET_HEATMAP_JS_PATH
  ? resolve(process.env.MARKET_HEATMAP_JS_PATH)
  : resolve(import.meta.dirname, "../../view/web/static/js/market_heatmap.js");

const SCAFFOLD = `
<div class="card" id="market-heatmap-card">
  <span id="market-heatmap-updated-at"></span>
  <div id="market-heatmap"></div>
</div>
<div class="card" id="domestic-heatmap-card">
  <span id="domestic-heatmap-updated-at"></span>
  <div id="domestic-heatmap"></div>
</div>
`;

// 홈 경로('/')로 만들면 스크립트의 DOMContentLoaded 자동 초기화가 끼어들어 호출 수가 흔들린다.
// 자동 초기화 자체는 아래 전용 테스트에서 홈 경로 창으로 검증한다.
function makeWindow(fetchWithTimeout, url = "http://localhost/heatmap-test") {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>${SCAFFOLD}</body></html>`, {
    url,
    runScripts: "outside-only",
  });
  const { window } = dom;
  applyCommonStubs(window);
  window.fetchWithTimeout = fetchWithTimeout;
  window.eval(readFileSync(HEATMAP_JS, "utf8"));
  return window;
}

function success(data) {
  return { ok: true, json: async () => ({ rt_cd: "0", data }) };
}

function marketMode(modes) {
  return { ok: true, json: async () => ({ enabled_market_modes: modes }) };
}

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

function item(overrides) {
  return {
    symbol: "AAA",
    name: "테스트",
    sector: "Technology",
    price: 100,
    change_rate: 1.0,
    market_cap_usd: 1e11,
    ...overrides,
  };
}

function pct(el, prop) {
  return Number.parseFloat(el.style[prop]);
}

test("시가총액이 큰 종목이 비례해서 큰 타일을 차지한다", async () => {
  const window = makeWindow(async () => success({
    items: [
      item({ symbol: "BIG", market_cap_usd: 900 }),
      item({ symbol: "SMALL", market_cap_usd: 100 }),
    ],
  }));

  await window.loadMarketHeatmap();

  const doc = window.document;
  const big = doc.querySelector('.heatmap-tile[data-symbol="BIG"]');
  const small = doc.querySelector('.heatmap-tile[data-symbol="SMALL"]');
  assert(big && small, "두 종목 타일이 모두 렌더되어야 함");

  const bigArea = pct(big, "width") * pct(big, "height");
  const smallArea = pct(small, "width") * pct(small, "height");
  const ratio = bigArea / smallArea;
  assert(ratio > 8.5 && ratio < 9.5, `면적비가 시총비(9:1)를 따라야 함 (실제 ${ratio.toFixed(2)})`);
});

test("섹터 블록이 시총 합계 내림차순으로 렌더된다", async () => {
  const window = makeWindow(async () => success({
    items: [
      item({ symbol: "H1", sector: "Health Care", market_cap_usd: 300 }),
      item({ symbol: "T1", sector: "Technology", market_cap_usd: 500 }),
      item({ symbol: "T2", sector: "Technology", market_cap_usd: 400 }),
      item({ symbol: "E1", sector: "Energy", market_cap_usd: 100 }),
    ],
  }));

  await window.loadMarketHeatmap();

  const sectors = [...window.document.querySelectorAll(".heatmap-sector")]
    .map(el => el.dataset.sector);
  assert(
    JSON.stringify(sectors) === JSON.stringify(["Technology", "Health Care", "Energy"]),
    `섹터가 시총 합계순이어야 함 (실제 ${sectors.join(", ")})`,
  );
});

test("모든 타일이 컨테이너 경계 안에 배치된다", async () => {
  const window = makeWindow(async () => success({
    items: [
      item({ symbol: "A", sector: "Technology", market_cap_usd: 500 }),
      item({ symbol: "B", sector: "Technology", market_cap_usd: 120 }),
      item({ symbol: "C", sector: "Technology", market_cap_usd: 40 }),
      item({ symbol: "D", sector: "Energy", market_cap_usd: 220 }),
      item({ symbol: "E", sector: "Energy", market_cap_usd: 60 }),
      item({ symbol: "F", sector: "Financials", market_cap_usd: 30 }),
    ],
  }));

  await window.loadMarketHeatmap();

  const boxes = [
    ...window.document.querySelectorAll(".heatmap-sector"),
    ...window.document.querySelectorAll(".heatmap-tile"),
  ];
  assert(boxes.length === 9, `섹터 3 + 타일 6 이 렌더되어야 함 (실제 ${boxes.length})`);
  boxes.forEach(box => {
    const left = pct(box, "left");
    const top = pct(box, "top");
    const width = pct(box, "width");
    const height = pct(box, "height");
    assert([left, top, width, height].every(Number.isFinite), "좌표가 숫자여야 함");
    assert(width > 0 && height > 0, "타일 크기는 0보다 커야 함");
    assert(left >= -0.01 && top >= -0.01, "타일이 컨테이너 왼쪽/위를 벗어남");
    assert(left + width <= 100.01 && top + height <= 100.01, "타일이 컨테이너 오른쪽/아래를 벗어남");
  });
});

test("등락 방향에 따라 색 계열과 표기 부호가 갈린다", async () => {
  const window = makeWindow(async () => success({
    items: [
      item({ symbol: "UP", change_rate: 3.5 }),
      item({ symbol: "DOWN", change_rate: -2.1 }),
      item({ symbol: "FLAT", change_rate: 0 }),
      item({ symbol: "NULL", change_rate: null }),
    ],
  }));

  await window.loadMarketHeatmap();

  const doc = window.document;
  const bySymbol = symbol => doc.querySelector(`.heatmap-tile[data-symbol="${symbol}"]`);
  assert(bySymbol("UP").dataset.direction === "up", "상승 타일 방향이 up 이어야 함");
  assert(bySymbol("DOWN").dataset.direction === "down", "하락 타일 방향이 down 이어야 함");
  assert(bySymbol("FLAT").dataset.direction === "flat", "보합 타일 방향이 flat 이어야 함");
  assert(bySymbol("NULL").dataset.direction === "unknown", "등락률 없는 타일은 unknown 이어야 함");

  const colors = new Set(["UP", "DOWN", "FLAT"].map(s => bySymbol(s).style.backgroundColor));
  assert(colors.size === 3, "상승/하락/보합 색이 서로 달라야 함");
  assert(bySymbol("UP").textContent.includes("+3.50%"), "상승 등락률에 + 부호가 붙어야 함");
  assert(bySymbol("DOWN").textContent.includes("-2.10%"), "하락 등락률이 표시되어야 함");
  assert(!bySymbol("NULL").textContent.includes("NaN"), "등락률 없음이 NaN 으로 노출됨");
});

test("응답 문자열을 HTML 이 아닌 텍스트로 렌더링한다", async () => {
  const window = makeWindow(async () => success({
    items: [item({
      symbol: '<img id="injected-symbol" src=x>',
      name: '<img id="injected-name" src=x>',
      sector: '<img id="injected-sector" src=x>',
    })],
  }));

  await window.loadMarketHeatmap();

  const target = window.document.getElementById("market-heatmap");
  assert(!target.querySelector("#injected-symbol, #injected-name, #injected-sector"),
    "응답 문자열이 HTML 로 해석됨");
  assert(target.textContent.includes('<img id="injected-symbol" src=x>'),
    "심볼이 텍스트로 표시되어야 함");
});

test("HTTP 오류는 JSON 파싱 없이 상태 안내를 표시한다", async () => {
  let jsonCalled = false;
  const window = makeWindow(async () => ({
    ok: false,
    status: 503,
    json: async () => { jsonCalled = true; throw new Error("JSON 파싱 실패"); },
  }));

  await window.loadMarketHeatmap();

  const target = window.document.getElementById("market-heatmap");
  assert(jsonCalled === false, "HTTP 오류 응답은 JSON 으로 파싱하면 안 됨");
  assert(target.textContent.includes("HTTP 503"), "HTTP 상태 안내가 표시되어야 함");
});

test("빈 목록과 비정상 응답을 사용자 안내로 처리한다", async () => {
  const responses = [success({ items: [] }), { ok: true, json: async () => ({ rt_cd: "1", msg1: "수집 실패" }) }];
  const window = makeWindow(async () => responses.shift());

  await window.loadMarketHeatmap();
  assert(window.document.getElementById("market-heatmap").textContent.includes("조회 결과가 없습니다"),
    "빈 목록 안내가 표시되어야 함");

  await window.loadMarketHeatmap();
  assert(window.document.getElementById("market-heatmap").textContent.includes("수집 실패"),
    "실패 사유가 표시되어야 함");
});

test("늦게 끝난 이전 요청이 최신 히트맵을 덮어쓰지 않는다", async () => {
  const pending = [];
  const window = makeWindow(() => {
    const request = deferred();
    pending.push(request);
    return request.promise;
  });

  const first = window.loadMarketHeatmap();
  const second = window.loadMarketHeatmap();
  pending[1].resolve(success({ items: [item({ symbol: "NEW" })] }));
  await second;
  pending[0].resolve(success({ items: [item({ symbol: "OLD" })] }));
  await first;

  const target = window.document.getElementById("market-heatmap");
  assert(target.querySelector('.heatmap-tile[data-symbol="NEW"]'), "최신 결과가 표시되어야 함");
  assert(!target.querySelector('.heatmap-tile[data-symbol="OLD"]'), "이전 결과가 최신 결과를 덮어씀");
});

test("미국장이 비활성인 run 에서는 히트맵 카드를 감춘다", async () => {
  const calls = [];
  const window = makeWindow(async (url) => {
    calls.push(url);
    if (url.startsWith("/api/market-mode")) return marketMode(["domestic"]);
    return success({ items: [item({})] });
  });

  await window.initMarketHeatmap();

  assert(window.document.getElementById("market-heatmap-card").style.display === "none",
    "미국장 비활성 시 카드가 감춰져야 함");
  assert(!calls.some(url => url.startsWith("/api/overseas/")),
    "비활성 상태에서 시가총액 API 를 호출하면 안 됨");
});

test("미국장이 활성이면 카드를 노출하고 히트맵을 조회한다", async () => {
  const window = makeWindow(async (url) => {
    if (url.startsWith("/api/market-mode")) return marketMode(["domestic", "overseas_us"]);
    return success({ items: [item({ symbol: "MSFT" })], updated_at: 1767225600 });
  });

  await window.initMarketHeatmap();

  const doc = window.document;
  assert(doc.getElementById("market-heatmap-card").style.display !== "none", "카드가 노출되어야 함");
  assert(doc.querySelector('.heatmap-tile[data-symbol="MSFT"]'), "히트맵 타일이 렌더되어야 함");
  assert(doc.getElementById("market-heatmap-updated-at").textContent.includes("최신 업데이트"),
    "업데이트 시각이 표시되어야 함");
});

function domesticItem(overrides) {
  return { code: "005930", name: "삼성전자", change_rate: "-0.72", market_cap: 12101797, market: "KOSPI", ...overrides };
}

test("국내 히트맵은 섹터 블록 없이 시총순 타일만 그린다", async () => {
  const window = makeWindow(async () => success({
    trade_date: "20260730",
    items: [
      domesticItem({ code: "005930", name: "삼성전자", market_cap: 900 }),
      domesticItem({ code: "000660", name: "SK하이닉스", market_cap: 100 }),
    ],
  }));

  await window.loadDomesticHeatmap();

  const doc = window.document;
  const target = doc.getElementById("domestic-heatmap");
  assert(!target.querySelector(".heatmap-sector-title"), "국내 맵에는 섹터 헤더가 없어야 함");
  assert(target.querySelectorAll(".heatmap-tile").length === 2, "종목 타일이 렌더되어야 함");

  const big = target.querySelector('.heatmap-tile[data-symbol="005930"]');
  const small = target.querySelector('.heatmap-tile[data-symbol="000660"]');
  const ratio = (pct(big, "width") * pct(big, "height")) / (pct(small, "width") * pct(small, "height"));
  assert(ratio > 8.5 && ratio < 9.5, `면적비가 시총비(9:1)를 따라야 함 (실제 ${ratio.toFixed(2)})`);
  assert(big.textContent.includes("삼성전자"), "국내 타일은 종목명을 표시해야 함");
});

test("국내 히트맵은 종가 기준일을 명시한다", async () => {
  const window = makeWindow(async () => success({ trade_date: "20260730", items: [domesticItem({})] }));

  await window.loadDomesticHeatmap();

  const label = window.document.getElementById("domestic-heatmap-updated-at").textContent;
  assert(label.includes("2026-07-30"), `기준일이 표시되어야 함 (실제 ${label})`);
  assert(label.includes("종가"), "장중 오해를 막기 위해 종가 기준임을 밝혀야 함");
});

test("국내 히트맵의 빈 스냅샷과 응답 문자열을 안전하게 처리한다", async () => {
  const responses = [
    success({ trade_date: null, items: [] }),
    success({ trade_date: "20260730", items: [domesticItem({ name: '<img id="injected-kr" src=x>' })] }),
  ];
  const window = makeWindow(async () => responses.shift());

  await window.loadDomesticHeatmap();
  assert(window.document.getElementById("domestic-heatmap").textContent.includes("조회 결과가 없습니다"),
    "빈 스냅샷 안내가 표시되어야 함");

  await window.loadDomesticHeatmap();
  const target = window.document.getElementById("domestic-heatmap");
  assert(!target.querySelector("#injected-kr"), "응답 문자열이 HTML 로 해석됨");
  assert(target.textContent.includes('<img id="injected-kr" src=x>'), "종목명이 텍스트로 표시되어야 함");
});

test("국내 조회가 미국 히트맵을 덮어쓰지 않는다", async () => {
  const window = makeWindow(async (url) => (url.startsWith("/api/heatmap/domestic")
    ? success({ trade_date: "20260730", items: [domesticItem({ code: "005930" })] })
    : success({ items: [item({ symbol: "MSFT" })] })));

  await window.loadMarketHeatmap();
  await window.loadDomesticHeatmap();

  const doc = window.document;
  assert(doc.querySelector('#market-heatmap .heatmap-tile[data-symbol="MSFT"]'), "미국 히트맵이 유지되어야 함");
  assert(doc.querySelector('#domestic-heatmap .heatmap-tile[data-symbol="005930"]'), "국내 히트맵이 렌더되어야 함");
});

test("홈 경로에서는 로드 시 자동으로 히트맵을 초기화한다", async () => {
  const calls = [];
  makeWindow(async (url) => {
    calls.push(url);
    if (url.startsWith("/api/market-mode")) return marketMode(["overseas_us"]);
    return success({ items: [item({})] });
  }, "http://localhost/");

  await new Promise(done => setTimeout(done, 50));

  assert(calls.some(url => url.startsWith("/api/market-mode")), "홈 진입 시 시장 활성 여부를 확인해야 함");
  assert(calls.some(url => url.startsWith("/api/overseas/top-market-cap")), "홈 진입 시 미국 히트맵을 조회해야 함");
  assert(calls.some(url => url.startsWith("/api/heatmap/domestic")), "홈 진입 시 국내 히트맵을 조회해야 함");
});

await run();
