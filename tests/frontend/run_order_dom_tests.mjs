/*
 * jsdom 기반 /order 화면 JS 회귀 테스트 — 국내 미체결 조회·취소.
 *
 * 실행: node run_order_dom_tests.mjs  (exit 0 = 전부 통과)
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { applyCommonStubs, test, assert, run } from "./harness.mjs";

const ORDER_JS = resolve(import.meta.dirname, "../../view/web/static/js/order.js");

const SCAFFOLD = `
<span id="status-env" data-mode="paper"></span>
<div id="order-real-banner"></div>
<span id="order-mode-label"></span>
<button class="btn-buy"></button>
<button class="btn-sell"></button>
<div id="order-result"></div>
<div id="unfilled-orders-result"></div>
`;

function makeWindow() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>${SCAFFOLD}</body></html>`, {
    url: "http://localhost/order",
    runScripts: "dangerously",
  });
  const { window } = dom;
  applyCommonStubs(window);
  window.fetchWithTimeout = async () => ({ ok: true, json: async () => ({}) });
  window.alert = () => {};
  window.confirm = () => true;
  window.prompt = () => null;
  const script = window.document.createElement("script");
  script.textContent = readFileSync(ORDER_JS, "utf8");
  window.document.body.appendChild(script);
  return window;
}

const UNFILLED_ROW = {
  odno: "0000012345",
  pdno: "005930",
  prdt_name: "<b>삼성전자</b>",
  sll_buy_dvsn_cd_name: "매수",
  ord_qty: "10",
  tot_ccld_qty: "3",
  psbl_qty: "7",
  ord_unpr: "70000",
  ord_tmd: "091530",
  ord_gno_brno: "91252",
};

test("loadUnfilledOrders 가 미체결 행과 data 속성 취소 버튼을 렌더한다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    assert(url.includes("/api/orders/unfilled"), `예상 밖 URL: ${url}`);
    return { ok: true, json: async () => ({ rt_cd: "0", data: [UNFILLED_ROW] }) };
  };

  await window.loadUnfilledOrders();

  const result = window.document.getElementById("unfilled-orders-result");
  const html = result.innerHTML;
  assert(html.includes("0000012345"), "주문번호 미표시");
  assert(html.includes("70,000"), "지정가 포맷 실패");
  assert(!html.includes("<b>삼성전자</b>"), "종목명이 이스케이프되지 않음");
  const button = result.querySelector("[data-unfilled-cancel]");
  assert(button, "취소 버튼 미표시");
  assert(button.getAttribute("onclick") === null, "외부 주문값을 인라인 onclick에 삽입함");
  assert(button.dataset.orderNo === "0000012345", "주문번호 data 속성 누락");
  assert(button.dataset.orgno === "91252", "주문채번지점번호 data 속성 누락");
});

test("loadUnfilledOrders 가 KIS body(dict의 output)도 처리하고 빈 목록을 표시한다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async () => ({
    ok: true, json: async () => ({ rt_cd: "0", data: { output: [] } }),
  });

  await window.loadUnfilledOrders();

  assert(
    window.document.getElementById("unfilled-orders-result").innerHTML.includes("미체결 없음"),
    "빈 목록 문구 미표시",
  );
});

test("loadUnfilledOrders 가 실패 응답의 메시지를 보여준다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async () => ({
    ok: true, json: async () => ({ rt_cd: "1", msg1: "조회 실패 사유" }),
  });

  await window.loadUnfilledOrders();

  assert(
    window.document.getElementById("unfilled-orders-result").innerHTML.includes("조회 실패 사유"),
    "실패 사유 미표시",
  );
});

test("cancelDomesticOrder 가 취소 API에 올바른 body로 POST하고 목록을 갱신한다", async () => {
  const window = makeWindow();
  const calls = [];
  window.fetchWithTimeout = async (url, options = {}) => {
    calls.push({ url, options });
    if (url.includes("/api/order/cancel")) {
      return { ok: true, json: async () => ({ rt_cd: "0", msg1: "취소 접수" }) };
    }
    return { ok: true, json: async () => ({ rt_cd: "0", data: [] }) };
  };

  await window.cancelDomesticOrder("0000012345", "91252", "005930", 7);

  const cancelCall = calls.find((c) => c.url.includes("/api/order/cancel"));
  assert(cancelCall, "취소 API 미호출");
  assert(cancelCall.options.method === "POST", "POST 가 아님");
  const body = JSON.parse(cancelCall.options.body);
  assert(body.broker_order_no === "0000012345", "주문번호 누락");
  assert(body.order_orgno === "91252", "주문채번지점번호 누락");
  assert(body.real_order_confirmation === null, "paper 모드에 확인 문자열이 들어감");
  assert(calls.some((c) => c.url.includes("/api/orders/unfilled")), "취소 후 목록 미갱신");
});

test("cancelDomesticOrder 가 confirm 취소 시 API를 호출하지 않는다", async () => {
  const window = makeWindow();
  let called = false;
  window.confirm = () => false;
  window.fetchWithTimeout = async () => { called = true; return { ok: true, json: async () => ({}) }; };

  await window.cancelDomesticOrder("0000012345", "91252", "005930", 7);

  assert(!called, "confirm 거절인데 API 호출됨");
});

test("cancelDomesticOrder 가 실전 모드에서 REAL 미입력 시 API를 호출하지 않는다", async () => {
  const window = makeWindow();
  window.document.getElementById("status-env").dataset.mode = "real";
  let called = false;
  window.prompt = () => "nope";
  window.fetchWithTimeout = async () => { called = true; return { ok: true, json: async () => ({}) }; };

  await window.cancelDomesticOrder("0000012345", "91252", "005930", 7);

  assert(!called, "REAL 확인 없이 API 호출됨");
});

test("cancelDomesticOrder 가 실전 모드에서 REAL 입력 시 확인 문자열을 보낸다", async () => {
  const window = makeWindow();
  window.document.getElementById("status-env").dataset.mode = "real";
  window.prompt = () => "REAL";
  let body = null;
  window.fetchWithTimeout = async (url, options = {}) => {
    if (url.includes("/api/order/cancel")) body = JSON.parse(options.body);
    return { ok: true, json: async () => ({ rt_cd: "0", data: [] }) };
  };

  await window.cancelDomesticOrder("0000012345", "91252", "005930", 7);

  assert(body && body.real_order_confirmation === "REAL", "REAL 확인 문자열 누락");
});

await run();
