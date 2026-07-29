/*
 * jsdom 기반 홈 화면 지수 패널 회귀 테스트.
 *
 * 국장은 KIS API(/api/market-index/*) + Chart.js, 미장/원자재는 TradingView 위젯으로
 * 렌더링 경로가 다르므로 두 경로를 모두 검증한다.
 *
 * 실행: node run_market_indices_dom_tests.mjs
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { applyCommonStubs, test, assert, run } from "./harness.mjs";

const MARKET_INDICES_JS = process.env.MARKET_INDICES_JS_PATH
  ? resolve(process.env.MARKET_INDICES_JS_PATH)
  : resolve(import.meta.dirname, "../../view/web/static/js/market_indices.js");

const EXPECTED_GROUPS = [
  { title: "국장", kind: "kis", keys: ["0001", "1001"] },
  {
    title: "미장",
    kind: "widget",
    // 거래소 실지수(SP:SPX, NASDAQ:NDX, CBOE:VIX ...)는 무료 임베드에서 차단되므로
    // 실제로 시세가 그려지는 CFD/ETF 심볼만 쓴다.
    keys: [
      "CAPITALCOM:US100",
      "CAPITALCOM:US500",
      "NASDAQ:SOXX",
      "CAPITALCOM:VIX",
      "CAPITALCOM:DXY",
    ],
  },
  { title: "원자재", kind: "widget", keys: ["TVC:GOLD", "TVC:USOIL", "CBOE:DRAM"] },
];

const BLOCKED_SYMBOLS = [
  "KRX:KOSPI", "KRX:KOSDAQ", "SP:SPX", "NASDAQ:NDX",
  "NASDAQ:SOX", "CBOE:VIX", "TVC:DXY", "TVC:US10Y", "TVC:US02Y",
];

const WIDGET_SRC =
  "https://s3.tradingview.com/external-embedding/embed-widget-mini-symbol-overview.js";

const SCAFFOLD = `<div id="market-indices"></div>`;

async function makeWindow(fetchWithTimeout) {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>${SCAFFOLD}</body></html>`, {
    url: "http://localhost/",
    runScripts: "outside-only",
  });
  const { window } = dom;
  // 실제 페이지처럼 DOM 준비 후 스크립트를 평가한다.
  // (loading 중에 eval 하면 스크립트가 건 DOMContentLoaded 렌더가 명시 렌더에 겹친다)
  if (window.document.readyState === "loading") {
    await new Promise(resolve => window.document.addEventListener("DOMContentLoaded", resolve));
  }
  applyCommonStubs(window);
  window.fetchWithTimeout = fetchWithTimeout || (async () => success(indexPayload()));
  // Chart.js 는 CDN 로드라 jsdom 에서는 생성 호출만 기록한다.
  window.HTMLCanvasElement.prototype.getContext = () => ({});
  window.currentCharts = [];
  window.__charts = [];
  window.Chart = function (ctx, config) {
    window.__charts.push({ ctx, config });
    this.destroy = () => {};
  };
  window.eval(readFileSync(MARKET_INDICES_JS, "utf8"));
  return window;
}

function indexPayload(overrides) {
  return Object.assign({
    code: "0001",
    name: "코스피",
    current: 2650.15,
    change: 12.3,
    change_rate: 0.47,
    points: [
      { date: "20260724", close: 2637.85 },
      { date: "20260727", close: 2650.15 },
    ],
  }, overrides || {});
}

function success(data) {
  return { ok: true, json: async () => ({ rt_cd: "0", data }) };
}

function groupsOf(window) {
  return Array.from(window.document.querySelectorAll("#market-indices .market-index-group"));
}

function expectedGroupsOf(window) {
  const groups = groupsOf(window);
  assert(groups.length === EXPECTED_GROUPS.length,
    `그룹 ${EXPECTED_GROUPS.length}개가 생성되어야 함 (실제 ${groups.length}개)`);
  return groups;
}

test("국장·미장·원자재 그룹이 순서대로 렌더링된다", async () => {
  const window = await makeWindow();

  await window.renderMarketIndices();

  expectedGroupsOf(window).forEach((group, i) => {
    const title = group.querySelector(".market-index-group-title");
    assert(title && title.textContent.trim() === EXPECTED_GROUPS[i].title,
      `${i}번째 그룹 제목이 ${EXPECTED_GROUPS[i].title} 이어야 함 (실제 ${title && title.textContent})`);
  });
});

test("각 그룹이 자기 시장의 지수만 담는다", async () => {
  const window = await makeWindow();

  await window.renderMarketIndices();

  expectedGroupsOf(window).forEach((group, i) => {
    const expected = EXPECTED_GROUPS[i];
    const cards = Array.from(group.querySelectorAll(".market-index-card"));
    assert(cards.length === expected.keys.length,
      `${expected.title} 그룹은 ${expected.keys.length}개여야 함 (실제 ${cards.length}개)`);
    cards.forEach(card => {
      assert(card.dataset.kind === expected.kind,
        `${expected.title} 카드 종류가 ${expected.kind} 이어야 함 (실제 ${card.dataset.kind})`);
    });
    const keys = cards.map(card => card.dataset.key);
    expected.keys.forEach(key => {
      assert(keys.includes(key), `${expected.title} 그룹에 ${key} 가 없음`);
    });
  });
});

test("임베드가 차단되는 거래소 실지수 심볼을 쓰지 않는다", async () => {
  const window = await makeWindow();

  await window.renderMarketIndices();

  const source = readFileSync(MARKET_INDICES_JS, "utf8");
  BLOCKED_SYMBOLS.forEach(symbol => {
    assert(!source.includes(`'${symbol}'`) && !source.includes(`"${symbol}"`),
      `${symbol} 은 무료 임베드에서 차트가 표시되지 않는 심볼임`);
  });
});

test("미장·원자재 카드는 TradingView 위젯 스크립트를 심는다", async () => {
  const window = await makeWindow();

  await window.renderMarketIndices();

  const widgetCards = window.document.querySelectorAll('.market-index-card[data-kind="widget"]');
  assert(widgetCards.length === 8, `위젯 카드는 8개여야 함 (실제 ${widgetCards.length}개)`);

  for (const card of widgetCards) {
    const script = card.querySelector(".tradingview-widget-container script");
    assert(script, `${card.dataset.key} 카드에 위젯 스크립트가 없음`);
    assert(script.src === WIDGET_SRC, `${card.dataset.key} 위젯 src 가 다름: ${script.src}`);
    const config = JSON.parse(script.textContent);
    assert(config.symbol === card.dataset.key,
      `${card.dataset.key} 위젯 설정 symbol 이 다름: ${config.symbol}`);
    assert(config.colorTheme === "light", "위젯 테마는 앱과 동일한 light 여야 함");
  }
});

test("국장 카드는 KIS API 를 조회해 값과 차트를 그린다", async () => {
  const requested = [];
  const window = await makeWindow(async (url) => {
    requested.push(url);
    const code = url.endsWith("1001") ? "1001" : "0001";
    return success(indexPayload({
      code,
      name: code === "0001" ? "코스피" : "코스닥",
      current: code === "0001" ? 2650.15 : 870.4,
    }));
  });

  await window.renderMarketIndices();

  assert(requested.some(u => u.includes("/api/market-index/0001")), "코스피 API 를 호출하지 않음");
  assert(requested.some(u => u.includes("/api/market-index/1001")), "코스닥 API 를 호출하지 않음");

  const kospi = window.document.querySelector('.market-index-card[data-key="0001"]');
  assert(kospi.textContent.includes("2,650.15"), `코스피 현재값이 표시되지 않음: ${kospi.textContent}`);
  assert(kospi.textContent.includes("+0.47%"), `코스피 등락률이 표시되지 않음: ${kospi.textContent}`);
  assert(kospi.querySelector("canvas"), "코스피 차트 캔버스가 없음");
  assert(window.__charts.length === 2, `Chart 인스턴스는 2개여야 함 (실제 ${window.__charts.length}개)`);
});

test("스파크라인 색은 등락률 부호를 따른다", async () => {
  const window = await makeWindow(async (url) => success(indexPayload({
    // 60일 추세는 우상향이지만 당일은 하락인 경우
    change: -4.2,
    change_rate: url.endsWith("1001") ? -0.48 : 0.47,
  })));

  await window.renderMarketIndices();

  const colors = window.__charts.map(c => c.config.data.datasets[0].borderColor);
  assert(colors.includes("#ff4757"), "상승 지수는 상승색이어야 함");
  assert(colors.includes("#3742fa"), "하락 지수는 하락색이어야 함");
});

test("국장 스파크라인은 x축에 날짜 눈금을 표시한다", async () => {
  const window = await makeWindow();

  await window.renderMarketIndices();

  const config = window.__charts[0].config;
  assert(config.options.scales.x.display === true, "국장 차트 x축이 표시되지 않음");
  assert(config.data.labels.join(",") === "07/24,07/27",
    `x축 라벨이 MM/DD 형식이어야 함 (실제 ${config.data.labels.join(",")})`);
  assert(config.options.scales.y.display === false, "y축은 계속 숨겨야 함");
});

test("x축 눈금은 조밀해지지 않도록 개수를 제한한다", async () => {
  const points = Array.from({ length: 60 }, (_, i) => ({
    date: `202605${String((i % 28) + 1).padStart(2, "0")}`,
    close: 2600 + i,
  }));
  const window = await makeWindow(async () => success(indexPayload({ points })));

  await window.renderMarketIndices();

  const ticks = window.__charts[0].config.options.scales.x.ticks;
  assert(ticks.autoSkip === true, "x축 눈금 autoSkip 이 꺼져 있음");
  assert(ticks.maxTicksLimit > 1 && ticks.maxTicksLimit <= 6,
    `x축 눈금 개수 제한이 비합리적임 (실제 ${ticks.maxTicksLimit})`);
  assert(ticks.maxRotation === 0, "좁은 카드에서 라벨이 기울면 안 됨");
});

test("국장 API 실패는 카드별 안내로 degrade 한다", async () => {
  const window = await makeWindow(async () => ({ ok: false, status: 503, json: async () => ({}) }));

  await window.renderMarketIndices();

  const kospi = window.document.querySelector('.market-index-card[data-key="0001"]');
  assert(kospi.querySelector(".market-index-error"), "국장 카드에 실패 안내가 없음");
  assert(!kospi.querySelector("canvas"), "실패한 카드에 차트를 그리면 안 됨");
  // 국장이 실패해도 미장/원자재 위젯은 살아 있어야 한다.
  assert(window.document.querySelectorAll('.market-index-card[data-kind="widget"]').length === 8,
    "국장 실패가 위젯 카드까지 없앰");
});

test("국장 응답에 포인트가 없으면 차트를 그리지 않는다", async () => {
  const window = await makeWindow(async () => success(indexPayload({ points: [] })));

  await window.renderMarketIndices();

  assert(window.__charts.length === 0, "포인트가 없는데 Chart 를 생성함");
  const kospi = window.document.querySelector('.market-index-card[data-key="0001"]');
  assert(kospi.textContent.includes("2,650.15"), "포인트가 없어도 현재값은 표시되어야 함");
});

test("위젯 스크립트 로드 실패 시 안내 문구로 대체한다", async () => {
  const window = await makeWindow();
  await window.renderMarketIndices();

  const card = window.document.querySelector('.market-index-card[data-kind="widget"]');
  const script = card.querySelector(".tradingview-widget-container script");
  script.dispatchEvent(new window.Event("error"));

  assert(!card.querySelector(".tradingview-widget-container script"),
    "실패한 위젯 스크립트가 남아 있음");
  const fallback = card.querySelector(".market-index-error");
  assert(fallback && fallback.textContent.includes("불러오지 못"),
    "위젯 로드 실패 안내가 표시되어야 함");
});

test("다시 렌더링해도 그룹과 카드가 중복되지 않는다", async () => {
  const window = await makeWindow();

  await window.renderMarketIndices();
  await window.renderMarketIndices();

  assert(groupsOf(window).length === EXPECTED_GROUPS.length, "재렌더링 후 그룹이 중복됨");
  const total = EXPECTED_GROUPS.reduce((sum, g) => sum + g.keys.length, 0);
  const cards = window.document.querySelectorAll("#market-indices .market-index-card");
  assert(cards.length === total, `재렌더링 후에도 카드는 ${total}개여야 함 (실제 ${cards.length}개)`);
});

test("컨테이너가 없는 화면에서는 아무 일도 하지 않는다", async () => {
  let fetched = false;
  const window = await makeWindow(async () => { fetched = true; return success(indexPayload()); });
  window.document.getElementById("market-indices").remove();

  await window.renderMarketIndices();

  assert(!window.document.querySelector(".market-index-card"),
    "컨테이너가 없으면 카드를 만들면 안 됨");
  assert(!fetched, "컨테이너가 없으면 API 도 호출하면 안 됨");
});

await run();
