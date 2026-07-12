/*
 * jsdom 기반 시가총액 랭킹 화면 회귀 테스트.
 *
 * 실행: node run_marketcap_dom_tests.mjs
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { applyCommonStubs, test, assert, run } from "./harness.mjs";

const MARKETCAP_JS = process.env.MARKETCAP_JS_PATH
  ? resolve(process.env.MARKETCAP_JS_PATH)
  : resolve(import.meta.dirname, "../../view/web/static/js/marketcap.js");

const SCAFFOLD = `
<div id="section-marketcap">
  <button class="ranking-tab active" data-market="0001">코스피</button>
  <button class="ranking-tab" data-market="1001">코스닥</button>
</div>
<div id="marketcap-result"></div>
`;

function makeWindow(fetchWithTimeout) {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>${SCAFFOLD}</body></html>`, {
    url: "http://localhost/marketcap",
    runScripts: "outside-only",
  });
  const { window } = dom;
  applyCommonStubs(window);
  window.fetchWithTimeout = fetchWithTimeout;
  window.eval(readFileSync(MARKETCAP_JS, "utf8"));
  return window;
}

function success(data) {
  return { ok: true, json: async () => ({ rt_cd: "0", data }) };
}

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}


test("늦게 끝난 이전 탭 요청이 최신 랭킹을 덮어쓰지 않는다", async () => {
  const pending = [];
  const window = makeWindow(() => {
    const request = deferred();
    pending.push(request);
    return request.promise;
  });

  const kospi = window.loadTopMarketCap("0001");
  const kosdaq = window.loadTopMarketCap("1001");
  pending[1].resolve(success([{ rank: "1", name: "코스닥", code: "KOSDAQ", current_price: "2000", change_rate: "1.5", market_cap: "20" }]));
  await kosdaq;
  pending[0].resolve(success([{ rank: "1", name: "코스피", code: "KOSPI", current_price: "1000", change_rate: "1.0", market_cap: "10" }]));
  await kospi;

  const result = window.document.getElementById("marketcap-result");
  assert(result.textContent.includes("코스닥"), "최신 코스닥 결과가 표시되어야 함");
  assert(!result.textContent.includes("코스피"), "이전 코스피 결과가 최신 결과를 덮어씀");
});

test("브로커 문자열을 텍스트와 URL로 안전하게 렌더링한다", async () => {
  const window = makeWindow(async () => success([{
    rank: "1",
    name: '<img id="injected-name" src=x>',
    code: '005930"><img id="injected-code" src=x>',
    current_price: "70000",
    change_rate: "1.2",
    market_cap: "100",
  }]));

  await window.loadTopMarketCap();

  const result = window.document.getElementById("marketcap-result");
  assert(!result.querySelector("#injected-name, #injected-code"), "브로커 응답이 HTML로 해석됨");
  assert(result.textContent.includes('<img id="injected-name" src=x>'), "종목명이 텍스트로 표시되어야 함");
  const link = result.querySelector(".stock-link");
  assert(link.getAttribute("href").includes("%22%3E%3Cimg"), "종목코드는 URL 인코딩되어야 함");
});

test("HTTP 오류는 JSON 파싱 없이 상태 안내를 표시한다", async () => {
  let jsonCalled = false;
  const window = makeWindow(async () => ({
    ok: false,
    status: 503,
    json: async () => { jsonCalled = true; throw new Error("JSON 파싱 실패"); },
  }));

  await window.loadTopMarketCap();

  const result = window.document.getElementById("marketcap-result");
  assert(jsonCalled === false, "HTTP 오류 응답은 JSON으로 파싱하면 안 됨");
  assert(result.textContent.includes("HTTP 503"), "HTTP 상태 안내가 표시되어야 함");
});

test("빈 목록과 비정상 목록을 사용자 안내로 처리한다", async () => {
  const responses = [success([]), { ok: true, json: async () => ({ rt_cd: "0", data: null }) }];
  const window = makeWindow(async () => responses.shift());

  await window.loadTopMarketCap();
  assert(window.document.getElementById("marketcap-result").textContent.includes("조회 결과가 없습니다"),
    "빈 목록 안내가 표시되어야 함");

  await window.loadTopMarketCap();
  assert(window.document.getElementById("marketcap-result").textContent.includes("응답 형식이 올바르지 않습니다"),
    "비정상 목록 안내가 표시되어야 함");
});

test("비정상 숫자는 NaN 대신 대체 기호로 표시한다", async () => {
  const window = makeWindow(async () => success([{
    rank: "1", name: "테스트", code: "000001", current_price: "invalid", change_rate: "invalid", market_cap: "invalid",
  }]));

  await window.loadTopMarketCap();

  const text = window.document.getElementById("marketcap-result").textContent;
  assert(!text.includes("NaN"), "비정상 숫자가 NaN으로 노출됨");
  assert(text.includes("-"), "비정상 숫자는 대체 기호로 표시되어야 함");
});

await run();
