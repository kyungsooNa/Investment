/*
 * jsdom 기반 scheduler.js 회귀 테스트.
 *
 * 미국장 페이지는 일반 StrategyScheduler가 아니라 백그라운드 태스크 상태를 보여준다.
 * 화면이 국내 자동전략 스케줄러처럼 보이면 운영자가 시작/정지 버튼을 눌러도 503만 보게 된다.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { applyCommonStubs, test, assert, run } from "./harness.mjs";

const SCHEDULER_JS = process.env.SCHEDULER_JS_PATH
  ? resolve(process.env.SCHEDULER_JS_PATH)
  : resolve(import.meta.dirname, "../../view/web/static/js/scheduler.js");

const SCAFFOLD = `
<span id="scheduler-status-badge" class="badge closed">정지</span>
<span id="scheduler-info"></span>
<button id="scheduler-start-btn">전체 시작</button>
<button id="scheduler-stop-btn">전체 정지</button>
<div id="scheduler-strategies"></div>
<div id="scheduler-market-tasks"></div>
<div id="scheduler-history-tabs"></div>
<table><tbody id="scheduler-history-body"></tbody></table>
<div id="scheduler-history-pagination"></div>
`;

function makeWindow() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>${SCAFFOLD}</body></html>`, {
    url: "http://localhost/overseas-scheduler",
    runScripts: "dangerously",
  });
  const { window } = dom;
  applyCommonStubs(window);
  window.ensureTableInCard = () => {};
  window.Paginator = null;
  window.SCHEDULER_MARKET = "overseas_us";
  window.fetch = async () => ({ ok: true, json: async () => ({}) });
  window.alert = () => {};

  const script = window.document.createElement("script");
  script.textContent = readFileSync(SCHEDULER_JS, "utf8");
  window.document.body.appendChild(script);
  return window;
}

test("미국장 태스크 기반 스케줄러는 시작/정지 버튼을 숨기고 잠금 상태를 설명한다", async () => {
  const window = makeWindow();

  window.renderSchedulerStatus({
    market: "overseas_us",
    market_label: "미국장",
    running: false,
    has_scheduler: false,
    scheduler_kind: "market_tasks",
    can_control_scheduler: false,
    status_note: "미국장은 백그라운드 전략 태스크 상태만 표시합니다. 실주문 자동매매는 잠금 상태입니다.",
    strategies: [],
    market_tasks: [{
      name: "overseas_intraday",
      display_name: "미국장 장중 전략",
      market: "overseas_us",
      market_label: "미국장",
      mode: "paper",
      live_trading: false,
      state: "running",
      running: true,
      progress: { watch_count: 3 },
    }],
  });

  const text = window.document.body.textContent;
  assert(text.includes("태스크 기반"), `태스크 기반 표면임을 보여줘야 함 (실제 "${text}")`);
  assert(text.includes("실주문 자동매매는 잠금"), `자동주문 잠금 안내가 있어야 함 (실제 "${text}")`);
  assert(window.document.getElementById("scheduler-start-btn").style.display === "none",
    "StrategyScheduler가 없으면 전체 시작 버튼을 숨겨야 함");
  assert(window.document.getElementById("scheduler-stop-btn").style.display === "none",
    "StrategyScheduler가 없으면 전체 정지 버튼을 숨겨야 함");
});

test("한국장 일반 스케줄러는 기존 제어 버튼을 유지한다", async () => {
  const window = makeWindow();
  window.SCHEDULER_MARKET = "domestic";

  window.renderSchedulerStatus({
    market: "domestic",
    market_label: "한국장",
    running: true,
    has_scheduler: true,
    scheduler_kind: "strategy_scheduler",
    can_control_scheduler: true,
    strategies: [{ name: "LarryWilliamsVBO", display_name: "VBO", current_holds: 0, max_positions: 3, holdings: [] }],
    market_tasks: [],
  });

  assert(window.document.getElementById("scheduler-start-btn").style.display === "",
    "한국장 StrategyScheduler는 시작 버튼을 유지해야 함");
  assert(window.document.getElementById("scheduler-stop-btn").style.display === "",
    "한국장 StrategyScheduler는 정지 버튼을 유지해야 함");
});

test("장중 태스크가 idle 이어도 마지막 폴링 패스의 사유를 보여준다", async () => {
  const window = makeWindow();

  window.renderSchedulerStatus({
    market: "overseas_us",
    market_label: "미국장",
    running: false,
    has_scheduler: false,
    scheduler_kind: "market_tasks",
    can_control_scheduler: false,
    status_note: "미국장은 백그라운드 전략 태스크 상태만 표시합니다.",
    strategies: [],
    market_tasks: [{
      name: "overseas_intraday",
      display_name: "미국장 장중 전략",
      market: "overseas_us",
      market_label: "미국장",
      mode: "paper",
      live_trading: false,
      state: "idle",
      running: false,
      progress: {
        watch_count: 0,
        phase: "polling",
        phase_detail: "감시 종목이 0개입니다 — 후보 조회 실패이거나 당일 셋업 조건을 만족한 종목이 없습니다.",
      },
    }],
  });

  const text = window.document.body.textContent;
  assert(text.includes("감시 0개"), `감시 종목 수를 보여줘야 함 (실제 "${text}")`);
  assert(text.includes("감시 종목이 0개입니다"),
    `idle 사유(phase_detail)를 보여줘야 함 (실제 "${text}")`);
});

await run();
