/* jsdom 기반 한국장 홈 종목 검색 → 현재가 화면 이동 회귀 테스트. */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { test, assert, run } from "./harness.mjs";

const AUTOCOMPLETE_JS = resolve(import.meta.dirname, "../../view/web/static/js/autocomplete.js");
const COMMON_JS = resolve(import.meta.dirname, "../../view/web/static/js/common.js");

/* common.js 전체를 실행하면 navigatePjax 스텁을 덮어쓰므로, 홈이 쓰는 유틸 함수만 추출한다. */
function extractCommonFunction(name) {
  const source = readFileSync(COMMON_JS, "utf8").replace(/\r\n/g, "\n");
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

const PREMARKET_REPORT = {
  rt_cd: "0",
  msg1: "장전 방향 예측 조회 성공",
  data: {
    ok: true,
    date: "2026-11-19",
    base_date: "2026-11-18",
    targets: [
      { code: "KOSPI", name: "코스피", probability: 0.723, outlook: "상승 우세",
        body_probability: 0.58, body_outlook: "양봉 우세", train_days: 1450,
        track: { n: 20, hits: 15, confident_n: 12, confident_hits: 10 },
        body_track: { n: 20, hits: 11, confident_n: 4, confident_hits: 2 },
        previous: { date: "2026-11-18", probability: 0.65, actual_change: 0.0045,
          body_probability: 0.4, actual_body_change: -0.003 } },
      { code: "000660", name: "SK하이닉스", probability: 0.31, outlook: "하락 우세",
        body_probability: 0.44, body_outlook: "음봉 우세", train_days: 1450,
        track: { n: 0, hits: 0, confident_n: 0, confident_hits: 0 }, previous: null },
    ],
    features: [
      { name: "spx", label: "S&P500", value: 0.0123, contribution: 0.4 },
      { name: "vix", label: "VIX 변화율", value: -0.051, contribution: 0.2 },
    ],
  },
};

async function makeWindow({ themeReport = THEME_REPORT, premarketReport = PREMARKET_REPORT } = {}) {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>
    <form id="search-form" onsubmit="return submitDomesticStockSearch(event)">
      <input id="domestic-stock-search" type="search">
      <ul id="domestic-stock-autocomplete"></ul>
      <p id="domestic-stock-search-error"></p>
    </form>
    <div id="premarket-direction"></div>
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
    if (String(url).startsWith("/api/market/premarket-direction")) {
      return { json: async () => premarketReport };
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

test("장전 양음봉과 전일 대비 방향 확률을 각각 대상별 표시", async () => {
  const window = await makeWindow();
  const box = window.document.getElementById("premarket-direction");
  assert(window.__fetched.includes("/api/market/premarket-direction"), "장전 예측 API 미호출");
  const rows = box.querySelectorAll(".premarket-direction-head");
  assert(rows.length === 2, `대상 수 불일치: ${rows.length}`);
  assert(rows[0].textContent.includes("코스피") && rows[0].textContent.includes("양봉 58%"), "코스피 양봉 확률 누락");
  assert(rows[0].textContent.indexOf("양봉 58%") < rows[0].textContent.indexOf("▲ 상승 마감 72%"), "양음봉이 주지표가 아님");
  assert(rows[0].textContent.includes("전일 대비 ▲ 상승 마감 72%"), "코스피 전일 방향 확률 누락");
  assert(rows[0].textContent.includes("상승 우세"), "코스피 판정 누락");
  assert(rows[0].querySelector(".premarket-direction-prob.text-red") !== null, "상승 우세 색상 누락");
  // 31% 상승 = 69% 하락 — 낮은 확률을 '하락 폭'으로 오해하지 않게 우세한 쪽으로 표기
  assert(rows[1].textContent.includes("SK하이닉스") && rows[1].textContent.includes("음봉 56%"), "하이닉스 음봉 확률 누락");
  assert(rows[1].textContent.includes("전일 대비 ▼ 하락 마감 69%"), "하이닉스 전일 방향 확률 누락");
  assert(!rows[1].textContent.includes("31%"), "하락 우세인데 상승 확률로 표기");
  assert(rows[1].querySelector(".premarket-direction-prob.text-blue") !== null, "하락 우세 색상 누락");
  assert(rows[0].textContent.includes("양음봉 최근 20일 11/20"), "양음봉 적중률 누락");
  assert(rows[0].textContent.includes("전일방향 최근 20일 15/20"), "전일방향 적중률 누락");
  assert(rows[0].textContent.includes("전일 음봉 60% → 음봉 적중 / 전일 대비 ▲ 상승 마감 65% → +0.45% 적중"), "전 거래일 결과 누락");
  assert(rows[1].textContent.includes("기록 쌓는 중"), "기록 없음 안내 누락");
  assert(box.textContent.includes("S&P500") && box.textContent.includes("+1.23%"), "지표 등락 누락");
  assert(box.textContent.includes("-5.10%"), "음수 지표 누락");
  assert(box.textContent.includes("2026-11-18"), "기준일 누락");
  assert(box.textContent.includes("등락 폭 아님"), "확률 의미 안내 누락");
});

test("장전 예측 실패 시 사유를 표시", async () => {
  const window = await makeWindow({
    premarketReport: { rt_cd: "1", msg1: "장전 방향 예측 실패: 시세 수신 실패: vix", data: null },
  });
  const box = window.document.getElementById("premarket-direction");
  assert(box.textContent.includes("시세 수신 실패"), "실패 사유 누락");
  assert(box.querySelector(".premarket-direction-prob") === null, "실패인데 확률 표시");
});

test("미국장 새 종가가 없으면 확률 대신 보류 사유를 표시", async () => {
  const window = await makeWindow({
    premarketReport: { rt_cd: "0", data: {
      ok: true, skipped: "no_new_us_session", message: "미국장 신규 종가가 없어 장전 예측을 보류합니다.", targets: [],
    } },
  });
  const box = window.document.getElementById("premarket-direction");
  assert(box.textContent.includes("미국장 신규 종가"), "보류 사유 누락");
  assert(box.querySelector(".premarket-direction-prob") === null, "보류 중 확률 표시");
});

await run();
