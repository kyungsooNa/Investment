/*
 * jsdom 기반 /virtual 성과 차트 회귀 테스트.
 *
 * 목적: ALL 성과 차트가 다시 단일 과밀 차트로 퇴행하지 않도록,
 * 전략별 미니 차트 생성과 Chart.js 인스턴스 정리를 DOM 행위로 검증한다.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";

const VIRTUAL_CHART_JS = resolve(import.meta.dirname, "../../view/web/static/js/virtual_chart.js");

const SCAFFOLD = `
<div id="section-virtual" class="section">
  <div id="virtualYieldChartGrid" class="virtual-chart-grid">
    <div class="virtual-chart-empty">차트 로딩 중...</div>
  </div>
</div>
`;

const SAMPLE_CHART_DATA = {
  histories: {
    ALL: [
      { date: "2026-06-01", return_rate: 0 },
      { date: "2026-06-02", return_rate: 1.5 },
    ],
    StrategyA: [
      { date: "2026-06-01", return_rate: 0 },
      { date: "2026-06-02", return_rate: 2.1 },
    ],
    StrategyB: [
      { date: "2026-06-01", return_rate: 0 },
      { date: "2026-06-02", return_rate: -1.2 },
    ],
  },
  benchmarks: {
    KOSPI200: [
      { date: "2026-06-01", return_rate: 0 },
      { date: "2026-06-02", return_rate: 0.3 },
    ],
    KOSDAQ150: [
      { date: "2026-06-01", return_rate: 0 },
      { date: "2026-06-02", return_rate: -0.4 },
    ],
  },
};

function makeWindow(fetchPayload = SAMPLE_CHART_DATA) {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>${SCAFFOLD}</body></html>`, {
    url: "http://localhost/virtual",
    runScripts: "dangerously",
  });
  const { window } = dom;
  window.HTMLCanvasElement.prototype.getContext = () => ({});
  window.currentCharts = [];
  window.__charts = [];
  window.fetch = async () => ({
    ok: true,
    json: async () => fetchPayload,
  });
  window.Chart = class MockChart {
    static defaults = {
      plugins: {
        legend: {
          labels: {
            generateLabels: chart => chart.data.datasets.map((dataset, datasetIndex) => ({
              datasetIndex,
              text: dataset.label,
            })),
          },
        },
      },
    };

    constructor(ctx, config) {
      this.ctx = ctx;
      this.config = config;
      this.data = config.data;
      this.options = config.options;
      this.destroyed = false;
      window.__charts.push(this);
    }

    update() {}

    destroy() {
      this.destroyed = true;
    }
  };

  const script = window.document.createElement("script");
  script.textContent = readFileSync(VIRTUAL_CHART_JS, "utf8");
  window.document.body.appendChild(script);
  return window;
}

const tests = [];
const test = (name, fn) => tests.push({ name, fn });
function assert(cond, msg) { if (!cond) throw new Error(msg); }

test("ALL 선택 시 ALL 집계선이 아니라 전략별 미니 차트를 생성한다", async () => {
  const window = makeWindow();

  await window.refreshVirtualChart(["ALL"]);

  const cards = [...window.document.querySelectorAll(".virtual-mini-chart-card")];
  const titles = cards.map(card => card.querySelector("h3").textContent);
  assert(cards.length === 2, `전략별 차트 2개가 필요함: ${cards.length}`);
  assert(titles.includes("StrategyA"), "StrategyA 차트 누락");
  assert(titles.includes("StrategyB"), "StrategyB 차트 누락");
  assert(!titles.includes("전체(ALL)"), "ALL 집계 차트가 표시되면 안 됨");
  assert(window.__charts.length === 2, "Chart 인스턴스는 전략별 1개씩 생성되어야 함");

  const firstLabels = window.__charts[0].data.datasets.map(dataset => dataset.label);
  assert(firstLabels.length === 3, "각 차트는 전략선과 벤치마크 2개만 가져야 함");
  assert(firstLabels.includes("KOSPI200 %"), "KOSPI200 벤치마크 누락");
  assert(firstLabels.includes("KOSDAQ150 %"), "KOSDAQ150 벤치마크 누락");
});

test("전략 선택 변경 시 이전 미니 차트를 정리하고 선택 전략만 렌더링한다", async () => {
  const window = makeWindow();

  await window.refreshVirtualChart(["ALL"]);
  const oldCharts = window.__charts.slice();
  await window.refreshVirtualChart(["StrategyB"]);

  assert(oldCharts.every(chart => chart.destroyed), "선택 변경 전 Chart 인스턴스가 정리되어야 함");
  const cards = [...window.document.querySelectorAll(".virtual-mini-chart-card")];
  assert(cards.length === 1, "선택 전략 차트만 남아야 함");
  assert(cards[0].querySelector("h3").textContent === "StrategyB", "StrategyB 차트만 표시되어야 함");
  assert(window.currentCharts.length === 1, "전역 Chart 레지스트리에는 현재 차트만 남아야 함");
});

test("히스토리가 없으면 차트 대신 빈 상태 메시지를 표시한다", async () => {
  const window = makeWindow({ histories: {}, benchmarks: {} });

  await window.refreshVirtualChart(["ALL"]);

  assert(window.document.querySelectorAll(".virtual-mini-chart-card").length === 0, "빈 데이터에서는 차트가 없어야 함");
  assert(
    window.document.querySelector(".virtual-chart-empty").textContent.includes("표시할 차트 데이터"),
    "빈 데이터 메시지가 표시되어야 함",
  );
});

test("BUY실패/SELL실패는 제외하고 나머지는 최신 수익률 내림차순으로 정렬한다", async () => {
  const window = makeWindow({
    histories: {
      ALL: [
        { date: "2026-06-01", return_rate: 0 },
        { date: "2026-06-02", return_rate: 1.5 },
      ],
      "BUY실패": [
        { date: "2026-06-01", return_rate: 0 },
        { date: "2026-06-02", return_rate: 0 },
      ],
      "SELL실패": [
        { date: "2026-06-01", return_rate: 0 },
        { date: "2026-06-02", return_rate: 0 },
      ],
      StrategyA: [
        { date: "2026-06-01", return_rate: 0 },
        { date: "2026-06-02", return_rate: 2.1 },
      ],
      StrategyB: [
        { date: "2026-06-01", return_rate: 0 },
        { date: "2026-06-02", return_rate: -1.2 },
      ],
    },
    benchmarks: SAMPLE_CHART_DATA.benchmarks,
  });

  await window.refreshVirtualChart(["ALL"]);

  const cards = [...window.document.querySelectorAll(".virtual-mini-chart-card")];
  const titles = cards.map(card => card.querySelector("h3").textContent);
  assert(!titles.includes("BUY실패"), "BUY실패 카드가 표시되면 안 됨");
  assert(!titles.includes("SELL실패"), "SELL실패 카드가 표시되면 안 됨");
  assert(
    titles.indexOf("StrategyA") < titles.indexOf("StrategyB"),
    `수익률 내림차순 정렬 필요: ${titles.join(", ")}`,
  );
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
