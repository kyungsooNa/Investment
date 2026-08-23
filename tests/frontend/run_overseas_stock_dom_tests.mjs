/*
 * jsdom 기반 /overseas-stock (미국장 현재가) 화면 JS 회귀 테스트.
 *
 * 대표 회귀: get_overseas_price 응답은 정규화된 요약(price/change_rate/volume) 위에
 * KIS 원본을 raw 로 얹어 내려준다. 전일종가(base)·전일대비(diff)·거래대금(tamt) 은
 * raw 안에만 있어서 최상위 키만 읽으면 화면이 비고, 심볼 미입력/기능 비활성 시에는
 * 브로커를 때리지 않아야 한다.
 *
 * 실행: node run_overseas_stock_dom_tests.mjs  (exit 0 = 전부 통과)
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { applyCommonStubs, test, assert, run } from "./harness.mjs";

const OVERSEAS_STOCK_JS = resolve(
  import.meta.dirname,
  "../../view/web/static/js/overseas_stock.js",
);

const SCAFFOLD = `
<span id="status-env" data-mode="paper"></span>
<input id="overseas-stock-symbol" type="text">
<ul id="overseas-stock-autocomplete-list"></ul>
<select id="overseas-stock-exchange"><option value="NASD">NASDAQ</option><option value="NYSE">NYSE</option></select>
<div id="overseas-stock-result"></div>
<div id="stock-chart-card" style="display:none;">
  <div id="chart-controls-area"></div>
  <canvas id="stockChart"></canvas>
</div>
`;

function makeWindow() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>${SCAFFOLD}</body></html>`, {
    url: "http://localhost/overseas-stock",
    runScripts: "dangerously",
  });
  const { window } = dom;
  applyCommonStubs(window);
  window.fetchWithTimeout = async () => ({ ok: true, json: async () => ({}) });
  window.alert = () => {};
  window.showToast = () => {};
  // 차트는 candle_chart.js 소유라 호출 여부만 본다.
  window.__chartCalls = [];
  window.loadAndRenderOverseasStockChart = async (symbol, exchange) => {
    window.__chartCalls.push([symbol, exchange]);
  };

  const script = window.document.createElement("script");
  script.textContent = readFileSync(OVERSEAS_STOCK_JS, "utf8");
  window.document.body.appendChild(script);
  return window;
}

function quoteResponse(overrides = {}) {
  return {
    rt_cd: "0",
    data: {
      symbol: "AAPL",
      exchange: "NASD",
      currency: "USD",
      price: 192.5,
      change_rate: 1.25,
      volume: 51234567,
      timestamp: "20260220",
      raw: { base: "190.12", diff: "2.38", sign: "2", tvol: "51234567", tamt: "9876543210", pvol: "48000000" },
      ...overrides,
    },
  };
}

test("searchOverseasStock 이 현재가 요약과 raw(전일종가/대비/거래대금)를 렌더한다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["domestic", "overseas_us"] }) };
    }
    if (url.includes("/api/overseas/stock/")) {
      return { ok: true, json: async () => quoteResponse() };
    }
    return { ok: false, json: async () => ({}) };
  };
  window.document.getElementById("overseas-stock-symbol").value = "aapl";

  await window.searchOverseasStock();

  const html = window.document.getElementById("overseas-stock-result").innerHTML;
  assert(html.includes("AAPL"), "심볼이 대문자로 표시되지 않음");
  assert(html.includes("$192.50"), "현재가 표시 실패");
  assert(html.includes("+1.25%"), "등락률 표시 실패");
  assert(html.includes("$190.12"), "회귀: 전일종가(raw.base) 미표시");
  assert(html.includes("2.38"), "회귀: 전일대비(raw.diff) 미표시");
  assert(html.includes("51,234,567"), "거래량 표시 실패");
  assert(html.includes("9,876,543,210"), "회귀: 거래대금(raw.tamt) 미표시");
});

test("조회 성공 시 같은 심볼/거래소로 일봉 차트를 그린다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    if (url.includes("/api/overseas/stock/")) {
      return { ok: true, json: async () => quoteResponse() };
    }
    return { ok: false, json: async () => ({}) };
  };
  window.document.getElementById("overseas-stock-symbol").value = "AAPL";
  window.document.getElementById("overseas-stock-exchange").value = "NYSE";

  await window.searchOverseasStock();

  assert(window.__chartCalls.length === 1, "일봉 차트 렌더가 호출되지 않음");
  assert(window.__chartCalls[0][0] === "AAPL", "차트에 넘긴 심볼이 다름");
  assert(window.__chartCalls[0][1] === "NYSE", "회귀: 선택한 거래소가 차트에 전달되지 않음");
});

test("심볼이 비어 있으면 브로커를 호출하지 않는다", async () => {
  const window = makeWindow();
  const calls = [];
  window.fetchWithTimeout = async (url) => {
    calls.push(url);
    return { ok: true, json: async () => ({}) };
  };
  window.document.getElementById("overseas-stock-symbol").value = "   ";

  await window.searchOverseasStock();

  assert(calls.length === 0, `심볼 없이 API 를 호출함: ${calls.join(", ")}`);
});

test("overseas_us 가 비활성이면 안내만 하고 현재가를 조회하지 않는다", async () => {
  const window = makeWindow();
  const calls = [];
  window.fetchWithTimeout = async (url) => {
    calls.push(url);
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["domestic"] }) };
    }
    return { ok: true, json: async () => quoteResponse() };
  };
  window.document.getElementById("overseas-stock-symbol").value = "AAPL";

  await window.searchOverseasStock();

  assert(!calls.some((url) => url.includes("/api/overseas/stock/")),
    "비활성 상태에서 현재가 API 를 호출함");
  const html = window.document.getElementById("overseas-stock-result").innerHTML;
  assert(html.includes("overseas_us"), "비활성 안내 문구가 없음");
});

test("조회 실패 응답은 에러 문구로 보여주고 차트를 그리지 않는다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    return { ok: true, json: async () => ({ rt_cd: "1", msg1: "조회할 수 없는 종목입니다.", data: null }) };
  };
  window.document.getElementById("overseas-stock-symbol").value = "ZZZZ";

  await window.searchOverseasStock();

  const html = window.document.getElementById("overseas-stock-result").innerHTML;
  assert(html.includes("조회할 수 없는 종목입니다."), "실패 메시지가 표시되지 않음");
  assert(window.__chartCalls.length === 0, "조회 실패인데 차트를 그림");
});

await run();
