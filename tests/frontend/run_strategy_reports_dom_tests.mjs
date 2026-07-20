/*
 * jsdom 기반 /strategy-reports Telegram 본문 표시 회귀 테스트.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { applyCommonStubs, test, assert, run } from "./harness.mjs";

const STRATEGY_REPORTS_JS = resolve(
  import.meta.dirname,
  "../../view/web/static/js/strategy_reports.js",
);

function makeWindow() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>
    <div id="strategy-report-list"></div>
    <div id="strategy-report-meta"></div>
    <div id="strategy-report-content"></div>
  </body></html>`, {
    url: "http://localhost/strategy-reports",
    runScripts: "dangerously",
  });
  const { window } = dom;
  applyCommonStubs(window);
  window.fetch = async (url) => {
    if (String(url).includes("telegram-1014")) {
      return {
        ok: true,
        json: async () => ({
          id: "telegram-1014",
          report_date: "20260720",
          created_at: "2026-07-20T16:34:57+09:00",
          kind: "telegram",
          title: "Minervini S2 갱신 완료",
          source: "backlog",
          content: "525개 수집, 소요: 759.4s",
        }),
      };
    }
    return {
      ok: true,
      json: async () => ({
        reports: [{
          id: "telegram-1014",
          report_date: "20260720",
          created_at: "2026-07-20T16:34:57+09:00",
          kind: "telegram",
          title: "Minervini S2 갱신 완료",
          source: "backlog",
          summary: "525개 수집, 소요: 759.4s",
        }],
      }),
    };
  };

  const script = window.document.createElement("script");
  script.textContent = readFileSync(STRATEGY_REPORTS_JS, "utf8");
  window.document.body.appendChild(script);
  return window;
}

test("Telegram 리포트 목록과 상세 뷰에 본문을 표시한다", async () => {
  const window = makeWindow();

  await window.loadStrategyReports();

  const listText = window.document.getElementById("strategy-report-list").textContent;
  const detailText = window.document.getElementById("strategy-report-content").textContent;
  assert(listText.includes("525개 수집, 소요: 759.4s"), "목록에 Telegram 본문 미리보기가 필요함");
  assert(detailText.includes("525개 수집, 소요: 759.4s"), "상세 뷰에 Telegram 본문이 필요함");
});

await run();
