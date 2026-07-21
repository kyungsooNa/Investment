# Investment Trading App - 남은 To-Do

최종 업데이트: 2026-07-20 (종목 뉴스 AI 검토 추가 — X-4)

이 문서는 **현재 남은 실행 항목**만 추린 목록이다. 완료된 구현 상세·완료 체크·과거 세션 요약은 git/PR과 리포트 파일로 추적하고 본 문서에서 제거한다.

정리 원칙:

- 실전 계좌 보호와 주문 안정성을 최우선으로 둔다.
- 이미 적용된 항목은 새 기능으로 다시 넣지 않고, 검증/고도화 과제로만 남긴다.
- 후보군 관리는 기존 `OneilUniverseService` / `SubscriptionPolicy` 공통 파이프라인 확장 과제로 본다.
- 주문/브로커/스케줄러 변경은 테스트 hang 가이드와 paper/real 분기 검증을 함께 적용한다.
- `VolumeBreakoutStrategy`, `VolumeBreakoutLiveStrategy`, `TraditionalVolumeBreakoutStrategy`, `GapUpPullbackStrategy`, `ProgramBuyFollowStrategy`, `MomentumStrategy`는 비활성/레거시이므로 신규 연결·개선 우선순위에서 제외한다.
- 항목 번호(0-1, 1-5, 2-4 등)는 기존 PR·리포트·메모 참조 보존을 위해 유지하고, **문서 배치만 우선 처리 순서**를 따른다.

---

## 우선 처리 순서 (2026-07-02 리뷰 기준)

리뷰 핵심 판단: 운영 인프라·리스크 규율(킬스위치 영속화·RiskGate·tiered force-exit·profitability gate·캐너리 사이징)은 갖춰졌다. 남은 크리티컬 패스는 **엣지(수익성) 입증**이다. 엣지의 원천으로 삼는 수급 필터(체결강도·프로그램매매)가 장중 히스토리 부재로 백테스트 검증 불가능한 상태이므로, 검증 데이터 축적을 지금 시작하는 것이 최우선이다.

