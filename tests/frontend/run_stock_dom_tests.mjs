/*
 * jsdom 기반 /stock 화면 회귀 테스트.
 *
 * 목적: 해외(overseas) 기능이 기존 국내 경로를 깨는 런타임 DOM 회귀를
 * "함수 이름 문자열 존재" 수준이 아니라 실제 DOM 행위로 잡는다.
 *
 * 대표 회귀: 국내 조회 후 차트 카드(#stock-chart-card)는 #stock-result 안으로
 * 이동된다. 모드 토글/해외 조회가 #stock-result 를 비울 때 차트 카드를 먼저
 * 대피시키지 않으면 카드가 영구 파괴되어 이후 국내 차트가 깨진다.
 *
 * 실행: node run_stock_dom_tests.mjs  (exit 0 = 전부 통과)
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";

// 기본은 실제 stock.js. STOCK_JS_PATH 로 다른 사본(예: 회귀 검증용 buggy 사본)을 지정 가능.
const STOCK_JS = process.env.STOCK_JS_PATH
  ? resolve(process.env.STOCK_JS_PATH)
  : resolve(import.meta.dirname, "../../view/web/static/js/stock.js");

// stock.html 의 #section-stock 구조를 회귀 검증에 필요한 만큼만 재현.
const SCAFFOLD = `
<div id="section-stock" class="section">
  <div class="card">
    <button id="stock-mode-domestic" class="exchange-btn active">국내</button>
    <button id="stock-mode-overseas" class="exchange-btn">해외</button>
    <div id="domestic-stock-row">
      <input id="stock-code-input" type="text">
      <ul id="stock-autocomplete-list"></ul>
    </div>
    <div id="overseas-stock-row" style="display:none;">
      <input id="overseas-stock-symbol" type="text">
      <select id="overseas-stock-exchange"><option value="NASD">NASDAQ</option></select>
    </div>
  </div>
  <div id="stock-result"></div>
  <div class="card" id="stock-chart-card" style="display:none;">
    <div id="chart-controls-area"></div>
    <canvas id="stockChart"></canvas>
  </div>
</div>
`;

function makeWindow() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>${SCAFFOLD}</body></html>`, {
    url: "http://localhost/stock",
    runScripts: "dangerously",
  });
  const { window } = dom;
  // stock.js 가 로드 시점에 호출/참조하는 전역 스텁
  window.StockAutocomplete = () => {};
  window._overseasStockSearch = () => [];
  window.showLoading = (el, msg) => { if (el) el.innerHTML = `<p class="loading">${msg}</p>`; };
  window.fetchWithTimeout = async () => ({ ok: true, json: async () => ({}) });
  window.fetch = async () => ({ json: async () => ({ stocks: [] }) });
  window.loadAndRenderStockChart = () => {};
  window.loadAndRenderOverseasStockChart = () => {};
  window.formatTradingValue = () => "";
  window.ALL_STOCKS = [];
  window._matchMixed = () => false;

  const script = window.document.createElement("script");
  script.textContent = readFileSync(STOCK_JS, "utf8");
  window.document.body.appendChild(script);
  return window;
}

/* 국내 조회 직후 상태 재현: 차트 카드를 #stock-result 안(#chart-placeholder)으로 이동 */
function simulateDomesticSearchRendered(window) {
  const result = window.document.getElementById("stock-result");
  result.innerHTML = '<div class="card stock-info-box"><div id="chart-placeholder"></div></div>';
  const placeholder = window.document.getElementById("chart-placeholder");
  placeholder.appendChild(window.document.getElementById("stock-chart-card"));
}

const tests = [];
const test = (name, fn) => tests.push({ name, fn });
function assert(cond, msg) { if (!cond) throw new Error(msg); }

