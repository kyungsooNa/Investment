/*
 * jsdom 기반 종목 자동완성(autocomplete.js + stock.js) 회귀 테스트.
 *
 * 목적:
 *  1) autocomplete.js 일반화(valueKey/searchImpl/formatItem/item 전달)가 기존
 *     국내 자동완성 동작을 깨지 않는지 행위로 검증.
 *  2) 해외 심볼 자동완성: 심볼 prefix / 영문명 부분일치 매칭, 선택 시 거래소
 *     드롭다운 자동설정 + 조회 트리거를 검증.
 *
 * 실행: node run_autocomplete_dom_tests.mjs  (exit 0 = 전부 통과)
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";

const AUTOCOMPLETE_JS = resolve(import.meta.dirname, "../../view/web/static/js/autocomplete.js");
const STOCK_JS = resolve(import.meta.dirname, "../../view/web/static/js/stock.js");

const SCAFFOLD = `
<div id="section-stock" class="section">
  <div class="card">
    <button id="stock-mode-domestic" class="exchange-btn active">국내</button>
    <button id="stock-mode-overseas" class="exchange-btn">해외</button>
    <div id="domestic-stock-row" style="position:relative;">
      <input id="stock-code-input" type="text">
      <ul id="stock-autocomplete-list"></ul>
    </div>
    <div id="overseas-stock-row" style="display:none; position:relative;">
      <input id="overseas-stock-symbol" type="text">
      <select id="overseas-stock-exchange">
        <option value="NASD">NASDAQ</option>
        <option value="NYSE">NYSE</option>
        <option value="AMEX">AMEX</option>
      </select>
      <ul id="overseas-autocomplete-list"></ul>
    </div>
  </div>
  <div id="stock-result"></div>
  <div class="card" id="stock-chart-card" style="display:none;">
    <div id="chart-controls-area"></div>
    <canvas id="stockChart"></canvas>
  </div>
</div>
`;

async function makeWindow() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>${SCAFFOLD}</body></html>`, {
    url: "http://localhost/stock",
    runScripts: "dangerously",
  });
  const { window } = dom;
  // stock.js 가 로드/실행 시점에 참조하는 전역 스텁
  window.showLoading = (el, msg) => { if (el) el.innerHTML = `<p class="loading">${msg}</p>`; };
  window.fetchWithTimeout = async () => ({ ok: true, json: async () => ({}) });
  window.fetch = async () => ({ json: async () => ({ stocks: [] }) });
  window.loadAndRenderStockChart = () => {};
  window.loadAndRenderOverseasStockChart = () => {};
  window.formatTradingValue = () => "";
  window.alert = () => {};
  window.ALL_STOCKS = [];

  const a = window.document.createElement("script");
  a.textContent = readFileSync(AUTOCOMPLETE_JS, "utf8");
  window.document.body.appendChild(a);

  const s = window.document.createElement("script");
  s.textContent = readFileSync(STOCK_JS, "utf8");
  window.document.body.appendChild(s);

  // JSDOM 은 DOMContentLoaded 를 비동기로 발사하므로, 자동완성 init(_initDom)이
  // 실행될 때까지 한 틱 대기한다.
  await new Promise((r) => setTimeout(r, 0));
  return window;
}

function typeInto(window, id, value) {
  const el = window.document.getElementById(id);
  el.value = value;
  el.dispatchEvent(new window.Event("input", { bubbles: true }));
}

const tests = [];
const test = (name, fn) => tests.push({ name, fn });
function assert(cond, msg) { if (!cond) throw new Error(msg); }

test("국내 자동완성: 종목명 입력 시 드롭다운 렌더 + 선택 시 searchStock(code) 호출", async () => {
  const window = await makeWindow();
  window.document.dispatchEvent(new window.CustomEvent("all-stocks-ready", {
    detail: [{ c: "005930", n: "삼성전자" }, { c: "035720", n: "카카오" }],
  }));
  let called = null;
  window.searchStock = (code) => { called = code; };

  typeInto(window, "stock-code-input", "삼성");
  const list = window.document.getElementById("stock-autocomplete-list");
  assert(list.style.display === "block", "회귀: 국내 드롭다운이 표시되지 않음");
  assert(list.querySelectorAll("li").length === 1, "회귀: 국내 검색 결과 수 불일치");
  assert(list.querySelector("li").textContent === "삼성전자 (005930)", "회귀: 국내 표시 포맷 변경됨");

  list.querySelector("li").click();
  assert(called === "005930", "회귀: 국내 선택 시 searchStock(code) 미호출");
});

test("국내 자동완성: 숫자 입력 시 종목코드 prefix 매칭", async () => {
  const window = await makeWindow();
  window.document.dispatchEvent(new window.CustomEvent("all-stocks-ready", {
    detail: [{ c: "005930", n: "삼성전자" }, { c: "035720", n: "카카오" }],
  }));
  typeInto(window, "stock-code-input", "0357");
  const list = window.document.getElementById("stock-autocomplete-list");
  assert(list.querySelectorAll("li").length === 1, "숫자 prefix 매칭 결과 수 불일치");
  assert(list.querySelector("li").textContent.includes("카카오"), "숫자 prefix → 코드 매칭 실패");
});

test("해외 자동완성: 심볼 prefix 입력 시 드롭다운 렌더", async () => {
  const window = await makeWindow();
  window.document.dispatchEvent(new window.CustomEvent("all-overseas-stocks-ready", {
    detail: [
      { s: "AAPL", n: "Apple Inc", e: "NASD" },
      { s: "LLY", n: "Eli Lilly and Co", e: "NYSE" },
    ],
  }));
  typeInto(window, "overseas-stock-symbol", "AAP");
  const list = window.document.getElementById("overseas-autocomplete-list");
  assert(list.style.display === "block", "해외 드롭다운이 표시되지 않음");
  assert(list.querySelectorAll("li").length === 1, "해외 심볼 prefix 결과 수 불일치");
  assert(list.querySelector("li").textContent === "AAPL — Apple Inc (NASD)", "해외 표시 포맷 불일치");
});

test("해외 자동완성: 영문명 부분일치(대소문자 무시) 매칭", async () => {
  const window = await makeWindow();
  window.document.dispatchEvent(new window.CustomEvent("all-overseas-stocks-ready", {
    detail: [
      { s: "AAPL", n: "Apple Inc", e: "NASD" },
      { s: "LLY", n: "Eli Lilly and Co", e: "NYSE" },
    ],
  }));
  typeInto(window, "overseas-stock-symbol", "lilly");
  const list = window.document.getElementById("overseas-autocomplete-list");
  assert(list.querySelectorAll("li").length === 1, "영문명 부분일치 결과 수 불일치");
  assert(list.querySelector("li").textContent.startsWith("LLY"), "영문명 매칭 실패");
});

test("해외 자동완성: 선택 시 거래소 드롭다운 자동설정 + searchOverseasStock 호출", async () => {
  const window = await makeWindow();
  window.document.dispatchEvent(new window.CustomEvent("all-overseas-stocks-ready", {
    detail: [{ s: "LLY", n: "Eli Lilly and Co", e: "NYSE" }],
  }));
  let searched = false;
  window.searchOverseasStock = () => { searched = true; };

  typeInto(window, "overseas-stock-symbol", "LLY");
  window.document.getElementById("overseas-autocomplete-list").querySelector("li").click();

  assert(window.document.getElementById("overseas-stock-symbol").value === "LLY", "심볼 input 미설정");
  assert(window.document.getElementById("overseas-stock-exchange").value === "NYSE",
    "회귀: 선택한 심볼의 거래소로 드롭다운이 자동설정되지 않음");
  assert(searched === true, "선택 후 searchOverseasStock 미호출");
});

test("해외 자동완성: 미선택 Enter 시 onConfirm(searchOverseasStock) 호출", async () => {
  const window = await makeWindow();
  let searched = false;
  window.searchOverseasStock = () => { searched = true; };
  const input = window.document.getElementById("overseas-stock-symbol");
  input.value = "TSLA";
  input.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter" }));
  assert(searched === true, "미선택 Enter 시 직접 입력 조회가 트리거되지 않음");
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
