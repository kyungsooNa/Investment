/*
 * jsdom 기반 히트맵 렌더 라이브러리(market_heatmap.js) 회귀 테스트.
 *
 * market_heatmap.js 는 화면 배선 없이 "소스(source) 를 받아 트리맵을 그리는" 라이브러리다.
 * 여기서는 그 라이브러리 자체(트리맵 기하, 색상, 이스케이프, 경합 가드)를 검증하고,
 * 탭/줌 등 화면 배선은 run_heatmap_page_dom_tests.mjs 가 맡는다.
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
<span id="overseas-caption"></span>
<div id="overseas-panel"></div>
<span id="domestic-caption"></span>
<div id="domestic-panel"></div>
`;

function makeWindow(fetchWithTimeout) {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>${SCAFFOLD}</body></html>`, {
    url: "http://localhost/heatmap-test",
    runScripts: "outside-only",
  });
  const { window } = dom;
  applyCommonStubs(window);
  window.fetchWithTimeout = fetchWithTimeout;
  window.eval(readFileSync(HEATMAP_JS, "utf8"));
  return window;
}

// 화면 배선 없이 라이브러리를 직접 구동하기 위한 최소 소스.
function overseasSource(window) {
  return {
    targetId: "overseas-panel",
    captionId: "overseas-caption",
    url: "/api/overseas/top-market-cap?limit=500",
    loadingText: "조회 중...",
    toGroups: window._overseasGroups,
    caption: window._overseasCaption,
    sequence: 0,
  };
}

function domesticSource(window) {
  return {
    targetId: "domestic-panel",
    captionId: "domestic-caption",
    url: "/api/heatmap/domestic?limit=500",
    loadingText: "조회 중...",
    toGroups: window._domesticGroups,
    caption: window._domesticCaption,
    sequence: 0,
  };
}

function success(data) {
  return { ok: true, json: async () => ({ rt_cd: "0", data }) };
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

  await window._loadHeatmap(overseasSource(window));

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

  await window._loadHeatmap(overseasSource(window));

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

  await window._loadHeatmap(overseasSource(window));

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

  await window._loadHeatmap(overseasSource(window));

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

  await window._loadHeatmap(overseasSource(window));

  const target = window.document.getElementById("overseas-panel");
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

  await window._loadHeatmap(overseasSource(window));

  const target = window.document.getElementById("overseas-panel");
  assert(jsonCalled === false, "HTTP 오류 응답은 JSON 으로 파싱하면 안 됨");
  assert(target.textContent.includes("HTTP 503"), "HTTP 상태 안내가 표시되어야 함");
});

test("빈 목록과 비정상 응답을 사용자 안내로 처리한다", async () => {
  const responses = [success({ items: [] }), { ok: true, json: async () => ({ rt_cd: "1", msg1: "수집 실패" }) }];
  const window = makeWindow(async () => responses.shift());
  const source = overseasSource(window);

  await window._loadHeatmap(source);
  assert(window.document.getElementById("overseas-panel").textContent.includes("조회 결과가 없습니다"),
    "빈 목록 안내가 표시되어야 함");

  await window._loadHeatmap(source);
  assert(window.document.getElementById("overseas-panel").textContent.includes("수집 실패"),
    "실패 사유가 표시되어야 함");
});

test("늦게 끝난 이전 요청이 최신 히트맵을 덮어쓰지 않는다", async () => {
  const pending = [];
  const window = makeWindow(() => {
    const request = deferred();
    pending.push(request);
    return request.promise;
  });
  const source = overseasSource(window);

  const first = window._loadHeatmap(source);
  const second = window._loadHeatmap(source);
  pending[1].resolve(success({ items: [item({ symbol: "NEW" })] }));
  await second;
  pending[0].resolve(success({ items: [item({ symbol: "OLD" })] }));
  await first;

  const target = window.document.getElementById("overseas-panel");
  assert(target.querySelector('.heatmap-tile[data-symbol="NEW"]'), "최신 결과가 표시되어야 함");
  assert(!target.querySelector('.heatmap-tile[data-symbol="OLD"]'), "이전 결과가 최신 결과를 덮어씀");
});

test("미국장 활성 여부를 시장 모드로 판단한다", async () => {
  const window = makeWindow(async (url) => {
    assert(url.startsWith("/api/market-mode"), `시장 모드 API 를 호출해야 함 (실제 ${url})`);
    return { ok: true, json: async () => ({ enabled_market_modes: ["domestic"] }) };
  });

  assert(await window._heatmapOverseasEnabled() === false, "미국장이 없으면 false 여야 함");
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

  await window._loadHeatmap(domesticSource(window));

  const doc = window.document;
  const target = doc.getElementById("domestic-panel");
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

  await window._loadHeatmap(domesticSource(window));

  const label = window.document.getElementById("domestic-caption").textContent;
  assert(label.includes("2026-07-30"), `기준일이 표시되어야 함 (실제 ${label})`);
  assert(label.includes("종가"), "장중 오해를 막기 위해 종가 기준임을 밝혀야 함");
});

test("국내 히트맵의 빈 스냅샷과 응답 문자열을 안전하게 처리한다", async () => {
  const responses = [
    success({ trade_date: null, items: [] }),
    success({ trade_date: "20260730", items: [domesticItem({ name: '<img id="injected-kr" src=x>' })] }),
  ];
  const window = makeWindow(async () => responses.shift());
  const source = domesticSource(window);

  await window._loadHeatmap(source);
  assert(window.document.getElementById("domestic-panel").textContent.includes("조회 결과가 없습니다"),
    "빈 스냅샷 안내가 표시되어야 함");

  await window._loadHeatmap(source);
  const target = window.document.getElementById("domestic-panel");
  assert(!target.querySelector("#injected-kr"), "응답 문자열이 HTML 로 해석됨");
  assert(target.textContent.includes('<img id="injected-kr" src=x>'), "종목명이 텍스트로 표시되어야 함");
});

test("한 소스의 조회가 다른 소스의 히트맵을 덮어쓰지 않는다", async () => {
  const window = makeWindow(async (url) => (url.startsWith("/api/heatmap/domestic")
    ? success({ trade_date: "20260730", items: [domesticItem({ code: "005930" })] })
    : success({ items: [item({ symbol: "MSFT" })] })));

  await window._loadHeatmap(overseasSource(window));
  await window._loadHeatmap(domesticSource(window));

  const doc = window.document;
  assert(doc.querySelector('#overseas-panel .heatmap-tile[data-symbol="MSFT"]'), "미국 히트맵이 유지되어야 함");
  assert(doc.querySelector('#domestic-panel .heatmap-tile[data-symbol="005930"]'), "국내 히트맵이 렌더되어야 함");
});

// 업종(sector)이 채워지면 미국 맵처럼 섹터 블록으로 묶는다. 네이버 업종은 배타적이라
// (2026-08-14 실측: 두 업종 이상 소속 0건) 종목 하나가 한 블록에만 들어간다.
test("국내 히트맵은 업종이 오면 섹터 블록으로 묶는다", async () => {
  const window = makeWindow(async () => success({
    trade_date: "20260814",
    items: [
      domesticItem({ code: "005930", name: "삼성전자", sector: "반도체와반도체장비", market_cap: 500 }),
      domesticItem({ code: "000660", name: "SK하이닉스", sector: "반도체와반도체장비", market_cap: 400 }),
      domesticItem({ code: "005380", name: "현대차", sector: "자동차", market_cap: 300 }),
    ],
  }));

  await window._loadHeatmap(domesticSource(window));

  const target = window.document.getElementById("domestic-panel");
  const sectors = [...target.querySelectorAll(".heatmap-sector")].map(el => el.dataset.sector);
  assert(
    JSON.stringify(sectors) === JSON.stringify(["반도체와반도체장비", "자동차"]),
    `섹터가 시총 합계순이어야 함 (실제 ${sectors.join(", ")})`,
  );
  assert(target.querySelectorAll(".heatmap-tile").length === 3, "종목 타일은 그대로 3개");
});

test("업종이 없는 종목은 기타로 묶는다", async () => {
  const window = makeWindow(async () => success({
    trade_date: "20260814",
    items: [
      domesticItem({ code: "005930", sector: "반도체와반도체장비", market_cap: 500 }),
      domesticItem({ code: "999999", name: "미분류주", sector: null, market_cap: 100 }),
    ],
  }));

  await window._loadHeatmap(domesticSource(window));

  const sectors = [...window.document.querySelectorAll("#domestic-panel .heatmap-sector")]
    .map(el => el.dataset.sector);
  assert(sectors.includes("기타"), `미분류는 기타로 (실제 ${sectors.join(", ")})`);
});

test("업종이 하나도 없으면 예전처럼 단일 그리드로 그린다", async () => {
  const window = makeWindow(async () => success({
    trade_date: "20260814",
    items: [domesticItem({ code: "005930" }), domesticItem({ code: "000660", market_cap: 100 })],
  }));

  await window._loadHeatmap(domesticSource(window));

  const target = window.document.getElementById("domestic-panel");
  assert(!target.querySelector(".heatmap-sector-title"), "분류 수집 전에는 섹터 헤더가 없어야 함");
  assert(target.querySelectorAll(".heatmap-tile").length === 2, "타일은 그대로 그려져야 함");
});

await run();
