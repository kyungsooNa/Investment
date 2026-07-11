/*
 * tests/frontend/harness.mjs — jsdom 회귀 테스트 공용 하네스.
 *
 * run_*_dom_tests.mjs 들이 공유하는 (1) 테스트 등록/실행 러너와
 * (2) common.js 공용 유틸(escapeHtml/showError/readJsonResponse/포매터) 페이지 stub 을 제공한다.
 *
 * common.js 는 pjax/상태 폴링 등 부작용이 커 jsdom 에 통째로 eval 하면 타이머·미스텁 fetch 가
 * 터지므로, page JS 가 의존하는 순수 유틸만 여기서 충실히 재현해 window 에 주입한다.
 */

// ── 테스트 러너 ──────────────────────────────────────────────
const _tests = [];

export function test(name, fn) {
  _tests.push({ name, fn });
}

export function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

export async function run() {
  let failed = 0;
  for (const { name, fn } of _tests) {
    try {
      await fn();
      console.log(`PASS  ${name}`);
    } catch (error) {
      failed += 1;
      console.error(`FAIL  ${name}\n      ${error.message}`);
    }
  }
  console.log(`\n${_tests.length - failed}/${_tests.length} passed`);
  process.exit(failed === 0 ? 0 : 1);
}

// ── common.js 공용 유틸 stub (하드닝된 정의와 일치) ─────────────
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[char]);
}

function formatTradingValue(val, isMillion = false) {
  let num = parseInt(val || '0');
  if (isMillion) num *= 1000000;
  const abs = Math.abs(num);
  const sign = num < 0 ? '-' : '';
  if (abs >= 1e12) {
    const jo = Math.floor(abs / 1e12);
    const eok = Math.round((abs % 1e12) / 1e8);
    return sign + jo.toLocaleString() + '조' + (eok > 0 ? ' ' + eok.toLocaleString() + '억' : '');
  }
  if (abs >= 1e8) return sign + (abs / 1e8).toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '억';
  if (abs >= 1e4) return sign + (abs / 1e4).toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '만';
  return num.toLocaleString();
}

function formatMarketCap(val) {
  const num = parseInt(val || '0');
  if (num >= 10000) {
    const jo = Math.floor(num / 10000);
    const eok = num % 10000;
    return jo.toLocaleString() + '조' + (eok > 0 ? ' ' + eok.toLocaleString() + '억' : '');
  }
  return num.toLocaleString() + '억';
}

async function readJsonResponse(res) {
  if (!res.ok) return { json: null, error: `HTTP ${res.status}` };
  try {
    const json = await res.json();
    return json && typeof json === 'object'
      ? { json, error: null }
      : { json: null, error: '응답 형식이 올바르지 않습니다.' };
  } catch (_) {
    return { json: null, error: '응답을 처리하지 못했습니다.' };
  }
}

/**
 * page JS 가 common.js 전역에 의존하는 순수 유틸을 window 에 설치한다.
 * 개별 테스트가 fetchWithTimeout 등은 케이스별로 덮어쓴다.
 */
export function applyCommonStubs(window) {
  window.escapeHtml = escapeHtml;
  window.formatTradingValue = formatTradingValue;
  window.formatMarketCap = formatMarketCap;
  window.readJsonResponse = readJsonResponse;
  window.showError = (el, message) => { if (el) el.innerHTML = `<p class="error">${escapeHtml(message)}</p>`; };
  window.showLoading = (el, message) => { if (el) el.innerHTML = `<p class="loading">${escapeHtml(message)}</p>`; };
}
