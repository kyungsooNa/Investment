/*
 * jsdom 기반 홈 화면 글로벌 지수 패널 회귀 테스트.
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

const EXPECTED_SYMBOLS = [
  "NASDAQ:NDX",
  "NASDAQ:SOX",
  "CBOE:VIX",
  "TVC:DXY",
  "TVC:GOLD",
  "TVC:USOIL",
  "TVC:US10Y",
  "TVC:US02Y",
];

const WIDGET_SRC =
  "https://s3.tradingview.com/external-embedding/embed-widget-mini-symbol-overview.js";

const SCAFFOLD = `<div id="market-indices" class="market-indices-grid"></div>`;

function makeWindow() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>${SCAFFOLD}</body></html>`, {
    url: "http://localhost/",
    runScripts: "outside-only",
  });
  const { window } = dom;
  applyCommonStubs(window);
  window.eval(readFileSync(MARKET_INDICES_JS, "utf8"));
  return window;
}

test("설정된 지수마다 카드가 하나씩 생성된다", () => {
  const window = makeWindow();

  window.renderMarketIndices();

  const cards = window.document.querySelectorAll("#market-indices .market-index-card");
  assert(cards.length === EXPECTED_SYMBOLS.length,
    `카드 ${EXPECTED_SYMBOLS.length}개가 생성되어야 함 (실제 ${cards.length}개)`);

  const symbols = Array.from(cards).map(card => card.dataset.symbol);
  EXPECTED_SYMBOLS.forEach(symbol => {
    assert(symbols.includes(symbol), `${symbol} 카드가 없음`);
  });
});

test("각 카드가 라벨과 TradingView 위젯 스크립트를 함께 심는다", () => {
  const window = makeWindow();

  window.renderMarketIndices();

  const cards = window.document.querySelectorAll("#market-indices .market-index-card");
  for (const card of cards) {
    const label = card.querySelector(".market-index-label");
    assert(label && label.textContent.trim().length > 0,
      `${card.dataset.symbol} 카드에 라벨이 없음`);

    const script = card.querySelector(".tradingview-widget-container script");
    assert(script, `${card.dataset.symbol} 카드에 위젯 스크립트가 없음`);
    assert(script.src === WIDGET_SRC,
      `${card.dataset.symbol} 위젯 스크립트 src 가 다름: ${script.src}`);

    const config = JSON.parse(script.textContent);
    assert(config.symbol === card.dataset.symbol,
      `${card.dataset.symbol} 위젯 설정의 symbol 이 다름: ${config.symbol}`);
    assert(config.colorTheme === "light", "위젯 테마는 앱과 동일한 light 여야 함");
  }
});

test("위젯 스크립트 로드 실패 시 안내 문구로 대체한다", () => {
  const window = makeWindow();
  window.renderMarketIndices();

  const card = window.document.querySelector("#market-indices .market-index-card");
  const script = card.querySelector(".tradingview-widget-container script");
  script.dispatchEvent(new window.Event("error"));

  assert(!card.querySelector(".tradingview-widget-container script"),
    "실패한 위젯 스크립트가 남아 있음");
  const fallback = card.querySelector(".market-index-error");
  assert(fallback && fallback.textContent.includes("불러오지 못"),
    "위젯 로드 실패 안내가 표시되어야 함");
});

test("다시 렌더링해도 카드가 중복되지 않는다", () => {
  const window = makeWindow();

  window.renderMarketIndices();
  window.renderMarketIndices();

  const cards = window.document.querySelectorAll("#market-indices .market-index-card");
  assert(cards.length === EXPECTED_SYMBOLS.length,
    `재렌더링 후에도 카드는 ${EXPECTED_SYMBOLS.length}개여야 함 (실제 ${cards.length}개)`);
});

test("컨테이너가 없는 화면에서는 아무 일도 하지 않는다", () => {
  const window = makeWindow();
  window.document.getElementById("market-indices").remove();

  window.renderMarketIndices();

  assert(!window.document.querySelector(".market-index-card"),
    "컨테이너가 없으면 카드를 만들면 안 됨");
});

await run();
