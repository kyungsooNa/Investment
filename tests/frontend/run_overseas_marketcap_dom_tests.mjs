/*
 * jsdom 기반 /overseas-marketcap 화면 JS 회귀 테스트.
 *
 * 이 화면은 S&P500 유니버스 스냅샷을 서버에서 정렬해 내려주므로, 클라이언트는
 * (1) limit 탭 전환이 실제 쿼리에 반영되는지 (2) USD/원화 표기가 뒤섞이지 않는지
 * (3) 늦게 끝난 이전 요청이 최신 결과를 덮어쓰지 않는지가 회귀 포인트다.
 *
 * 실행: node run_overseas_marketcap_dom_tests.mjs  (exit 0 = 전부 통과)
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { applyCommonStubs, test, assert, run } from "./harness.mjs";

const PAGE_JS = process.env.OVERSEAS_MARKETCAP_JS_PATH
  ? resolve(process.env.OVERSEAS_MARKETCAP_JS_PATH)
  : resolve(import.meta.dirname, "../../view/web/static/js/overseas_marketcap.js");

const SCAFFOLD = `
<div id="section-overseas-marketcap" class="section">
  <button class="btn ranking-tab active" data-limit="30"></button>
  <button class="btn ranking-tab" data-limit="50"></button>
  <button class="btn ranking-tab" data-limit="100"></button>
  <div id="overseas-marketcap-result"></div>
</div>
`;

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}
const flush = () => new Promise((r) => setTimeout(r, 0));

/**
 * 스크립트는 DOMContentLoaded 에서 초기 로드를 건다. jsdom 에서도 그 초기 로드가
 * 발동하므로, 각 테스트가 자기 요청만 관찰하도록 소진한 뒤 탭 상태를 리셋해서 돌려준다.
 */
async function makeWindow() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>${SCAFFOLD}</body></html>`, {
    url: "http://localhost/overseas-marketcap",
    runScripts: "dangerously",
  });
  const { window } = dom;
  applyCommonStubs(window);
  window.fetchWithTimeout = async () => ({ ok: true, json: async () => ({}) });

  const script = window.document.createElement("script");
  script.textContent = readFileSync(PAGE_JS, "utf8");
  window.document.body.appendChild(script);

  await flush();
  await flush();
  window.document.getElementById("overseas-marketcap-result").innerHTML = "";
  return window;
}

function okPayload(items, fxRate = 1400) {
  return { ok: true, json: async () => ({ rt_cd: "0", msg1: "OK", data: { fx_rate: fxRate, items } }) };
}

const APPLE = {
  rank: 1, symbol: "AAPL", name: "Apple Inc.", sector: "Information Technology",
  price: 190.5, change_rate: 1.234, volume: 50000000,
  market_cap_usd: 3_000_000_000_000, market_cap_krw: 4_200_000_000_000_000,
};

test("시가총액 행을 순위·심볼·섹터·USD/원화로 렌더한다", async () => {
  const window = await makeWindow();
  window.fetchWithTimeout = async () => okPayload([APPLE]);

  await window.loadOverseasTopMarketCap(30);

  const text = window.document.getElementById("overseas-marketcap-result").textContent;
  assert(text.includes("AAPL"), "심볼이 표시되어야 함");
  assert(text.includes("Apple Inc."), "종목명이 표시되어야 함");
  assert(text.includes("Information Technology"), "섹터가 표시되어야 함");
  assert(text.includes("$190.50"), "현재가가 USD 로 표시되어야 함");
  assert(text.includes("+1.23%"), "등락률이 소수 2자리로 표시되어야 함");
  assert(text.includes("$3.00T"), "회귀: USD 시가총액 축약 표기 실패");
  assert(text.includes("4,200조"), "회귀: 원화 환산이 조/억 표기로 표시되지 않음");
});

test("limit 탭 전환이 요청 쿼리와 active 클래스에 반영된다", async () => {
  const window = await makeWindow();
  const urls = [];
  window.fetchWithTimeout = async (url) => { urls.push(url); return okPayload([APPLE]); };

  await window.loadOverseasTopMarketCap(100);

  assert(
    urls.some((url) => url.includes("limit=100")),
    `회귀: limit 이 쿼리에 반영되지 않음 (${JSON.stringify(urls)})`,
  );
  const active = window.document.querySelector("#section-overseas-marketcap .ranking-tab.active");
  assert(active && active.dataset.limit === "100", "선택한 상위 N 탭만 active 여야 함");
});

test("환율이 없으면 원화 환산 대신 안내 문구를 보여준다", async () => {
  const window = await makeWindow();
  const noKrw = { ...APPLE, market_cap_krw: null };
  window.fetchWithTimeout = async () => okPayload([noKrw], null);

  await window.loadOverseasTopMarketCap(30);

  const text = window.document.getElementById("overseas-marketcap-result").textContent;
  assert(text.includes("환율 정보 없음"), "회귀: 환율 미확보 시 'USD/KRW 0' 같은 표기가 나옴");
  assert(text.includes("$3.00T"), "환율이 없어도 USD 시총은 표시되어야 함");
});

test("빈 목록이면 안내 문구를 보여준다", async () => {
  const window = await makeWindow();
  window.fetchWithTimeout = async () => okPayload([]);

  await window.loadOverseasTopMarketCap(30);

  const text = window.document.getElementById("overseas-marketcap-result").textContent;
  assert(text.includes("조회 결과가 없습니다."), "빈 목록 안내가 표시되어야 함");
});

test("서버가 rt_cd=1 로 응답하면 실패 메시지를 보여준다", async () => {
  const window = await makeWindow();
  window.fetchWithTimeout = async () => ({
    ok: true,
    json: async () => ({ rt_cd: "1", msg1: "미국 시가총액을 조회하지 못했습니다.", data: null }),
  });

  await window.loadOverseasTopMarketCap(30);

  const text = window.document.getElementById("overseas-marketcap-result").textContent;
  assert(text.includes("미국 시가총액을 조회하지 못했습니다."), "서버 실패 사유가 노출되어야 함");
});

test("늦게 끝난 이전 요청이 최신 결과를 덮어쓰지 않는다", async () => {
  const window = await makeWindow();
  const pending = [];
  window.fetchWithTimeout = async () => {
    const request = deferred();
    pending.push(request);
    return request.promise;
  };

  const first = window.loadOverseasTopMarketCap(30);
  const second = window.loadOverseasTopMarketCap(100);
  await flush();
  assert(pending.length === 2, "두 요청이 모두 진행되어야 함");

  pending[1].resolve(okPayload([{ ...APPLE, symbol: "NVDA", name: "NVIDIA" }]));
  await second;
  pending[0].resolve(okPayload([APPLE]));
  await first;

  const text = window.document.getElementById("overseas-marketcap-result").textContent;
  assert(text.includes("NVDA"), "최신 요청 결과가 표시되어야 함");
  assert(!text.includes("AAPL"), "회귀: 늦게 끝난 이전 요청이 최신 결과를 덮어씀");
});

test("외부 문자열은 HTML 로 해석되지 않는다", async () => {
  const window = await makeWindow();
  window.fetchWithTimeout = async () => okPayload([{
    ...APPLE,
    name: '<img src=x onerror="window.__pwned=1">',
    sector: "<script>window.__pwned2=1</script>",
  }]);

  await window.loadOverseasTopMarketCap(30);
  await flush();

  assert(window.__pwned === undefined, "회귀: 종목명이 HTML 로 해석됨");
  assert(window.__pwned2 === undefined, "회귀: 섹터가 스크립트로 해석됨");
});

await run();
