/*
 * jsdom 기반 candle_chart.js 회귀 테스트 (Minervini Stage 배경 밴드).
 *
 * 목적: 차트 일자별 Stage 배경 밴드 기능을 "문자열 존재"가 아니라 실제 렌더 경로
 * (loadAndRenderStockChart → renderStockChart)로 검증한다. Chart.js 는 jsdom 에
 * 없으므로 생성자를 스텁해 config 를 캡처하고, 캡처한 stageBandPlugin 의
 * beforeDatasetsDraw 를 가짜 chart 로 호출해 fillRect 행위를 확인한다.
 *
 * 실행: node run_candle_chart_dom_tests.mjs  (exit 0 = 전부 통과)
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { test, assert, run } from "./harness.mjs";

const CANDLE_JS = resolve(import.meta.dirname, "../../view/web/static/js/candle_chart.js");

// 길이 n 의 OHLCV + 지표(루프가 접근하는 배열) 생성. minervini_stage 는 옵션.
function makeChartJson(n, { withStage = true } = {}) {
  const ohlcv = [];
  for (let i = 0; i < n; i++) {
    const close = 5000 + i * 30;
    ohlcv.push({ date: `2023${String(i).padStart(4, "0")}`, open: close, high: close * 1.01, low: close * 0.95, close, volume: 1000 + i });
  }
  const nulls = () => new Array(n).fill(null);
  const indicators = { ma5: nulls(), ma10: nulls(), ma20: nulls(), ma60: nulls(), ma120: nulls(), bb: nulls() };
  if (withStage) {
    // 앞 199 미계산(0) + 이후 상승 구간을 Stage 2 로, 마지막 10개를 Stage 4 로.
    const stage = new Array(n).fill(0);
    for (let i = 199; i < n; i++) stage[i] = 2;
    for (let i = n - 10; i < n; i++) stage[i] = 4;
    indicators.minervini_stage = stage;
  }
  return { rt_cd: "0", data: { ohlcv, indicators } };
}

function makeWindow() {
  const dom = new JSDOM(
    `<!DOCTYPE html><html><body>
      <div class="card" id="stock-chart-card" style="display:none;">
        <div id="chart-controls-area"></div>
        <canvas id="stockChart"></canvas>
      </div>
    </body></html>`,
    { url: "http://localhost/stock", runScripts: "dangerously" }
  );
  const { window } = dom;

  // 캡처용 + 스텁
  window.__lastChartConfig = null;
  window.performance = window.performance || { now: () => 0 };
  window.HTMLCanvasElement.prototype.getContext = function () { return {}; };
  class FakeChart {
    constructor(ctx, config) {
      window.__lastChartConfig = config;
      this.data = config.data;
      this.config = config;
    }
    destroy() {}
    update() {}
    getDatasetMeta() { return { data: [] }; }
  }
  FakeChart.defaults = { plugins: { legend: { labels: { generateLabels: () => [] } } } };
  window.Chart = FakeChart;
  window.EventSource = class { constructor() {} close() {} };

  const script = window.document.createElement("script");
  script.textContent = readFileSync(CANDLE_JS, "utf8");
  window.document.body.appendChild(script);
  return window;
}

// stageBandPlugin 호출용 가짜 chart (fillRect/fillText 기록).
function makeFakeChart(len) {
  const calls = { fillRect: [], fillStyles: [] };
  const ctx = {
    save() {}, restore() {},
    set fillStyle(v) { calls.fillStyles.push(v); }, get fillStyle() { return ""; },
    fillRect(...a) { calls.fillRect.push(a); },
    fillText() {}, set font(_) {}, set textAlign(_) {},
  };
  const scale = (lo, hi) => ({ top: 0, bottom: 300, left: 0, right: 600, getPixelForValue: (v) => (v / Math.max(1, len - 1)) * 600 });
  return {
    calls,
    chart: {
      ctx,
      chartArea: { left: 0, right: 600, top: 0, bottom: 400 },
      scales: { x: scale(), y: scale() },
    },
  };
}


test("renderStockChart 가 stageBandPlugin 을 캔들보다 먼저 그리도록 등록한다", async () => {
  const window = makeWindow();
  window.fetch = async () => ({ json: async () => makeChartJson(260) });

  await window.loadAndRenderStockChart("005930");

  const cfg = window.__lastChartConfig;
  assert(cfg, "Chart 가 생성되지 않음");
  const ids = cfg.plugins.map((p) => p.id);
  assert(ids.includes("minerviniStageBand"), "stageBandPlugin 미등록");
  // 캔들 데이터셋보다 먼저 그려져야 배경이 됨 (plugins 배열 선두 + beforeDatasetsDraw)
  const plugin = cfg.plugins.find((p) => p.id === "minerviniStageBand");
  assert(typeof plugin.beforeDatasetsDraw === "function", "beforeDatasetsDraw 훅 없음");
});

test("stageBandPlugin 이 Stage 구간별 배경 사각형을 그린다", async () => {
  const window = makeWindow();
  window.fetch = async () => ({ json: async () => makeChartJson(260) });
  await window.loadAndRenderStockChart("005930");

  const plugin = window.__lastChartConfig.plugins.find((p) => p.id === "minerviniStageBand");
  // 3M 슬라이스(66봉)에는 Stage2 와 마지막 Stage4 구간이 포함됨 → 최소 2개 밴드.
  const fake = makeFakeChart(66);
  plugin.beforeDatasetsDraw(fake.chart);

  assert(fake.calls.fillRect.length >= 2, `밴드 사각형이 그려지지 않음 (fillRect=${fake.calls.fillRect.length})`);
  const greenish = fake.calls.fillStyles.some((s) => typeof s === "string" && s.includes("0, 200, 80"));
  const reddish = fake.calls.fillStyles.some((s) => typeof s === "string" && s.includes("255, 60, 60"));
  assert(greenish, "Stage2(초록) 밴드 색상 누락");
  assert(reddish, "Stage4(빨강) 밴드 색상 누락");
});

test("minervini_stage 가 없으면 밴드를 그리지 않는다 (no-op, 회귀 안전)", async () => {
  const window = makeWindow();
  window.fetch = async () => ({ json: async () => makeChartJson(260, { withStage: false }) });
  await window.loadAndRenderStockChart("005930");

  const plugin = window.__lastChartConfig.plugins.find((p) => p.id === "minerviniStageBand");
  const fake = makeFakeChart(66);
  plugin.beforeDatasetsDraw(fake.chart);

  assert(fake.calls.fillRect.length === 0, "stage 데이터 없는데 밴드가 그려짐");
});

await run();
