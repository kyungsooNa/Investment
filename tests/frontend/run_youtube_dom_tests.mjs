/*
 * jsdom 기반 유튜브 다이제스트 화면 회귀 테스트.
 *
 * 이 화면은 (1) 채널 CRUD 후 목록 재조회, (2) AI 가 만든 자유 텍스트 렌더링이
 * 핵심이라 문자열 검사로는 회귀를 못 잡는다. 특히 리포트 본문·영상 제목은 외부
 * (유튜브 제목, LLM 출력)에서 오므로 escape 누락이 곧 XSS 다.
 *
 * 실행: node run_youtube_dom_tests.mjs
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { applyCommonStubs, test, assert, run } from "./harness.mjs";

const YOUTUBE_JS = process.env.YOUTUBE_JS_PATH
  ? resolve(process.env.YOUTUBE_JS_PATH)
  : resolve(import.meta.dirname, "../../view/web/static/js/youtube.js");

const SCAFFOLD = `
  <div id="youtube-page">
    <span id="youtube-run-status"></span>
    <div id="youtube-status"></div>
    <input type="text" id="youtube-channel-url">
    <span id="youtube-channel-status"></span>
    <table><tbody id="youtube-channel-body"></tbody></table>
    <select id="youtube-report-date"></select>
    <div id="youtube-report-body"></div>
  </div>`;

function ok(payload) {
  return { ok: true, status: 200, json: async () => payload };
}

function httpError(status) {
  return { ok: false, status, json: async () => ({}) };
}

/** 라우트별 응답을 지정하고 호출 기록을 남기는 fetch 대역. */
function makeFetch(routes) {
  const calls = [];
  const fetchWithTimeout = async (url, options = {}, timeoutMs) => {
    calls.push({ url, method: (options.method || "GET").toUpperCase(), body: options.body, timeoutMs });
    for (const [prefix, handler] of routes) {
      if (url.startsWith(prefix)) {
        return typeof handler === "function" ? handler(url, options) : handler;
      }
    }
    return ok(null);
  };
  return { fetchWithTimeout, calls };
}

/**
 * `calls` 를 넘기면 페이지 자동 초기화가 낸 요청을 비우고 돌려준다.
 * 그래야 각 테스트가 자기 동작이 낸 요청만 센다.
 */
