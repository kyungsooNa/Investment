/* jsdom 기반 한국장 홈 종목 검색 → 현재가 화면 이동 회귀 테스트. */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { test, assert, run } from "./harness.mjs";

const AUTOCOMPLETE_JS = resolve(import.meta.dirname, "../../view/web/static/js/autocomplete.js");
const DOMESTIC_HOME_JS = resolve(import.meta.dirname, "../../view/web/static/js/domestic_home.js");

async function makeWindow() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>
    <form id="search-form" onsubmit="return submitDomesticStockSearch(event)">
      <input id="domestic-stock-search" type="search">
      <ul id="domestic-stock-autocomplete"></ul>
      <p id="domestic-stock-search-error"></p>
    </form>
  </body></html>`, {
    url: "http://localhost/domestic",
    runScripts: "dangerously",
  });
  const { window } = dom;
  window.localStorage.clear();
  window.fetch = async () => ({
    json: async () => ({
      stocks: [
        { c: "005930", n: "삼성전자" },
        { c: "005935", n: "삼성전자우" },
        { c: "035720", n: "카카오" },
      ],
    }),
  });
  window.__navigatedTo = null;
  window.navigatePjax = async href => { window.__navigatedTo = href; };

  for (const path of [AUTOCOMPLETE_JS, DOMESTIC_HOME_JS]) {
    const script = window.document.createElement("script");
    script.textContent = readFileSync(path, "utf8");
    window.document.body.appendChild(script);
  }
  await new Promise(resolveTick => setTimeout(resolveTick, 0));
  await new Promise(resolveTick => setTimeout(resolveTick, 0));
  return window;
}

function submit(window, value) {
  const input = window.document.getElementById("domestic-stock-search");
  input.value = value;
  window.document.getElementById("search-form").dispatchEvent(
    new window.Event("submit", { bubbles: true, cancelable: true }),
  );
}

test("정확한 종목명 검색은 현재가 URL로 이동", async () => {
  const window = await makeWindow();
  submit(window, "카카오");
  assert(window.__navigatedTo === "/stock?code=035720", "정확한 종목명 이동 실패");
});

test("자동완성 선택은 현재가 URL로 이동", async () => {
  const window = await makeWindow();
  const input = window.document.getElementById("domestic-stock-search");
  input.value = "카카";
  input.dispatchEvent(new window.Event("input", { bubbles: true }));
  window.document.querySelector("#domestic-stock-autocomplete li").click();
  assert(window.__navigatedTo === "/stock?code=035720", "자동완성 선택 이동 실패");
});

test("여러 종목이 일치하면 이동하지 않고 선택 안내", async () => {
  const window = await makeWindow();
  submit(window, "삼성");
  assert(window.__navigatedTo === null, "모호한 검색이 임의 종목으로 이동함");
  assert(
    window.document.getElementById("domestic-stock-search-error").textContent.includes("여러 개"),
    "모호한 검색 안내가 표시되지 않음",
  );
});

await run();
