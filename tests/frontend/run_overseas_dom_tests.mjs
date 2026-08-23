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
import { applyCommonStubs, test, assert, run } from "./harness.mjs";

const OVERSEAS_JS = process.env.OVERSEAS_JS_PATH
  ? resolve(process.env.OVERSEAS_JS_PATH)
  : resolve(import.meta.dirname, "../../view/web/static/js/overseas.js");

const SCAFFOLD = `
<span id="status-env" data-mode="paper"></span>
<p id="overseas-status"></p>
<button id="overseas-tab-overview" data-overseas-tab="overview" class="sub-tab-btn active"></button>
<button id="overseas-tab-marketcap" data-overseas-tab="marketcap" class="sub-tab-btn"></button>
<button id="overseas-tab-favorite" data-overseas-tab="favorite" class="sub-tab-btn"></button>
<button id="overseas-tab-orders" data-overseas-tab="orders" class="sub-tab-btn"></button>
<section id="overseas-panel-overview"></section>
<section id="overseas-panel-marketcap" hidden><div id="overseas-marketcap-result"></div></section>
<section id="overseas-panel-favorite" hidden>
  <input id="overseas-fav-symbol" type="text">
  <ul id="overseas-fav-autocomplete-list"></ul>
  <span id="overseas-favorite-updated">최근 업데이트: --</span>
  <table><tbody id="overseas-favorite-body"></tbody></table>
</section>
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
<div id="overseas-trades-result"></div>
<div id="overseas-reconcile-result"></div>
`;