1. **[즉시 착수 — 엣지 검증 크리티컬 패스]**
   - 1-5 장중 microstructure 캡처 **상시 가동** (blocked 해제 단계 자체가 착수 항목) — 태스크 배선 완료(#618), 코퍼스 축적 중, 1일차 품질 결함 보정 + 체결강도 장중 시계열 배선 완료(2026-07-04), QC 1주차 양호 판정 + 거래대금 랭킹 보충으로 코퍼스 폭 확대(2026-07-08)
   - 1-6 shadow/paper/소액 canary journal 축적 (운영 상시 — 무틱 블로커와 독립)
2. **[외부 블로커 — 병행 진행]**
   - 2-4 WebSocket 무틱 — KIS 에스컬레이션 **접수 대기** (패키지 준비 완료 #630, 남은 액션은 실제 접수뿐; 무틱 보통주만 — ETF/우선주는 무틱 수용으로 종결)
   - X-1 OpenDART API 인증키 발급 → 공시 모니터 활성화·재시작·수신 검증
   - X-2 AI 공시 분석용 API 제공자 확정 및 API 키 발급
3. **[확대·전환 전 필수 게이트]**
   - 0-1 실전 체결필드 fixture · 2-2 KIS 유량 한도 실측 (실전 전환 직전, 외부 의존)
4. **[데이터·정책 대기]**
   - 1-8 백테스트 재실행 (CLI 노출 완료 #619 — PIT 후보 부재로 blocked, 2026-07-03 파일럿 판정)
   - 1-7 DSR hard threshold (canary 데이터 후) · R-2 Phase 4 (베어 paper 데이터 후) · 해외 Phase 5 (dry-run 검증 후 — O-1 #621/O-2 완료)
5. **[조건부·저위험 상시]**
   - Pool B 완화 (**캡처 우회 적용 2026-07-08 — 트레이딩 완화는 시장 회복 후 재판단**) · 2-6 핫패스 (보류) · 3-4 lifecycle 분해 (정책 합의 시) · M-2 문서 동기화 (**완료 2026-07-12 — service_container bootstrap 분해 반영**) · M-3 #653 UI 리뷰 후속 (**완료 2026-07-11 — 즉시수정·하드닝·중복정리 12건**) · X-4 뉴스 AI 검토 후속 (**1차 완료 2026-07-20 #689 — 한도 절약 개선 4건 미착수**) · T-0/R-6 (선택/관찰)

---

## X. 외부 API 키 준비 [즉시]

### X-1. OpenDART API 인증키 발급 및 공시 모니터 활성화

- [ ] OpenDART에서 API 인증키를 신청·발급받는다.
- [ ] `config/config.yaml`의 `dart_disclosure.api_key`에 키를 저장하고 `enabled: true`로 전환한다. 키는 git에 포함하지 않는다.
- [ ] 웹 앱을 재시작한 뒤 `data/dart_disclosures.db` 생성과 `dart_disclosure_monitor` 태스크 실행을 확인한다.
- [ ] 최초 동기화에서 2026-07-15 SK하이닉스 유상증자 공시가 관심종목 요약으로 수신되는지 검증한다.

### X-2. AI 분석 (1차 공시 요약 / 2차 종목 분석 — 완료)

- [x] 제공자·모델 확정: **Gemini 2.5 Flash** (무료 티어). provider 차이는 OpenAI 호환 엔드포인트로 흡수 — `ai_analysis` config(`base_url`/`api_key`/`model`)만 바꾸면 Groq·Ollama 전환 가능(코드 불변).
- [x] 키 발급·보관: `config/config.yaml`의 `ai_analysis.api_key`(git 비추적). 키 형식은 `AIza`(구형)/`AQ.`(신형) 모두 유효.
- [x] 정책 확정: AI 실패·타임아웃·빈 응답 시 `None` 반환 → 기존 `DartDisclosureRuleService` 규칙 판정으로 자동 폴백(알림은 계속). `enabled=false`면 현행과 완전 동일.
- [x] **1차 구현·검증 완료**: 관심종목 중요 공시(규칙 ≥`immediate_alert_score`)에 제목/메타 기반 AI 요약을 텔레그램 알림에 덧붙임. `services/ai_client.py`(OpenAI 호환)·`services/ai_disclosure_analyzer.py` 신규, `dart_disclosure_monitor_task`/`telegram_notifier`/`service_container` 연동. 진단: `scripts/check_ai_key.py`·`scripts/check_disclosure_ai.py`.
  - 실데이터 검증(20260715 공시)에서 버그 4건 발견·수정: base_url 폴백 누락 / 비ASCII 키 처리 / 콘솔 출력 잘림(UTF-8) / **max_tokens 256→2048**(Gemini 2.5 thinking 토큰이 출력 예산 소비 — 짧으면 요약 잘림).
  - ⚠️ 실전 발동 전제: `dart_disclosure.enabled`(X-1) + 텔레그램 리포터 + 관심종목 1개+ + `ai_analysis.enabled`.
- [x] **2차 (종목 자체 AI 분석)**: 검증된 `ctx.ai_client`를 재사용해 현재가·재무비율·Minervini Stage·RS Rating·최근 5일 수급·로컬 OpenDART 공시를 병렬 수집하고 AI 종합 분석을 생성한다. 일부 소스 실패 시 남은 데이터로 계속 분석하며 사용 소스를 응답에 노출한다. 종목 상세 UI에서 명시적 버튼을 눌렀을 때만 호출하고, 45초 타임아웃·중복 클릭 방지·안전한 텍스트 렌더링·투자 권유 아님 안내를 적용했다. (`POST /api/stock/{code}/ai-analysis`)
- [x] **AI API 호출 경로 공통화**: 랭킹 분석의 별도 Gemini/OpenAI SDK·환경변수 경로를 제거하고 공시·종목 분석과 동일한 `ctx.ai_client` 및 `ai_analysis` 설정을 사용한다. API 요청에서 임의 provider/model 재지정을 금지하고, AI 비활성 상태는 외부 SDK 지연 생성 대신 503으로 일관되게 응답한다.
- [~] **AI API 운영 하드닝**: **일일 호출량 차단 완료(2026-07-19)** — SQLite로 재시작 후에도 사용량을 유지하고 provider 기준 태평양 날짜에 초기화한다. 기본 100회 중 20회를 공시 요약에 예약해 종목·랭킹 분석은 80회에서 HTTP 429로 차단하며, 공시는 총 한도까지 계속 시도한다. `daily_request_limit: 0`이면 로컬 차단을 끌 수 있고 랭킹 UI에도 구체적인 한도 메시지를 표시한다. 남은 작업은 서버 단위 동시 호출·단시간 재호출 제한, 429/일시적 5xx 재시도 정책, 입력 컨텍스트 예산, provider/model·지연·토큰 사용량 계측이다. 자동·반복 분석 확대 전 완료한다.

### X-3. AI 공시 요약 코드리뷰 후속 [완료 — 2026-07-19]

- [x] **Telegram 메시지 길이 제한 적용** — AI 요약을 최대 1,000자로 제한하고 초과 시 말줄임표를 붙인다. 장문 응답 회귀 테스트로 단건 메시지가 4,000자 이내인지 검증한다.
- [x] **Telegram 재시도 시 AI 중복 호출 제거** — `DartDisclosureMonitorTask`가 `receipt_no`별 AI 요약과 `None` 폴백을 프로세스 수명 내 캐시하고, Telegram 전송 성공 시 제거한다. 전송 실패 후 재시도에서 AI 호출이 1회로 유지되는 두 경로를 테스트한다.
- [x] **빈 API 키 Ollama 진단 지원** — `check_ai_key.py`는 Ollama에 한해 빈 키를 허용하고, `check_disclosure_ai.py`는 `enabled` 기준으로 analyzer를 생성한다. 두 진단 스크립트의 Ollama 빈 키 경로 테스트를 추가했다.

주요 파일: `services/telegram_notifier.py`, `task/background/always_on/dart_disclosure_monitor_task.py`, `scripts/check_ai_key.py`, `scripts/check_disclosure_ai.py`, `tests/unit_test/services/test_dart_disclosure_telegram.py`, `tests/unit_test/task/background/always_on/test_dart_disclosure_monitor_task.py`

### X-4. 종목 뉴스 AI 검토 [1차 완료 — PR #689, 2026-07-20]

공시(DART)는 규제 이벤트만 잡아내 언론 이벤트(수주·계약·소송·증설·리포트)가 사각지대였다. 네이버 금융 종목뉴스를 수집해 기존 `ctx.ai_client` 경로로 검토한다. **주기 없는 100% 온디맨드**(버튼 클릭 시에만) — 백그라운드 폴링·텔레그램·영속 저장 없음.

- [x] **1차 구현**: `services/stock_news_collector_service.py`(aiohttp+BeautifulSoup, euc-kr) · `services/ai_news_analyzer.py`(`usage_type="news"`) · `POST /api/stock/{code}/ai-news` · 종목 상세 UI 패널. 기사 본문은 받지 않고 제목·언론사·시각만 AI 입력으로 쓴다. 뉴스 0건이면 AI 미호출(한도 절약). 진단: `scripts/check_news_ai.py`(`--no-ai`로 한도 0건 수집 확인).
  - ⚠️ **네이버 스크래핑 계약 함정 2건** (실측 2026-07-20, 둘 다 예외 없이 "뉴스 없음"으로 조용히 degrade): ① **Referer 헤더 필수** — 없으면 기사 0건(User-Agent는 무관). 기존 `ThemeClassificationCollectorService`는 UA만 설정하므로 그 패턴 복사 시 빈 수집기가 된다. `clusterId=` 파라미터(빈 값)도 필수. ② **`tr.relation_lst` 행 자신도 제외** — 연관기사 블록이 중첩 `<table>`이라 그 행 자신이 중첩 anchor 때문에 `td.title a.tit`에 걸린다. 라이브 데이터에선 제목 dedup이 이 버그를 가려 고정 fixture 테스트로만 잡힌다.
- [x] **AI 사용량 가시화**: `AiUsageLimiter.get_snapshot()`에 `by_type` 추가 + 미노출 상태였던 스냅샷을 `GET /api/ai/usage`로 노출. `news`는 기존 인터랙티브 풀(daily 100 − disclosure_reserve 20 = 80건)에 편입되며 공시 예약분은 영향 없음.
- [x] **뉴스 수집 페이지네이션 (2026-07-20)**: 첫 페이지(클러스터 제외 후 ~10건)만 긁던 수집기가 `limit`(라우트 상한 15→30) 도달까지 최대 5페이지를 이어 붙인다. 페이지 간 제목 dedup, 마지막 페이지를 넘겨 같은 기사가 반복되면 중단, 도중 페이지 실패 시 수집분 유지. 뉴스가 몰리는 날(삼성전자 등) 검토 범위가 1~2시간치에서 넓어진다.
- [x] **AI 종합 분석에 뉴스 반영 (2026-07-20)**: `POST /api/stock/{code}/ai-analysis` 컨텍스트에 최근 뉴스 10건(제목·언론사·시각만, 링크 제외)을 추가하고 `sources.news`로 노출(UI 라벨 '최근 뉴스'). collector 부재·수집 실패 시 기존 소스만으로 계속 진행.
- [x] **AI 응답 잘림(max_tokens) 감지·해소 (2026-07-21)**: gemini-2.5-flash 의 thinking 토큰이 `max_tokens` 2048을 소진해 종합 분석·뉴스 검토 본문이 문장 중간에 잘리는 것이 실측됨(한줄 요약만 나오고 끊김). `AiClient._parse`가 `finish_reason=="length"`를 감지해 응답 끝에 ⚠️ 잘림 안내를 덧붙이고(전 usage_type 공통), example·로컬 config 의 `max_tokens`를 4096으로 상향.
- [x] **신호 판정 근거 강화 (2026-07-21)**: 프롬프트에 판정 규칙 명시(긍정/위험·호재/악재를 비교해 한쪽이 명백히 우세할 때만 상/하, 애매하면 중; 뉴스는 노이즈 제외 후 판정) + 둘째 줄 `신호 근거: <한 문장>` 강제. `extract_signal`이 (신호, 근거, 본문) 3-튜플로 확장되고 API `signal_reason`·UI 배지 옆 근거 한 줄(hover 툴팁 포함)로 노출. 근거 미출력 시 배지만 표시(기존 graceful degrade 유지).
- [x] **AI 신호등(상/중/하) 배지 (2026-07-21)**: 종합 분석·뉴스 검토 프롬프트가 첫 줄에 `신호: 상|중|하`(데이터/제목 톤 분류, 매매 지시 아님)를 출력하도록 지시하고, `services/ai_signal.py`의 `extract_signal`이 이 줄을 파싱·제거해 API `data.signal`로 내려준다. UI는 패널 제목 옆 🟢상/🟡중/🔴하 배지로 렌더, 형식 미준수 시 배지 숨김(본문만 표시). 본문의 '긍정 신호:'/'위험 신호:' 섹션 제목 오탐 방지를 위해 줄 시작·앞쪽 3줄만 인식.

**개선포인트 (미착수 — 우선순위 순)**

- [ ] **(추천, 서버 변경 0) 프론트엔드 결과 보존** — 현재 캐시가 없어 버튼을 두 번 누르면 한도가 2건 나간다. 주가·수급과 달리 **뉴스 목록은 몇 시간씩 그대로**라(삼성전자 첫 페이지 10건이 전부 같은 날 00:01~00:15에 몰림) 재분석 실익이 낮다. 이미 검토한 종목으로 돌아왔을 때 직전 결과를 다시 보여주고 재요청은 명시적으로만 받는다. 실사용에서 한도를 가장 많이 낭비하는 패턴(화면 왕복 중 재클릭)을 정확히 막는다.
- [ ] **수집 결과 해시 비교로 AI 스킵** — 스크래핑은 매번 수행(무료)하되 기사 제목 집합이 직전과 같으면 AI를 건너뛰고 이전 결과 재사용. 인메모리 dict로 충분. `by_type` 실측으로 뉴스 소비가 실제로 많다고 확인된 뒤 착수.
- [ ] **news 전용 하위 한도(`news_daily_limit`)** — 지금은 정할 근거가 없어 넣지 않았다. `GET /api/ai/usage`의 `by_type`을 며칠 실측해 stock/ranking 대비 news 비중을 본 뒤 판단한다(선구현 금지).
- [ ] **(조건부·큰 작업) 주기적 백그라운드 수집** — 관심종목 뉴스 폴링. 한도 소비가 관심종목 수 × 주기로 폭증하므로 **중요도 필터가 선행 조건**이며, 사실상 공시 파이프라인(dedup DB·재시도·다이제스트) 규모의 작업이다. 위 3개 항목 이후에 재검토.
- [ ] **기존 진단 스크립트 리미터 미부착** — `check_ai_key.py`·`check_disclosure_ai.py`는 `AiClient`에 `usage_limiter`를 붙이지 않아 소비가 `/api/ai/usage` 집계에서 빠진다(`check_news_ai.py`는 부착됨). 저위험 정리 과제.

주요 파일: `services/stock_news_collector_service.py`, `services/ai_news_analyzer.py`, `services/ai_usage_limiter.py`, `view/web/routes/stock.py`, `view/web/routes/ai.py`, `view/web/static/js/stock.js`, `scripts/check_news_ai.py`, `tests/frontend/run_stock_dom_tests.mjs`

---

## P1. 전략 수익성 검증 [최우선 승격 — 2026-07-02 리뷰]

### 1-5. 백테스트 검증 확장 [승격: 캡처 가동은 즉시 착수 가능]

- [~] **(최우선) 장중 microstructure 캡처 파이프라인 상시 가동** — `scripts.capture_backtest_microstructure`를 장중 운영 루틴으로 돌려 후보 종목 bid/ask·잔량·체결강도·프로그램매매 리플레이 코퍼스 축적을 시작한다. VBO/OSB의 수급 게이트(체결강도 ≥120%, 프로그램 순매수 ≥ 거래대금 10%)가 현재 백테스트로 검증 불가능한 상태 — 백테스트가 검증하는 전략과 라이브가 거래하는 전략이 다르다. 이 캡처가 엣지 판별의 크리티컬 패스. (2026-07-02 리뷰)
  - 가동 확인 + 1일차(20260703, 후보 10종목) 산출물 검증에서 품질 결함 3건 발견·보정 (2026-07-04): ① 프로그램 overlay 전량 null — 태스크가 DB 파일 존재만으로 `program_db` 소스 확정하는데 `pt_subscriptions`가 고정 4종목뿐이라 후보 행 없음 → DB 미스 종목만 daily_rest per-code 폴백 + `metadata.program_fallback_codes` 기록. ② 무거래/정지 종목에 직전 거래일 분봉 유입(033160에 2025-12-30 행 57건) → `trade_date` 불일치 행 필터. ③ 분봉 0건 종목 무플래그 → `metadata.quality`(empty_minute_codes·stale_minute_rows_dropped) + 태스크 last_result/로그 노출.
  - 프로그램 장중 시계열 캡처 경로 배선 완료 (2026-07-04): `ProgramCaptureSubscriptionTask`(intraday)가 장중에 캡처 후보(보유+워치리스트)를 LOW 우선순위 `PROGRAM_TRADING` 구독(cap 10종목, PT=2슬롯)으로 동기화해 `pt_history`에 장중 순매수 시계열을 축적 → 장마감 캡처가 program_db 소스로 소비(미커버 종목만 daily_rest 폴백). 수동 UI PT 구독은 제외, 슬롯 압박 시 트레이딩 구독이 아닌 이 카테고리가 먼저 해지됨. 구독 목록 영속화로 크래시 잔재는 재시작 시 자동 정리. `program_capture_subscription_enabled`(기본 on).
  - 캡처 QC 리포트 추가 (2026-07-04): `scripts/analyze_backtest_microstructure_quality.py`로 `replay_microstructure_*.json`의 intraday/execution_strength/program overlay 커버리지, stale row, program DB/fallback 비율을 날짜별 집계하고 `--fail-on-gate`로 품질 게이트를 자동 판정한다.
  - 캡처 태스크 QC 노출 추가 (2026-07-04): `MicrostructureCaptureTask.last_result`에 `quality_gate_passed`, `quality_issues`, intraday/execution_strength/program/program_db coverage를 노출하고, 게이트 실패 시 warning 로그와 BACKGROUND/WARNING notification을 남긴다.
  - 체결강도 장중 시계열 캡처 배선 완료 (2026-07-04): WS 체결 틱(H0STCNT0)에 포함된 `체결강도`를 `PriceStreamService.on_price_tick`에서 종목당 60초 샘플링해 `es_history` SQLite(`data/execution_strength/`, `ExecutionStrengthRepository`)에 축적 — **신규 WS 구독 없음**(기존 PRICE 틱 무임승차, 슬롯 비간섭). 장마감 캡처가 `execution_strength_source=es_db`로 소비하고 DB 미스 종목은 기존 REST 스칼라 유지 + `metadata.execution_strength_fallback_codes` 기록. QC에 `execution_strength_db_coverage_pct`(임계 30% — 배선 전손 감지용) 노출, overlay 파일 `replay_execution_strength_intraday_*.json` 추가. `execution_strength_capture_enabled`(기본 on).
  - 최우선 호가·잔량 장중 시계열 캡처 배선 완료 (2026-07-21): 기존 PRICE 체결 틱(H0STCNT0/H0UNCNT0)에 포함된 `매도호가1`·`매수호가1`·1호가 잔량·총호가 잔량을 종목당 60초 샘플링해 `data/orderbook_snapshots/orderbook_snapshots.db`에 축적한다. 신규 WS 슬롯을 쓰지 않으며 장마감 캡처가 `replay_orderbook_intraday_YYYYMMDD.json`으로 내보낸다. QC에 `orderbook_db_coverage_pct`(임계 30%, 종목당 최소 30행)와 실패 이슈를 추가했다. `--microstructure-dir` replay는 분봉 시각 이전 최신 호가만 정렬하고 120초 초과 스냅샷을 폐기해 실제 bid/ask 스프레드를 체결 모델에 전달한다. 이는 fill 모델에 필요한 **top-of-book** 캡처이며, H0STASP0 별도 구독이 필요한 10단 전체 호가장은 슬롯 정책 확정 후 후속 검토한다. `orderbook_capture_enabled`(기본 on).
    - 운영 재시작 실측(12:11): 약 1분 내 19종목 20행 저장, 1호가/잔량 NULL 0건, 역전호가 0건. PRICE active 25/pending 0, price gap 0.2초 상태에서 별도 슬롯 증가 없이 누적 확인.
    - 후속 replay/QC 검증(12:45): 586행·19종목, NULL/역전호가 0건, 16종목이 QC 최소 30행 충족. replay bar는 날짜별 quality sidecar를 전역 manifest보다 우선하고, 미래 호가 및 120초 초과 stale 호가를 제외한 뒤 실제 bid/ask를 체결 스프레드에 사용한다.
    - 장후 전체 후보 검증(16:12): 31종목 분봉은 종목당 391행(총 12,121행)으로 100% 복구. 반면 원본 장중 시계열은 체결강도 17/31(54.8%), 호가 QC 충족 15/31(48.4%)로 부분 커버임을 확인했다. 후속 개선으로 기존 LOW PT 캡처 10종목을 30분 단위로 순환하고, 우선주는 PRICE 전용으로 포함한다. 원본 체결강도·호가 QC는 50% 미만 실패, 50~70% 경고로 강화했다.
  - QC 1주차 판정 (2026-07-08, 4거래일 07-03/06/07/08 실측): ① program fallback 2종목(07-06)→0→0 수렴 — PT 구독 배선 의도대로 작동, program_db 완전 커버 도달. ② ES fallback(일 4~6종목)은 3일 모두 `empty_minute_codes`(무거래/정지 종목)와 정확히 일치 — 거래가 있었던 후보는 ES 시계열 전량 커버, "무틱 ~55% 제한" 우려는 캡처 후보군에선 실측상 미발현(표본 9~12종목/일 단서). ③ stale row 필터 작동(033160 매일 57행 드랍). 이후 관찰은 이 기준선 대비 악화 여부로 판단.
  - 캡처 전용 후보 확장 (2026-07-08): 워치리스트 기근(Pool B 항목 참조)으로 코퍼스 폭이 9~12종목/일에 갇혀, `resolve_capture_codes`에 거래대금 상위 랭킹 보충(기본 20종목, ETF 프리픽스 제외, 조회 실패 시 무보충) 추가 — 장마감 캡처·장중 PT 구독 공통 적용, 트레이딩 후보/주문 경로 불변. 랭킹 API는 실전 전용이라 모의 환경에선 자동으로 기존 동작.
  - QC 2주차 판정 (2026-07-17, 07-09~16 6거래일 실측): 코퍼스 폭 34종목/일 안착(랭킹 보충 효과). ① program fallback 10→8→5→4 — 1주차 "0 수렴" 기준선과 다르나 후보 34 vs PT 구독 cap 10의 구조적 결과이며 program_db coverage는 48→88%로 상승 추세(pt_history 누적·구독 로테이션). ② ES fallback(일 14~19종목)이 empty_minute(9~11) 초과하는 초과분은 랭킹 보충 대형주(PRICE 미구독 → 설계된 EOD 스칼라 폴백)로 설명 가능. ③ stale 필터 정상(033160 57행/일, 07-16부터 019210 89행 추가).
  - **⚠ QC 게이트 실패 = 진짜 분봉 캡처 결함 (정정 2026-07-17)**: 07-09~16 6일 전부 `quality_gate_passed=false`(intraday_coverage 45~74% < 80%). **당초 "무거래/정지 종목 탓 구조적 오탐"으로 기록했으나 재검증 결과 오류.** empty_minute 종목(일 9~11개)은 무거래가 아니라 **매일 정상 거래되는 유동주인데 분봉만 0행**이다 — 삼성전자우·HPSP·원익IPS·주성엔지니어링·후성·SK증권·엠케이전자 등이 매일 재현되며, 이들 전부 program 순매수 nonzero + REST 체결강도 스칼라(매일 변동)를 보유(=거래 입증)하는데 `get_day_intraday_minutes_list`가 첫 배치부터 빈 응답/에러를 반환(stale 필터 아님, 대조군 에스엘 377행). intraday 30% 결손은 시장 상태가 아니라 캡처 결함이고 QC 게이트는 이를 정확히 잡고 있었다. → **다음 착수: empty-minute 원인(rt_cd/msg) 계측 후 근본원인 진단** (아래 신규 항목).
- [~] **(신규 2026-07-17) 분봉 캡처 결손 원인 계측·근본진단**: `get_day_intraday_minutes_list`가 특정 유동주(삼성전자우·HPSP·원익IPS 등 stable set)에 대해 첫 배치부터 empty/에러를 주는 원인을 진단.
  - 계측 배선 완료 (2026-07-18): `BacktestMicrostructureCaptureService._diagnose_empty_minutes`가 분봉 0행 종목을 `get_intraday_minutes_by_date` 단일 배치(end_hhmmss 기준=첫 배치와 동일)로 재조회해 `metadata.quality.empty_minute_reasons`에 원인 분류 기록 — `api_error:{rt_cd}:{msg}` / `empty_response`(무거래 확정) / `stale_date_only`(직전거래일 행만) / `has_rows_capture_empty`(단일 프로브엔 당일 행 있으나 페이지네이션 캡처는 빔=커서 갭) / `probe_failed` / `no_probe`. 프로브는 empty 종목만(일 ~10건) 대상, try/except로 캡처 불파괴. 태스크 `last_result.quality`로 자동 노출.
  - 원인 확정·수정 (2026-07-21): 20260720 런의 `empty_minute_reasons`는 `has_rows_capture_empty` 3건 + `stale_date_only` 6건. 운영 로그에서 모의투자 캡처가 오늘 날짜를 명시했다는 이유로 실전 전용 `inquire-time-dailychartprice`를 호출하고, KIS가 분봉 대신 투자자 일별 행을 반환해 전량 필터되는 경로를 확인. 오늘 날짜를 `date_ymd`로 명시해도 모의/실전 공통 당일 분봉 API를 사용하도록 수정.
  - 즉시 재검증 통과 (2026-07-21 10:02): 직전 결손 9종목만 재수집해 종목당 391행(총 3,519행, 09:00~15:30), intraday coverage 100%, empty 종목 0, stale 제거 0을 확인했고 분봉 QC 게이트를 통과. 호출 로그에서도 수정된 당일 `inquire-time-itemchartprice` 경로 사용을 확인. 단, 장중 모의 API가 장 종료 시각까지 행을 반환하므로 운영 전체 후보의 최종 판정은 아래 16:05 자동 캡처로 확인한다.
  - WebSocket 슬롯 보호 보정 (2026-07-21): 재시작 복원 시 PT 18종목이 36슬롯을 선점해 PRICE 요청 20종목 중 3종목만 활성화되던 문제를 보정. 현재 PRICE 요청 수(최소 10슬롯)를 먼저 예약한 뒤 남은 슬롯으로 PT 복원 상한을 동적 계산한다. 운영 재검증에서 persisted `program/legacy` 직접 복원과 캡처 정책 동기화가 같은 슬롯을 이중 계산해 churn하는 추가 원인도 확인 — 워치독은 명시적 `manual` PT만 직접 복원하고 `program`은 캡처 태스크가 단일 관리, `legacy`는 삭제 없이 대기시킨다. 상태 API에는 desired/active/pending/capacity_pending 개수를 분리 노출해 `subscribed_*`(legacy desired 계수) 오독을 방지. 최종 재시작 실측: PRICE active 25/pending 0(기존 active 3), PT active 13/pending 25, data gap 0.2초·price gap 0.1초.
  - 불완전 코퍼스 격리 (2026-07-21): 캡처 본문 `metadata.quality_gate.valid_for_backtest`와 `replay_quality_YYYYMMDD.json` sidecar를 영속화하고, QC 분석기에 `--output-manifest`를 추가. 기존 07-03~07-20 11거래일은 `microstructure_quality_manifest.json`에서 전부 invalid로 표시해 백테스트 사용 대상과 분리(원본 보존).
  - [ ] 변경 반영 후 다음 16:05 캡처에서 intraday coverage ≥80% 및 `empty_minute_reasons` 축소 확인.
  - 캡처 후보 소스 구분 metadata 기록 (2026-07-17): `resolve_capture_codes_with_sources`로 base(보유+워치리스트) vs ranking_supplement를 구분해 `metadata.candidate_sources`에 기록, 태스크 `last_result.candidate_source_counts` 노출. 기존엔 info 로그에 개수만 남아 replay 파일 단독으로 PIT 후보(base) 식별이 불가능했던 갭 해소 — 1-8 해제 조건 (a)의 전제.
  - ※ 남은 한계: 체결강도 시계열 커버리지가 "PRICE 구독 중 + 유틱" 종목으로 제한(무틱 ~55% — 2-4 해소 시 개선, 미커버는 EOD 스칼라 폴백; 랭킹 보충분도 PRICE 미구독이면 EOD 스칼라). 남은 것: base 종목 기준 fallback 발생 여부 일일 관찰(신기준선), QC 게이트 coverage 정의 재조정.
- [blocked — 캡처 코퍼스 축적 후] 실제 replay fixture를 통과 케이스까지 확장 → replay overlay.
- [ ] 한국장 실전 microstructure fixture(bid/ask book·잔량·체결강도·프로그램매매 overlay)로 체결 모델 보정 + 시장가/최유리/지정가별 fill quality가 live journal과 얼마나 벌어지는지 리포트.

주요 파일: `services/backtest_execution_simulator.py`, `services/backtest_replay_context.py`, `services/backtest_microstructure_capture.py`, `repositories/execution_strength_repo.py`, `scripts/run_backtest.py`, `tests/fixtures/backtest/`

### 1-8. 현행 전략 버전 백테스트 재실행 [blocked — PIT 후보 부재, 2026-07-03 파일럿 판정]

- [x] 슬리피지/스프레드 CLI 노출 (#619) — `--market-slippage-pct`(MARKET/STOP 체결)·`--spread-pct`. LIMIT 체결엔 미적용(시뮬레이터 의미론).
- [blocked] 활성 전략 전체 walk-forward + Monte Carlo 재실행 + 민감도 표 — **2026-07-03 파일럿(VBO 6/15-30, PP 6/1-12) 결과 전 구간 0거래**로 현시점 무의미.
  - 원인 ①: 백테스트 유니버스가 **현재 시점** `data/premium_stocks.json`(PIT 아님) — 2026-07-03 재생성 기준 KOSPI 0종목/KOSDAQ 0종목이라 어떤 과거 구간을 돌려도 후보가 없다.
  - 원인 ②: 6월 하순 양시장 MA 하락 → 마켓 타이밍 게이트가 스캔 차단(`market_timing_off_both`) — 롱온리 전략의 정상 휴식.
  - 해제 조건: (a) 1-5 캡처 코퍼스 축적(`replay_microstructure_*.json`의 `metadata.codes`가 당일 후보 스냅샷 = PIT 후보군 역할) 또는 (b) 시장 회복으로 Pool B 복원. ※ 2026-07-08 랭킹 보충으로 codes 폭 확대 — 단, 보충분은 필터 통과 '후보'가 아닌 유동성 상위 종목이므로 PIT 후보군으로 해석할 때 소스 구분에 유의. 2026-07-17부터 `metadata.candidate_sources`(base/ranking_supplement)가 파일에 기록되므로 PIT 후보군은 base만 사용(그 이전 파일은 로그 교차 참조 필요).
  - 실행 메모: 첫 런이 REST 지배적(12거래일 ≈ 18~23분), **캐시 워밍 후 변형 런 ≈ 1분** — 후보만 갖춰지면 민감도 그리드는 저렴.

주요 파일: `scripts/run_backtest.py`, `services/backtest_walk_forward.py`, `docs/backtest.md`, `data/backtest_*.json`

### 1-6. 실전 수익성 데이터 확보와 profitability gate 운영 [라이브 축적 — 코드 착수 가능]

- [ ] shadow / paper / 소액 canary journal을 표준 포맷으로 누적하고, 전략별 profitability gate 통과 근거를 리포트한다. (journal은 `virtual_trade_service.get_standard_journal_records` ← polling scan + REST 가격 경로로 채워져 WS 틱 비의존 — 무틱 블로커와 독립적으로 축적 가능. shadow 하위 요소만 2-4와 동일 의존. 라이브 런타임 시간만 필요.)
  - 표준 journal 축적 현황 리포트 추가 (2026-07-04): 일일 전략 리포트에 source별 레코드 수, SOLD/진행중 status, 전략별 SOLD 표본 진행률(`min_trades` 대비)을 노출해 paper/canary 데이터가 gate 해제 기준까지 얼마나 쌓였는지 확인 가능.
  - 전략명 별칭 통합 (2026-07-09): 2026-05-26경 기록 전략명이 표시명→ID로 전환되며 표본이 두 표기로 갈라져 있던 것을 `normalize_virtual_trade` read-time 별칭 매핑으로 통합(래리윌리엄스VBO→larry_williams_vbo 등 4쌍, DB 불변). 통합 후 실측 진행률: VBO SOLD 34/100, RSI2 14, CB 4 — 7월은 마켓타이밍 차단으로 +3건뿐(설계된 정체). regime 태그는 62건 중 53건 null(태깅 도입 전)이라 regime별 최소 30은 사실상 신규 축적 필요.
  - 최소 유지 기준: 실전 override `min_trades=100`, `profit_factor>=1.3`, `payoff_ratio>=1.2`, `win_rate>=40%`, `max_drawdown<=12%`, regime별 최소 거래 30. parameter stability·Monte Carlo·regime balance·multiple testing 보정을 운영 편의로 낮추지 않는다.

주요 파일: `services/strategy_live_expansion_gate_service.py`, `services/strategy_profitability_gate_service.py`, `scheduler/strategy_scheduler.py`, `services/strategy_log_report_service.py`

### 1-7. Multiple testing / 과최적화 방어 고도화 [canary 데이터 후]

- [~] formal PBO/DSR의 profitability gate **hard 적용 정책** 결정 — PBO/adjusted-Sharpe hard gate 옵션과 real-mode overlay(`require_multiple_testing_adjustment=True`, `multiple_testing_max_pbo_probability=0.5`)는 구현됨. DSR hard threshold만 미정 → canary 성과 축적 후 enforce 기준 확정.
- [ ] 전략 수·필터 조합 증가 시 canary 통과 기준과 자금 확대 기준 분리(자금 확대 = formal 검증 + 실전 성과 + regime 일관성).
- 우선순위 낮음: purged K-fold 별도 CV 러너(walk-forward+embargo·CSCV embargo로 실질 커버).

주요 파일: `services/multiple_testing_bias_service.py`, `services/strategy_ablation_service.py`, `scripts/run_backtest.py`

---

## P2. 시스템 성능

### 2-4. Polling → event-driven 전환 [최우선 블로커 — KIS 에스컬레이션]

- **[최우선 블로커] WebSocket price 피드 무틱 ≈55%**: 구독 종목 절반 이상이 종일 `subscribed_no_tick` → shadow parity 수집 불가 + 라이브 실시간 데이터 품질 문제. **이 레포의 코드 작업은 종결**(무틱 종목 격리 구현 완료). 진단 확정: 종목·상품군·계정 단위 **KIS측 프레임 미전송**(`a1_kis_no_send`).
  - 근거: 2026-06-19 로그 진단(`reports/no_tick_diagnosis_20260619.md`) + 2026-06-22 운영 실험 A~D(`reports/no_tick_operational_experiment_analysis_20260622_live.md`) — subscribe/ack/quality_reject 전부 0, received 5 vs no_tick 18. 보통주 일부만 0틱(종목 단위), ETF/우선주 전부 0틱(상품군 단위), 격리해도 0틱 지속(계정 단위), refresh 무효.
  - **정책 결정(2026-07-01)**: ETF/우선주는 WS tick 없음으로 **간주**(상품군 단위 무틱 수용) → B군 KIS 문의 **드롭**. REST 폴링 경로로 처리하고, 무틱 지표에서 ETF/우선주는 정상(예상된 무틱)으로 본다. 코드 변경 없음(사후 격리가 이미 churn 중단). ※ 실전 전략은 보통주 롱온리라 ETF/우선주 WS 구독은 부수적.
  - **다음 액션(코드 아님)**: 접수 준비 **완료**(#630, 패키지 완결성 재확인 2026-07-08 — 문의 문안·첨부 목록·완료 판정 기준 포함). 무틱 **보통주**만 대상 문의 패키지 `reports/kis_no_tick_common_stock_escalation_20260705.md`와 C군 runner 출력(`reports/no_tick_operational_experiment_result_C_no_tick_common_solo_20260622_live.{json,md}`) 첨부. **남은 액션: 실제 KIS 접수(사용자 외부 액션)뿐.** 완료 판정은 KIS 답변으로 서버/권한/종목 예외 확인 또는 재현 실험 요청 수신.
  - ※ 폴링 지연(5분 scan + stagger + limiter)은 돌파 전략의 실질 슬리피지로 작용 — 무틱 해소 → event-driven 전환이 VBO 계열 수익성의 전제 조건이기도 하다. (2026-07-02 리뷰)
- [ ] (블로커 해소 후) `event_shadow`/`event_shadow_exit` 5거래일 jsonl 수집 → `scripts/analyze_event_shadow_parity.py`로 entry/exit parity 리포트 → PR-3 진입 판정.
- [ ] event-driven signal은 별도 승인 전 shadow/latency 측정용으로만 운영(실주문은 polling + full gate 경로만). VBO fast path는 execution strength/program-buy 생략.
- [blocked] PR-3: 관찰 양호 시 VBO 실 적용 + OSB shadow 진입. / PR-4+: 단계적 확장.

구현 결정(`docs/event_driven_architecture.md` §9): event throttle 0.5s / stale snapshot 5s / shadow 운영 1주(5거래일) / `signal_source`=metadata JSON 키 / trigger crossing은 평가 허용·publish만 debounce.

주요 파일: `services/strategy_event_router.py`, `services/event_shadow_journal_service.py`, `services/price_stream_service.py`, `services/streaming_service.py`, `brokers/korea_investment/korea_invest_websocket_api.py`, `task/background/intraday/websocket_watchdog_task.py`, `scheduler/strategy_scheduler.py`

### 2-2. API 호출 최적화 [외부 — 운영 직전 재확인]

- [~] API budget limiter 운영 정책 — 동시성/rate 분리·emergency overlay·전역 8/s 등 구현 완료.
  - 남은 작업(외부): 실제 KIS 계정별 REST/WebSocket 유량 한도 숫자를 공식 포털/계정 공지로 **운영 직전 재확인** → 필요 시 `_global` 8/s 운영값 조정. 공개 자료가 갈리므로 코드 기본값은 보수값 유지.
  - [x] 남은 작업(내부) **종결** (2026-07-08): #635/#638 BudgetSnapshot 계측 3거래일(07-06/07/08) 실측 — `quotation_ohlcv`만 유의미 대기(콜당 0.87~1.02s, 일 1,250~1,540콜에 총 ~21분)이나 **그 99%가 장외 16:15~16:45 장마감 OHLCV 배치에 집중**, 장중 대기는 일 2~69s. 07-02 결론의 "폴링 지연=budget 슬리피지" 성분은 **장중에선 이미 해소된 것으로 실측 확인**(콜당 2.61s→0.87~1.02s, quotation 1.46s→0.01s). 배치 피크 구간 전역 사용률 ~3.2/s(8/s 대비 헤드룸 ~4.8/s)라 카테고리 한도 상향 여지는 있으나 실익이 배치 10~15분 단축뿐이라 **현행 유지 결정**. 잔여 관찰: 07-07 하루 `quotation` 세마포어 대기 578s 1회 관측 — 재발 시 동시성 4→6 검토.

주요 파일: `core/retry_queue/api_budget_limiter.py`, `docs/api_budget_coverage_matrix.md`

### 2-6. 라이브 핫패스 성능 — 잔여 [보류]

- [~] 활성 전략 `scan()` 잔여 순차 후보 처리를 `bounded_gather`로 전환 — **보류**(저비용 단계 대다수 탈락 + 전역 limiter 8/s 직렬화로 실익 marginal). 재개 시 2-pass(돌파 후보만 `execution_strength` bounded)가 외과적.

주요 파일: `strategies/larry_williams_vbo_strategy.py`

---

## P0. 실전 손실 방지

### 0-1. 실전 KIS `inquire-daily-ccld` 응답 필드 검증 [외부 데이터 의존 — 실전 전환 직전 필수]

- [~] 실전 submit response/체결통보(signing notice) raw fixture를 확보해 `BrokerOrderResponseMapper` 회귀를 보강한다.
  - 남은 것: 취소/거부 실전 row 포함 raw fixture 추가 확보 시 회귀 고정.
  - ※ 체결 대사가 실전 필드명에서 깨지면 킬스위치 손익 집계 자체가 틀어진다 — 실전 첫날 소액 검증 필수. (2026-07-02 리뷰)

주요 파일: `common/broker_order_response_mapper.py`, `services/order_execution_service.py`, `services/fill_reconciliation_service.py`, `tests/fixtures/kis/`

---

## 해외주식 전략 적용 (VBO 일봉)

결론: 일봉 셋업형 전략만 적용 가능(해외 일봉 API 존재), 장중/실시간 전략은 불가. 첫 대상 = `LarryWilliamsVBOStrategy`. 제약: **해외 주문 TR은 실전(TTTS6036U 등)만, 모의 주문 TR 없음** → dry-run 검증 전 실주문 배선 금지. Phase 1~4(데이터 어댑터·일봉 백테스트·dry-run·주문/사이징) 완료, 자동 전략 경로 `live_enabled=False` 잠금.

### O-1. 미국 휴장일/조기폐장 캘린더 [완료 — #621, 2026-07-03]

- [x] 규칙 기반 NYSE 캘린더 `USMarketCalendarService` 추가 — 전휴장 10종(신정 토요일 무관측·성금요일 computus·준틴스 2022~) + 조기폐장(13:00 ET) 3종(7/3·추수감사절 익일·12/24) + `get_close_time_str()`(Phase 5 EOD 청산 소비용). `time_dispatcher_us`/`OverseasDryRunTask`/`MarketCapGapReportTask` 3곳 주입. KIS에 해외 휴장일 TR이 없어 로컬 규칙 계산. 한계: 임시 특별휴장 미반영(docstring 명시).

주요 파일: `services/us_market_calendar_service.py`, `task/background/after_market/overseas_dryrun_task.py`, `view/web/bootstrap/service_container.py`

### O-2. dry-run 비용/진입가 가정 보정 [신규]

- [x] `scripts/analyze_overseas_dryrun.py`의 왕복 비용 기본값 0.2%를 미국주식 온라인 기본 수수료 0.25%/side 기준 0.5%로 보정 (2026-07-04). 환전 스프레드·SEC/TAF 등 매도 제비용은 별도이므로 리포트에 `commission_only` 가정으로 명시.
- [x] 일봉 기반 would-be 진입가의 낙관 편향(장중 실체결가 대비 유리하게 잡힘)을 dry-run Markdown 리포트 `가정/주의` 섹션에 명시.

주요 파일: `scripts/analyze_overseas_dryrun.py`, `services/overseas_vbo_dryrun_service.py`

### Phase 5. 안전/canary [dry-run 검증 후 — O-1/O-2 완료]

- [ ] **Phase 5 안전/canary**: `get_overseas_balance`/`ccnl` reconcile(`OverseasReconcileService` scaffolding 존재), risk gate/kill switch/canary USD 확장, 실전 소액 canary, canary auto-fire 배선 + `live_enabled=True` 전환 — dry-run 검증 + canary 게이팅.

주요 파일: `brokers/korea_investment/korea_invest_overseas_stock_api.py`, `brokers/broker_api_wrapper.py`, `services/overseas_order_execution_service.py`, `services/overseas_position_sizing_service.py`, `services/overseas_reconcile_service.py`, `services/stock_query_service.py`, `view/web/bootstrap/{service_container,strategy_factory}.py`, `config/tr_ids_config.yaml`

---

## M. 유지보수 / 구조 [신규 섹션 — 2026-07-02 리뷰]

### M-1. 포지션 사이징 단일화 [재조사 완료 2026-07-05 — 필수 게이트 아님으로 하향]

- [x] **재조사 결과: "4곳 분산"은 과장된 진단이었다.** 실측 결과 3곳은 이미 하나의 파이프라인(`PositionSizingService._compute_buy_qty`)으로 수렴되어 있었다.
  - `position_sizer`는 "후주입 옵션"이 아니라 `service_container.py`에서 상시 구성되어 프로덕션 경로에 항상 주입됨 — canary/real_mode별 `per_trade_risk_pct` 계층(0.25%/0.5%)까지 이미 존재.
  - RiskGate `max_order_amount_won`은 `_calc_max_order_amount_qty`를 통해 이미 이 파이프라인의 `min()` 후보 중 하나로 통합되어 있음 — 별도 경로 아님.
  - `order_qty=1`(스케줄러 config)은 신규 진입 사이징과 무관 — 강제청산 시 보유수량 조회 실패 폴백 전용([strategy_scheduler.py:68](scheduler/strategy_scheduler.py:68)에 주석 명시).
  - 남은 유일한 잠재적 중복은 `position_size_pct`(전략 config, 7곳)인데, `final_qty = min(candidates.values())` 구조상 이는 `signal_cap`으로만 참여해 risk 기반 계산보다 **더 타이트해질 수만 있고 느슨해질 수 없음**을 코드 구조로 증명([position_sizing_service.py:222](services/position_sizing_service.py:222)에 주석 명시) — 자금 확대 시 사고 위험 없음.
  - 로그 실측(최근 3일 `[PositionSizing]` 결정 로그)은 캐너리 신호 자체가 희소해(3일간 1건) 표본 부족으로 보류 — 위 구조적 증명으로 안전성 판정을 대체.
- [ ] (저위험, 선택) `position_size_pct` 필드가 risk 기반 계산에 의해 사실상 항상 지배되는지 canary 신호가 쌓인 뒤 재확인하고, 지배 확정 시 7개 전략 config에서 deprecate 검토(안전 문제 아닌 정리 과제).

주요 파일: `services/position_sizing_service.py`, `scheduler/strategy_scheduler.py`, `view/web/bootstrap/strategy_factory.py`, `strategies/oneil_common_types.py` 등 전략 config 계열, `config/config_loader.py`

### M-2. 문서 동기화 + 구조 감시 [저위험 상시]

- [x] `docs/backtest.md`(본문은 #619 슬리피지/스프레드 CLI까지 이미 반영, 날짜만 동기화)·`CODEBASE_SUMMARY.md`(bootstrap 분해 진전·EventShadowManager 분리·해외/테마 계층·브로커 계층 계측 4곳 반영) 갱신 완료 (2026-07-05).
- [x] **`service_container.py` 비대화 경고 해소 — bootstrap 분해 + 의존성 경계 테스트 추가 (2026-07-12)**: #659(서비스↔전략 의존성 경계 강제, `oneil_common_types.py`를 `strategies/`→`common/`으로 이관, `test_architecture_dependencies.py` 신규) · #660(`backtest_task_bootstrap.py` 추출) · #661(`repository_bootstrap.py` 추출) · #662(`market_data_bootstrap.py` 추출) · #663(`query_bootstrap.py` 추출) · #664(`realtime_bootstrap.py` 추출) 6개 PR로 `service_container.py`가 962→**710줄**로 감소, 07-05까지의 증가 추세 반전.
- 감시(조치 아님, 2026-07-18 실측): `scheduler/strategy_scheduler.py` 2,153→**2,426줄**(+273) / `services/strategy_log_report_service.py` 2,144→**2,298줄**(+154, 07-08 "정체" 판정 이후 증가 재개) / `view/web/bootstrap/service_container.py` #659~664 분해로 710줄까지 감소 후 **746줄**로 재증가(+36) — 분해는 3-4 재승격과 함께 진행.

### M-3. 시가총액/미국주식 UI 하드닝 후속 [#653 코드리뷰 xhigh, 2026-07-11]

#653(marketcap/overseas UI 하드닝)의 취지는 유효하나, 리뷰에서 즉시 수정 2건 + 하드닝 보완 4건 + 중복 정리 후속을 확인.

**즉시 수정 (버그) — [완료 2026-07-11, jsdom 회귀 테스트 동반]**

- [x] **marketcap.html 캐시버스팅 갱신** — `marketcap.html:18`을 `?v=2`로 올려 캐시된 구버전(무방비) 스크립트 서빙 차단(#653 XSS 수정 실효화).
- [x] **환율 null → "USD/KRW 0" 오표시** — `loadOverseasMarketCap`에서 `data.fx_rate == null ? NaN : Number(...)` 선제 검사로 null 시 '환율 정보 없음' 표시.

**하드닝 보완 (#653과 같은 클래스) — [완료 2026-07-11]**

- [x] 잔고 조회의 거래소 기준이 다른 탭에 숨은 셀렉트 — `loadOverseasBalance`가 같은 '보유·주문' 탭의 `overseas-order-exchange`를 사용하도록 변경(숨겨진 개요 탭 `overseas-exchange` 대신).
- [x] `loadOverseasMarketCap` stale-response 가드 — `_overseasMarketCapSequence` 시퀀스 가드 추가(marketcap.js 패턴 이식).
- [x] catch 블록 진단정보 소실 — overseas.js 7곳·marketcap.js 1곳 catch에 `console.error` 추가(화면 문구는 불변).
- [x] 가용성 확인 1회성 — `setOverseasTab` 전환마다 `_refreshOverseasAvailability` 재호출(init 1회 + 탭 전환 이벤트 구동).

**중복 정리 (별도 리팩토링 PR — [완료 2026-07-11])**

- [x] **escapeHtml 단일화(깊이 수정)** — `common.js` escapeHtml을 `String(v??'')` 강제변환 + 작은따옴표 이스케이프로 하드닝하고 `showError`를 공용화. `_escapeMarketCapHtml`/`_escapeOverseasHtml`/`_showMarketCapError`/`_showOverseasError` 제거하고 공용본 호출로 통합(ranking.js·notifications.js도 하드닝 수혜).
- [x] `readJsonResponse`/오류 렌더러 common.js 승격 — `_readOverseasJson`을 `readJsonResponse`로 common.js에 이관, overseas.js 7곳 + marketcap.js 응답검증 블록이 공용본 사용.
- [x] MarketCapGapService 조립 단일화 — `MarketCapGapService.build_default(broker, logger)` 팩토리 도입, `service_container.py`와 `stock.py` 라우트가 모두 이 진입점 사용(생성 인자 변경 시 1곳 수렴). 라우트 지연 조립은 미개방 시 불필요 생성을 막는 이점이 있어 유지.
- [x] KRW/숫자 포매터 통일 — `_formatKrwMarketCap`이 공용 `formatTradingValue`(조/억) 위임(무효값 '-' 가드 유지), `_formatUsd`는 콤마 제거 후 파싱.
- [x] tests/frontend 러너 공용화 — 공용 `tests/frontend/harness.mjs`(test/assert/run + `applyCommonStubs`) 추출, `run_*_dom_tests.mjs` 7개가 로컬 러너 보일러플레이트 제거하고 import.
- [x] `get_us_market_caps` 병렬화 — `asyncio.gather(fetch_usdkrw, fetch_quotes)` + `_build_us_items` 헬퍼로 fx·시세 동시 조회.

주요 파일: `view/web/templates/marketcap.html`, `view/web/static/js/marketcap.js`, `view/web/static/js/overseas.js`, `view/web/static/js/common.js`, `view/web/routes/stock.py`, `view/web/bootstrap/service_container.py`, `services/market_cap_gap_service.py`, `tests/frontend/`

---

## 테마/분류 데이터

네이버 테마(주 소스)는 `ThemeClassificationTask` 자동 수집 가동 중(BATCH 모드 장마감 후, 기본 7일 간격). 수동 트리거 `POST /api/background/theme-classification/force-update`. 분류 데이터는 네이버 단일 소스로 충분 — 키움 등 추가 소스 연동 계획 없음(T-1 드롭). 멀티소스 병합 인프라는 `StockClassificationRepository`에 잔존하나 신규 소스 연결 계획 없음.

### T-0. StockEasy 섹터RS taxonomy 참고 (선택)

- [ ] StockEasy 종합 RS 화면(`stockeasy.intellio.kr/stock-analysis`)의 섹터/테마 분류를 네이버 테마 alias/표시명 후보 참고자료로 정리한다. StockEasy 자체를 무단 수집 소스로 고정하지 말고, 실제 구성종목 데이터는 네이버 등 수집 가능한 source에 귀속한다.
  - 주요 후보: 반도체소재, 지주사, 메모리, 비메모리/팹리스, 전력기기, 반도체장비, 보험, 건설, 테스트소켓, 유통, 로봇/자동화, 미용기기, 산업기계, 완성차, SW/AI, 자동차부품, 증권, 우주항공, 배터리셀, 통신, 원자력, 양극재, 신재생, 전자장비, 조선기자재, 조선, 타이어, 바이오신약, 방위산업, 음극재/소재, 은행, 정유/화학, 철강/비철, 의료기기, 해운, 여행/레저, 음식료, 패션/의류, 제약, 인터넷/플랫폼, 유틸리티, 리츠/부동산, 게임, CDMO, 화장품, 엔터/미디어.

---

## 조건부 / 정책 결정 대기

### Pool B 튜닝 (후보 부족 지속 — **트레이딩 완화는 보류, 캡처는 랭킹 보충으로 우회** 2026-07-08)

- **기근 지속**: 07-02 KOSPI 1/KOSDAQ 0 → 07-03 0/0 → 07-07 1/1 → 07-08 1/0. 양시장 MA 하락 구간이라 마켓 타이밍 게이트가 어차피 스캔을 차단 중 — 하락장에서 Stage 2+RS 필터가 마르는 것은 설계상 자연스러운 면이 있어, 완화가 실익(추가 진입 기회)으로 이어지는지는 시장 회복 국면의 후보 수로 판단할 것.
- **funnel 실측 (2026-07-08, 프리미엄 배치 3일 07-02/07/08 동일 패턴)**: 1차 통과 812~839종목 → **거래대금 100억 필터에서 596~620종목(73%) 탈락** → 통과 ~216종목 중 **정배열(current>ma20>ma50)에서 201종목(93%) 탈락** → 52주고가·스퀴즈 → 최종 1~2종목. what-if: 100억→50억 완화 시 +98~110종목이 정배열 관문으로 진행하나 최종 생존 추정 +2~7종목/일에 그침.
- **결정 (2026-07-08)**: 트레이딩 필터 완화는 보류 — 마켓타이밍 차단 중이라 진입 실익이 없고, 기근이 물고 있던 캡처 코퍼스 폭·1-8 PIT 후보 문제는 1-5 캡처 전용 랭킹 보충(주문 경로 불변)으로 우회 해결.
- [ ] (시장 회복 후 재판단) 거래대금 기준 완화 — Pool B 장중 임계 50억→30억 및 프리미엄 배치 임계 100억→50억(기근의 실측 관문은 배치 100억 쪽).
- [ ] (시장 회복 후 재판단) 정배열 조건을 Pool B 전용 `current > ma_20d` 중심으로 완화 검토.

### R-2. 전략 상관 / 단일 regime 집중 [엣지 도입 — Phase 1~3 완료, Phase 4 데이터 대기]

- 활성 7전략 전부 long-only 모멘텀/돌파/눌림목 → 단일 "상승/추세 regime 베팅". 상관행렬·regime 분해는 일일 리포트에 노출 완료.
- 비상관 엣지 = **인버스 ETF 레짐 슬리브**(KOSPI bear에서만 -1x 인버스 ETF 추세추종 매수, long-only와 음의 상관). 직접 숏(공매도)은 개인 비현실적이라 제외.
  - 완료: Phase 1 전략(#594) · Phase 2 다중 베어 사이클 백테스트(#595, 실데이터 46거래 복리 +20% MDD −11.8%) · Phase 3 factory 배선 `enabled=False`(#596, shadow/paper 관찰). 일봉+REST라 ETF 무틱(2-4) 무관.
- [ ] **Phase 4 (베어 paper 데이터 + 정책 후 구현)**: profitability gate는 전역 단일 config(per-strategy override 없음)라 인버스 슬리브가 표준 gate와 3중 충돌(① win_rate 0.35/0.40 vs 실측 34.8% ② min_trades 30/100 vs regime-conditional ③ regime-balance vs BEAR 단일). **방향 A 확정**: 추세추종 디코릴레이터엔 win_rate가 잘못된 기준 → gate에 per-strategy override 추가 + 인버스 전용 프로파일(win_rate 완화/제거, payoff·profit_factor·양(+)PnL 유지, min_trades 완화+≥2 독립 베어 에피소드, regime-balance 면제). 임계값 보정할 paper 베어 데이터가 없어 **다음 베어장 paper 축적 후 구현**(선구현 금지). 이후 canary → `enabled=True` 전환.

주요 파일: `strategies/inverse_etf_regime_strategy.py`, `strategies/inverse_etf_regime_backtest.py`, `services/strategy_profitability_gate_service.py`, `services/market_regime_service.py`, `view/web/bootstrap/strategy_factory.py`

### R-6. 비용 모델 — capacity/시장충격 [관찰]

- [ ] (관찰) 전략별 후보 종목 평균 거래대금 분포 + 주문 규모 대비 시장충격 추정을 리포트에 노출할지 검토. (`max_top_of_book_participation_pct` 이미 존재)

### 보류 — 정책 합의 후 재승격

- [ ] **3-4 active strategy lifecycle 7단계 분해**(`get_watchlist`/`filter_candidates`/`evaluate_entries_bounded`/`evaluate_exits_bounded`/`emit_metrics`) — 현재 `scan`/`check_exits`에 묻혀 있어 대형 리팩토링. checklist 테스트는 적용 완료. 공통 흐름이 더 쌓이면 재승격. (M-2의 god class 줄수 계측이 재승격 근거 — `strategy_scheduler.py` 2,152줄.)
- [ ] 기타 정책/임계값 결정: RiskGate 실패 주문 cap 정책 / 전략별 min trading value·market cap 하한 / 매도 RiskGate 우회 / volatility hard gate / 성과 저하 자동 해제·수량 축소 / 레거시 전략 백테스트 통합 여부.

주요 파일: `interfaces/live_strategy.py`, `tests/unit_test/strategies/test_live_strategy_lifecycle_contract.py`

---

## 운영 안전 기준 현황

표기: `[x]` 완료, `[~]` 부분 완료/진행 필요, `[ ]` 미완료

- [x] 실전 모드에서 주문 접수만으로 보유/체결이 확정되지 않는다. (`SUBMITTED` 유지)
- [x] 모든 주문은 Risk Gate 통과 전 broker API를 호출하지 않는다.
- [x] 서비스 재시작 후 미체결 주문·잔고를 복원/reconcile한다. (`restore_state_from_broker`/`reconcile_orders_with_broker`)
- [x] paper/real URL·TR ID·토큰·계좌 분기가 테스트로 검증된다.
- [x] 장애·데이터 지연·websocket 끊김·reconcile 실패 시 신규 주문 차단 또는 경고 전환.
- [x] 킬스위치 상태(연속손실·일손실 카운터)는 atomic JSON으로 영속화되어 재시작 시 유지된다.
- [~] 전략 성과는 수수료·세금·슬리피지 반영 순수익 기준으로 추적된다.
  - 진행 필요: `MomentumStrategy` 등 비활성/레거시 독립 백테스트 경로까지 동일 체결 리포트/장부 통합할지 결정.

---

## 해소된 리뷰 결론 (참고 — 코드 후속 없음)

> 상세는 git/PR·리포트 파일로 추적. 결론만 보존.

- **R-1 생존편향 [해소]**: 의무 손절이 PnL 생존편향을 방어(손절-5%/트레일8% 멀티데이 시뮬 GAP ≈ -0.15%p/거래). 손절 규율 자체가 방어선. 상세 `data/survivorship/survivorship_exposure_report.md`.
- **R-3 포트폴리오 heat [해소]**: 전 포지션 합산 open-risk 한도 도입(profile별 1%/3%/6%). 재승격 조건: flat full-sum을 분산 크레딧 모델로 바꾸기로 정책 결정할 때 상관 가중 함께 설계.
- **R-4 갭 스톱 [해소]**: 백테스트 `OrderType.STOP` 갭 관통 보수 체결(매도=`min(stop,open)`). 잔여: 실제 forward gap 정량 측정은 종목별 OHLCV 조인 필요(R-1과 동일 맥락, 미구현).
- **R-5 증권거래세율 [해소]**: 0.20%(0.002) 현행 정확값 — 변경 없음.
- **S-1~S-10 StrategyScheduler 리뷰**: S-1~S-8 버그/수명/구조 수정 완료, S-3/S-10 의도된 설계 확인, S-9는 `EventShadowManager`(`scheduler/event_shadow_manager.py`) 추출 완료(2026-06-28).
- **tiered force-exit window [해소]**: 단계화 청산(`FORCE_EXIT_TIERS=[(30,0.5),(15,1.0)]`) 완료(2026-06-28). 잔여 작업 없음.
- **운영 guard 일부 [해소]**: KillSwitch auto-trigger, WebSocket watchdog/health 계열, daily cap 계열은 구현 확인. 잔여는 위 "보류"의 정책/임계값 결정만 추적.
- **T-1 키움 테마 REST [드롭]**: 네이버 테마(`ThemeClassificationCollectorService`) 자동 수집으로 분류 데이터 충분, 키움 추가 소스 불필요.
