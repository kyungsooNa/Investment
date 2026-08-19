/*
 * jsdom 기반 관심종목 페이지(/favorite) 회귀 테스트.
 *
 * 이 화면은 열어둔 채로 시세를 지켜보는 용도라 주기적으로 스스로 다시 조회한다.
 * 자동 갱신은 (1) 화면을 깜빡이지 않고 (2) 사용자가 잡아둔 정렬을 흔들지 않으며
 * (3) 안 보는 창에서는 쉬어야 한다 — 그 계약을 여기서 잠근다.
 *
 * 실행: node run_favorite_dom_tests.mjs
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { applyCommonStubs, test, assert, run } from "./harness.mjs";

const FAVORITE_JS = resolve(import.meta.dirname, "../../view/web/static/js/favorite.js");

const SCAFFOLD = `
<div id="section-favorite" class="section">
  <div class="card">
    <input type="text" id="fav-search-input">
    <ul id="fav-autocomplete-list"></ul>
    <button class="btn" onclick="loadFavoriteList()">새로고침</button>
    <span id="favorite-updated">최근 업데이트: --</span>
  </div>
  <div class="card">
    <table class="data-table">
      <thead>
        <tr>
          <th class="sortable" data-sort="name">종목명(Code)</th>
          <th class="sortable" data-sort="minervini_stage">Stage</th>
          <th class="sortable" data-sort="rs_rating">RS</th>
          <th class="sortable" data-sort="price">현재가</th>
          <th class="sortable" data-sort="rate">등락률</th>
          <th>액션</th>
        </tr>
      </thead>
      <tbody id="favorite-list-body"></tbody>
    </table>
  </div>
</div>
`;

// '/favorite' 로 만들면 DOMContentLoaded 자동 초기화가 끼어들어 호출 수가 흔들린다.
function makeWindow(fetchImpl, url = "http://localhost/favorite-test") {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>${SCAFFOLD}</body></html>`, {
    url,
    runScripts: "outside-only",
  });
  const { window } = dom;
  applyCommonStubs(window);
  // favorite.js 는 로드 시점에 자동완성을 건다 — 페이지 모듈은 이 테스트 대상이 아니다.
  window.StockAutocomplete = () => {};
  window.showToast = () => {};
  window.fetch = fetchImpl;
  window.eval(readFileSync(FAVORITE_JS, "utf8"));
  return window;
}

function favoriteItem(overrides) {
  return {
    code: "005930",
    name: "삼성전자",
    price: 70000,
    rate: 1.5,
    rs_rating: 90,
    minervini_stage: 2,
    ...overrides,
  };
}

// 매 호출마다 다른 목록을 주도록 해, 자동 갱신이 실제로 화면을 갈아끼웠는지 볼 수 있게 한다.
function favoritePage(pages) {
  const calls = [];
  const window = makeWindow(async (url) => {
    calls.push(url);
    const page = pages[Math.min(calls.length - 1, pages.length - 1)];
    if (page instanceof Error) throw page;
    return { ok: true, status: 200, json: async () => page };
  });
  return { window, calls };
}

// 자동 갱신은 분 단위라 실시간으로 기다릴 수 없다. 창의 setInterval 을 가로채 콜백을
// 직접 굴려 계약(주기·건너뛸 조건·정리)만 확인한다.
function captureTimer(window) {
  const timer = { fn: null, ms: null, starts: 0, cleared: [] };
  window.setInterval = (fn, ms) => {
    timer.fn = fn;
    timer.ms = ms;
    timer.starts += 1;
    return timer.starts;
  };
  window.clearInterval = (id) => { timer.cleared.push(id); };
  return timer;
}

// jsdom 의 visibilityState 기본값은 'prerender'(= hidden true) 라 손대지 않으면
// 자동 갱신이 늘 건너뛰어진다. 보이는 창인지를 테스트가 명시한다.
function setPageVisible(window, visible) {
  Object.defineProperty(window.document, "hidden", { value: !visible, configurable: true });
}

function rowCodes(window) {
  return [...window.document.querySelectorAll("#favorite-list-body tr")]
    .map(tr => tr.id.replace("fav-row-", ""));
}

test("목록을 그리고 최근 업데이트 시각을 표기한다", async () => {
  const { window } = favoritePage([[favoriteItem({})]]);

  await window.loadFavoriteList();

  assert(rowCodes(window).includes("005930"), "관심종목 행이 그려져야 함");
  const label = window.document.getElementById("favorite-updated").textContent;
  assert(/최근 업데이트: .*\d/.test(label), `갱신 시각이 표기돼야 함 (실제 "${label}")`);
  assert(/\b30\b/.test(label), `자동 갱신 주기(초)도 함께 알려야 함 (실제 "${label}")`);
});

test("주기마다 조용히 다시 조회한다", async () => {
  const { window, calls } = favoritePage([
    [favoriteItem({})],
    [favoriteItem({ code: "000660", name: "SK하이닉스" })],
  ]);
  const timer = captureTimer(window);
  setPageVisible(window, true);

  await window.initFavoritePage();
  assert(timer.ms === 30000, `자동 갱신 주기는 30초여야 함 (실제 ${timer.ms})`);

  const before = calls.length;
  await timer.fn();

  assert(calls.length === before + 1, `주기마다 목록을 다시 받아야 함 (실제 ${calls.length - before}회)`);
  assert(rowCodes(window).includes("000660"), "새로 받은 목록으로 갈아끼워야 함");
  const body = window.document.getElementById("favorite-list-body").innerHTML;
  assert(!body.includes("로딩 중"), "자동 갱신이 로딩 문구로 화면을 깜빡이면 안 됨");
});

test("자동 갱신은 사용자가 잡아둔 정렬을 유지한다", async () => {
  const twoRows = [favoriteItem({ code: "000660", name: "SK하이닉스", rate: 5.0 }), favoriteItem({ rate: 1.0 })];
  const { window } = favoritePage([twoRows, twoRows]);
  const timer = captureTimer(window);
  setPageVisible(window, true);

  await window.initFavoritePage();
  window.sortFavorites("rate");  // 등락률 내림차순
  assert(rowCodes(window)[0] === "000660", "정렬 직후 상위는 등락률이 높은 종목이어야 함(전제)");

  await timer.fn();

  assert(rowCodes(window)[0] === "000660", "자동 갱신이 정렬을 원래대로 되돌리면 안 됨");
});

test("자동 갱신 실패는 보고 있던 목록을 지우지 않는다", async () => {
  const { window } = favoritePage([[favoriteItem({})], new Error("network")]);
  const timer = captureTimer(window);
  setPageVisible(window, true);

  await window.initFavoritePage();
  const stamp = window.document.getElementById("favorite-updated").textContent;
  await timer.fn();

  assert(rowCodes(window).includes("005930"), "한 번 실패했다고 보고 있던 시세를 지우면 안 됨");
  assert(window.document.getElementById("favorite-updated").textContent === stamp,
    "갱신 시각이 멈춰 있는 것으로 실패를 알린다");
});

test("수동 새로고침 실패는 실패를 화면에 알린다", async () => {
  const { window } = favoritePage([new Error("network")]);

  await window.loadFavoriteList();

  const body = window.document.getElementById("favorite-list-body").innerHTML;
  assert(body.includes("불러오기 실패"), `버튼을 누른 실패는 알려야 함 (실제 "${body}")`);
});

test("창이 백그라운드면 자동 갱신을 건너뛴다", async () => {
  const { window, calls } = favoritePage([[favoriteItem({})]]);
  const timer = captureTimer(window);
  setPageVisible(window, false);

  await window.initFavoritePage();
  const before = calls.length;
  await timer.fn();

  assert(calls.length === before, "안 보는 창 때문에 계속 조회하면 안 됨");
});

test("백그라운드에서 돌아오면 다음 주기를 기다리지 않고 갱신한다", async () => {
  const { window, calls } = favoritePage([[favoriteItem({})]]);
  const timer = captureTimer(window);
  setPageVisible(window, false);

  await window.initFavoritePage();
  const before = calls.length;
  await timer.fn();

  setPageVisible(window, true);
  window.document.dispatchEvent(new window.Event("visibilitychange"));
  await new Promise(done => setTimeout(done, 0));

  assert(calls.length === before + 1, `돌아온 즉시 한 번 갱신해야 함 (실제 ${calls.length - before}회)`);
});

test("다른 화면으로 떠나면 자동 갱신 타이머를 스스로 멈춘다", async () => {
  const { window, calls } = favoritePage([[favoriteItem({})]]);
  const timer = captureTimer(window);
  setPageVisible(window, true);

  await window.initFavoritePage();
  window.document.getElementById("favorite-list-body").remove();
  const before = calls.length;
  await timer.fn();

  assert(calls.length === before, "떠난 화면을 위해 조회하면 안 됨");
  assert(timer.cleared.length >= 1, "타이머를 해제해야 함");
});

test("pjax 재진입은 자동 갱신 타이머를 겹쳐 걸지 않는다", async () => {
  const { window } = favoritePage([[favoriteItem({})]]);
  setPageVisible(window, true);
  // 이 화면 전용 스크립트라 로드 시 자동 초기화가 따라붙는다 — 먼저 끝나게 두고 센다.
  await new Promise(done => setTimeout(done, 0));
  const timer = captureTimer(window);

  await window.initFavoritePage();
  await window.initFavoritePage();

  assert(timer.starts === 2, `재진입마다 타이머를 다시 걸어야 함 (실제 ${timer.starts}회)`);
  assert(timer.cleared.length === timer.starts,
    `새로 걸 때마다 직전 타이머를 해제해야 함 (건 횟수 ${timer.starts}, 해제 ${timer.cleared.length})`);
});

await run();
