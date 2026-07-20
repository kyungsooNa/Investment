/*
 * jsdom 기반 /stock 화면 회귀 테스트.
 *
 * 목적: /stock 화면이 국내 전용 조회 경로를 유지하고 차트 렌더링이
 * 정상 동작하는지 실제 DOM 행위로 검증한다.
 *
 * 실행: node run_stock_dom_tests.mjs  (exit 0 = 전부 통과)
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { test, assert, run } from "./harness.mjs";

// 기본은 실제 stock.js. STOCK_JS_PATH 로 다른 사본(예: 회귀 검증용 buggy 사본)을 지정 가능.
const STOCK_JS = process.env.STOCK_JS_PATH
  ? resolve(process.env.STOCK_JS_PATH)
  : resolve(import.meta.dirname, "../../view/web/static/js/stock.js");

// stock.html 의 #section-stock 구조를 회귀 검증에 필요한 만큼만 재현.
const SCAFFOLD = `
<div id="section-stock" class="section">
  <div class="card">
    <div id="domestic-stock-row">
      <input id="stock-code-input" type="text">
      <ul id="stock-autocomplete-list"></ul>
    </div>
  </div>
  <div id="stock-result"></div>
  <div class="card" id="stock-chart-card" style="display:none;">
    <div id="chart-controls-area"></div>
    <canvas id="stockChart"></canvas>
  </div>
</div>
`;

function makeWindow() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>${SCAFFOLD}</body></html>`, {
    url: "http://localhost/stock",
    runScripts: "dangerously",
  });
  const { window } = dom;
  // stock.js 가 로드 시점에 호출/참조하는 전역 스텁
  window.StockAutocomplete = () => {};
  window.showLoading = (el, msg) => { if (el) el.innerHTML = `<p class="loading">${msg}</p>`; };
  window.fetchWithTimeout = async () => ({ ok: true, json: async () => ({}) });
  window.fetch = async () => ({ json: async () => ({ stocks: [] }) });
  window.loadAndRenderStockChart = () => {};
  window.formatTradingValue = () => "";
  window.ALL_STOCKS = [];
  window._matchMixed = () => false;

  const script = window.document.createElement("script");
  script.textContent = readFileSync(STOCK_JS, "utf8");
  window.document.body.appendChild(script);
  return window;
}

test("stock.js는 해외 조회 진입점을 노출하지 않는다", async () => {
  const window = makeWindow();
  assert(typeof window.setStockMarketMode === "undefined", "국내/해외 모드 토글이 남아 있음");
  assert(typeof window.searchOverseasStock === "undefined", "해외 조회 함수가 남아 있음");
});

test("국내 조회에 필요한 입력과 차트 DOM이 존재한다", async () => {
  const window = makeWindow();
  assert(window.document.getElementById("stock-code-input"), "국내 종목 입력 누락");
  assert(window.document.getElementById("stock-chart-card"), "차트 카드 누락");
  assert(window.document.getElementById("stockChart"), "차트 캔버스 누락");
});

test("현재가 조회 결과에 KOSPI/KOSDAQ 시장 배지를 표시한다", async () => {
  const window = makeWindow();
  window.fetchWithTimeout = async (url) => {
    if (url.startsWith("/api/stock/123456?")) {
      return {
        ok: true,
        json: async () => ({
          rt_cd: "0",
          data: {
            code: "123456", name: "테스트종목", market: "KOSDAQ",
            price: "10000", change: "0", rate: "0", sign: "",
            open: "10000", high: "10100", low: "9900", prev_close: "10000",
          },
        }),
      };
    }
    return { ok: true, json: async () => ({ rt_cd: "1", data: null }) };
  };

  await window.searchStock("123456");

  const badge = window.document.querySelector(".stock-market-badge");
  assert(badge, "시장 구분 배지가 없음");
  assert(badge.textContent === "KOSDAQ", "시장 구분 배지 값이 KOSDAQ이 아님");
  assert(window.document.getElementById("ai-stock-analysis-btn"), "AI 분석 버튼이 없음");
  assert(window.document.getElementById("ai-stock-analysis-output"), "AI 분석 결과 영역이 없음");
  assert(window.document.getElementById("ai-stock-analysis-signal"), "AI 분석 신호 배지가 없음");
  assert(window.document.getElementById("ai-news-btn"), "AI 뉴스 검토 버튼이 없음");
  assert(window.document.getElementById("ai-news-list"), "AI 뉴스 목록 영역이 없음");
  assert(window.document.getElementById("ai-news-signal"), "AI 뉴스 신호 배지가 없음");
});

test("AI 종목 분석은 POST로 요청하고 결과를 텍스트로 안전하게 표시한다", async () => {
  const window = makeWindow();
  window.document.getElementById("stock-result").innerHTML = `
    <button id="ai-stock-analysis-btn"></button>
    <span id="ai-stock-analysis-signal" class="ai-signal"></span>
    <span id="ai-stock-analysis-status"></span>
    <div id="ai-stock-analysis-sources"></div>
    <div id="ai-stock-analysis-output"></div>
  `;
  let requested = null;
  window.fetchWithTimeout = async (url, options, timeout) => {
    requested = { url, options, timeout };
    return {
      ok: true,
      json: async () => ({
        rt_cd: "0",
        data: {
          analysis: "<img src=x onerror=alert(1)>상승 추세",
          signal: "상",
          generated_at: "2026-07-19T12:00:00+09:00",
          sources: {
            current: true,
            financial: true,
            stage: true,
            rs_rating: true,
            investor_flow: false,
            disclosures: false,
            news: true,
          },
        },
      }),
    };
  };

  await window.requestAiStockAnalysis("005930");

  assert(requested.url === "/api/stock/005930/ai-analysis", "AI 분석 URL이 잘못됨");
  assert(requested.options.method === "POST", "AI 분석이 POST 요청이 아님");
  assert(requested.timeout === 45000, "AI 분석 타임아웃이 45초가 아님");
  const output = window.document.getElementById("ai-stock-analysis-output");
  assert(output.textContent.includes("<img src=x"), "AI 결과가 텍스트로 보존되지 않음");
  assert(!output.querySelector("img"), "AI 결과가 HTML로 실행됨");
  assert(
    window.document.getElementById("ai-stock-analysis-sources").textContent.includes("현재가"),
    "사용 데이터 출처가 표시되지 않음",
  );
  assert(
    window.document.getElementById("ai-stock-analysis-sources").textContent.includes("최근 뉴스"),
    "뉴스 출처 라벨이 표시되지 않음",
  );
  const analysisSignal = window.document.getElementById("ai-stock-analysis-signal");
  assert(analysisSignal.textContent.includes("상"), "AI 신호 배지 텍스트가 없음");
  assert(analysisSignal.style.display !== "none", "AI 신호 배지가 숨겨져 있음");
});

function makeNewsWindow() {
  const window = makeWindow();
  window.document.getElementById("stock-result").innerHTML = `
    <button id="ai-news-btn"></button>
    <span id="ai-news-signal" class="ai-signal"></span>
    <span id="ai-news-status"></span>
    <div id="ai-news-output"></div>
    <ul id="ai-news-list"></ul>
  `;
  return window;
}

test("AI 뉴스 검토는 POST로 요청하고 기사 제목을 텍스트로 안전하게 렌더한다", async () => {
  const window = makeNewsWindow();
  let requested = null;
  window.fetchWithTimeout = async (url, options, timeout) => {
    requested = { url, options, timeout };
    return {
      ok: true,
      json: async () => ({
        rt_cd: "0",
        data: {
          analysis: "한줄 요약: 수주 모멘텀",
          signal: "하",
          news_count: 2,
          generated_at: "2026-07-20T12:00:00+09:00",
          news: [
            {
              title: "<img src=x onerror=alert(1)>수주 계약 체결",
              press: "연합뉴스",
              published_at: "2026.07.20 09:10",
              url: "https://finance.naver.com/item/news_read.naver?article_id=1",
            },
            {
              title: "코스피 시황",
              press: "주간조선",
              published_at: "2026.07.19 18:00",
              url: "https://finance.naver.com/item/news_read.naver?article_id=2",
            },
          ],
        },
      }),
    };
  };

  await window.requestAiNewsReview("005930");

  assert(requested.url === "/api/stock/005930/ai-news", "뉴스 검토 URL이 잘못됨");
  assert(requested.options.method === "POST", "뉴스 검토가 POST 요청이 아님");
  assert(requested.timeout === 45000, "뉴스 검토 타임아웃이 45초가 아님");

  const list = window.document.getElementById("ai-news-list");
  assert(list.querySelectorAll("li").length === 2, "기사 목록이 2건으로 렌더되지 않음");
  assert(!list.querySelector("img"), "기사 제목이 HTML로 실행됨");
  assert(
    list.querySelector("a").textContent.includes("<img src=x"),
    "기사 제목이 텍스트로 보존되지 않음",
  );
  assert(list.querySelector("a").rel.includes("noopener"), "외부 링크에 noopener가 없음");
  assert(
    window.document.getElementById("ai-news-output").textContent.includes("수주 모멘텀"),
    "AI 검토 결과가 표시되지 않음",
  );
  const newsSignal = window.document.getElementById("ai-news-signal");
  assert(newsSignal.textContent.includes("하"), "뉴스 신호 배지 텍스트가 없음");
  assert(newsSignal.style.display !== "none", "뉴스 신호 배지가 숨겨져 있음");
});

test("뉴스가 없으면 안내만 표시하고 결과 영역을 비운다", async () => {
  const window = makeNewsWindow();
  window.fetchWithTimeout = async () => ({
    ok: true,
    json: async () => ({
      rt_cd: "0",
      msg1: "최근 뉴스를 찾지 못했습니다.",
      data: { analysis: null, news: [], news_count: 0 },
    }),
  });

  await window.requestAiNewsReview("005930");

  const status = window.document.getElementById("ai-news-status");
  const output = window.document.getElementById("ai-news-output");
  assert(status.textContent.includes("찾지 못했"), "빈 뉴스 안내가 표시되지 않음");
  assert(output.style.display === "none", "빈 결과인데 결과 영역이 열림");
  assert(
    window.document.getElementById("ai-news-list").querySelectorAll("li").length === 0,
    "빈 뉴스인데 목록이 렌더됨",
  );
  assert(
    window.document.getElementById("ai-news-signal").style.display === "none",
    "신호 없는 응답인데 배지가 보임",
  );
});

test("뉴스 검토 실패 시 오류를 표시하고 버튼을 복구한다", async () => {
  const window = makeNewsWindow();
  window.fetchWithTimeout = async () => ({
    ok: false,
    status: 429,
    json: async () => ({ detail: "일일 AI 요청 한도를 초과했습니다." }),
  });

  await window.requestAiNewsReview("005930");

  const output = window.document.getElementById("ai-news-output");
  const button = window.document.getElementById("ai-news-btn");
  assert(output.classList.contains("error"), "오류 스타일이 적용되지 않음");
  assert(output.textContent.includes("한도"), "오류 메시지가 표시되지 않음");
  assert(button.disabled === false, "실패 후 버튼이 잠긴 채로 남음");
});

await run();
