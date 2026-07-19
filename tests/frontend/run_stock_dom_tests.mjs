/*
 * jsdom 기반 /stock 화면 회귀 테스트.
 *
 * 목적: /stock 화면이 국내 전용 조회 경로를 유지하고 차트 렌더링이
 * 정상 동작하는지 실제 DOM 행위로 검증한다.
 *
 * 실행: node run_stock_dom_tests.mjs  (exit 0 = 전부 통과)
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { test, assert, run } from "./harness.mjs";

// 기본은 실제 stock.js. STOCK_JS_PATH 로 다른 사본(예: 회귀 검증용 buggy 사본)을 지정 가능.
const STOCK_JS = process.env.STOCK_JS_PATH
  ? resolve(process.env.STOCK_JS_PATH)
  : resolve(import.meta.dirname, "../../view/web/static/js/stock.js");

// stock.html 의 #section-stock 구조를 회귀 검증에 필요한 만큼만 재현.
const SCAFFOLD = `
<div id="section-stock" class="section">
  <div class="card">
    <div id="domestic-stock-row">
      <input id="stock-code-input" type="text">
      <ul id="stock-autocomplete-list"></ul>
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
  window.showLoading = (el, msg) => { if (el) el.innerHTML = `<p class="loading">${msg}</p>`; };
  window.fetchWithTimeout = async () => ({ ok: true, json: async () => ({}) });
  window.fetch = async () => ({ json: async () => ({ stocks: [] }) });
  window.loadAndRenderStockChart = () => {};
  window.formatTradingValue = () => "";
  window.ALL_STOCKS = [];
  window._matchMixed = () => false;

  const script = window.document.createElement("script");
  script.textContent = readFileSync(STOCK_JS, "utf8");
  window.document.body.appendChild(script);
  return window;
}

test("stock.js는 해외 조회 진입점을 노출하지 않는다", async () => {
  const window = makeWindow();
  assert(typeof window.setStockMarketMode === "undefined", "국내/해외 모드 토글이 남아 있음");
  assert(typeof window.searchOverseasStock === "undefined", "해외 조회 함수가 남아 있음");
});

test("국내 조회에 필요한 입력과 차트 DOM이 존재한다", async () => {
  const window = makeWindow();
  assert(window.document.getElementById("stock-code-input"), "국내 종목 입력 누락");
  assert(window.document.getElementById("stock-chart-card"), "차트 카드 누락");
  assert(window.document.getElementById("stockChart"), "차트 캔버스 누락");
});

test("현재가 조회 결과에 KOSPI/KOSDAQ 시장 배지를 표시한다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.startsWith("/api/stock/123456?")) {
      return {
        ok: true,
        json: async () => ({
          rt_cd: "0",
          data: {
            code: "123456", name: "테스트종목", market: "KOSDAQ",
            price: "10000", change: "0", rate: "0", sign: "",
            open: "10000", high: "10100", low: "9900", prev_close: "10000",
          },
        }),
      };
    }
    return { ok: true, json: async () => ({ rt_cd: "1", data: null }) };
  };

  await window.searchStock("123456");

  const badge = window.document.querySelector(".stock-market-badge");
  assert(badge, "시장 구분 배지가 없음");
  assert(badge.textContent === "KOSDAQ", "시장 구분 배지 값이 KOSDAQ이 아님");
  assert(window.document.getElementById("ai-stock-analysis-btn"), "AI 분석 버튼이 없음");
  assert(window.document.getElementById("ai-stock-analysis-output"), "AI 분석 결과 영역이 없음");
});

test("AI 종목 분석은 POST로 요청하고 결과를 텍스트로 안전하게 표시한다", async () => {
  const window = makeWindow();
  window.document.getElementById("stock-result").innerHTML = `
    <button id="ai-stock-analysis-btn"></button>
    <span id="ai-stock-analysis-status"></span>
    <div id="ai-stock-analysis-sources"></div>
    <div id="ai-stock-analysis-output"></div>
  `;
  let requested = null;
  window.fetchWithTimeout = async (url, options, timeout) => {
    requested = { url, options, timeout };
    return {
      ok: true,
      json: async () => ({
        rt_cd: "0",
        data: {
          analysis: "<img src=x onerror=alert(1)>상승 추세",
          generated_at: "2026-07-19T12:00:00+09:00",
          sources: {
            current: true,
            financial: true,
            stage: true,
            rs_rating: true,
            investor_flow: false,
            disclosures: false,
          },
        },
      }),
    };
  };

  await window.requestAiStockAnalysis("005930");

  assert(requested.url === "/api/stock/005930/ai-analysis", "AI 분석 URL이 잘못됨");
  assert(requested.options.method === "POST", "AI 분석이 POST 요청이 아님");
  assert(requested.timeout === 45000, "AI 분석 타임아웃이 45초가 아님");
  const output = window.document.getElementById("ai-stock-analysis-output");
  assert(output.textContent.includes("<img src=x"), "AI 결과가 텍스트로 보존되지 않음");
  assert(!output.querySelector("img"), "AI 결과가 HTML로 실행됨");
  assert(
    window.document.getElementById("ai-stock-analysis-sources").textContent.includes("현재가"),
    "사용 데이터 출처가 표시되지 않음",
  );
});

await run();
