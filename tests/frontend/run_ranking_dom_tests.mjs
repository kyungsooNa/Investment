/*
 * jsdom 기반 /ranking AI 분석 오류 표시 회귀 테스트.
 *
 * 실행: node run_ranking_dom_tests.mjs  (exit 0 = 전부 통과)
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { applyCommonStubs, test, assert, run } from "./harness.mjs";

const RANKING_JS = resolve(
  import.meta.dirname,
  "../../view/web/static/js/ranking.js",
);

function makeWindow() {
  const dom = new JSDOM(
    '<!DOCTYPE html><html><body><div id="ranking-result"></div></body></html>',
    { url: "http://localhost/ranking", runScripts: "dangerously" },
  );
  const { window } = dom;
  applyCommonStubs(window);
  window.fetchWithTimeout = async () => ({
    ok: false,
    status: 429,
    json: async () => ({
      detail: "AI 일반 분석 일일 한도(80회)에 도달했습니다. 공시 요약용 20회는 보호됩니다.",
    }),
  });

  const script = window.document.createElement("script");
  script.textContent = readFileSync(RANKING_JS, "utf8");
  window.document.body.appendChild(script);
  window.eval(`
    _rankingCurrentCategory = "volume";
    _rankingData = [{
      data_rank: "1",
      stck_shrn_iscd: "005930",
      hts_kor_isnm: "삼성전자",
      stck_prpr: "70000",
      prdy_ctrt: "1.2",
      acml_vol: "1000000"
    }];
  `);
  return window;
}

test("AI 일일 한도 429의 detail을 랭킹 화면에 표시한다", async () => {
  const window = makeWindow();

  await window.runRankingAIAnalysis();

  const text = window.document.getElementById("ranking-result").textContent;
  assert(text.includes("일일 한도(80회)"), "일일 한도 메시지가 표시되지 않음");
  assert(text.includes("공시 요약용 20회"), "공시 예약량 안내가 표시되지 않음");
});

await run();
