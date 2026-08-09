/*
 * jsdom 기반 히트맵 전용 페이지(/heatmap) 회귀 테스트.
 *
 * 홈의 미리보기와 달리 전용 페이지는 (1) 한국/미국 탭 전환과 (2) 줌을 소유한다.
 * 줌은 CSS transform 이 아니라 캔버스 크기 + 재렌더로 구현돼 있어(작은 타일의
 * 라벨이 실제로 살아나야 함) 그 계약을 여기서 잠근다.
 *
 * 실행: node run_heatmap_page_dom_tests.mjs
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { applyCommonStubs, test, assert, run } from "./harness.mjs";

const HEATMAP_JS = resolve(import.meta.dirname, "../../view/web/static/js/market_heatmap.js");
const HEATMAP_PAGE_JS = resolve(import.meta.dirname, "../../view/web/static/js/heatmap_page.js");

const SCAFFOLD = `
<div class="heatmap-page-tabs">
  <button type="button" id="heatmap-page-tab-domestic" class="sub-tab-btn active">한국</button>
  <button type="button" id="heatmap-page-tab-overseas" class="sub-tab-btn">미국</button>
</div>
<div class="heatmap-toolbar">
  <span id="heatmap-page-domestic-caption" class="heatmap-updated">기준일: --</span>
  <span id="heatmap-page-overseas-caption" class="heatmap-updated" style="display:none;">최신 업데이트: --</span>
  <span class="heatmap-search" id="heatmap-page-search-wrap">
    <input type="search" id="heatmap-page-search" placeholder="종목명·코드 검색">
    <span id="heatmap-page-search-count"></span>
  </span>
  <span class="heatmap-period" id="heatmap-page-market-wrap">
    <label for="heatmap-page-market">시장</label>
    <select id="heatmap-page-market">
      <option value="ALL" selected>공통</option>
      <option value="KOSPI">코스피</option>
      <option value="KOSDAQ">코스닥</option>
    </select>
  </span>
  <span class="heatmap-period" id="heatmap-page-period-wrap">
    <label for="heatmap-page-period">기간</label>
    <select id="heatmap-page-period">
      <option value="1d" selected>1일</option>
      <option value="1w">1주</option>
      <option value="1m">1개월</option>
      <option value="3m">3개월</option>
      <option value="6m">6개월</option>
      <option value="ytd">올해</option>
      <option value="1y">1년</option>
    </select>
  </span>
  <span class="heatmap-zoom">
    <button type="button" id="heatmap-page-zoom-out">−</button>
    <span id="heatmap-page-zoom-level">100%</span>
    <button type="button" id="heatmap-page-zoom-in">+</button>
    <button type="button" id="heatmap-page-zoom-reset">초기화</button>
  </span>
</div>
<div id="heatmap-page-viewport" class="heatmap-page-viewport">
  <div id="heatmap-page-sizer" class="heatmap-page-sizer">
    <div id="heatmap-page-domestic" class="heatmap-page-panel"></div>
    <div id="heatmap-page-overseas" class="heatmap-page-panel" style="display:none;"></div>
  </div>
</div>
`;

// 전용 페이지 경로('/heatmap')로 만들면 DOMContentLoaded 자동 초기화가 끼어들어 호출 수가 흔들린다.
// 자동 초기화는 아래 전용 테스트에서 실제 경로 창으로 검증한다.
function makeWindow(fetchWithTimeout, url = "http://localhost/heatmap-test") {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>${SCAFFOLD}</body></html>`, {
    url,
    runScripts: "outside-only",
  });
  const { window } = dom;
  applyCommonStubs(window);
  window.fetchWithTimeout = fetchWithTimeout;
  window.eval(readFileSync(HEATMAP_JS, "utf8"));
  window.eval(readFileSync(HEATMAP_PAGE_JS, "utf8"));
  return window;
}

function success(data) {
  return { ok: true, json: async () => ({ rt_cd: "0", data }) };
}

function marketMode(modes) {
  return { ok: true, json: async () => ({ enabled_market_modes: modes }) };
}

function domesticItem(overrides) {
  return { code: "005930", name: "삼성전자", change_rate: "1.20", market_cap: 1000, market: "KOSPI", ...overrides };
}

function overseasItem(overrides) {
  return { symbol: "AAA", name: "테스트", sector: "Technology", change_rate: 1.0, market_cap_usd: 1e11, ...overrides };
}

// 큰 종목 몇 개 + 꼬리 종목 다수 → 줌 1배에서는 라벨이 생략되는 타일이 반드시 생긴다.
function domesticSnapshot() {
  const items = [];
  for (let i = 0; i < 6; i += 1) {
    items.push(domesticItem({ code: `B${i}`, name: `대형${i}`, market_cap: 4000 - i * 100 }));
  }
  for (let i = 0; i < 40; i += 1) {
    items.push(domesticItem({ code: `S${i}`, name: `소형${i}`, market_cap: 40 - i * 0.5 }));
  }
  return { trade_date: "20260731", items };
}

function labeledSymbols(window, targetId) {
  return [...window.document.querySelectorAll(`#${targetId} .heatmap-tile`)]
    .filter(tile => tile.querySelector(".heatmap-tile-symbol"))
    .map(tile => tile.dataset.symbol);
}

function routed(handlers) {
  return async (url) => {
    const key = Object.keys(handlers).find(prefix => url.startsWith(prefix));
    if (!key) throw new Error(`stub 되지 않은 요청: ${url}`);
    return handlers[key]();
  };
}

test("전용 페이지는 한국 히트맵부터 그리고 미국은 탭 전환 전까지 조회하지 않는다", async () => {
  const calls = [];
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic", "overseas_us"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
    "/api/overseas/top-market-cap": () => success({ items: [overseasItem({})] }),
  }));
  const traced = window.fetchWithTimeout;
  window.fetchWithTimeout = (url, ...rest) => { calls.push(url); return traced(url, ...rest); };

  await window.initHeatmapPage();

  const doc = window.document;
  assert(doc.querySelectorAll("#heatmap-page-domestic .heatmap-tile").length > 0, "한국 히트맵이 그려져야 함");
  assert(!calls.some(url => url.startsWith("/api/overseas/")), "미국 탭 전환 전에는 미국 API 를 호출하면 안 됨");
  assert(doc.getElementById("heatmap-page-overseas").style.display === "none", "미국 패널은 감춰져 있어야 함");
  assert(doc.getElementById("heatmap-page-tab-domestic").classList.contains("active"), "한국 탭이 활성이어야 함");
});

test("미국 탭으로 전환하면 미국 히트맵을 조회하고 패널을 바꾼다", async () => {
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic", "overseas_us"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
    "/api/overseas/top-market-cap": () => success({
      items: [overseasItem({ symbol: "MSFT" })],
      updated_at: 1767225600,
    }),
  }));

  await window.initHeatmapPage();
  await window.setHeatmapTab("overseas");

  const doc = window.document;
  assert(doc.querySelector('#heatmap-page-overseas .heatmap-tile[data-symbol="MSFT"]'), "미국 타일이 렌더되어야 함");
  assert(doc.getElementById("heatmap-page-overseas").style.display !== "none", "미국 패널이 보여야 함");
  assert(doc.getElementById("heatmap-page-domestic").style.display === "none", "한국 패널이 감춰져야 함");
  assert(doc.getElementById("heatmap-page-tab-overseas").classList.contains("active"), "미국 탭이 활성이어야 함");
  assert(!doc.getElementById("heatmap-page-tab-domestic").classList.contains("active"), "한국 탭 활성이 해제돼야 함");
});

test("탭마다 자기 기준 시각 캡션을 보여준다", async () => {
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic", "overseas_us"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
    "/api/overseas/top-market-cap": () => success({ items: [overseasItem({})], updated_at: 1767225600 }),
  }));

  await window.initHeatmapPage();
  const doc = window.document;
  const krCaption = doc.getElementById("heatmap-page-domestic-caption");
  const usCaption = doc.getElementById("heatmap-page-overseas-caption");

  assert(krCaption.textContent.includes("2026-07-31"), `국내 기준일이 표시되어야 함 (실제 ${krCaption.textContent})`);
  assert(krCaption.textContent.includes("종가"), "장중 오해를 막기 위해 종가 기준임을 밝혀야 함");
  assert(usCaption.style.display === "none", "비활성 탭 캡션은 감춰져야 함");

  await window.setHeatmapTab("overseas");
  assert(usCaption.textContent.includes("최신 업데이트"), "미국 갱신 시각이 표시되어야 함");
  assert(krCaption.style.display === "none", "탭 전환 시 이전 캡션이 감춰져야 함");
});

test("미국장이 비활성인 run 에서는 미국 탭을 감춘다", async () => {
  const calls = [];
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
  }));
  const traced = window.fetchWithTimeout;
  window.fetchWithTimeout = (url, ...rest) => { calls.push(url); return traced(url, ...rest); };

  await window.initHeatmapPage();

  assert(window.document.getElementById("heatmap-page-tab-overseas").style.display === "none",
    "미국장 비활성 시 탭이 감춰져야 함");
  assert(!calls.some(url => url.startsWith("/api/overseas/")), "비활성 상태에서 미국 API 를 호출하면 안 됨");
});

test("줌을 올리면 캔버스가 커지고 작은 타일에도 라벨이 붙는다", async () => {
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
  }));

  await window.initHeatmapPage();
  const doc = window.document;
  const sizer = doc.getElementById("heatmap-page-sizer");
  const before = labeledSymbols(window, "heatmap-page-domestic");
  assert(sizer.style.width === "100%", `기본 줌은 100% 여야 함 (실제 ${sizer.style.width})`);
  assert(before.length < doc.querySelectorAll("#heatmap-page-domestic .heatmap-tile").length,
    "기본 줌에서 라벨이 생략된 작은 타일이 있어야 테스트가 의미를 가짐");

  window.zoomHeatmapPage(2);

  const zoom = Number.parseFloat(sizer.style.width);
  assert(zoom > 100, `줌 인 시 캔버스가 커져야 함 (실제 ${sizer.style.width})`);
  assert(sizer.style.height === sizer.style.width, "가로/세로 배율이 같아야 트리맵 비율이 유지됨");
  assert(doc.getElementById("heatmap-page-zoom-level").textContent === `${Math.round(zoom)}%`,
    "줌 배율 표시가 캔버스 배율과 일치해야 함");

  const after = labeledSymbols(window, "heatmap-page-domestic");
  assert(after.length > before.length,
    `줌 인 후 라벨이 늘어야 함 (${before.length} → ${after.length})`);
  before.forEach(symbol => assert(after.includes(symbol), `${symbol} 라벨이 줌 인 후 사라짐`));
});

test("줌 변경은 데이터를 다시 조회하지 않는다", async () => {
  let domesticCalls = 0;
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => { domesticCalls += 1; return success(domesticSnapshot()); },
  }));

  await window.initHeatmapPage();
  assert(domesticCalls === 1, `초기 조회는 1회여야 함 (실제 ${domesticCalls})`);

  window.zoomHeatmapPage(1);
  window.zoomHeatmapPage(1);
  window.resetHeatmapPageZoom();

  assert(domesticCalls === 1, `줌은 재조회 없이 다시 그려야 함 (실제 ${domesticCalls})`);
});

test("줌은 상·하한을 벗어나지 않고 초기화로 100% 로 돌아온다", async () => {
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
  }));

  await window.initHeatmapPage();
  const sizer = window.document.getElementById("heatmap-page-sizer");

  window.zoomHeatmapPage(-1);
  assert(sizer.style.width === "100%", `기본 배율 아래로 축소되면 안 됨 (실제 ${sizer.style.width})`);

  for (let i = 0; i < 20; i += 1) window.zoomHeatmapPage(1);
  const maxZoom = Number.parseFloat(sizer.style.width);
  assert(Number.isFinite(maxZoom) && maxZoom > 100, "최대 배율이 유한한 값이어야 함");
  window.zoomHeatmapPage(1);
  assert(Number.parseFloat(sizer.style.width) === maxZoom, "최대 배율을 넘어서면 안 됨");

  window.resetHeatmapPageZoom();
  assert(sizer.style.width === "100%", "초기화하면 100% 로 돌아와야 함");
  assert(window.document.getElementById("heatmap-page-zoom-level").textContent === "100%",
    "초기화 후 배율 표시도 100% 여야 함");
});

// jsdom 은 레이아웃이 없어 scrollWidth/clientWidth 가 모두 0 이다.
// 커서 고정 계산은 아래 전용 테스트에서 크기를 가진 가짜 viewport 로 검증한다.
function wheel(window, deltaY, options = {}) {
  const viewport = window.document.getElementById("heatmap-page-viewport");
  const event = new window.WheelEvent("wheel", {
    deltaY, deltaMode: 0, clientX: 0, clientY: 0, bubbles: true, cancelable: true, ...options,
  });
  viewport.dispatchEvent(event);
  return event;
}

test("마우스 휠을 위로 굴리면 확대되고 아래로 굴리면 축소된다", async () => {
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
  }));

  await window.initHeatmapPage();
  const sizer = window.document.getElementById("heatmap-page-sizer");
  const before = labeledSymbols(window, "heatmap-page-domestic");

  // 라벨 생략 기준이 완화되는지는 버튼 줌과 같은 배율(2칸=2배)에서 비교한다.
  wheel(window, -100);
  wheel(window, -100);
  const zoomedIn = Number.parseFloat(sizer.style.width);
  assert(zoomedIn > 100, `휠 업이 확대여야 함 (실제 ${sizer.style.width})`);
  assert(sizer.style.height === sizer.style.width, "휠 확대도 가로/세로 배율이 같아야 함");
  assert(window.document.getElementById("heatmap-page-zoom-level").textContent === `${Math.round(zoomedIn)}%`,
    "휠 확대 후 배율 표시가 캔버스 배율과 일치해야 함");
  assert(labeledSymbols(window, "heatmap-page-domestic").length > before.length,
    "휠 확대도 버튼 확대처럼 라벨을 살려야 함");

  wheel(window, 100);
  wheel(window, 100);
  assert(sizer.style.width === "100%", `휠 다운이 축소여야 함 (실제 ${sizer.style.width})`);
});

test("휠 확대는 재조회 없이 다시 그리고 페이지 스크롤을 막는다", async () => {
  let domesticCalls = 0;
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => { domesticCalls += 1; return success(domesticSnapshot()); },
  }));

  await window.initHeatmapPage();
  const event = wheel(window, -100);

  assert(event.defaultPrevented, "휠 줌 중에는 페이지가 같이 스크롤되지 않도록 기본 동작을 막아야 함");
  assert(domesticCalls === 1, `휠 줌은 재조회하지 않아야 함 (실제 ${domesticCalls})`);
});

test("트랙패드의 작은 휠 delta 는 한 칸 분량이 모일 때만 배율을 바꾼다", async () => {
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
  }));

  await window.initHeatmapPage();
  const sizer = window.document.getElementById("heatmap-page-sizer");

  for (let i = 0; i < 4; i += 1) wheel(window, -10);
  assert(sizer.style.width === "100%", `누적이 한 칸에 못 미치면 배율이 그대로여야 함 (실제 ${sizer.style.width})`);

  for (let i = 0; i < 6; i += 1) wheel(window, -10);
  assert(Number.parseFloat(sizer.style.width) > 100, "누적이 한 칸을 채우면 확대돼야 함");
});

test("휠 축소는 기본 배율 아래로 내려가지 않는다", async () => {
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
  }));

  await window.initHeatmapPage();
  const sizer = window.document.getElementById("heatmap-page-sizer");

  for (let i = 0; i < 5; i += 1) wheel(window, 100);
  assert(sizer.style.width === "100%", `기본 배율 아래로 축소되면 안 됨 (실제 ${sizer.style.width})`);
});

// jsdom 은 레이아웃이 없어 스크롤바 두께(offsetWidth - clientWidth)도 0 이다.
// 스크롤바 위 판정은 실제 픽셀에 걸려 있으므로 viewport 에 크기를 심어 재현한다.
function giveViewportScrollbars(window, { width = 800, height = 600, bar = 15 } = {}) {
  const viewport = window.document.getElementById("heatmap-page-viewport");
  const metrics = {
    offsetWidth: width, offsetHeight: height,
    clientWidth: width - bar, clientHeight: height - bar,
    scrollWidth: width * 2, scrollHeight: height * 2,
  };
  Object.entries(metrics).forEach(([key, value]) => {
    Object.defineProperty(viewport, key, { value, configurable: true });
  });
  // scrollTop/scrollLeft 는 jsdom 이 레이아웃 없이 항상 0 으로 두므로 쓰기 가능하게 바꾼다.
  Object.defineProperty(viewport, "scrollTop", { value: 300, writable: true, configurable: true });
  Object.defineProperty(viewport, "scrollLeft", { value: 400, writable: true, configurable: true });
  viewport.getBoundingClientRect = () => ({ left: 0, top: 0, width, height });
  return viewport;
}

test("세로 스크롤바 위에서는 확대가 아니라 위아래로 이동한다", async () => {
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
  }));
  await window.initHeatmapPage();
  const viewport = giveViewportScrollbars(window);
  const zoomBefore = window.document.getElementById("heatmap-page-sizer").style.width;

  // clientWidth(785) 오른쪽 = 세로 스크롤바 영역
  const event = wheel(window, 100, { clientX: 792, clientY: 300 });

  assert(window.document.getElementById("heatmap-page-sizer").style.width === zoomBefore,
    "스크롤바 위 휠은 배율을 바꾸면 안 됨");
  assert(viewport.scrollTop === 400, `아래로 굴리면 아래로 이동해야 함 (실제 ${viewport.scrollTop})`);
  assert(viewport.scrollLeft === 400, "세로 이동이 가로 스크롤을 건드리면 안 됨");
  assert(event.defaultPrevented, "직접 이동시키므로 기본 스크롤은 막아야 함");

  wheel(window, -100, { clientX: 792, clientY: 300 });
  assert(viewport.scrollTop === 300, `위로 굴리면 위로 돌아와야 함 (실제 ${viewport.scrollTop})`);
});

test("가로 스크롤바 위에서도 휠은 이동이다", async () => {
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
  }));
  await window.initHeatmapPage();
  const viewport = giveViewportScrollbars(window);
  const zoomBefore = window.document.getElementById("heatmap-page-sizer").style.width;

  // clientHeight(585) 아래 = 가로 스크롤바 영역
  wheel(window, 100, { clientX: 400, clientY: 592 });

  assert(window.document.getElementById("heatmap-page-sizer").style.width === zoomBefore,
    "스크롤바 위 휠은 배율을 바꾸면 안 됨");
  assert(viewport.scrollTop === 400, `아래로 굴리면 아래로 이동해야 함 (실제 ${viewport.scrollTop})`);
});

test("스크롤바 위에서 Ctrl+휠은 좌우로 이동한다", async () => {
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
  }));
  await window.initHeatmapPage();
  const viewport = giveViewportScrollbars(window);
  const zoomBefore = window.document.getElementById("heatmap-page-sizer").style.width;

  const event = wheel(window, 100, { clientX: 792, clientY: 300, ctrlKey: true });

  assert(viewport.scrollLeft === 500, `Ctrl+휠 다운은 오른쪽으로 이동해야 함 (실제 ${viewport.scrollLeft})`);
  assert(viewport.scrollTop === 300, "가로 이동이 세로 스크롤을 건드리면 안 됨");
  assert(window.document.getElementById("heatmap-page-sizer").style.width === zoomBefore,
    "Ctrl+휠도 배율을 바꾸면 안 됨");
  assert(event.defaultPrevented, "브라우저 페이지 확대(Ctrl+휠 기본동작)를 막아야 함");

  wheel(window, -100, { clientX: 792, clientY: 300, ctrlKey: true });
  assert(viewport.scrollLeft === 400, `Ctrl+휠 업은 왼쪽으로 이동해야 함 (실제 ${viewport.scrollLeft})`);
});

test("스크롤바 이동은 0 아래로 내려가지 않는다", async () => {
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
  }));
  await window.initHeatmapPage();
  const viewport = giveViewportScrollbars(window);

  for (let i = 0; i < 10; i += 1) wheel(window, -100, { clientX: 792, clientY: 300 });
  assert(viewport.scrollTop === 0, `세로 이동 하한이 0 이어야 함 (실제 ${viewport.scrollTop})`);

  for (let i = 0; i < 10; i += 1) wheel(window, -100, { clientX: 792, clientY: 300, ctrlKey: true });
  assert(viewport.scrollLeft === 0, `가로 이동 하한이 0 이어야 함 (실제 ${viewport.scrollLeft})`);
});

test("스크롤바가 아닌 히트맵 본문 위에서는 그대로 확대·축소한다", async () => {
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
  }));
  await window.initHeatmapPage();
  const viewport = giveViewportScrollbars(window);

  wheel(window, -100, { clientX: 400, clientY: 300 });

  assert(Number.parseFloat(window.document.getElementById("heatmap-page-sizer").style.width) > 100,
    "본문 위 휠은 확대여야 함 (스크롤바 판정이 본문까지 삼키면 안 됨)");
  assert(viewport.scrollTop !== 200, "본문 확대는 이동이 아니라 커서 고정 재계산이어야 함");
});

test("휠 줌은 커서 아래 지점을 화면에 고정한다", async () => {
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
  }));
  await window.initHeatmapPage();

  // 커서는 viewport 좌상단에서 (300, 100) → 콘텐츠 기준 (800, 350) = 비율 (0.4, 0.35)
  const viewport = {
    scrollWidth: 2000, scrollHeight: 1000,
    clientWidth: 1000, clientHeight: 500,
    scrollLeft: 500, scrollTop: 250,
    getBoundingClientRect: () => ({ left: 0, top: 0 }),
  };
  const anchor = window._heatmapPagePointRatio(viewport, 300, 100);
  assert(Math.abs(anchor.x - 0.4) < 1e-9 && Math.abs(anchor.y - 0.35) < 1e-9,
    `커서 지점 비율이 어긋남 (실제 ${anchor.x}, ${anchor.y})`);

  // 2배로 커진 캔버스에서도 같은 지점이 커서 위치에 남아야 한다.
  viewport.scrollWidth = 4000;
  viewport.scrollHeight = 2000;
  window._restoreHeatmapPagePoint(viewport, anchor);
  assert(viewport.scrollLeft === 0.4 * 4000 - 300, `가로 스크롤이 커서 기준으로 복원돼야 함 (실제 ${viewport.scrollLeft})`);
  assert(viewport.scrollTop === 0.35 * 2000 - 100, `세로 스크롤이 커서 기준으로 복원돼야 함 (실제 ${viewport.scrollTop})`);
});

function mouse(target, type, clientX, clientY, options = {}) {
  const view = target.ownerDocument ? target.ownerDocument.defaultView : target.defaultView;
  const event = new view.MouseEvent(type, {
    clientX, clientY, button: 0, bubbles: true, cancelable: true, ...options,
  });
  target.dispatchEvent(event);
  return event;
}

test("히트맵을 마우스로 끌면 휴대폰처럼 상하좌우로 움직인다", async () => {
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
  }));
  await window.initHeatmapPage();
  const viewport = giveViewportScrollbars(window);  // scrollLeft 400 / scrollTop 300

  const down = mouse(viewport, "mousedown", 400, 300);
  assert(down.defaultPrevented, "끄는 동안 타일 라벨이 선택되지 않도록 기본 동작을 막아야 함");
  assert(viewport.classList.contains("heatmap-panning"), "끄는 중임을 커서로 알 수 있어야 함");

  // 왼쪽·위로 끌면 화면은 그 반대(오른쪽·아래)로 이동한다 — 종이를 밀듯이.
  mouse(window.document, "mousemove", 350, 250);
  assert(viewport.scrollLeft === 450, `왼쪽으로 끌면 오른쪽으로 이동해야 함 (실제 ${viewport.scrollLeft})`);
  assert(viewport.scrollTop === 350, `위로 끌면 아래로 이동해야 함 (실제 ${viewport.scrollTop})`);

  // 이동량은 누적이 아니라 시작점 기준이다(중간 이동을 두 번 세면 두 배로 튄다).
  mouse(window.document, "mousemove", 300, 200);
  assert(viewport.scrollLeft === 500 && viewport.scrollTop === 400,
    `이동량은 시작점 기준이어야 함 (실제 ${viewport.scrollLeft}, ${viewport.scrollTop})`);

  mouse(window.document, "mouseup", 300, 200);
  assert(!viewport.classList.contains("heatmap-panning"), "손을 떼면 끌기 상태가 풀려야 함");

  mouse(window.document, "mousemove", 100, 100);
  assert(viewport.scrollLeft === 500 && viewport.scrollTop === 400, "손을 뗀 뒤 커서 이동은 화면을 움직이면 안 됨");
});

test("끌기는 경계를 넘어가지 않는다", async () => {
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
  }));
  await window.initHeatmapPage();
  const viewport = giveViewportScrollbars(window);

  mouse(viewport, "mousedown", 400, 300);
  mouse(window.document, "mousemove", 5000, 5000);

  assert(viewport.scrollLeft === 0 && viewport.scrollTop === 0,
    `왼쪽 위 끝을 넘어가면 안 됨 (실제 ${viewport.scrollLeft}, ${viewport.scrollTop})`);
});

test("스크롤바를 잡은 드래그는 브라우저 기본 동작에 맡긴다", async () => {
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
  }));
  await window.initHeatmapPage();
  const viewport = giveViewportScrollbars(window);

  // clientWidth(785) 오른쪽 = 세로 스크롤바 영역
  const down = mouse(viewport, "mousedown", 792, 300);
  assert(!down.defaultPrevented, "스크롤바 드래그는 브라우저가 처리해야 함");
  assert(!viewport.classList.contains("heatmap-panning"), "스크롤바에서는 끌기 상태로 들어가면 안 됨");

  mouse(window.document, "mousemove", 792, 200);
  assert(viewport.scrollTop === 300, "스크롤바 드래그를 이중으로 이동시키면 안 됨");
});

test("오른쪽 버튼 드래그는 화면을 움직이지 않는다", async () => {
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
  }));
  await window.initHeatmapPage();
  const viewport = giveViewportScrollbars(window);

  mouse(viewport, "mousedown", 400, 300, { button: 2 });
  mouse(window.document, "mousemove", 300, 200);

  assert(viewport.scrollLeft === 400 && viewport.scrollTop === 300, "좌클릭 드래그만 이동이어야 함");
});

test("기간을 바꾸면 그 기간으로 다시 조회하고 캡션에 비교 구간을 밝힌다", async () => {
  const calls = [];
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success({
      trade_date: "20260807", period: "3m", base_date: "20260508", items: domesticSnapshot().items,
    }),
  }));
  const traced = window.fetchWithTimeout;
  window.fetchWithTimeout = (url, ...rest) => { calls.push(url); return traced(url, ...rest); };

  await window.initHeatmapPage();
  await window.setHeatmapPeriod("3m");

  const domesticCalls = calls.filter(url => url.startsWith("/api/heatmap/domestic"));
  assert(domesticCalls.length === 2, `기간 변경은 재조회를 유발해야 함 (실제 ${domesticCalls.length}회)`);
  assert(domesticCalls[domesticCalls.length - 1].includes("period=3m"),
    `조회 URL 에 기간이 실려야 함 (실제 ${domesticCalls[domesticCalls.length - 1]})`);

  const caption = window.document.getElementById("heatmap-page-domestic-caption").textContent;
  assert(caption.includes("3개월"), `캡션에 기간이 표시돼야 함 (실제 ${caption})`);
  assert(caption.includes("2026-05-08") && caption.includes("2026-08-07"),
    `캡션에 비교 구간이 표시돼야 함 (실제 ${caption})`);
  assert(window.document.querySelectorAll("#heatmap-page-domestic .heatmap-tile").length > 0,
    "기간 변경 후에도 타일이 그려져야 함");
});

test("올해(ytd)도 같은 경로로 조회하고 캡션에 연초부터의 구간을 밝힌다", async () => {
  const calls = [];
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success({
      trade_date: "20260807", period: "ytd", base_date: "20260102", items: domesticSnapshot().items,
    }),
  }));
  const traced = window.fetchWithTimeout;
  window.fetchWithTimeout = (url, ...rest) => { calls.push(url); return traced(url, ...rest); };

  await window.initHeatmapPage();
  await window.setHeatmapPeriod("ytd");

  const domesticCalls = calls.filter(url => url.startsWith("/api/heatmap/domestic"));
  assert(domesticCalls[domesticCalls.length - 1].includes("period=ytd"),
    `조회 URL 에 기간이 실려야 함 (실제 ${domesticCalls[domesticCalls.length - 1]})`);

  const caption = window.document.getElementById("heatmap-page-domestic-caption").textContent;
  assert(caption.includes("올해"), `캡션에 기간이 표시돼야 함 (실제 ${caption})`);
  assert(caption.includes("2026-01-02") && caption.includes("2026-08-07"),
    `캡션에 비교 구간이 표시돼야 함 (실제 ${caption})`);
});

test("시장을 고르면 그 시장만 다시 조회하고 기간 선택은 유지된다", async () => {
  const calls = [];
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success({
      trade_date: "20260807", period: "3m", base_date: "20260508", items: domesticSnapshot().items,
    }),
  }));
  const traced = window.fetchWithTimeout;
  window.fetchWithTimeout = (url, ...rest) => { calls.push(url); return traced(url, ...rest); };

  await window.initHeatmapPage();
  const first = calls.filter(url => url.startsWith("/api/heatmap/domestic")).pop();
  assert(first.includes("market=ALL"), `기본은 공통(ALL)이어야 함 (실제 ${first})`);

  await window.setHeatmapPeriod("3m");
  await window.setHeatmapMarket("KOSDAQ");

  const domesticCalls = calls.filter(url => url.startsWith("/api/heatmap/domestic"));
  assert(domesticCalls.length === 3, `시장 변경도 재조회를 유발해야 함 (실제 ${domesticCalls.length}회)`);
  const last = domesticCalls[domesticCalls.length - 1];
  assert(last.includes("market=KOSDAQ"), `조회 URL 에 시장이 실려야 함 (실제 ${last})`);
  assert(last.includes("period=3m"), `시장을 바꿔도 기간은 유지돼야 함 (실제 ${last})`);
  assert(window.document.querySelectorAll("#heatmap-page-domestic .heatmap-tile").length > 0,
    "시장 변경 후에도 타일이 그려져야 함");
});

test("같은 시장을 다시 고르면 재조회하지 않는다", async () => {
  const calls = [];
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
  }));
  const traced = window.fetchWithTimeout;
  window.fetchWithTimeout = (url, ...rest) => { calls.push(url); return traced(url, ...rest); };

  await window.initHeatmapPage();
  await window.setHeatmapMarket("ALL");
  await window.setHeatmapMarket("NASDAQ");  // 허용 목록 밖

  assert(calls.filter(url => url.startsWith("/api/heatmap/domestic")).length === 1,
    "같은 값·모르는 값은 재조회 대상이 아님");
});

test("미국 탭에서는 시장 선택도 감춘다 (S&P 500 고정)", async () => {
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic", "overseas_us"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
    "/api/overseas/top-market-cap": () => success({ items: [overseasItem({})], updated_at: 1767225600 }),
  }));

  await window.initHeatmapPage();
  const wrap = window.document.getElementById("heatmap-page-market-wrap");
  assert(wrap.style.display !== "none", "한국 탭에서는 시장 선택이 보여야 함");

  await window.setHeatmapTab("overseas");
  assert(wrap.style.display === "none", "미국 탭에서는 시장 선택을 감춰야 함");

  await window.setHeatmapTab("domestic");
  assert(wrap.style.display !== "none", "한국 탭으로 돌아오면 다시 보여야 함");
});

test("기간 비교 데이터가 없으면 캡션이 그 사실을 밝힌다", async () => {
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success({
      trade_date: "20260807", period: "1y", base_date: null,
      items: domesticSnapshot().items.map(item => ({ ...item, change_rate: null })),
    }),
  }));

  await window.initHeatmapPage();
  await window.setHeatmapPeriod("1y");

  const caption = window.document.getElementById("heatmap-page-domestic-caption").textContent;
  assert(caption.includes("1년") && caption.includes("없"), `비교 불가를 알려야 함 (실제 ${caption})`);
  assert(window.document.querySelector('#heatmap-page-domestic .heatmap-tile[data-direction="unknown"]'),
    "등락률을 못 낸 종목은 unknown 타일이어야 함");
});

test("기간을 바꿔도 보고 있던 배율은 유지된다", async () => {
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success({
      trade_date: "20260807", period: "1m", base_date: "20260707", items: domesticSnapshot().items,
    }),
  }));

  await window.initHeatmapPage();
  window.zoomHeatmapPage(2);
  const zoomed = window.document.getElementById("heatmap-page-sizer").style.width;

  await window.setHeatmapPeriod("1m");

  assert(window.document.getElementById("heatmap-page-sizer").style.width === zoomed,
    `기간 변경이 배율을 되돌리면 안 됨 (실제 ${window.document.getElementById("heatmap-page-sizer").style.width})`);
  assert(window.document.getElementById("heatmap-page-zoom-level").textContent === `${Math.round(Number.parseFloat(zoomed))}%`,
    "배율 표시도 유지돼야 함");
});

test("미국 탭에서는 기간 선택을 감춘다 (미국 스냅샷에 기간 데이터가 없음)", async () => {
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic", "overseas_us"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
    "/api/overseas/top-market-cap": () => success({ items: [overseasItem({})], updated_at: 1767225600 }),
  }));

  await window.initHeatmapPage();
  const wrap = window.document.getElementById("heatmap-page-period-wrap");
  assert(wrap.style.display !== "none", "한국 탭에서는 기간 선택이 보여야 함");

  await window.setHeatmapTab("overseas");
  assert(wrap.style.display === "none", "미국 탭에서는 기간 선택을 감춰야 함");

  await window.setHeatmapTab("domestic");
  assert(wrap.style.display !== "none", "한국 탭으로 돌아오면 기간 선택이 다시 보여야 함");
});

// 검색은 재조회가 아니라 이미 그린 타일을 짚어 주는 기능이다 — 면적(시가총액)이 유지돼야
// 히트맵으로서 읽히므로 일치하지 않는 타일을 지우지 않고 흐리게 둔다.
function hitSymbols(window) {
  return [...window.document.querySelectorAll("#heatmap-page-domestic .heatmap-tile.heatmap-tile-hit")]
    .map(tile => tile.dataset.symbol);
}

async function searchablePage() {
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success(domesticSnapshot()),
  }));
  await window.initHeatmapPage();
  return window;
}

test("종목명으로 검색하면 일치 타일만 강조하고 개수를 알려준다", async () => {
  const window = await searchablePage();
  const panel = window.document.getElementById("heatmap-page-domestic");

  window.searchHeatmapPage("대형1");

  assert(panel.classList.contains("heatmap-searching"), "검색 중에는 나머지를 흐리게 할 표시가 필요함");
  assert(JSON.stringify(hitSymbols(window)) === JSON.stringify(["B1"]),
    `이름이 일치하는 타일만 강조돼야 함 (실제 ${hitSymbols(window)})`);
  const count = window.document.getElementById("heatmap-page-search-count").textContent;
  assert(count.includes("1"), `일치 개수를 알려야 함 (실제 ${count})`);
  assert(window.document.querySelectorAll("#heatmap-page-domestic .heatmap-tile").length > 40,
    "일치하지 않는 타일도 그대로 남아야 함(면적이 히트맵의 정보다)");
});

test("코드로도 찾고 대소문자는 가리지 않는다", async () => {
  const window = await searchablePage();

  window.searchHeatmapPage("  b3 ");

  assert(JSON.stringify(hitSymbols(window)) === JSON.stringify(["B3"]),
    `코드로도 찾아야 함 (실제 ${hitSymbols(window)})`);
});

test("검색어를 비우면 강조가 풀린다", async () => {
  const window = await searchablePage();
  const panel = window.document.getElementById("heatmap-page-domestic");

  window.searchHeatmapPage("대형1");
  window.searchHeatmapPage("");

  assert(!panel.classList.contains("heatmap-searching"), "검색을 비우면 원래 화면으로 돌아와야 함");
  assert(hitSymbols(window).length === 0, "강조가 남으면 안 됨");
  assert(window.document.getElementById("heatmap-page-search-count").textContent === "",
    "안내 문구도 지워져야 함");
});

test("일치하는 종목이 없으면 그 사실을 알린다", async () => {
  const window = await searchablePage();

  window.searchHeatmapPage("없는종목");

  assert(hitSymbols(window).length === 0, "일치가 없어야 함");
  const count = window.document.getElementById("heatmap-page-search-count").textContent;
  assert(count.includes("없"), `일치 없음을 알려야 함 (실제 ${count})`);
});

test("확대하거나 다시 조회해도 검색 강조는 유지된다", async () => {
  const window = await searchablePage();

  window.searchHeatmapPage("대형1");
  window.zoomHeatmapPage(2);  // 재렌더 → 타일 DOM 이 통째로 바뀐다
  assert(JSON.stringify(hitSymbols(window)) === JSON.stringify(["B1"]),
    `확대 후에도 강조가 남아야 함 (실제 ${hitSymbols(window)})`);

  await window.setHeatmapMarket("KOSPI");
  assert(JSON.stringify(hitSymbols(window)) === JSON.stringify(["B1"]),
    `재조회 후에도 강조가 남아야 함 (실제 ${hitSymbols(window)})`);
});

test("pjax 재진입 시 기간·시장은 기본값으로 돌아온다", async () => {
  const calls = [];
  const window = makeWindow(routed({
    "/api/market-mode": () => marketMode(["domestic"]),
    "/api/heatmap/domestic": () => success({
      trade_date: "20260807", period: "3m", base_date: "20260508", items: domesticSnapshot().items,
    }),
  }));
  const traced = window.fetchWithTimeout;
  window.fetchWithTimeout = (url, ...rest) => { calls.push(url); return traced(url, ...rest); };

  await window.initHeatmapPage();
  await window.setHeatmapPeriod("3m");
  await window.setHeatmapMarket("KOSPI");
  calls.length = 0;
  await window.initHeatmapPage();

  const domesticCalls = calls.filter(url => url.startsWith("/api/heatmap/domestic"));
  assert(domesticCalls[0].includes("period=1d"), `재진입은 1일로 시작해야 함 (실제 ${domesticCalls[0]})`);
  assert(domesticCalls[0].includes("market=ALL"), `재진입은 공통으로 시작해야 함 (실제 ${domesticCalls[0]})`);
  assert(window.document.getElementById("heatmap-page-period").value === "1d",
    "기간 선택 UI 도 1일로 되돌아야 함");
  assert(window.document.getElementById("heatmap-page-market").value === "ALL",
    "시장 선택 UI 도 공통으로 되돌아야 함");
  assert(window.document.getElementById("heatmap-page-search").value === "",
    "검색어도 비워져야 함");
});

test("전용 페이지 경로에서는 로드 시 자동으로 초기화한다", async () => {
  const calls = [];
  makeWindow(routed({
    "/api/market-mode": () => { calls.push("/api/market-mode"); return marketMode(["domestic"]); },
    "/api/heatmap/domestic": () => { calls.push("/api/heatmap/domestic"); return success(domesticSnapshot()); },
  }), "http://localhost/heatmap");

  await new Promise(done => setTimeout(done, 50));

  assert(calls.includes("/api/market-mode"), "진입 시 시장 활성 여부를 확인해야 함");
  assert(calls.includes("/api/heatmap/domestic"), "진입 시 한국 히트맵을 조회해야 함");
});

await run();
