/*
 * jsdom 기반 /overseas-ranking 화면 JS 회귀 테스트.
 *
 * 정렬은 서버가 하므로 클라이언트 회귀 포인트는 (1) 카테고리/상위N 이 실제 요청 경로에
 * 반영되는지 (2) 상위N 을 바꿔도 보고 있던 카테고리가 유지되는지 (3) 늦게 끝난 이전 요청이
 * 최신 결과를 덮어쓰지 않는지다.
 *
 * 실행: node run_overseas_ranking_dom_tests.mjs  (exit 0 = 전부 통과)
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { applyCommonStubs, test, assert, run } from "./harness.mjs";

const PAGE_JS = process.env.OVERSEAS_RANKING_JS_PATH
  ? resolve(process.env.OVERSEAS_RANKING_JS_PATH)
  : resolve(import.meta.dirname, "../../view/web/static/js/overseas_ranking.js");

const SCAFFOLD = `
<div id="section-overseas-ranking" class="section">
  <button class="btn ranking-tab active" data-category="rise"></button>
  <button class="btn ranking-tab" data-category="fall"></button>
  <button class="btn ranking-tab" data-category="volume"></button>
  <button class="btn ranking-tab" data-category="trading_value"></button>
  <button class="btn ranking-limit active" data-limit="30"></button>
  <button class="btn ranking-limit" data-limit="50"></button>
  <button class="btn ranking-limit" data-limit="100"></button>
  <div id="overseas-ranking-result"></div>
</div>
`;

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}
const flush = () => new Promise((r) => setTimeout(r, 0));

/** DOMContentLoaded 초기 로드를 소진한 뒤 각 테스트가 자기 요청만 보게 한다. */
async function makeWindow() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>${SCAFFOLD}</body></html>`, {
    url: "http://localhost/overseas-ranking",
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
  window.document.getElementById("overseas-ranking-result").innerHTML = "";
  return window;
}

function okPayload(items) {
  return { ok: true, json: async () => ({ rt_cd: "0", msg1: "OK", data: { fx_rate: 1400, items } }) };
}

const APPLE = {
  rank: 1, symbol: "AAPL", name: "Apple Inc.", sector: "Information Technology",
  price: 200.0, change_rate: 3.456, volume: 12_345_678, trading_value_usd: 2_469_135_600,
};

test("랭킹 행을 순위·심볼·섹터·등락률·거래량·거래대금으로 렌더한다", async () => {
  const window = await makeWindow();
  window.fetchWithTimeout = async () => okPayload([APPLE]);

  await window.loadOverseasRanking("rise");

  const text = window.document.getElementById("overseas-ranking-result").textContent;
  assert(text.includes("AAPL"), "심볼이 표시되어야 함");
  assert(text.includes("Information Technology"), "섹터가 표시되어야 함");
  assert(text.includes("$200.00"), "현재가가 USD 로 표시되어야 함");
  assert(text.includes("+3.46%"), "등락률이 소수 2자리로 표시되어야 함");
  assert(text.includes("12,345,678"), "회귀: 거래량이 주식 수 표기로 나오지 않음");
  assert(text.includes("$2.47B"), "회귀: 거래대금 축약 표기 실패");
});

test("심볼·종목명을 미국장 현재가 화면 링크로 렌더한다", async () => {
  const window = await makeWindow();
  window.fetchWithTimeout = async () => okPayload([APPLE]);

  await window.loadOverseasRanking("rise");

  const link = window.document.querySelector("#overseas-ranking-result a.stock-link");
  assert(link, "회귀: 종목 셀이 현재가 화면으로 가는 링크가 아님");
  assert(
    link.getAttribute("href") === "/overseas-stock?symbol=AAPL",
    `회귀: 링크가 /overseas-stock 심볼 조회로 가지 않음 (${link.getAttribute("href")})`,
  );
  assert(link.textContent.includes("AAPL"), "링크에 심볼이 포함되어야 함");
  assert(link.textContent.includes("Apple Inc."), "회귀: 종목명이 링크 밖에 남아 클릭되지 않음");
});

test("카테고리 탭이 요청 경로와 active 클래스에 반영된다", async () => {
  const window = await makeWindow();
  const urls = [];
  window.fetchWithTimeout = async (url) => { urls.push(url); return okPayload([APPLE]); };

  await window.loadOverseasRanking("trading_value");

  assert(
    urls.some((url) => url.includes("/api/overseas/ranking/trading_value")),
    `회귀: 카테고리가 경로에 반영되지 않음 (${JSON.stringify(urls)})`,
  );
  const active = window.document.querySelector("#section-overseas-ranking .ranking-tab.active");
  assert(active && active.dataset.category === "trading_value", "선택한 카테고리 탭만 active 여야 함");
});

test("상위 N 을 바꿔도 보고 있던 카테고리가 유지된다", async () => {
  const window = await makeWindow();
  const urls = [];
  window.fetchWithTimeout = async (url) => { urls.push(url); return okPayload([APPLE]); };

  await window.loadOverseasRanking("volume");
  window.setOverseasRankingLimit(100);
  await flush();

  const last = urls[urls.length - 1];
  assert(
    last.includes("/api/overseas/ranking/volume") && last.includes("limit=100"),
    `회귀: 상위 N 변경이 카테고리를 rise 로 되돌림 (${last})`,
  );
  const activeLimit = window.document.querySelector("#section-overseas-ranking .ranking-limit.active");
  assert(activeLimit && activeLimit.dataset.limit === "100", "선택한 상위 N 만 active 여야 함");
});

test("빈 목록이면 안내 문구를 보여준다", async () => {
  const window = await makeWindow();
  window.fetchWithTimeout = async () => okPayload([]);

  await window.loadOverseasRanking("rise");

  const text = window.document.getElementById("overseas-ranking-result").textContent;
  assert(text.includes("조회 결과가 없습니다."), "빈 목록 안내가 표시되어야 함");
});

test("서버가 rt_cd=1 로 응답하면 실패 사유를 보여준다", async () => {
  const window = await makeWindow();
  window.fetchWithTimeout = async () => ({
    ok: true,
    json: async () => ({ rt_cd: "1", msg1: "미국 랭킹을 조회하지 못했습니다.", data: null }),
  });

  await window.loadOverseasRanking("rise");

  const text = window.document.getElementById("overseas-ranking-result").textContent;
  assert(text.includes("미국 랭킹을 조회하지 못했습니다."), "서버 실패 사유가 노출되어야 함");
});

test("늦게 끝난 이전 카테고리 요청이 최신 결과를 덮어쓰지 않는다", async () => {
  const window = await makeWindow();
  const pending = [];
  window.fetchWithTimeout = async () => {
    const request = deferred();
    pending.push(request);
    return request.promise;
  };

  const first = window.loadOverseasRanking("rise");
  const second = window.loadOverseasRanking("volume");
  await flush();
  assert(pending.length === 2, "두 요청이 모두 진행되어야 함");

  pending[1].resolve(okPayload([{ ...APPLE, symbol: "NVDA", name: "NVIDIA" }]));
  await second;
  pending[0].resolve(okPayload([APPLE]));
  await first;

  const text = window.document.getElementById("overseas-ranking-result").textContent;
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

  await window.loadOverseasRanking("rise");
  await flush();

  assert(window.__pwned === undefined, "회귀: 종목명이 HTML 로 해석됨");
  assert(window.__pwned2 === undefined, "회귀: 섹터가 스크립트로 해석됨");
});

await run();
