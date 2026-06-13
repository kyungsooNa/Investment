/*
 * jsdom 기반 /overseas 화면 JS 회귀 테스트.
 *
 * 대표 회귀: get_overseas_dailyprice 는 KIS 원본 body(dict: output1/output2)를
 * 그대로 반환하므로 json.data 는 배열이 아니다. loadOverseasChart 가 이를
 * 배열로 가정하면 일봉 테이블이 항상 빈다. 또한 output2 행 키는 KIS 해외
 * 스키마(xymd/clos/tvol)라 국내 키 매핑으로는 종가/거래량이 표시되지 않는다.
 *
 * 실행: node run_overseas_dom_tests.mjs  (exit 0 = 전부 통과)
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";

const OVERSEAS_JS = process.env.OVERSEAS_JS_PATH
  ? resolve(process.env.OVERSEAS_JS_PATH)
  : resolve(import.meta.dirname, "../../view/web/static/js/overseas.js");

const SCAFFOLD = `
<span id="status-env" data-mode="paper"></span>
<input id="overseas-symbol" type="text">
<select id="overseas-exchange"><option value="NASD">NASDAQ</option></select>
<div id="overseas-quote-result"></div>
<div id="overseas-chart-result"></div>
<div id="overseas-balance-result"></div>
`;

function makeWindow() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>${SCAFFOLD}</body></html>`, {
    url: "http://localhost/overseas",
    runScripts: "dangerously",
  });
  const { window } = dom;
  window.showLoading = (el, msg) => { if (el) el.innerHTML = `<p class="loading">${msg}</p>`; };
  window.fetchWithTimeout = async () => ({ ok: true, json: async () => ({}) });
  window.alert = () => {};

  const script = window.document.createElement("script");
  script.textContent = readFileSync(OVERSEAS_JS, "utf8");
  window.document.body.appendChild(script);
  return window;
}

const tests = [];
const test = (name, fn) => tests.push({ name, fn });
function assert(cond, msg) { if (!cond) throw new Error(msg); }

test("loadOverseasChart 가 KIS body(dict의 output2)에서 일봉 행을 렌더한다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["domestic", "overseas_us"] }) };
    }
    if (url.includes("/api/overseas/chart/")) {
      return { ok: true, json: async () => ({
        rt_cd: "0",
        // KIS 해외 일봉 원본 형태: 전체 body dict(output1 + output2 배열)
        data: {
          output1: { rsym: "DNASAAPL" },
          output2: [
            { xymd: "20260101", clos: "190.50", tvol: "1000" },
            { xymd: "20260102", clos: "192.00", tvol: "2000" },
          ],
        },
      }) };
    }
    return { ok: false, json: async () => ({}) };
  };
  window.document.getElementById("overseas-symbol").value = "AAPL";

  await window.loadOverseasChart();

  const html = window.document.getElementById("overseas-chart-result").innerHTML;
  assert(html.includes("20260102"), "회귀: 일봉 일자 행이 렌더되지 않음(output2 미추출)");
  assert(html.includes("$192.00"), "회귀: 종가(clos) 매핑 실패");
  assert(html.includes("2,000"), "회귀: 거래량(tvol) 매핑 실패");
  assert(!html.includes("데이터 없음"), "회귀: 데이터 있는데 빈 테이블 표시");
});

test("loadOverseasChart 가 이미 배열인 data 도 처리한다(하위호환)", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    if (url.includes("/api/overseas/chart/")) {
      return { ok: true, json: async () => ({
        rt_cd: "0",
        data: [{ xymd: "20260103", clos: "195.00", tvol: "3000" }],
      }) };
    }
    return { ok: false, json: async () => ({}) };
  };
  window.document.getElementById("overseas-symbol").value = "AAPL";

  await window.loadOverseasChart();

  const html = window.document.getElementById("overseas-chart-result").innerHTML;
  assert(html.includes("20260103"), "배열 형태 data 미처리");
  assert(html.includes("$195.00"), "배열 형태 종가 미표시");
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