function makeWindow() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>${SCAFFOLD}</body></html>`, {
    url: "http://localhost/overseas",
    runScripts: "dangerously",
  });
  const { window } = dom;
  applyCommonStubs(window);
  window.fetchWithTimeout = async () => ({ ok: true, json: async () => ({}) });
  window.alert = () => {};
  window.showToast = () => {};

  const script = window.document.createElement("script");
  script.textContent = readFileSync(OVERSEAS_JS, "utf8");
  window.document.body.appendChild(script);
  return window;
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}
const flush = () => new Promise((r) => setTimeout(r, 0));

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
  assert(text.includes("4,200조"), "원화 환산이 공용 formatTradingValue(조/억) 표기로 표시되어야 함");
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

test("환율 정보가 없으면 'USD/KRW 0' 대신 안내 문구를 표시한다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    if (url.includes("/api/overseas/market-cap")) {
      return { ok: true, json: async () => ({
        rt_cd: "0",
        // 야후 FX 조회 실패 시 백엔드 fetch_usdkrw() 는 None → JSON null
        data: { fx_rate: null, items: [{ symbol: "AAPL", name: "Apple", market_cap_usd: 3_000_000_000_000, market_cap_krw: null }] },
      }) };
    }
    return { ok: false, json: async () => ({}) };
  };

  await window.loadOverseasMarketCap();

  const text = window.document.getElementById("overseas-marketcap-result").textContent;
  assert(text.includes("환율 정보 없음"), "회귀: 환율 null 시 안내 문구가 표시되어야 함");
  assert(!text.includes("USD/KRW"), "회귀: 환율 null 이 'USD/KRW 0' 으로 표시됨");
});

test("loadOverseasBalance 가 주문 패널의 거래소 선택값(overseas-order-exchange)을 사용한다", async () => {
  const window = makeWindow();
  // 개요 탭이 아닌 보유·주문 탭에서 잔고를 조회하므로, 같은 패널의 주문 거래소 셀렉트를 기준으로 삼아야 한다.
  const orderSel = window.document.getElementById("overseas-order-exchange");
  orderSel.insertAdjacentHTML("beforeend", '<option value="NYSE">NYSE</option>');
  orderSel.value = "NYSE";
  // 숨겨진 개요 탭의 overseas-exchange 는 기본값(NASD) 유지 — 이 값이 쓰이면 회귀.
  let balanceUrl = "";
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    if (url.includes("/api/overseas/balance")) {
      balanceUrl = url;
      return { ok: true, json: async () => ({ rt_cd: "0", data: { output1: [] } }) };
    }
    return { ok: false, json: async () => ({}) };
  };

  await window.loadOverseasBalance();

  assert(balanceUrl.includes("exchange=NYSE"), "회귀: 잔고 조회가 숨겨진 개요 탭 거래소를 사용함");
});

test("늦게 끝난 이전 시가총액 요청이 최신 결과를 덮어쓰지 않는다", async () => {
  const window = makeWindow();
  const pending = [];
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    if (url.includes("/api/overseas/market-cap")) {
      const request = deferred();
      pending.push(request);
      return request.promise;
    }
    return { ok: false, json: async () => ({}) };
  };

  const first = window.loadOverseasMarketCap();
  const second = window.loadOverseasMarketCap();
  await flush();
  assert(pending.length === 2, "두 요청이 모두 market-cap 조회까지 진행되어야 함");

  // 최신(second) 요청을 먼저 완료
  pending[1].resolve({ ok: true, json: async () => ({
    rt_cd: "0", data: { fx_rate: 1400, items: [{ symbol: "NVDA", name: "NVIDIA", market_cap_usd: 3e12, market_cap_krw: 4.2e15 }] },
  }) });
  await second;
  // 이전(first) 요청을 나중에 완료 — 최신 결과를 덮어쓰면 안 됨
  pending[0].resolve({ ok: true, json: async () => ({
    rt_cd: "0", data: { fx_rate: 1400, items: [{ symbol: "AAPL", name: "Apple", market_cap_usd: 2e12, market_cap_krw: 2.8e15 }] },
  }) });
  await first;

  const text = window.document.getElementById("overseas-marketcap-result").textContent;
  assert(text.includes("NVDA"), "최신 시가총액 결과(NVDA)가 표시되어야 함");
  assert(!text.includes("AAPL"), "회귀: 늦게 끝난 이전 요청(AAPL)이 최신 결과를 덮어씀");
});

test("setOverseasTab 전환 시 가용성을 재확인해 버튼 상태를 갱신한다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    return { ok: false, json: async () => ({}) };
  };
  // jsdom DOMContentLoaded init 이 먼저 발동할 수 있으므로 소진한 뒤 상태를 리셋 —
  // 이후 상태 변화는 오직 setOverseasTab 의 가용성 재확인에서만 발생해야 한다.
  await flush();
  const btn = window.document.createElement("button");
  btn.setAttribute("data-overseas-required", "");
  btn.disabled = true;
  window.document.body.appendChild(btn);
  window.document.getElementById("overseas-status").textContent = "";

  await window.setOverseasTab("orders");
  await flush();

  assert(btn.disabled === false, "회귀: 탭 전환 시 가용성 재확인으로 버튼이 활성화되지 않음");
  const status = window.document.getElementById("overseas-status").textContent;
  assert(status === "미국주식 기능이 활성화되어 있습니다.", "회귀: 상태 문구가 갱신되지 않음");
});

test("즐겨찾기 탭 전환 시 미국장 즐겨찾기 목록을 렌더한다", async () => {
  const window = makeWindow();
  const requested = [];
  window.fetchWithTimeout = async (url) => {
    requested.push(url);
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    if (url.includes("/api/favorite")) {
      return { ok: true, json: async () => ([
        { code: "AAPL", name: "Apple Inc.", exchange: "NASD", price: 190.5, rate: 1.23 },
      ]) };
    }
    return { ok: false, json: async () => ({}) };
  };

  await window.setOverseasTab("favorite");
  await flush();

  assert(
    requested.some((url) => url.includes("/api/favorite?market=overseas_us")),
    "회귀: 즐겨찾기 조회가 market=overseas_us 로 호출되지 않음",
  );
  const body = window.document.getElementById("overseas-favorite-body").textContent;
  assert(body.includes("AAPL"), "심볼이 표시되어야 함");
  assert(body.includes("Apple Inc."), "종목명이 표시되어야 함");
  assert(body.includes("$190.50"), "현재가가 USD 로 표시되어야 함");
  assert(body.includes("+1.23%"), "등락률이 표시되어야 함");
});

test("즐겨찾기 목록이 비면 안내 문구를 보여준다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    return { ok: true, json: async () => ([]) };
  };

  await window.loadOverseasFavorites();
  await flush();

  const body = window.document.getElementById("overseas-favorite-body").textContent;
  assert(body.includes("등록된 미국장 즐겨찾기가 없습니다."), "빈 목록 안내가 표시되어야 함");
});

test("심볼 추가는 대문자로 정규화해 POST 하고 목록을 새로고침한다", async () => {
  const window = makeWindow();
  const calls = [];
  window.fetchWithTimeout = async (url, options = {}) => {
    calls.push({ url, method: options.method || "GET" });
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    if (options.method === "POST") {
      return { ok: true, json: async () => ({ success: true, added: true, market: "overseas_us" }) };
    }
    return { ok: true, json: async () => ([
      { code: "TSLA", name: "Tesla", exchange: "NASD", price: 250, rate: -0.5 },
    ]) };
  };

  window.document.getElementById("overseas-fav-symbol").value = " tsla ";
  await window.addOverseasFavorite();
  await flush();

  assert(
    calls.some((c) => c.method === "POST" && c.url === "/api/favorite/TSLA?market=overseas_us"),
    `회귀: 심볼 정규화/마켓 파라미터가 잘못됨 (${JSON.stringify(calls)})`,
  );
  assert(
    window.document.getElementById("overseas-favorite-body").textContent.includes("TSLA"),
    "추가 후 목록이 새로고침되어야 함",
  );
  assert(window.document.getElementById("overseas-fav-symbol").value === "", "입력값이 비워져야 함");
});

test("삭제 버튼은 해당 심볼만 DELETE 하고 행을 제거한다", async () => {
  const window = makeWindow();
  const calls = [];
  window.fetchWithTimeout = async (url, options = {}) => {
    calls.push({ url, method: options.method || "GET" });
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    if (options.method === "DELETE") {
      return { ok: true, json: async () => ({ success: true, removed: true }) };
    }
    return { ok: true, json: async () => ([
      { code: "AAPL", name: "Apple Inc.", exchange: "NASD", price: 190.5, rate: 1.23 },
      { code: "MSFT", name: "Microsoft", exchange: "NASD", price: 410, rate: 0.4 },
    ]) };
  };

  await window.loadOverseasFavorites();
  await flush();

  const removeButtons = window.document.querySelectorAll("[data-overseas-fav-remove]");
  assert(removeButtons.length === 2, "행마다 삭제 버튼이 있어야 함");
  removeButtons[0].click();
  await flush();

  assert(
    calls.some((c) => c.method === "DELETE" && c.url === "/api/favorite/AAPL?market=overseas_us"),
    `회귀: 삭제 요청 URL 이 잘못됨 (${JSON.stringify(calls)})`,
  );
  const body = window.document.getElementById("overseas-favorite-body").textContent;
  assert(!body.includes("AAPL"), "삭제한 행이 사라져야 함");
  assert(body.includes("MSFT"), "다른 행은 남아야 함");
});

// 즐겨찾기는 열어둔 채로 시세를 지켜보는 화면이라 스스로 다시 조회한다. 다만 국내와 달리
// 실시간 스트림이 없어 심볼 수만큼 해외 시세 API 를 부르므로, 보고 있을 때만 돌아야 한다.

function favoriteAutoRefreshWindow(pages) {
  const window = makeWindow();
  const calls = [];
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    calls.push(url);
    const page = pages[Math.min(calls.length - 1, pages.length - 1)];
    if (page instanceof Error) throw page;
    return { ok: true, status: 200, json: async () => page };
  };
  return { window, calls };
}

// initOverseasPage 는 실전 배너 타이머(3초)도 건다 — 주기로 즐겨찾기 타이머만 골라낸다.
function captureIntervals(window) {
  const entries = [];
  const cleared = [];
  window.setInterval = (fn, ms) => {
    entries.push({ fn, ms });
    return entries.length;
  };
  window.clearInterval = (id) => { cleared.push(id); };
  return {
    entries,
    cleared,
    favorite: () => [...entries].reverse().find(entry => entry.ms === 60000),
  };
}

function setPageVisible(window, visible) {
  Object.defineProperty(window.document, "hidden", { value: !visible, configurable: true });
}

const APPLE = [{ code: "AAPL", name: "Apple Inc.", exchange: "NASD", price: 190.5, rate: 1.23 }];
const MSFT = [{ code: "MSFT", name: "Microsoft", exchange: "NASD", price: 410, rate: 0.4 }];

test("즐겨찾기 목록을 그리면 최근 업데이트 시각을 표기한다", async () => {
  const { window } = favoriteAutoRefreshWindow([APPLE]);

  await window.loadOverseasFavorites();
  await flush();

  const label = window.document.getElementById("overseas-favorite-updated").textContent;
  assert(/최근 업데이트: .*\d/.test(label), `갱신 시각이 표기돼야 함 (실제 "${label}")`);
  assert(/\b60\b/.test(label), `자동 갱신 주기(초)도 함께 알려야 함 (실제 "${label}")`);
});

test("즐겨찾기 탭을 보고 있으면 주기마다 조용히 다시 조회한다", async () => {
  const { window, calls } = favoriteAutoRefreshWindow([APPLE, MSFT]);
  const timers = captureIntervals(window);
  setPageVisible(window, true);

  window.initOverseasPage();
  await window.setOverseasTab("favorite");
  await flush();

  const timer = timers.favorite();
  assert(timer, "즐겨찾기 자동 갱신 주기는 60초여야 함");

  const before = calls.length;
  await timer.fn();

  assert(calls.length === before + 1, `주기마다 목록을 다시 받아야 함 (실제 ${calls.length - before}회)`);
  const body = window.document.getElementById("overseas-favorite-body");
  assert(body.textContent.includes("MSFT"), "새로 받은 목록으로 갈아끼워야 함");
  assert(!body.innerHTML.includes("로딩 중"), "자동 갱신이 로딩 문구로 화면을 깜빡이면 안 됨");
});

test("다른 탭을 보고 있으면 즐겨찾기를 되묻지 않는다", async () => {
  const { window, calls } = favoriteAutoRefreshWindow([APPLE]);
  const timers = captureIntervals(window);
  setPageVisible(window, true);

  window.initOverseasPage();
  await window.setOverseasTab("favorite");
  await window.setOverseasTab("overview");
  await flush();

  const before = calls.length;
  await timers.favorite().fn();

  assert(calls.length === before, "안 보는 탭 때문에 심볼 수만큼 해외 시세를 부르면 안 됨");
});

test("창이 백그라운드면 즐겨찾기 자동 갱신을 건너뛴다", async () => {
  const { window, calls } = favoriteAutoRefreshWindow([APPLE]);
  const timers = captureIntervals(window);
  setPageVisible(window, false);

  window.initOverseasPage();
  await window.setOverseasTab("favorite");
  await flush();

  const before = calls.length;
  await timers.favorite().fn();

  assert(calls.length === before, "안 보는 창 때문에 계속 조회하면 안 됨");
});

test("즐겨찾기 자동 갱신 실패는 보고 있던 목록을 지우지 않는다", async () => {
  const { window } = favoriteAutoRefreshWindow([APPLE, new Error("network")]);
  const timers = captureIntervals(window);
  setPageVisible(window, true);

  window.initOverseasPage();
  await window.setOverseasTab("favorite");
  await flush();
  const stamp = window.document.getElementById("overseas-favorite-updated").textContent;

  await timers.favorite().fn();

  const body = window.document.getElementById("overseas-favorite-body").textContent;
  assert(body.includes("AAPL"), "한 번 실패했다고 보고 있던 시세를 지우면 안 됨");
  assert(window.document.getElementById("overseas-favorite-updated").textContent === stamp,
    "갱신 시각이 멈춰 있는 것으로 실패를 알린다");
});

// USD 거래 원장 — 원화 원장(/api/virtual/*)과 분리돼 있어 성과 요약을 볼 화면이 여기뿐이다.

function tradesResponse(summary, trades) {
  return {
    ok: true,
    json: async () => ({ rt_cd: "0", data: { summary, trades } }),
  };
}

const SAMPLE_SUMMARY = {
  total_trades: 2, sold_trades: 1, win_rate: 100.0, avg_return: 9.48, canceled_trades: 0,
};

function tradeRow(overrides) {
  return {
    id: 1, symbol: "AAPL", exchange: "NASD", currency: "USD",
    buy_date: "2026-08-13 10:00:00", buy_price: 190.0, qty: 3,
    sell_date: null, sell_price: null, return_rate: 0.0,
    status: "HOLD", reason: "", source: "manual", order_no: "0001234",
    ...overrides,
  };
}

test("loadOverseasTrades 가 성과 요약과 거래 행을 렌더한다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    return tradesResponse(SAMPLE_SUMMARY, [
      tradeRow({}),
      tradeRow({ id: 2, symbol: "MSFT", status: "SOLD", sell_price: 210.0, return_rate: 9.48 }),
    ]);
  };

  await window.loadOverseasTrades();

  const text = window.document.getElementById("overseas-trades-result").textContent;
  assert(text.includes("9.48"), "평균 수익률이 보여야 함");
  assert(text.includes("100"), "승률이 보여야 함");
  assert(text.includes("AAPL") && text.includes("MSFT"), "거래 행이 보여야 함");
});

test("취소(CANCELED) lot 은 보유와 구분되게 표시한다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    return tradesResponse(
      { ...SAMPLE_SUMMARY, total_trades: 1, canceled_trades: 1 },
      [tradeRow({ status: "CANCELED", reason: "fill_reconcile: 미체결" })],
    );
  };

  await window.loadOverseasTrades();

  const text = window.document.getElementById("overseas-trades-result").textContent;
  assert(text.includes("취소"), `취소 상태가 드러나야 함 (실제 "${text.slice(0, 200)}")`);
});

test("거래 기록이 비면 안내 문구를 보여준다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    return tradesResponse(
      { total_trades: 0, sold_trades: 0, win_rate: 0.0, avg_return: 0.0, canceled_trades: 0 }, [],
    );
  };

  await window.loadOverseasTrades();

  const text = window.document.getElementById("overseas-trades-result").textContent;
  assert(text.includes("없"), `빈 상태 안내가 있어야 함 (실제 "${text.slice(0, 200)}")`);
});

test("원장의 외부 문자열은 HTML 로 해석되지 않는다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    return tradesResponse(SAMPLE_SUMMARY, [
      tradeRow({ symbol: "<img src=x onerror=alert(1)>" }),
    ]);
  };

  await window.loadOverseasTrades();

  const result = window.document.getElementById("overseas-trades-result");
  assert(result.querySelector("img") === null, "주입된 태그가 엘리먼트로 살아나면 안 됨");
  assert(result.textContent.includes("<img"), "문자열 자체는 그대로 보여야 함");
});

test("보유·주문 탭을 열면 USD 원장을 함께 불러온다", async () => {
  const window = makeWindow();
  const calls = [];
  window.fetchWithTimeout = async (url) => {
    calls.push(url);
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    return tradesResponse(SAMPLE_SUMMARY, [tradeRow({})]);
  };

  await window.setOverseasTab("orders");
  await flush();

  assert(calls.some((url) => url.startsWith("/api/overseas/trades")),
    `탭을 열면 원장을 조회해야 함 (실제 ${JSON.stringify(calls)})`);
});

// 체결 대사 실행 UI — 원장은 주문 접수 시점 기록이라 미체결이 섞인다. 대사 구간·거래소를
// 손으로 넣게 하면 구간 밖 lot 이 조용히 판정불가로 빠지므로 원장 HOLD lot 에서 끌어낸다.

function reconcileResponse(counts, diffs, applied = false) {
  return {
    ok: true,
    json: async () => ({
      rt_cd: "0",
      data: { applied, checked: diffs.length, counts, diffs },
    }),
  };
}

const NO_DIFF_COUNTS = { ok: 1, partial: 0, unfilled: 0, unknown: 0 };

function reconcileWindow(holds, onReconcile) {
  const window = makeWindow();
  const calls = [];
  window.confirm = () => true;
  window.fetchWithTimeout = async (url, options = {}) => {
    calls.push(url);
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    if (url.startsWith("/api/overseas/trades/reconcile")) {
      return onReconcile ? onReconcile(url, options) : reconcileResponse(NO_DIFF_COUNTS, []);
    }
    return tradesResponse(SAMPLE_SUMMARY, holds);
  };
  return { window, calls };
}

test("대사는 원장 HOLD lot 의 매수일부터 오늘까지를 구간으로 잡는다", async () => {
  const { window, calls } = reconcileWindow([
    tradeRow({ id: 1, buy_date: "2026-08-11 22:30:00" }),
    tradeRow({ id: 2, buy_date: "2026-08-13 23:10:00" }),
    // 청산분은 대사 대상이 아니므로 구간을 앞으로 끌면 안 된다.
    tradeRow({ id: 3, buy_date: "2026-01-02 10:00:00", status: "SOLD" }),
  ]);

  await window.reconcileOverseasTrades();

  const call = calls.find((url) => url.startsWith("/api/overseas/trades/reconcile"));
  assert(call.includes("start_date=20260811"), `가장 오래된 HOLD 매수일이어야 함 (실제 ${call})`);
  assert(/end_date=\d{8}/.test(call), `종료일이 YYYYMMDD 여야 함 (실제 ${call})`);
});

test("대사 기본 호출은 apply=false 다", async () => {
  const { window, calls } = reconcileWindow([tradeRow({})]);

  await window.reconcileOverseasTrades();

  const call = calls.find((url) => url.startsWith("/api/overseas/trades/reconcile"));
  assert(call.includes("apply=false"), `기본은 판정만이어야 함 (실제 ${call})`);
});

test("HOLD lot 의 거래소마다 각각 대사한다", async () => {
  const { window, calls } = reconcileWindow([
    tradeRow({ id: 1, exchange: "NASD" }),
    tradeRow({ id: 2, exchange: "NYSE", symbol: "BRK" }),
  ]);

  await window.reconcileOverseasTrades();

  const reconciles = calls.filter((url) => url.startsWith("/api/overseas/trades/reconcile"));
  assert(reconciles.some((url) => url.includes("exchange=NASD")), "NASD 를 대사해야 함");
  assert(reconciles.some((url) => url.includes("exchange=NYSE")),
    `거래소를 하나만 고르면 나머지가 판정되지 않는다 (실제 ${JSON.stringify(reconciles)})`);
});

test("HOLD lot 이 없으면 대사 API 를 호출하지 않는다", async () => {
  const { window, calls } = reconcileWindow([tradeRow({ status: "SOLD" })]);

  await window.reconcileOverseasTrades();

  assert(!calls.some((url) => url.startsWith("/api/overseas/trades/reconcile")),
    "대사할 보유가 없으면 브로커를 부를 이유가 없음");
  assert(window.document.getElementById("overseas-reconcile-result").textContent.includes("없"),
    "이유가 화면에 남아야 함");
});

test("대사 판정 결과를 행으로 렌더한다", async () => {
  const { window } = reconcileWindow([tradeRow({})], () => reconcileResponse(
    { ok: 0, partial: 0, unfilled: 1, unknown: 0 },
    [{ trade_id: 1, symbol: "AAPL", order_no: "0001234", ledger_qty: 3, filled_qty: 0,
       verdict: "unfilled", corrected: false }],
  ));

  await window.reconcileOverseasTrades();

  const text = window.document.getElementById("overseas-reconcile-result").textContent;
  assert(text.includes("AAPL"), "판정 대상이 보여야 함");
  assert(text.includes("미체결"), `판정이 한글로 보여야 함 (실제 "${text.slice(0, 200)}")`);
});

test("보정 대상이 없으면 '보정 적용' 버튼을 내주지 않는다", async () => {
  const { window } = reconcileWindow([tradeRow({})], () => reconcileResponse(
    { ok: 1, partial: 0, unfilled: 0, unknown: 0 },
    [{ trade_id: 1, symbol: "AAPL", order_no: "0001234", ledger_qty: 3, filled_qty: 3,
       verdict: "ok", corrected: false }],
  ));

  await window.reconcileOverseasTrades();

  assert(window.document.querySelector("[data-overseas-reconcile-apply]") === null,
    "고칠 게 없는데 원장 변경 버튼을 노출하면 안 됨");
});

test("판정불가만 있으면 '보정 적용' 버튼을 내주지 않는다", async () => {
  const { window } = reconcileWindow([tradeRow({ order_no: "" })], () => reconcileResponse(
    { ok: 0, partial: 0, unfilled: 0, unknown: 1 },
    [{ trade_id: 1, symbol: "AAPL", order_no: "", ledger_qty: 3, filled_qty: null,
       verdict: "unknown", corrected: false }],
  ));

  await window.reconcileOverseasTrades();

  assert(window.document.querySelector("[data-overseas-reconcile-apply]") === null,
    "unknown 은 무조작 대상이라 적용할 보정이 없음");
});

test("보정 적용은 confirm 을 거부하면 호출하지 않는다", async () => {
  const { window, calls } = reconcileWindow([tradeRow({})], () => reconcileResponse(
    { ok: 0, partial: 0, unfilled: 1, unknown: 0 },
    [{ trade_id: 1, symbol: "AAPL", order_no: "0001234", ledger_qty: 3, filled_qty: 0,
       verdict: "unfilled", corrected: false }],
  ));

  await window.reconcileOverseasTrades();
  window.confirm = () => false;
  calls.length = 0;
  await window.reconcileOverseasTrades(true);

  assert(!calls.some((url) => url.includes("apply=true")),
    "회귀: confirm 거부에도 원장이 변경됨");
});

test("보정을 적용하면 apply=true 로 부르고 원장을 다시 읽는다", async () => {
  const { window, calls } = reconcileWindow([tradeRow({})], (url) => reconcileResponse(
    { ok: 0, partial: 0, unfilled: 1, unknown: 0 },
    [{ trade_id: 1, symbol: "AAPL", order_no: "0001234", ledger_qty: 3, filled_qty: 0,
       verdict: "unfilled", corrected: url.includes("apply=true") }],
    url.includes("apply=true"),
  ));

  await window.reconcileOverseasTrades();
  calls.length = 0;
  await window.reconcileOverseasTrades(true);

  assert(calls.some((url) => url.includes("apply=true")), "보정 요청이 나가야 함");
  assert(calls.filter((url) => url === "/api/overseas/trades").length >= 1,
    "보정 후 원장 표가 낡은 채로 남으면 안 됨");
});

/* ── 종목 클릭 → 미국장 현재가 화면(/overseas-stock) 이동 ── */

function stockLinks(containerId, window) {
  return Array.from(
    window.document.getElementById(containerId).querySelectorAll("a.stock-link"),
  ).map((a) => ({ href: a.getAttribute("href"), text: a.textContent.trim() }));
}

test("주요 대형주 표의 심볼·종목명이 현재가 화면 링크다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    return { ok: true, json: async () => ({
      rt_cd: "0",
      data: { fx_rate: 1400, items: [{ symbol: "NVDA", name: "NVIDIA", market_cap_usd: 3e12, market_cap_krw: 4.2e15 }] },
    }) };
  };

  await window.loadOverseasMarketCap();

  const links = stockLinks("overseas-marketcap-result", window);
  assert(links.length === 2, `회귀: 심볼/종목명 셀이 링크가 아님 (${links.length}개)`);
  assert(links.every((l) => l.href === "/overseas-stock?symbol=NVDA"),
    `회귀: 현재가 화면 링크가 아님 (${JSON.stringify(links)})`);
});

test("잔고 표의 심볼·종목명은 조회한 거래소를 붙여 현재가 화면으로 간다", async () => {
  const window = makeWindow();
  const orderSel = window.document.getElementById("overseas-order-exchange");
  orderSel.insertAdjacentHTML("beforeend", '<option value="NYSE">NYSE</option>');
  orderSel.value = "NYSE";
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    return { ok: true, json: async () => ({
      rt_cd: "0",
      data: { output1: [{ ovrs_pdno: "BRK", ovrs_item_name: "Berkshire", ovrs_cblc_qty: "2", now_pric2: "500.00" }] },
    }) };
  };

  await window.loadOverseasBalance();

  const links = stockLinks("overseas-balance-result", window);
  assert(links.length === 2, `회귀: 잔고 심볼/종목명 셀이 링크가 아님 (${links.length}개)`);
  assert(links.every((l) => l.href === "/overseas-stock?symbol=BRK&exchange=NYSE"),
    `회귀: 잔고 링크에 거래소가 빠짐 (${JSON.stringify(links)})`);
});

test("즐겨찾기 행의 심볼·종목명은 해당 거래소로 현재가 화면에 간다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    if (url.includes("/api/favorite")) {
      return { ok: true, json: async () => ([
        { code: "BRK", name: "Berkshire", exchange: "NYSE", price: 500.0, rate: -0.5 },
      ]) };
    }
    return { ok: false, json: async () => ({}) };
  };

  await window.loadOverseasFavorites();

  const links = stockLinks("overseas-favorite-body", window);
  assert(links.length === 2, `회귀: 즐겨찾기 심볼/종목명 셀이 링크가 아님 (${links.length}개)`);
  assert(links.every((l) => l.href === "/overseas-stock?symbol=BRK&exchange=NYSE"),
    `회귀: 즐겨찾기 링크가 거래소를 잃음 (${JSON.stringify(links)})`);
  // 삭제 버튼은 계속 동작해야 한다 (링크가 행 액션을 가리면 안 됨).
  assert(window.document.querySelector("#overseas-favorite-body [data-overseas-fav-remove]"),
    "삭제 버튼이 남아 있어야 함");
});

test("USD 거래 기록의 심볼은 기록된 거래소로 현재가 화면에 간다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    return tradesResponse(SAMPLE_SUMMARY, [tradeRow({ symbol: "BRK", exchange: "NYSE" })]);
  };

  await window.loadOverseasTrades();

  const links = stockLinks("overseas-trades-result", window);
  assert(links.length === 1, `회귀: 거래 기록 심볼이 링크가 아님 (${links.length}개)`);
  assert(links[0].href === "/overseas-stock?symbol=BRK&exchange=NYSE",
    `회귀: 거래 기록 링크가 잘못됨 (${links[0].href})`);
});

test("종목명이 HTML 을 담고 있어도 링크 안에서 문자열로만 남는다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.includes("/api/market-mode")) {
      return { ok: true, json: async () => ({ enabled_market_modes: ["overseas_us"] }) };
    }
    return { ok: true, json: async () => ({
      rt_cd: "0",
      data: { fx_rate: 1400, items: [{
        symbol: '"><img src=x onerror="window.__pwned=1">',
        name: "<script>window.__pwned2=1</script>",
        market_cap_usd: 1e9, market_cap_krw: 1.4e12,
      }] },
    }) };
  };

  await window.loadOverseasMarketCap();
  await flush();

  assert(window.__pwned === undefined, "회귀: 심볼이 링크 속성을 탈출해 HTML 로 해석됨");
  assert(window.__pwned2 === undefined, "회귀: 종목명이 스크립트로 해석됨");
});

await run();
