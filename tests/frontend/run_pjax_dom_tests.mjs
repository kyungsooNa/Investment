/*
 * jsdom 기반 PJAX 페이지 전환 회귀 테스트 (common.js navigatePjax).
 *
 * 배포로 정적 자산 버전(?v=N)이 바뀐 뒤 열려 있던 탭에서 메뉴를 이동하면, 새 버전 스크립트가
 * 같은 전역 const 를 재선언하다 SyntaxError 로 죽고 옛 함수가 계속 그렸다.
 * 같은 경로의 다른 버전 스크립트가 필요하면 PJAX 대신 전체 새로고침으로 넘어가야 한다.
 *
 * 실행: node run_pjax_dom_tests.mjs
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM, VirtualConsole } from "jsdom";
import { test, assert, run } from "./harness.mjs";

const COMMON_JS = resolve(import.meta.dirname, "../../view/web/static/js/common.js");

function layout({ head = "", main = "", scripts = "" }) {
  return `<!DOCTYPE html><html><head>${head}</head><body>
    <nav class="market-nav"><a href="/domestic">한국장</a><a href="/overseas">미국장</a></nav>
    <nav class="nav"><a href="/domestic">홈</a><a href="/overseas-stock">홈</a></nav>
    <main id="page-main">${main}${scripts}</main>
  </body></html>`;
}

function makeWindow(currentPage, nextPage) {
  const navigations = [];
  const virtualConsole = new VirtualConsole();
  // jsdom 은 전체 페이지 이동을 구현하지 않고 이 오류로 알린다.
  virtualConsole.on("jsdomError", error => {
    if (String(error.message).includes("navigation")) navigations.push(error.message);
  });
  const dom = new JSDOM(currentPage, {
    url: "http://localhost/domestic",
    runScripts: "outside-only",
    virtualConsole,
  });
  const { window } = dom;
  window.alert = () => {};
  // runScripts: outside-only 라 외부 스크립트를 받지 않는다. loadScript 가 기다리는 load 만 쏴 준다.
  new window.MutationObserver(records => records.forEach(record => record.addedNodes.forEach(node => {
    if (node.tagName === "SCRIPT") setTimeout(() => node.dispatchEvent(new window.Event("load")), 0);
  }))).observe(window.document.head, { childList: true });
  window.fetch = async (url) => (String(url).startsWith("/api/")
    ? { ok: true, json: async () => ({}) }
    : { ok: true, text: async () => nextPage });
  // 실제 순서처럼 head 의 common.js 가 먼저 돌고, 본문 끝의 페이지 스크립트는 그 뒤에 파싱된다.
  const pageScripts = Array.from(window.document.querySelectorAll("#page-main script[src]"));
  pageScripts.forEach(script => script.remove());
  window.eval(readFileSync(COMMON_JS, "utf8"));
  pageScripts.forEach(script => window.document.getElementById("page-main").appendChild(script));
  window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
  return { window, navigations };
}

function appendedScripts(window) {
  return Array.from(window.document.head.querySelectorAll("script[src]")).map(s => {
    const url = new URL(s.src);
    return url.pathname + url.search;
  });
}

const CURRENT = layout({
  head: '<script src="/static/js/common.js?v=5"></script>',
  main: "<p id='old'>한국장 홈</p>",
  scripts: '<script src="/static/js/market_indices.js?v=12"></script>',
});

test("같은 경로 스크립트의 버전이 바뀌었으면 PJAX 대신 전체 새로고침으로 이동한다", async () => {
  const next = layout({
    main: "<p id='new'>미국장 홈</p>",
    scripts: '<script src="/static/js/market_indices.js?v=14"></script>',
  });
  const { window, navigations } = makeWindow(CURRENT, next);

  await window.navigatePjax("/overseas-stock");

  assert(navigations.length === 1, `전체 새로고침으로 넘어가지 않음 (이동 ${navigations.length}회)`);
  assert(window.document.getElementById("old"), "새로고침할 페이지인데 본문을 PJAX 로 바꿔치움");
  assert(!appendedScripts(window).includes("/static/js/market_indices.js?v=14"),
    "옛 스크립트 위에 새 버전을 덧로드함 (전역 const 재선언 SyntaxError)");
});

test("head 스크립트 버전이 바뀌어도 전체 새로고침으로 이동한다", async () => {
  const next = layout({
    head: '<script src="/static/js/common.js?v=6"></script>',
    main: "<p id='new'>미국장 홈</p>",
  });
  const { window, navigations } = makeWindow(CURRENT, next);

  await window.navigatePjax("/overseas-stock");

  assert(navigations.length === 1, `head 스크립트 버전 변경을 놓침 (이동 ${navigations.length}회)`);
  assert(window.document.getElementById("old"), "새로고침할 페이지인데 본문을 PJAX 로 바꿔치움");
});

test("버전이 같거나 처음 쓰는 스크립트면 그대로 PJAX 로 전환한다", async () => {
  const next = layout({
    head: '<script src="/static/js/common.js?v=5"></script>',
    main: "<p id='new'>미국장 홈</p>",
    scripts: '<script src="/static/js/market_indices.js?v=12"></script>'
      + '<script src="/static/js/overseas_stock.js?v=3"></script>',
  });
  const { window, navigations } = makeWindow(CURRENT, next);

  await window.navigatePjax("/overseas-stock");

  assert(navigations.length === 0, `불필요한 전체 새로고침 (이동 ${navigations.length}회)`);
  assert(window.document.getElementById("new"), "PJAX 로 본문을 바꾸지 않음");
  const added = appendedScripts(window);
  assert(added.includes("/static/js/overseas_stock.js?v=3"), `처음 쓰는 스크립트를 로드하지 않음: ${added}`);
  assert(!added.includes("/static/js/market_indices.js?v=12"), "이미 로드한 같은 버전을 다시 로드함");
});

await run();
