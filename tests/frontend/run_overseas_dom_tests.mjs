/*
 * jsdom 기반 /overseas 화면 JS 회귀 테스트.
 *
 * 대표 회귀: get_overseas_dailyprice 는 KIS 원본 body(dict: output1/output2)를
 * 그대로 반환하므로 json.data 는 배열이 아니다. loadOverseasChart 가 이를
 * 배열로 가정하면 일봉 테이블이 항상 빈다. 또한 output2 행 키는 KIS 해외
 * 스키마(xymd/clos/tvol)라 국내 키 매핑으로는 종가/거래량이 표시되지 않는다.
 *
 * 실행: node run_overseas_dom_tests.mjs  (exit 0 = 전부 통과)
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";

const OVERSEAS_JS = process.env.OVERSEAS_JS_PATH
  ? resolve(process.env.OVERSEAS_JS_PATH)
  : resolve(import.meta.dirname, "../../view/web/static/js/overseas.js");

const SCAFFOLD = `
<span id="status-env" data-mode="paper"></span>
<p id="overseas-status"></p>
<button id="overseas-tab-overview" data-overseas-tab="overview" class="sub-tab-btn active"></button>
<button id="overseas-tab-marketcap" data-overseas-tab="marketcap" class="sub-tab-btn"></button>
<button id="overseas-tab-orders" data-overseas-tab="orders" class="sub-tab-btn"></button>
<section id="overseas-panel-overview"></section>
<section id="overseas-panel-marketcap" hidden><div id="overseas-marketcap-result"></div></section>
<section id="overseas-panel-orders" hidden></section>
<input id="overseas-symbol" type="text">
<select id="overseas-exchange"><option value="NASD">NASDAQ</option></select>
<div id="overseas-quote-result"></div>
<div id="overseas-chart-result"></div>
<div id="overseas-balance-result"></div>
<input id="overseas-order-symbol" type="text">
<select id="overseas-order-exchange"><option value="NASD">NASDAQ</option></select>
<input id="overseas-order-qty" type="number">
<input id="overseas-order-price" type="number">
<div id="overseas-order-result"></div>
<div id="overseas-orders-result"></div>
`;

function makeWindow() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>${SCAFFOLD}</body></html>`, {
    url: "http://localhost/overseas",
    runScripts: "dangerously",
  });
  const { window } = dom;
  window.showLoading = (el, msg) => { if (el) el.innerHTML = `<p class="loading">${msg}</p>`; };
  window.fetchWithTimeout = async () => ({ ok: true, json: async () => ({}) });
  window.alert = () => {};

  const script = window.document.createElement("script");
  script.textContent = readFileSync(OVERSEAS_JS, "utf8");
  window.document.body.appendChild(script);
  return window;
}

const tests = [];
const test = (name, fn) => tests.push({ name, fn });
function assert(cond, msg) { if (!cond) throw new Error(msg); }

test("loadOverseasChart 가 KIS body(dict의 output2)에서 일봉 행을 렌더한다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["domestic", "overseas_us"] }) };
    }
    if (url.includes("/api/overseas/chart/")) {
      return { ok: true, json: async () => ({
        rt_cd: "0",
        // KIS 해외 일봉 원본 형태: 전체 body dict(output1 + output2 배열)
        data: {
          output1: { rsym: "DNASAAPL" },
          output2: [
            { xymd: "20260101", clos: "190.50", tvol: "1000" },
            { xymd: "20260102", clos: "192.00", tvol: "2000" },
          ],
        },
      }) };
    }
    return { ok: false, json: async () => ({}) };
  };
  window.document.getElementById("overseas-symbol").value = "AAPL";

  await window.loadOverseasChart();

  const html = window.document.getElementById("overseas-chart-result").innerHTML;
  assert(html.includes("20260102"), "회귀: 일봉 일자 행이 렌더되지 않음(output2 미추출)");
  assert(html.includes("$192.00"), "회귀: 종가(clos) 매핑 실패");
  assert(html.includes("2,000"), "회귀: 거래량(tvol) 매핑 실패");
  assert(!html.includes("데이터 없음"), "회귀: 데이터 있는데 빈 테이블 표시");
});

test("loadOverseasChart 가 이미 배열인 data 도 처리한다(하위호환)", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    if (url.includes("/api/overseas/chart/")) {
      return { ok: true, json: async () => ({
        rt_cd: "0",
        data: [{ xymd: "20260103", clos: "195.00", tvol: "3000" }],
      }) };
    }
    return { ok: false, json: async () => ({}) };
  };
  window.document.getElementById("overseas-symbol").value = "AAPL";

  await window.loadOverseasChart();

  const html = window.document.getElementById("overseas-chart-result").innerHTML;
  assert(html.includes("20260103"), "배열 형태 data 미처리");
  assert(html.includes("$195.00"), "배열 형태 종가 미표시");
});

test("loadOverseasOrders 가 미체결 주문을 행으로 렌더하고 취소 버튼을 단다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    if (url.includes("/api/overseas/orders")) {
      return { ok: true, json: async () => ({
        rt_cd: "0",
        // KIS 미체결(inquire_ccnl, ccld_nccs_dvsn=02) 원본: data.output 배열
        data: { output: [
          { odno: "39870", pdno: "AAPL", prdt_name: "애플", sll_buy_dvsn_cd_name: "매수",
            ft_ord_qty: "1", nccs_qty: "1", ft_ord_unpr3: "197.00000000", ovrs_excg_cd: "NASD" },
        ] },
      }) };
    }
    return { ok: false, json: async () => ({}) };
  };

  await window.loadOverseasOrders();

  const html = window.document.getElementById("overseas-orders-result").innerHTML;
  assert(html.includes("39870"), "회귀: 주문번호 미표시");
  assert(html.includes("AAPL"), "회귀: 심볼 미표시");
  assert(html.includes("$197.00"), "회귀: 지정가(ft_ord_unpr3) 매핑 실패");
  const cancelButton = window.document.querySelector("[data-overseas-cancel]");
  assert(cancelButton, "회귀: 취소 버튼 미표시");
  assert(cancelButton.getAttribute("onclick") === null, "회귀: 외부 주문값을 인라인 onclick에 삽입함");
  assert(!html.includes("미체결 없음"), "회귀: 데이터 있는데 빈 표시");
});

test("setOverseasTab 이 시가총액 패널을 열고 주요 미국 대형주를 로드한다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    if (url.includes("/api/overseas/market-cap")) {
      return { ok: true, json: async () => ({
        rt_cd: "0",
        data: {
          fx_rate: 1400,
          items: [{ symbol: "NVDA", name: "NVIDIA", currency: "USD", market_cap_usd: 3_000_000_000_000, market_cap_krw: 4_200_000_000_000_000 }],
        },
      }) };
    }
    return { ok: false, json: async () => ({}) };
  };

  await window.setOverseasTab("marketcap");

  assert(window.document.getElementById("overseas-panel-overview").hidden === true,
    "개요 패널은 숨겨져야 함");
  assert(window.document.getElementById("overseas-panel-marketcap").hidden === false,
    "시가총액 패널은 표시되어야 함");
  const text = window.document.getElementById("overseas-marketcap-result").textContent;
  assert(text.includes("NVDA"), "주요 미국 대형주 심볼이 표시되어야 함");
  assert(text.includes("$3.00T"), "USD 시가총액이 축약 표기로 표시되어야 함");
});

test("미체결 주문의 외부 문자열은 HTML이나 인라인 스크립트로 해석되지 않는다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    if (url.includes("/api/overseas/orders")) {
      return { ok: true, json: async () => ({
        rt_cd: "0",
        data: { output: [{
          odno: '1\"><img id="injected-order" src=x>',
          pdno: '<img id="injected-symbol" src=x>',
          ovrs_excg_cd: "NASD",
          sll_buy_dvsn_cd_name: "매수",
          ft_ord_qty: "1",
          nccs_qty: "1",
          ft_ord_unpr3: "197.00",
        }] },
      }) };
    }
    return { ok: false, json: async () => ({}) };
  };

  await window.loadOverseasOrders();

  const result = window.document.getElementById("overseas-orders-result");
  assert(!result.querySelector("#injected-order, #injected-symbol"), "외부 주문값이 HTML로 해석됨");
  assert(result.querySelector("[data-overseas-cancel]"), "취소 버튼이 data 속성으로 연결되어야 함");
});

test("cancelOverseasOrder 가 취소 API에 올바른 body로 POST하고 목록을 갱신한다", async () => {
  const window = makeWindow();
  window.confirm = () => true;
  let postBody = null;
  let ordersReloaded = 0;
  window.fetchWithTimeout = async (url, opts) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    if (url.includes("/api/overseas/order/cancel")) {
      postBody = JSON.parse(opts.body);
      return { ok: true, json: async () => ({ rt_cd: "0", data: {} }) };
    }
    if (url.includes("/api/overseas/orders")) {
      ordersReloaded += 1;
      return { ok: true, json: async () => ({ rt_cd: "0", data: { output: [] } }) };
    }
    return { ok: false, json: async () => ({}) };
  };

  await window.cancelOverseasOrder("39870", "AAPL", "NASD", 1);

  assert(postBody, "회귀: 취소 API가 호출되지 않음");
  assert(postBody.original_order_no === "39870", "회귀: 주문번호 전달 오류");
  assert(postBody.symbol === "AAPL", "회귀: 심볼 전달 오류");
  assert(postBody.exchange === "NASD", "회귀: 거래소 전달 오류");
  assert(postBody.qty === 1, "회귀: 수량 전달 오류");
  assert(ordersReloaded >= 1, "회귀: 취소 후 미체결 목록 갱신 안 함");
});

test("cancelOverseasOrder 가 paper 모드에서 confirm 취소 시 API를 호출하지 않는다", async () => {
  const window = makeWindow();
  window.confirm = () => false;
  let called = false;
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/overseas/order/cancel")) called = true;
    return { ok: true, json: async () => ({ rt_cd: "0", data: {} }) };
  };

  await window.cancelOverseasOrder("39870", "AAPL", "NASD", 1);

  assert(!called, "회귀: confirm 거부에도 취소 API가 호출됨");
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