async function makeWindow(fetchWithTimeout, calls) {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>${SCAFFOLD}</body></html>`, {
    url: "http://localhost/youtube",
    runScripts: "outside-only",
  });
  const { window } = dom;
  if (window.document.readyState === "loading") {
    await new Promise((r) => window.document.addEventListener("DOMContentLoaded", r));
  }
  applyCommonStubs(window);
  window.fetchWithTimeout = fetchWithTimeout;
  window.eval(readFileSync(YOUTUBE_JS, "utf8"));
  await settle();
  if (calls) calls.length = 0;
  return window;
}

/** 자동 초기화의 fetch 체인이 끝날 때까지 이벤트 루프를 돌린다. */
async function settle() {
  for (let i = 0; i < 10; i += 1) {
    await new Promise((r) => setTimeout(r, 0));
  }
}

const CHANNELS = [
  { channel_id: "UC1", handle: "chesleytv", title: "체슬리TV", enabled: true },
  { channel_id: "UC2", handle: "rsangmoo", title: "상무니tv", enabled: false },
];

const REPORT = {
  report_date: "20260810",
  video_count: 2,
  failed_summary_count: 0,
  digest_text: "오늘의 공통 화두는 반도체다.",
  mentions: [
    { name: "삼성전자", code: "005930", count: 16, video_count: 4 },
    { name: "SK하이닉스", code: "000660", count: 8, video_count: 3 },
  ],
  videos: [
    {
      video_id: "v1",
      title: "영상 제목",
      channel_title: "체슬리TV",
      url: "https://www.youtube.com/watch?v=v1",
      summary: "v1 요약",
    },
  ],
};

function defaultRoutes(overrides = []) {
  return [
    ...overrides,
    ["/api/youtube/channels", ok(CHANNELS)],
    ["/api/youtube/reports/latest", ok({ report: REPORT })],
    ["/api/youtube/reports/", ok(REPORT)],
    ["/api/youtube/reports", ok([{ report_date: "20260810", video_count: 2 }])],
    ["/api/youtube/status", ok({ enabled: true, progress: { last_report_date: "20260810" } })],
  ];
}

// ── 채널 관리 ────────────────────────────────────────────────

test("채널 목록이 행으로 렌더된다", async () => {
  const { fetchWithTimeout, calls } = makeFetch(defaultRoutes());
  const window = await makeWindow(fetchWithTimeout, calls);

  await window.loadYoutubeChannels();

  const rows = window.document.querySelectorAll("#youtube-channel-body tr");
  assert(rows.length === 2, `행 2개여야 하는데 ${rows.length}`);
  assert(rows[0].textContent.includes("체슬리TV"), "채널명이 보여야 한다");
  assert(rows[0].textContent.includes("@chesleytv"), "핸들이 보여야 한다");
});

test("수집 on/off 상태가 버튼 라벨로 구분된다", async () => {
  const { fetchWithTimeout, calls } = makeFetch(defaultRoutes());
  const window = await makeWindow(fetchWithTimeout, calls);

  await window.loadYoutubeChannels();

  const buttons = window.document.querySelectorAll(".yt-toggle");
  assert(buttons[0].textContent === "수집 중", `on 라벨 불일치: ${buttons[0].textContent}`);
  assert(buttons[1].textContent === "중지", `off 라벨 불일치: ${buttons[1].textContent}`);
});

test("빈 채널 목록이면 안내 문구가 뜬다", async () => {
  const { fetchWithTimeout, calls } = makeFetch(defaultRoutes([["/api/youtube/channels", ok([])]]));
  const window = await makeWindow(fetchWithTimeout, calls);

  await window.loadYoutubeChannels();

  assert(
    window.document.getElementById("youtube-channel-body").textContent.includes("등록된 채널이 없습니다"),
    "빈 목록 안내가 있어야 한다",
  );
});

test("페이지가 로드되면 채널·상태·리포트를 자동으로 읽는다", async () => {
  const { fetchWithTimeout, calls } = makeFetch(defaultRoutes());

  await makeWindow(fetchWithTimeout);  // 초기화 호출을 비우지 않는다

  const urls = calls.map((c) => c.url);
  assert(urls.includes("/api/youtube/status"), "상태를 읽어야 한다");
  assert(urls.includes("/api/youtube/channels"), "채널을 읽어야 한다");
  assert(urls.includes("/api/youtube/reports/latest"), "최신 리포트를 읽어야 한다");
});

test("채널 추가는 URL 을 POST 하고 목록을 다시 읽는다", async () => {
  const { fetchWithTimeout, calls } = makeFetch(
    defaultRoutes([["/api/youtube/channels", (url, options) =>
      (options.method === "POST"
        ? ok({ added: true, channel_id: "UC3", title: "새채널" })
        : ok(CHANNELS))]]),
  );
  const window = await makeWindow(fetchWithTimeout, calls);
  window.document.getElementById("youtube-channel-url").value = "@newone";

  await window.addYoutubeChannel();

  const post = calls.find((c) => c.method === "POST");
  assert(post, "POST 요청이 있어야 한다");
  assert(JSON.parse(post.body).url === "@newone", "입력한 URL 을 보내야 한다");
  assert(
    calls.filter((c) => c.method === "GET" && c.url === "/api/youtube/channels").length === 1,
    "추가 후 목록을 다시 읽어야 한다",
  );
  assert(window.document.getElementById("youtube-channel-url").value === "", "입력칸이 비워져야 한다");
});

test("빈 입력으로 추가하면 요청을 보내지 않는다", async () => {
  const { fetchWithTimeout, calls } = makeFetch(defaultRoutes());
  const window = await makeWindow(fetchWithTimeout, calls);
  window.document.getElementById("youtube-channel-url").value = "   ";

  await window.addYoutubeChannel();

  assert(calls.length === 0, "요청이 없어야 한다");
  assert(
    window.document.getElementById("youtube-channel-status").textContent.includes("입력"),
    "안내가 떠야 한다",
  );
});

test("해석 실패한 채널은 오류 문구로 알린다", async () => {
  const { fetchWithTimeout, calls } = makeFetch(
    defaultRoutes([["/api/youtube/channels", (url, options) =>
      (options.method === "POST" ? httpError(400) : ok(CHANNELS))]]),
  );
  const window = await makeWindow(fetchWithTimeout, calls);
  window.document.getElementById("youtube-channel-url").value = "@nope";

  await window.addYoutubeChannel();

  assert(
    window.document.getElementById("youtube-channel-status").textContent.includes("실패"),
    "실패 안내가 떠야 한다",
  );
});

test("채널 삭제는 DELETE 를 보낸다", async () => {
  const { fetchWithTimeout, calls } = makeFetch(defaultRoutes());
  const window = await makeWindow(fetchWithTimeout, calls);

  await window.removeYoutubeChannel("UC1");

  const del = calls.find((c) => c.method === "DELETE");
  assert(del && del.url.endsWith("/UC1"), "DELETE 대상이 맞아야 한다");
});

test("수집 토글은 PATCH 로 반대값을 보낸다", async () => {
  const { fetchWithTimeout, calls } = makeFetch(defaultRoutes());
  const window = await makeWindow(fetchWithTimeout, calls);

  await window.toggleYoutubeChannel("UC1", false);

  const patch = calls.find((c) => c.method === "PATCH");
  assert(patch, "PATCH 요청이 있어야 한다");
  assert(JSON.parse(patch.body).enabled === false, "enabled=false 여야 한다");
});

// ── 리포트 ───────────────────────────────────────────────────

test("리포트 본문·언급표·영상목록이 렌더된다", async () => {
  const { fetchWithTimeout, calls } = makeFetch(defaultRoutes());
  const window = await makeWindow(fetchWithTimeout, calls);

  await window.loadYoutubeReport("");

  const doc = window.document;
  assert(doc.getElementById("youtube-digest-text").textContent.includes("반도체"), "본문이 있어야 한다");
  assert(doc.querySelectorAll("#youtube-mention-body tr").length === 2, "언급 2행이어야 한다");
  assert(doc.querySelectorAll("#youtube-video-list li").length === 1, "영상 1건이어야 한다");
});

test("리포트가 없으면 빈 화면 대신 안내가 뜬다", async () => {
  const { fetchWithTimeout, calls } = makeFetch(
    defaultRoutes([["/api/youtube/reports/latest", ok({ report: null })]]),
  );
  const window = await makeWindow(fetchWithTimeout, calls);

  await window.loadYoutubeReport("");

  assert(
    window.document.getElementById("youtube-report-body").textContent.includes("아직 생성된 리포트가 없습니다"),
    "첫 실행 전 안내가 떠야 한다",
  );
});

test("AI 출력과 유튜브 제목은 HTML 로 해석되지 않는다", async () => {
  const evil = JSON.parse(JSON.stringify(REPORT));
  evil.digest_text = '<img src=x onerror="window.__pwned=1">';
  evil.videos[0].title = '<script>window.__pwned2=1</script>';
  evil.mentions[0].name = '<b>삼성전자</b>';
  const { fetchWithTimeout, calls } = makeFetch(defaultRoutes([["/api/youtube/reports/latest", ok(evil)]]));
  const window = await makeWindow(fetchWithTimeout, calls);

  await window.loadYoutubeReport("");

  const body = window.document.getElementById("youtube-report-body");
  assert(body.querySelector("img") === null, "img 태그가 생성되면 안 된다");
  assert(body.querySelector("script") === null, "script 태그가 생성되면 안 된다");
  assert(body.querySelector("#youtube-mention-body b") === null, "종목명이 마크업으로 해석되면 안 된다");
  assert(window.__pwned === undefined && window.__pwned2 === undefined, "스크립트가 실행되면 안 된다");
});

test("요약 실패 편수가 있으면 경고를 표시한다", async () => {
  const partial = Object.assign({}, REPORT, { failed_summary_count: 2 });
  const { fetchWithTimeout, calls } = makeFetch(
    defaultRoutes([["/api/youtube/reports/latest", ok({ report: partial })]]),
  );
  const window = await makeWindow(fetchWithTimeout, calls);

  await window.loadYoutubeReport("");

  assert(
    window.document.querySelector("#youtube-report-body .warn").textContent.includes("2편"),
    "실패 편수를 알려야 한다",
  );
});

test("날짜를 고르면 해당 날짜를 조회한다", async () => {
  const { fetchWithTimeout, calls } = makeFetch(defaultRoutes());
  const window = await makeWindow(fetchWithTimeout, calls);

  await window.loadYoutubeReport("20260809");

  assert(
    calls.some((c) => c.url === "/api/youtube/reports/20260809"),
    "선택한 날짜로 조회해야 한다",
  );
});

test("날짜 목록이 select 옵션으로 채워진다", async () => {
  const { fetchWithTimeout, calls } = makeFetch(defaultRoutes());
  const window = await makeWindow(fetchWithTimeout, calls);

  await window.loadYoutubeReportDates();

  const options = window.document.querySelectorAll("#youtube-report-date option");
  assert(options.length === 1, `옵션 1개여야 하는데 ${options.length}`);
  assert(options[0].value === "20260810", "날짜 값이 맞아야 한다");
});

// ── 수동 실행 ────────────────────────────────────────────────

test("지금 생성은 기본 타임아웃보다 긴 예산을 준다", async () => {
  // 자막 수집 + 영상별 AI 요약이라 15초 기본값으로는 항상 끊긴다.
  const { fetchWithTimeout, calls } = makeFetch(defaultRoutes([["/api/youtube/run", ok({ ok: true })]]));
  const window = await makeWindow(fetchWithTimeout, calls);

  await window.runYoutubeDigestNow();

  const runCall = calls.find((c) => c.url === "/api/youtube/run");
  assert(runCall.method === "POST", "POST 여야 한다");
  assert(runCall.timeoutMs > 60000, `타임아웃이 너무 짧다: ${runCall.timeoutMs}`);
});

test("생성 실패는 조용히 넘어가지 않는다", async () => {
  const { fetchWithTimeout, calls } = makeFetch(defaultRoutes([["/api/youtube/run", httpError(503)]]));
  const window = await makeWindow(fetchWithTimeout, calls);

  await window.runYoutubeDigestNow();

  assert(
    window.document.getElementById("youtube-run-status").textContent.includes("실패"),
    "실패를 화면에 알려야 한다",
  );
});

test("AI 비활성 상태를 상태줄에 알린다", async () => {
  const { fetchWithTimeout, calls } = makeFetch(
    defaultRoutes([["/api/youtube/status", ok({ enabled: false, progress: null })]]),
  );
  const window = await makeWindow(fetchWithTimeout, calls);

  await window.loadYoutubeStatus();

  assert(
    window.document.getElementById("youtube-status").textContent.includes("비활성"),
    "비활성 안내가 떠야 한다",
  );
});

test("마지막 오류가 있으면 상태줄에 보인다", async () => {
  const { fetchWithTimeout, calls } = makeFetch(
    defaultRoutes([["/api/youtube/status",
      ok({ enabled: true, progress: { last_report_date: "20260809", last_error: "blocked" } })]]),
  );
  const window = await makeWindow(fetchWithTimeout, calls);

  await window.loadYoutubeStatus();

  assert(
    window.document.getElementById("youtube-status").textContent.includes("blocked"),
    "마지막 오류가 보여야 한다",
  );
});

run();
