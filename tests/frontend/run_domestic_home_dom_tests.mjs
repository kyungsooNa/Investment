/* jsdom 기반 한국장 홈 종목 검색 → 현재가 화면 이동 회귀 테스트. */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { test, assert, run } from "./harness.mjs";

const AUTOCOMPLETE_JS = resolve(import.meta.dirname, "../../view/web/static/js/autocomplete.js");
const COMMON_JS = resolve(import.meta.dirname, "../../view/web/static/js/common.js");

/* common.js 전체를 실행하면 navigatePjax 스텁을 덮어쓰므로, 홈이 쓰는 유틸 함수만 추출한다. */
function extractCommonFunction(name) {
  const source = readFileSync(COMMON_JS, "utf8");
  const start = source.indexOf(`function ${name}(`);
  const end = source.indexOf("\n}\n", start);
  assert(start >= 0 && end > start, `common.js 에서 ${name} 를 찾지 못함`);
  return source.slice(start, end + 2);
}
const DOMESTIC_HOME_JS = resolve(import.meta.dirname, "../../view/web/static/js/domestic_home.js");

const THEME_REPORT = {
  rt_cd: "0",
  msg1: "장중 주도 테마 조회 성공",
  captured_at: "20261012 10:15",
  data: [
    {
      normalized_name: "2차전지",
      advancing_ratio: 80,
      trading_value_sum_won: 350000000000,
      leaders: [
        { code: "247540", name: "에코프로비엠", change_rate: 7.5 },
        { code: "086520", name: "에코프로", change_rate: 5.1 },
      ],
    },
    {
      normalized_name: "<반도체>",
      advancing_ratio: 60,
      trading_value_sum_won: 120000000000,
      leaders: [{ code: "000660", name: "SK하이닉스", change_rate: 2.3 }],
    },
  ],
};

async function makeWindow({ themeReport = THEME_REPORT } = {}) {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>
    <form id="search-form" onsubmit="return submitDomesticStockSearch(event)">
      <input id="domestic-stock-search" type="search">
      <ul id="domestic-stock-autocomplete"></ul>
      <p id="domestic-stock-search-error"></p>
    </form>
    <div id="domestic-theme-leaders"></div>
    <span id="domestic-theme-updated"></span>
  </body></html>`, {
    url: "http://localhost/domestic",
    runScripts: "dangerously",
  });
  const { window } = dom;
  window.localStorage.clear();
  window.__fetched = [];
  window.fetch = async url => {
    window.__fetched.push(String(url));
    if (String(url).startsWith("/api/ranking/themes/intraday")) {
      return { json: async () => themeReport };
    }
    return { json: async () => ({
      stocks: [
        { c: "005930", n: "삼성전자" },
        { c: "005935", n: "삼성전자우" },
        { c: "035720", n: "카카오" },
      ],
    }) };
  };
  window.__navigatedTo = null;
  window.navigatePjax = async href => { window.__navigatedTo = href; };

  const sources = [
    extractCommonFunction("formatTradingValue") + "\n" + extractCommonFunction("escapeHtml"),
    readFileSync(AUTOCOMPLETE_JS, "utf8"),
    readFileSync(DOMESTIC_HOME_JS, "utf8"),
  ];
  for (const source of sources) {
    const script = window.document.createElement("script");
    script.textContent = source;
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

test("당일 주도 테마를 순위·대장주 링크와 함께 표시", async () => {
  const window = await makeWindow();
  const box = window.document.getElementById("domestic-theme-leaders");
  assert(window.__fetched.includes("/api/ranking/themes/intraday"), "주도 테마 API 미호출");
  const items = box.querySelectorAll(".domestic-theme-item");
  assert(items.length === 2, `테마 수 불일치: ${items.length}`);
  assert(items[0].textContent.includes("2차전지"), "1위 테마명 누락");
  assert(items[0].textContent.includes("+7.50%"), "대장주 등락률 누락");
  assert(
    box.querySelector('a[href="/stock?code=247540"]') !== null,
    "대장주 현재가 링크 누락",
  );
  assert(box.innerHTML.includes("&lt;반도체&gt;"), "테마명이 이스케이프되지 않음");
  assert(
    window.document.getElementById("domestic-theme-updated").textContent.includes("10:15"),
    "기준 시각 누락",
  );
});

test("주도 테마 데이터가 없으면 안내 문구 표시", async () => {
  const window = await makeWindow({
    themeReport: { rt_cd: "0", msg1: "장중 테마 데이터를 수집 중입니다.", captured_at: null, data: [] },
  });
  const box = window.document.getElementById("domestic-theme-leaders");
  assert(box.querySelectorAll(".domestic-theme-item").length === 0, "빈 데이터인데 항목 표시");
  assert(box.textContent.includes("수집 중"), "빈 데이터 안내 누락");
});

await run();