test("setStockMarketMode('overseas') 가 stock-result 안의 차트 카드를 파괴하지 않는다", async () => {
  const window = makeWindow();
  simulateDomesticSearchRendered(window);
  assert(window.document.getElementById("stock-chart-card"), "사전조건: 차트 카드 존재");

  window.setStockMarketMode("overseas");

  assert(window.document.getElementById("stock-chart-card"),
    "회귀: 국내→해외 전환 시 차트 카드가 DOM에서 사라짐");
  const card = window.document.getElementById("stock-chart-card");
  assert(card.parentElement.id === "section-stock", "차트 카드는 section-stock 으로 대피되어야 함");
  assert(window.document.getElementById("stock-result").innerHTML === "", "결과 영역은 비워져야 함");
  assert(window.document.getElementById("overseas-stock-row").style.display !== "none", "해외 입력행이 보여야 함");
  assert(window.document.getElementById("domestic-stock-row").style.display === "none", "국내 입력행은 숨겨져야 함");
});

test("국내→해외→국내 왕복 후에도 차트 카드와 캔버스가 보존된다", async () => {
  const window = makeWindow();
  simulateDomesticSearchRendered(window);
  window.setStockMarketMode("overseas");
  window.setStockMarketMode("domestic");
  assert(window.document.getElementById("stock-chart-card"), "왕복 후 차트 카드 소실");
  assert(window.document.getElementById("stockChart"), "왕복 후 차트 캔버스 소실");
});

test("searchOverseasStock 가 차트 카드를 보존하고 최소 view model 을 렌더한다", async () => {
  const window = makeWindow();
  simulateDomesticSearchRendered(window);
  let chartCalledWith = null;
  window.loadAndRenderOverseasStockChart = (symbol, exchange) => {
    chartCalledWith = { symbol, exchange };
  };
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["domestic", "overseas_us"] }) };
    }
    if (url.includes("/api/overseas/stock/")) {
      return { ok: true, json: async () => ({
        rt_cd: "0",
        data: { symbol: "AAPL", exchange: "NASD", currency: "USD", price: 190.5, change_rate: 1.23, volume: 1000, timestamp: "20260614" },
      }) };
    }
    return { ok: false, json: async () => ({}) };
  };
  window.document.getElementById("overseas-stock-symbol").value = "aapl";

  await window.searchOverseasStock();

  assert(window.document.getElementById("stock-chart-card"), "회귀: 해외 조회가 차트 카드를 파괴함");
  const html = window.document.getElementById("stock-result").innerHTML;
  assert(html.includes("AAPL"), "심볼 표시 누락");
  assert(html.includes("NASDAQ"), "거래소 표시명 누락");
  assert(html.includes("$190.50"), "가격(USD) 표시 누락");
  assert(chartCalledWith && chartCalledWith.symbol === "AAPL" && chartCalledWith.exchange === "NASD",
    "해외 조회 성공 후 해외 차트 로더가 호출되어야 함");
  // 해외 렌더는 국내 전용 SSE 타깃 id 를 만들면 안 된다(틱 핸들러 오작동 방지)
  assert(!html.includes('id="rt-price"'), "해외 렌더가 국내 전용 rt-price id 를 생성함");
});

test("overseas_us 미enabled 시 searchOverseasStock 가 fail-close 하고 시세 API 를 호출하지 않는다", async () => {
  const window = makeWindow();
  let priceCalled = false;
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["domestic"] }) };
    }
    if (url.includes("/api/overseas/stock/")) {
      priceCalled = true;
      return { ok: true, json: async () => ({ rt_cd: "0", data: {} }) };
    }
    return { ok: false, json: async () => ({}) };
  };
  window.document.getElementById("overseas-stock-symbol").value = "AAPL";

  await window.searchOverseasStock();

  assert(priceCalled === false, "fail-close 인데 해외 시세 API 가 호출됨");
  const html = window.document.getElementById("stock-result").innerHTML;
  assert(html.includes("overseas_us"), "fail-close 메시지 누락");
});

let failed = 0;
for (const { name, fn } of tests) {
  try {
    await fn();
    console.log(`PASS  ${name}`);
  } catch (e) {
    failed += 1;
    console.error(`FAIL  ${name}\n      ${e.message}`);
  }
}
console.log(`\n${tests.length - failed}/${tests.length} passed`);
process.exit(failed === 0 ? 0 : 1);
