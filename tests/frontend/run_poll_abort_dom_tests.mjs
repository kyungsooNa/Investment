/*
 * Polling fetch AbortError 회귀 테스트.
 *
 * 실행: node run_poll_abort_dom_tests.mjs
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";

const COMMON_JS = resolve(import.meta.dirname, "../../view/web/static/js/common.js");
const SYSTEM_JS = resolve(import.meta.dirname, "../../view/web/static/js/system.js");
const KILL_SWITCH_JS = resolve(import.meta.dirname, "../../view/web/static/js/kill_switch.js");

function makeAbortError() {
  try {
    return new DOMException("signal is aborted without reason", "AbortError");
  } catch (_) {
    const err = new Error("signal is aborted without reason");
    err.name = "AbortError";
    return err;
  }
}

function makeWindow(pathname = "/") {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>
    <span id="status-time"></span>
    <span id="status-market"></span>
    <span id="status-env"></span>
    <div id="background-tasks-body"></div>
    <div id="operations-summary"></div>
    <div id="sub-table-body"></div>
    <button id="ks-badge"><span id="ks-badge-text"></span></button>
    <input id="input-max-order-amount">
    <input id="input-max-position-pct">
  </body></html>`, {
    url: `http://localhost${pathname}`,
    runScripts: "outside-only",
  });

  const { window } = dom;
  window.Paginator = null;
  window.alert = () => {};
  window.confirm = () => true;
  window.prompt = () => "REAL";
  return window;
}

async function withConsoleErrorSpy(window, fn) {
  const calls = [];
  const original = window.console.error;
  window.console.error = (...args) => calls.push(args);
  try {
    await fn();
  } finally {
    window.console.error = original;
  }
  return calls;
}

const tests = [];
const test = (name, fn) => tests.push({ name, fn });
function assert(cond, msg) { if (!cond) throw new Error(msg); }

test("common updateStatus ignores fetch AbortError", async () => {
  const window = makeWindow("/");
  window.fetch = async () => { throw makeAbortError(); };
  window.eval(readFileSync(COMMON_JS, "utf8"));

  const errors = await withConsoleErrorSpy(window, () => window.updateStatus());

  assert(errors.length === 0, "AbortError should not be logged by updateStatus");
});

test("header polling fetches do not force abort signals", async () => {
  const window = makeWindow("/");
  const calls = [];
  window.fetch = async (url, options = {}) => {
    calls.push({ url, hasSignal: !!options.signal });
    return {
      ok: true,
      json: async () => url.includes("kill-switch")
        ? { is_tripped: false }
        : { current_time: "09:00:00", market_open: true, env_type: "모의투자", is_paper_trading: true },
    };
  };
  window.eval(readFileSync(COMMON_JS, "utf8"));
  window.eval(readFileSync(KILL_SWITCH_JS, "utf8"));

  await window.updateStatus();
  await window.loadKillSwitchStatus();

  assert(calls.some(call => call.url === "/api/status"), "status polling should fetch");
  assert(calls.some(call => call.url === "/api/kill-switch/status"), "kill switch polling should fetch");
  assert(calls.every(call => call.hasSignal === false), "header polling should not pass AbortController signals");
});

test("system polling updates ignore fetch AbortError", async () => {
  const window = makeWindow("/system");
  window.fetch = async () => { throw makeAbortError(); };
  window.eval(readFileSync(COMMON_JS, "utf8"));
  window.eval(readFileSync(SYSTEM_JS, "utf8"));

  const errors = await withConsoleErrorSpy(window, async () => {
    await window.updateCacheStatus();
    await window.updateBackgroundStatus();
    await window.updateOperationsStatus();
    await window.updateSubscriptionStatus();
  });

  assert(errors.length === 0, "AbortError should not be logged by system polling updates");
});

test("system polling fetches do not force abort signals", async () => {
  const window = makeWindow("/system");
  const calls = [];
  window.fetch = async (url, options = {}) => {
    calls.push({ url, hasSignal: !!options.signal });
    return {
      ok: true,
      json: async () => ({ success: true, data: [] }),
    };
  };
  window.eval(readFileSync(COMMON_JS, "utf8"));
  window.eval(readFileSync(SYSTEM_JS, "utf8"));

  await window.updateCacheStatus();
  await window.updateBackgroundStatus();
  await window.updateOperationsStatus();
  await window.updateSubscriptionStatus();

  assert(calls.length >= 4, "system polling should fetch status endpoints");
  assert(calls.every(call => call.hasSignal === false), "system polling should not pass AbortController signals");
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
