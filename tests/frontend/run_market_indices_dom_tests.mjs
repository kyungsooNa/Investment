/*
 * jsdom 기반 홈 화면 지수 패널 회귀 테스트.
 *
 * 국장은 KIS API(/api/market-index/*) + Chart.js, 미장/원자재는 TradingView 차트 포함 위젯으로
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
  { title: "가상자산", kind: "widget", keys: ["BITSTAMP:BTCUSD", "BITSTAMP:ETHUSD"] },
  // 국채 금리 지표(TVC:US02Y/US10Y)는 임베드가 차단되고 FRED 공개데이터는 열린다(2026-08-26 실측).
  { title: "채권", kind: "widget", keys: ["FRED:DGS10", "FRED:DGS2"] },
];

const BLOCKED_SYMBOLS = [
  "KRX:KOSPI", "KRX:KOSDAQ", "SP:SPX", "NASDAQ:NDX",
  "NASDAQ:SOX", "CBOE:VIX", "TVC:DXY", "TVC:US10Y", "TVC:US02Y",
];

const WIDGET_SRC =
  "https://s3.tradingview.com/external-embedding/embed-widget-symbol-overview.js";

const SCAFFOLD = `<div id="market-indices"></div>`;

function flowPayload(overrides) {
  return Object.assign({
    code: "0001",
    investors: { individual: -31912, foreign: 28357, institution: 5277 },
    breadth: { up: 380, upper_limit: 3, unchanged: 53, down: 477, lower_limit: 0 },
  }, overrides || {});
}

// 차트와 수급은 서로 다른 엔드포인트라 기본 스텁은 URL 로 갈라준다.
function defaultFetch(chartData) {
  return async (url) => success(url.includes("/flow") ? flowPayload() : (chartData || indexPayload()));
}

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
  window.fetchWithTimeout = fetchWithTimeout || defaultFetch();
  // Chart.js 는 CDN 로드라 jsdom 에서는 생성 호출만 기록한다.
  window.HTMLCanvasElement.prototype.getContext = () => ({});
  window.currentCharts = [];
  window.__charts = [];
  window.Chart = function (ctx, config) {
    const record = { ctx, config, destroyed: false };
    window.__charts.push(record);
    this.destroy = () => { record.destroyed = true; };
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

test("국장·미장·원자재·가상자산·채권 그룹이 순서대로 렌더링된다", async () => {
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

test("미장·원자재·가상자산·채권 카드는 TradingView 차트 포함 위젯 스크립트를 심는다", async () => {
  const window = await makeWindow();

  await window.renderMarketIndices();

  const widgetCards = window.document.querySelectorAll('.market-index-card[data-kind="widget"]');
  assert(widgetCards.length === 12, `위젯 카드는 12개여야 함 (실제 ${widgetCards.length}개)`);

  for (const card of widgetCards) {
    const script = card.querySelector(".tradingview-widget-container script");
    assert(script, `${card.dataset.key} 카드에 위젯 스크립트가 없음`);
    assert(script.src === WIDGET_SRC, `${card.dataset.key} 위젯 src 가 다름: ${script.src}`);
    const config = JSON.parse(script.textContent);
    assert(Array.isArray(config.symbols), `${card.dataset.key} 위젯 설정 symbols 가 없음`);
    assert(config.symbols.flat().some(item => String(item).includes(card.dataset.key)),
      `${card.dataset.key} 위젯 설정에 심볼이 없음: ${JSON.stringify(config.symbols)}`);
    assert(config.chartOnly === false, "위젯은 값과 그래프를 함께 보여야 함");
    assert(config.hideDateRanges === true, "좁은 카드에서는 위젯 기간 버튼을 숨겨야 함");
    assert(config.colorTheme === "light", "위젯 테마는 앱과 동일한 light 여야 함");
  }
});

test("국장 카드는 KIS API 를 조회해 값과 차트를 그린다", async () => {
  const requested = [];
  const window = await makeWindow(async (url) => {
    requested.push(url);
    const code = url.includes("/1001") ? "1001" : "0001";
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
    change_rate: url.includes("/1001") ? -0.48 : 0.47,
  })));

  await window.renderMarketIndices();

  const colors = window.__charts.map(c => c.config.data.datasets[0].borderColor);
  assert(colors.includes("#ff4757"), "상승 지수는 상승색이어야 함");
  assert(colors.includes("#3742fa"), "하락 지수는 하락색이어야 함");
});

// 기준선(1D 는 전일 종가 = 당일 시가 기준선) 위/아래 색 분리 검증용 페이로드.
// 09:00 에 기준선 아래로 출발했다가 09:20 에 기준선 위로 올라선 하루다.
function crossingPayload() {
  return indexPayload({
    period: "1D",
    current: 2652.0,
    change: 2.0,
    change_rate: 0.08,
    points: [
      { close: 2650.0, prev: true },
      { date: "20260729", time: "090000", close: 2640.1 },
      { date: "20260729", time: "091000", close: 2645.0 },
      { date: "20260729", time: "092000", close: 2652.0 },
    ],
  });
}

// segment.borderColor 는 Chart.js 가 구간마다 부르는 콜백이라 직접 호출해 색을 얻는다.
function segmentColorAt(chart, index) {
  const dataset = chart.config.data.datasets[0];
  const values = dataset.data;
  return dataset.segment.borderColor({
    p0: { parsed: { y: values[index - 1] } },
    p1: { parsed: { y: values[index] } },
  });
}

test("스파크라인은 기준선 위 구간을 빨강, 아래 구간을 파랑으로 그린다", async () => {
  const window = await makeWindow(defaultFetch(crossingPayload()));

  await window.renderMarketIndices();

  const chart = window.__charts[0];
  assert(segmentColorAt(chart, 1) === "#3742fa", "기준선 아래로 내려간 구간은 파랑이어야 함");
  assert(segmentColorAt(chart, 2) === "#3742fa", "기준선 아래에 머무는 구간은 파랑이어야 함");
  assert(segmentColorAt(chart, 3) === "#ff4757", "기준선 위로 올라선 구간은 빨강이어야 함");
});

test("스파크라인은 기준선을 경계로 위아래 영역을 다른 색으로 채운다", async () => {
  const window = await makeWindow(defaultFetch(crossingPayload()));

  await window.renderMarketIndices();

  const { fill } = window.__charts[0].config.data.datasets[0];
  assert(fill && fill.target && fill.target.value === 2650.0,
    `채움 기준선은 첫 점(전일 종가)이어야 함 (실제 ${JSON.stringify(fill)})`);
  assert(fill.above.includes("255, 71, 87"), `기준선 위는 상승색 영역이어야 함 (실제 ${fill.above})`);
  assert(fill.below.includes("55, 66, 250"), `기준선 아래는 하락색 영역이어야 함 (실제 ${fill.below})`);
});

test("일봉 기간도 구간 첫 종가를 기준선으로 삼는다", async () => {
  const window = await makeWindow(defaultFetch(indexPayload({
    points: [
      { date: "20260724", close: 2637.85 },
      { date: "20260725", close: 2630.0 },
      { date: "20260727", close: 2650.15 },
    ],
  })));

  await window.renderMarketIndices();

  const chart = window.__charts[0];
  assert(chart.config.data.datasets[0].fill.target.value === 2637.85,
    "일봉 차트 기준선은 구간 첫 종가여야 함");
  assert(segmentColorAt(chart, 1) === "#3742fa", "구간 첫 종가보다 낮은 구간은 파랑이어야 함");
  assert(segmentColorAt(chart, 2) === "#ff4757", "구간 첫 종가보다 높은 구간은 빨강이어야 함");
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

// 눈금 콜백이 실제로 그리는 라벨만 추린다 (빈 문자열은 숨긴 눈금).
function visibleTicks(chart) {
  const { labels } = chart.config.data;
  const { callback } = chart.config.options.scales.x.ticks;
  return labels.map((_, i) => callback(i, i, labels)).filter(label => label !== "");
}

test("x축 눈금은 조밀해지지 않도록 개수를 제한한다", async () => {
  const points = Array.from({ length: 60 }, (_, i) => ({
    date: `202605${String((i % 28) + 1).padStart(2, "0")}`,
    close: 2600 + i,
  }));
  const window = await makeWindow(defaultFetch(indexPayload({ points })));

  await window.renderMarketIndices();

  const chart = window.__charts[0];
  const ticks = chart.config.options.scales.x.ticks;
  assert(ticks.maxRotation === 0, "좁은 카드에서 라벨이 기울면 안 됨");
  // 눈금 선택을 직접 하므로 Chart.js 의 autoSkip 이 겹쳐 잘라내면 안 된다.
  assert(ticks.autoSkip === false, "직접 고른 눈금을 autoSkip 이 또 솎아냄");
  const shown = visibleTicks(chart);
  assert(shown.length > 3 && shown.length <= 7,
    `x축 라벨 개수가 비합리적임 (실제 ${shown.length}개: ${shown})`);
});

test("x축 마지막 눈금은 항상 표시한다", async () => {
  // 전일 기준점 + 09:00~15:30 10분봉 40개 = 41점 (정규장 하루치)
  const points = [{ close: 6345.53, prev: true }];
  for (let i = 0; i < 40; i++) {
    const minutes = 9 * 60 + i * 10;
    const hh = String(Math.floor(minutes / 60)).padStart(2, "0");
    const mm = String(minutes % 60).padStart(2, "0");
    points.push({ date: "20260812", time: `${hh}${mm}00`, close: 6400 + i });
  }
  const window = await makeWindow(defaultFetch(indexPayload({ period: "1D", points })));

  await window.renderMarketIndices();

  const chart = window.__charts[0];
  const labels = chart.config.data.labels;
  const { callback } = chart.config.options.scales.x.ticks;
  assert(labels[labels.length - 1] === "15:30",
    `마지막 라벨이 15:30 이어야 함 (실제 ${labels[labels.length - 1]})`);
  // 기존에는 앞에서부터 솎아내느라 장 마감 시각이 잘려나갔다.
  assert(callback(labels.length - 1, labels.length - 1, labels) === "15:30",
    "장 마감 눈금이 표시되지 않음");
  const shown = visibleTicks(chart);
  assert(shown.length >= 5, `하루치 차트 눈금이 너무 적음 (실제 ${shown.length}개: ${shown})`);
});

test("점이 눈금 수보다 적으면 모든 라벨을 표시한다", async () => {
  const window = await makeWindow(defaultFetch(minutePayload()));

  await window.renderMarketIndices();

  const shown = visibleTicks(window.__charts[0]);
  assert(shown.join(",") === "09:00,09:10,09:20",
    `점이 적으면 전부 표시해야 함 (실제 ${shown})`);
});

function minutePayload() {
  return indexPayload({
    period: "1D",
    points: [
      { date: "20260729", time: "090000", close: 2640.1 },
      { date: "20260729", time: "091000", close: 2645.0 },
      { date: "20260729", time: "092000", close: 2650.15 },
    ],
  });
}

test("국장 카드는 기간 선택 버튼을 갖고 기본은 1D 로 조회한다", async () => {
  const requested = [];
  const window = await makeWindow(async (url) => {
    requested.push(url);
    return success(minutePayload());
  });

  await window.renderMarketIndices();

  const kospi = window.document.querySelector('.market-index-card[data-key="0001"]');
  const buttons = Array.from(kospi.querySelectorAll(".market-index-period"));
  assert(buttons.map(b => b.dataset.period).join(",") === "1D,1W,1M,1Y",
    `기간 버튼이 1D,1W,1M,1Y 여야 함 (실제 ${buttons.map(b => b.dataset.period)})`);
  assert(buttons[0].classList.contains("active"), "기본 기간 1D 가 선택 표시되어야 함");
  assert(requested.filter(u => !u.includes("/flow")).every(u => u.includes("period=1D")),
    `기본 조회가 period=1D 여야 함 (실제 ${requested})`);
  // 미장 위젯 카드에는 기간 버튼을 붙이지 않는다(위젯이 자체 제공).
  const widget = window.document.querySelector('.market-index-card[data-kind="widget"]');
  assert(!widget.querySelector(".market-index-period"), "위젯 카드에 기간 버튼이 붙음");
});

test("1D 는 x축에 분봉 시각을 표시한다", async () => {
  const window = await makeWindow(async () => success(minutePayload()));

  await window.renderMarketIndices();

  const labels = window.__charts[0].config.data.labels;
  assert(labels.join(",") === "09:00,09:10,09:20",
    `1D x축 라벨이 HH:MM 이어야 함 (실제 ${labels.join(",")})`);
});

test("1D 첫 점이 전일 종가면 x축에 '전일' 로 표시한다", async () => {
  // 개장 직후엔 분봉이 1개뿐이라 서버가 전일 종가를 기준점으로 앞에 붙인다.
  const window = await makeWindow(async () => success(indexPayload({
    period: "1D",
    points: [
      { close: 2637.85, prev: true },
      { date: "20260729", time: "090000", close: 2650.15 },
    ],
  })));

  await window.renderMarketIndices();

  const labels = window.__charts[0].config.data.labels;
  assert(labels.join(",") === "전일,09:00",
    `전일 기준점 라벨이 '전일,09:00' 이어야 함 (실제 ${labels.join(",")})`);
  // 점이 2개가 되므로 선이 실제로 그려져야 한다.
  assert(window.__charts[0].config.data.datasets[0].data.length === 2,
    "전일 기준점을 포함해 2개 점이 그려져야 함");
});

test("기간 버튼을 누르면 그 기간으로 다시 조회하고 차트를 교체한다", async () => {
  const requested = [];
  const window = await makeWindow(async (url) => {
    requested.push(url);
    return success(url.includes("period=1D") ? minutePayload() : indexPayload({ period: "1M" }));
  });
  await window.renderMarketIndices();
  const kospi = window.document.querySelector('.market-index-card[data-key="0001"]');
  const before = window.__charts.length;

  const monthly = kospi.querySelector('.market-index-period[data-period="1M"]');
  await window.selectMarketIndexPeriod(kospi, "0001", "1M");

  assert(requested.some(u => u.includes("/api/market-index/0001?period=1M")),
    `1M 재조회를 하지 않음 (실제 ${requested})`);
  assert(monthly.classList.contains("active"), "선택한 기간 버튼이 활성화되어야 함");
  assert(!kospi.querySelector('.market-index-period[data-period="1D"]').classList.contains("active"),
    "이전 기간 버튼이 활성 상태로 남음");
  // 이전 차트는 파기하고 캔버스도 하나만 남아야 한다.
  // (두 국장 카드가 동시에 렌더되므로 인덱스가 아니라 파기된 개수로 확인한다)
  const destroyed = window.__charts.filter(chart => chart.destroyed);
  assert(destroyed.length === 1, `교체한 카드의 이전 차트 1개만 파기되어야 함 (실제 ${destroyed.length}개)`);
  assert(window.currentCharts.length === before,
    `currentCharts 가 누적되면 안 됨 (before ${before}, after ${window.currentCharts.length})`);
  assert(kospi.querySelectorAll("canvas").length === 1,
    `카드에 캔버스가 하나만 있어야 함 (실제 ${kospi.querySelectorAll("canvas").length}개)`);
  assert(window.__charts[before].config.data.labels.join(",") === "07/24,07/27",
    "1M 로 바꾸면 x축이 날짜 라벨이어야 함");
});

test("기간 버튼 클릭도 같은 재조회 경로를 탄다", async () => {
  const requested = [];
  const window = await makeWindow(async (url) => {
    requested.push(url);
    return success(url.includes("period=1D") ? minutePayload() : indexPayload({ period: "1Y" }));
  });
  await window.renderMarketIndices();
  const kospi = window.document.querySelector('.market-index-card[data-key="0001"]');

  kospi.querySelector('.market-index-period[data-period="1Y"]').click();
  await new Promise(resolve => setTimeout(resolve, 0));

  assert(requested.some(u => u.includes("period=1Y")), `클릭으로 1Y 조회를 못함 (실제 ${requested})`);
});

test("기간 재조회가 실패하면 그 카드만 안내로 degrade 한다", async () => {
  let first = true;
  const window = await makeWindow(async () => {
    if (first) { first = false; return success(minutePayload()); }
    return { ok: false, status: 503, json: async () => ({}) };
  });
  await window.renderMarketIndices();
  const kospi = window.document.querySelector('.market-index-card[data-key="0001"]');

  await window.selectMarketIndexPeriod(kospi, "0001", "1M");

  assert(kospi.querySelector(".market-index-error"), "재조회 실패 안내가 없음");
  assert(!kospi.querySelector("canvas"), "재조회 실패 시 이전 차트가 남음");
  // 기간 버튼은 남아 있어야 다시 시도할 수 있다.
  assert(kospi.querySelectorAll(".market-index-period").length === 4, "실패 후 기간 버튼이 사라짐");
});

test("국장 API 실패는 카드별 안내로 degrade 한다", async () => {
  const window = await makeWindow(async () => ({ ok: false, status: 503, json: async () => ({}) }));

  await window.renderMarketIndices();

  const kospi = window.document.querySelector('.market-index-card[data-key="0001"]');
  assert(kospi.querySelector(".market-index-error"), "국장 카드에 실패 안내가 없음");
  assert(!kospi.querySelector("canvas"), "실패한 카드에 차트를 그리면 안 됨");
  // 국장이 실패해도 미장/원자재/가상자산/채권 위젯은 살아 있어야 한다.
  assert(window.document.querySelectorAll('.market-index-card[data-kind="widget"]').length === 12,
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

// ── 수급 (투자자 순매수 + 등락 종목수) ──────────────────────────────────

function flowOf(window, code = "0001") {
  return window.document.querySelector(`.market-index-card[data-key="${code}"] .market-index-flow`);
}

test("국장 카드는 투자자 순매수와 등락 종목수를 함께 보여준다", async () => {
  const requested = [];
  const window = await makeWindow(async (url) => {
    requested.push(url);
    return success(url.includes("/flow") ? flowPayload() : minutePayload());
  });

  await window.renderMarketIndices();

  assert(requested.some(u => u.includes("/api/market-index/0001/flow")), "코스피 수급 API 를 호출하지 않음");
  assert(requested.some(u => u.includes("/api/market-index/1001/flow")), "코스닥 수급 API 를 호출하지 않음");

  const flow = flowOf(window);
  assert(flow, "수급 영역이 없음");
  const text = flow.textContent;
  ["개인", "외국인", "기관", "상승", "보합", "하락"].forEach(label => {
    assert(text.includes(label), `수급에 '${label}' 이 없음: ${text}`);
  });
  assert(text.includes("-31,912"), `개인 순매수가 없음: ${text}`);
  assert(text.includes("+28,357"), `외국인 순매수가 없음: ${text}`);
  assert(text.includes("+5,277"), `기관 순매수가 없음: ${text}`);
  // 상·하한 종목수는 네이버처럼 괄호로 덧붙인다.
  assert(text.includes("380(3)"), `상승 종목수가 없음: ${text}`);
  assert(text.includes("477(0)"), `하락 종목수가 없음: ${text}`);
  assert(text.includes("53"), `보합 종목수가 없음: ${text}`);
});

test("순매수 부호와 등락은 상승/하락 색을 따른다", async () => {
  const window = await makeWindow();

  await window.renderMarketIndices();

  const flow = flowOf(window);
  const buy = Array.from(flow.querySelectorAll(".text-red")).map(el => el.textContent);
  const sell = Array.from(flow.querySelectorAll(".text-blue")).map(el => el.textContent);
  assert(buy.some(t => t.includes("28,357")), `순매수(+)는 상승색이어야 함 (실제 ${buy})`);
  assert(sell.some(t => t.includes("31,912")), `순매도(-)는 하락색이어야 함 (실제 ${sell})`);
  assert(buy.some(t => t.includes("380")), `상승 종목수는 상승색이어야 함 (실제 ${buy})`);
  assert(sell.some(t => t.includes("477")), `하락 종목수는 하락색이어야 함 (실제 ${sell})`);
});

test("수급 조회가 실패하면 조용히 숨긴다", async () => {
  const window = await makeWindow(async (url) => {
    if (url.includes("/flow")) return { ok: false, status: 500, json: async () => ({}) };
    return success(minutePayload());
  });

  await window.renderMarketIndices();

  assert(!flowOf(window), "수급 조회 실패인데 영역이 남음");
  const kospi = window.document.querySelector('.market-index-card[data-key="0001"]');
  assert(!kospi.querySelector(".market-index-error"), "수급 실패로 카드에 에러 문구가 뜸");
  assert(kospi.querySelector("canvas"), "수급 실패가 차트까지 지움");
});

test("모의투자처럼 한쪽만 막히면 나오는 쪽만 보여준다", async () => {
  const window = await makeWindow(async (url) => success(
    url.includes("/flow") ? flowPayload({ investors: null }) : minutePayload()
  ));

  await window.renderMarketIndices();

  const flow = flowOf(window);
  assert(flow, "등락 종목수는 살아있는데 수급 영역이 없음");
  assert(!flow.textContent.includes("개인"), `투자자 수급이 없는데 표시됨: ${flow.textContent}`);
  assert(flow.textContent.includes("380(3)"), `등락 종목수가 없음: ${flow.textContent}`);
});

test("기간을 바꿔도 수급은 다시 조회하지 않는다", async () => {
  const requested = [];
  const window = await makeWindow(async (url) => {
    requested.push(url);
    return success(url.includes("/flow") ? flowPayload() : minutePayload());
  });
  await window.renderMarketIndices();
  const kospi = window.document.querySelector('.market-index-card[data-key="0001"]');
  const flowCallsBefore = requested.filter(u => u.includes("/flow")).length;

  await window.selectMarketIndexPeriod(kospi, "0001", "1M");

  assert(requested.filter(u => u.includes("/flow")).length === flowCallsBefore,
    "기간 전환이 수급을 재조회함 (기간과 무관한 값)");
  // 기간 전환이 body 를 비워도 수급 영역은 남아 있어야 한다.
  assert(flowOf(window), "기간 전환이 수급 영역을 지움");
});

await run();
