# Investment Trading App - 남은 To-Do

최종 업데이트: 2026-06-22 (no-tick 운영 실험 A~D 라이브 결과 반영)

이 문서는 **현재 남은 실행 항목**만 추린 목록이다. 완료된 구현 상세·완료 체크·과거 세션 요약은 git/PR로 추적하고 본 문서에서 제거한다.

정리 원칙:

- 실전 계좌 보호와 주문 안정성을 최우선으로 둔다.
- 이미 적용된 항목은 새 기능으로 다시 넣지 않고, 검증/고도화 과제로만 남긴다.
- 후보군 관리는 기존 `OneilUniverseService` / `SubscriptionPolicy` 공통 파이프라인 확장 과제로 본다.
- 주문/브로커/스케줄러 변경은 테스트 hang 가이드와 paper/real 분기 검증을 함께 적용한다.
- `VolumeBreakoutStrategy`, `VolumeBreakoutLiveStrategy`, `TraditionalVolumeBreakoutStrategy`, `GapUpPullbackStrategy`, `ProgramBuyFollowStrategy`, `MomentumStrategy`는 비활성/레거시이므로 신규 연결·개선 우선순위에서 제외한다.

## 남은 실행 영역 요약 (우선순위 순)

- **[최우선·블로커] P2 2-4 WS price 피드 무틱 ≈55%**: 구독 종목 절반 이상이 종일 no-tick → VBO shadow parity 수집 불가 + 라이브 실시간 데이터 품질 문제. 2026-06-19 로그 진단 결과 `a1_kis_no_send` 우세(ACK 후 KIS 프레임 미전송)로 확인. 2026-06-22 운영 실험 A~D 라이브 실행으로 원인을 **종목/상품군/계정 단위 KIS측 미전송**으로 좁힘(subscribe/ack/quality_reject 전부 0, received 5 vs no_tick 18). 후속은 코드 선제 수정이 아니라 KIS 에스컬레이션/운영 우회책(무틱 종목 격리) 판단.
- **P1 1-6** 실전 journal/shadow/paper/canary 성과 데이터로 profitability gate 실제 통과 근거 확보 — "돈을 버는가"의 핵심, 라이브 데이터 축적 의존.
- **P1 1-7** formal 과최적화 방어(DSR + PBO CSCV + purge embargo) 완료. PBO/adjusted-Sharpe hard gate 옵션과 real-mode overlay는 구현됨. 남은 것은 DSR hard 기준 포함 최종 운영 정책 결정(canary 데이터 후).
- **P0 0-1** 실전 submit/signing notice raw fixture 확보 후 mapper 회귀 보강(외부 데이터 의존).
- **P1 1-5** 한국장 microstructure fixture로 체결 모델 보수성 검증(장중 캡처 의존, blocked).
- **P2 2-2** 외부: 실 KIS 계정 유량 한도 운영 직전 재확인.
- **해외주식** Phase 4(주문/사이징) 컴포넌트 완료(order-gating·USD sizing·FX·일봉 exit, 자동 전략 경로 `live_enabled=False` 잠금), 잔여는 Phase 5(canary auto-fire 배선·reconcile·live 전환, 라이브 검증 gated). 수동 해외 지정가 주문 API는 별도 존재하며 실전은 `allow_live_trading=true` + 확인 문자열로만 허용. Phase 1~3(데이터 어댑터·일봉 백테스트·dry-run) 완료.
- **테마/분류 데이터**: 네이버 테마를 1차 소스로 적용하고, 키움 테마 REST API(`ka90001`/`ka90002`)는 후속 TODO로 분리. StockEasy 섹터RS 화면의 테마/섹터 taxonomy를 정규화 참고자료로 활용. 통합 테마는 source별 원본을 보존한 뒤 `normalized_name` 기준 OR 병합.
- **R-1 생존편향**: 노출·PnL 정량화 완료 — 결론 "의무 손절이 PnL 생존편향을 방어"(상세 `data/survivorship/survivorship_exposure_report.md`). 코드 후속 없음.
- **조건부/정책**: Pool B 튜닝(재발 시), R-2 비상관 엣지 도입, S-9 god class 분리(PR-3 판정 후) 등 — 하단.

---

## P0. 실전 손실 방지

> 완료(git): 0-7 canary profile 분리 · 0-8 당일 미완성 봉 제외(#478) · 0-9 net PnL exit(#479) · 0-10 reserved cash/same-symbol 노출(#481) · 0-11 atomic write 통일(#481).

### 0-1. 실전 KIS `inquire-daily-ccld` 응답 필드 검증

- [~] 실전 submit response/체결통보(signing notice) raw fixture를 확보해 `BrokerOrderResponseMapper` 회귀를 보강한다.
  - 완료: 실전 체결 fixture 회귀 잠금, `EXCG_ID_DVSN_CD=KRX` 고정, `BrokerOrderResponseMapper` 분리 + 다양한 주문번호 키/체결통보 키 단위 테스트, 추출 실패 시 raw payload 보존 + 신규 주문 차단 방어.
  - 남은 것: 실전 submit/signing payload 원본 표본 부족 — 취소/거부 실전 row 포함 raw fixture 추가 확보 시 회귀 고정.

주요 파일: `common/broker_order_response_mapper.py`, `services/order_execution_service.py`, `services/fill_reconciliation_service.py`, `tests/fixtures/kis/`

---

## P1. 전략 수익성 검증

### 1-5. 백테스트 검증 확장

- [blocked] 실제 replay fixture를 통과 케이스까지 확장 — 장중 프로그램매매 WebSocket 샘플 미확보. 해제 조건: 장중 `scripts.capture_backtest_microstructure`로 후보 종목 샘플 확보 → replay overlay.
- [ ] 한국장 실전 microstructure fixture(bid/ask book·잔량·체결강도·프로그램매매 overlay)로 체결 모델 보정 + 시장가/최유리/지정가별 fill quality가 live journal과 얼마나 벌어지는지 리포트.
  - 완료(기반): `BacktestBar` microstructure 필드, VI/상하한가/거래정지 차단, BEST_LIMIT, bid/ask spread, liquidity slippage buckets.

주요 파일: `services/backtest_execution_simulator.py`, `services/backtest_replay_context.py`, `scripts/run_backtest.py`, `tests/fixtures/backtest/`

### 1-6. 실전 수익성 데이터 확보와 profitability gate 운영

- [ ] shadow / paper / 소액 canary journal을 표준 포맷으로 누적하고, 전략별 profitability gate 통과 근거를 리포트한다. (라이브 데이터 축적 의존 — "돈을 버는가"의 실증 단계)
  - 최소 유지 기준: 실전 override `min_trades=100`, `profit_factor>=1.3`, `payoff_ratio>=1.2`, `win_rate>=40%`, `max_drawdown<=12%`, regime별 최소 거래 30. parameter stability·Monte Carlo·regime balance·multiple testing 보정을 운영 편의로 낮추지 않는다.
  - 완료(기반): fail-close 잠금(provider/journal 부재 시 real BUY 차단, paper bypass), gate 배선 lock 테스트, 전략 신호 9필드(`entry_reason`/`invalidation_price`/`stop_loss_price`/`target_price`/`trailing_rule`/`expected_holding_period_days`/`confidence`/`required_data`/`config_hash`) journal persist + surfacing.

주요 파일: `services/strategy_live_expansion_gate_service.py`, `services/strategy_profitability_gate_service.py`, `scheduler/strategy_scheduler.py`, `services/strategy_log_report_service.py`

### 1-7. Multiple testing / 과최적화 방어 고도화

- 완료: formal Deflated Sharpe(Bailey & López de Prado 2014) + 수익률 모멘트 metric(skew/kurtosis) + 일일 리포트 DSR 섹션 + **formal PBO(CSCV)** 알고리즘·실데이터 가동(parameter-stability·ablation 두 sweep 경로, `--output json`/콘솔 노출) + **purged CSCV(embargo)**. 기존 proxy(adjusted Sharpe·PBO-like)는 불변 병행. (git/PR #555·#556)
- [~] formal PBO/DSR의 profitability gate **hard 적용 정책** 결정 — PBO/adjusted-Sharpe hard gate 옵션과 real-mode overlay(`require_multiple_testing_adjustment=True`, `multiple_testing_max_pbo_probability=0.5`)는 구현됨. DSR hard threshold는 아직 운영 기준 미정. canary 성과 데이터 축적 후 enforce 기준 확정.
- [ ] 전략 수·필터 조합 증가 시 canary 통과 기준과 자금 확대 기준 분리(자금 확대 = formal 검증 + 실전 성과 + regime 일관성).
- 우선순위 낮음: purged K-fold 별도 CV 러너(walk-forward+embargo·CSCV embargo로 실질 커버).

주요 파일: `services/multiple_testing_bias_service.py`, `services/strategy_ablation_service.py`, `scripts/run_backtest.py`

---

## P2. 시스템 성능

> 완료(git): 2-5 scan 현재가 batch/snapshot 최적화(`prefetch_prices` + multi_price circuit-breaker, 활성 7전략 배선). 2-6 라이브 핫패스(WebSocket 틱 루프 상수 캐싱/lazy 로그, 장마감 `iterrows()` 축소, HTTP/token 정합성, VBO `check_exits()` bounded).

### 2-2. API 호출 최적화

- [~] API budget limiter 운영 정책 — 완료: endpoint category별 동시성/rate 분리, emergency overlay lane, 전역 normal 8/s + emergency 2/s bucket, coverage matrix·강제 주입 검증·predeploy 점검.
  - 남은 작업(외부): 실제 KIS 계정별 REST/WebSocket 유량 한도 숫자를 공식 포털/계정 공지로 **운영 직전 재확인** → 필요 시 `_global` 8/s 운영값 조정. 공개 자료가 갈리므로 코드 기본값은 보수값 유지.

주요 파일: `core/retry_queue/api_budget_limiter.py`, `docs/api_budget_coverage_matrix.md`

### 2-4. Polling에서 event-driven으로 점진 전환

- **[최우선 블로커] WebSocket price 피드 무틱 ≈55%**: 6/9~6/12 streaming 로그 실측 — 구독 30~34종목 중 18~21종목이 종일 `subscribed_no_tick`. shadow는 tick에만 의존하므로 parity 수집 자체가 불가. `connection_lost` 12~18/일 + `price_data_gap` 단조 증가(force-reconnect로 미치유). ACK 수정(#509)으로 미해소. **shadow뿐 아니라 라이브 실시간 데이터 전반 품질 문제** → 별도 최우선 과제로 격상. 코드 감사 결과 슬롯 cap(40)·ACK 확정은 정상. 2026-06-19 라이브 로그 진단 결과 no-tick 16종목 중 15종목이 `a1_kis_no_send`(ACK 후 KIS 프레임 미전송, received=0), 1종목은 `unknown_no_snapshot`, `not_subscribed`는 0건. 최신 리포트: `reports/no_tick_diagnosis_20260619.md`.
  - **2026-06-22 운영 분리 실험(A~D, 각 180초 라이브) 결과**(`reports/no_tick_operational_experiment_analysis_20260622_live.md`): 종합 received 5 / no_tick 18, subscribe_failure·ack_failure·quality_reject 전부 0 → 구독·ACK는 성공인데 KIS가 프레임 미전송(`a1_kis_no_send` 패턴 재확인). 실험별: **A(보통주만)** `common_no_tick_persists` — 동일 코호트에서 일부 보통주만 틱 수신(삼성물산 729·원익IPS 806 등) vs 일부 0틱(제주반도체·대덕전자·한솔테크닉스·강동씨앤엘·한울반도체), 종목 단위 차이 시사. **B(ETF/우선주만)** `non_common_product_class_no_tick_likely` — KODEX200·TIGER반도체TOP10 등 5종목 전부 0틱 → 상품군 단위 미지원 가능성. **C(무틱 보통주 단독 구독)** `symbol_or_account_level_issue_likely` — 격리해도 0틱 지속. **D(refresh 관찰)** `refresh_ineffective` — refresh로 회복 0. → 원인이 코드/슬롯/디스패치가 아니라 **종목·상품군·계정 단위 KIS측**임이 라이브로 확인됨. 다음 액션: B는 KIS에 ETF/우선주 WS 지원 여부 확인, C는 무틱 보통주를 runner 출력 첨부해 KIS 에스컬레이션. **D(무틱 종목 격리)는 구현 완료** — `WebSocketWatchdogTask`가 무효 refresh를 `QUARANTINE_NO_TICK_REFRESH_THRESHOLD`(3)회 누적하면 종목을 격리해 unsub/resub churn을 중단하고 `quarantined_no_tick`으로 가시화, 이후 틱 재개 시 `no_tick_recovered`로 자동 해제(`task/background/intraday/websocket_watchdog_task.py`).
  - 디스패치 갭은 해소(#551): `StreamingService`가 `PriceStreamService.on_price_tick`을 event-loop handler로 유지해 running loop 의존 디스패치가 끊기지 않도록 처리한다. 기타 동기/블로킹 핸들러만 executor로 오프로드된다. tick 도착 종목은 shadow 평가 실행됨. #533 tick-ingest 카운터(received/quality_reject/dispatched) 관측 배선 존재.
  - 진단 로깅 완비 근거(2026-06-19 코드/로그 확인): ① `_active_codes_price`는 **KIS 등록 ACK 확정 종목만** 마킹(`subscription_policy.py` ≈389-401, 전송 성공≠active, 유령 구독 방지 — 테스트 잠금). ② watchdog가 `subscribed_no_tick`(=active AND `last_tick_ts<=0`, 즉 ACK 확정 후 0틱) vs `not_subscribed`(ACK 미확정)를 종목별 분리 로깅(`websocket_watchdog_task.py` ≈271-279). ③ `tick_ingest_stats_snapshot`이 `received=0`(프레임 자체 0=a1) vs `received>0 & quality_reject↑`(게이트 탈락=a2) 구별. tick-ingest 카운터는 monotonic 누적(`price_stream_service.py` `received`/`quality_reject`/`dispatched` `+=1`, reset 없음)이고, 스냅샷은 매 scan(`interval_minutes` 기본 5분)마다 `subscriptions_refreshed`로 event_shadow에 첨부(`strategy_scheduler.py` ≈598·1245). 조인 파싱 스크립트 `scripts/analyze_no_tick_diagnosis.py`(+`tests/unit_test/scripts/test_analyze_no_tick_diagnosis.py`) 실행 결과 `a1_kis_no_send` 우세 판정. **남은 것은 KIS 프레임 미전송 원인 확인, 구독 대상/시장/상품군 분리 실험, 운영 우회책 판단.**
- [ ] (블로커 해소 후) `event_shadow`/`event_shadow_exit` 5거래일 jsonl 수집 → `scripts/analyze_event_shadow_parity.py`로 entry/exit parity 리포트 → PR-3 진입 판정.
- [ ] event-driven signal은 별도 승인 전 shadow/latency 측정용으로만 운영(실주문은 polling + full gate 경로만). VBO fast path는 execution strength/program-buy 생략.
- [blocked] PR-3: PR-2.5 관찰 양호 시 VBO 실 적용 + OSB shadow 진입. / PR-4+: 단계적 확장.
- 완료(기반): VBO entry+exit shadow 측정 경로, parity 분석 스크립트(entry/exit 분리), exit 손절 shadow(`evaluate_exit_single`).

구현 결정(`docs/event_driven_architecture.md` §9): event throttle 0.5s / stale snapshot 5s / shadow 운영 1주(5거래일) / `signal_source`=metadata JSON 키 / trigger crossing은 평가 허용·publish만 debounce.

주요 파일: `services/strategy_event_router.py`, `services/event_shadow_journal_service.py`, `services/price_stream_service.py`, `services/streaming_service.py`, `brokers/korea_investment/korea_invest_websocket_api.py`, `task/background/intraday/websocket_watchdog_task.py`, `scheduler/strategy_scheduler.py`

### 2-6. 라이브 핫패스 성능 — 잔여

- [~] 활성 전략 `scan()` 잔여 순차 후보 처리(현재가/체결강도/변동성 보강)를 `bounded_gather`로 전환 — **보류**. 저비용 단계에서 대다수 후보 탈락 + 고비용 REST는 소수 돌파 후보만 + 전역 limiter 8/s 직렬화로 실익 marginal. 실전 진입 경로 동등성 검증 부담 대비 이득 작음. 재개 시 2-pass(돌파 후보만 `execution_strength` bounded)가 외과적.

주요 파일: `strategies/larry_williams_vbo_strategy.py`

---

## 테마/분류 데이터

> 네이버 테마 수집(1차 소스) 상태: `ThemeClassificationTask`가 `SchedulerBootstrap._register_batch_tasks`에 미등록이라 한 번도 수집되지 않던 배선 누락을 복구(PR #586). 등록 회귀를 단위/통합 테스트로 잠그고, end-to-end 1회 수집으로 `data/stock_classifications.db` 0건→6,445건(테마 265/종목 2,343) 확인. 주기 가드를 무시하는 수동 트리거 `POST /api/background/theme-classification/force-update` 추가. 운영에서는 BATCH 모드 장마감 후 자동 수집(기본 7일 간격).

### T-0. StockEasy 섹터RS taxonomy 참고

- [ ] StockEasy 종합 RS 화면(`stockeasy.intellio.kr/stock-analysis`)의 섹터/테마 분류를 네이버/키움 통합 테마 정규화 참고자료로 사용한다.
- [ ] 화면 기준 주요 후보: 반도체소재, 지주사, 메모리, 비메모리/팹리스, 전력기기, 반도체장비, 보험, 건설, 테스트소켓, 유통, 로봇/자동화, 미용기기, 산업기계, 완성차, SW/AI, 자동차부품, 증권, 우주항공, 배터리셀, 통신, 원자력, 양극재, 신재생, 전자장비, 조선기자재, 조선, 타이어, 바이오신약, 방위산업, 음극재/소재, 은행, 정유/화학, 철강/비철, 의료기기, 해운, 여행/레저, 음식료, 패션/의류, 제약, 인터넷/플랫폼, 유틸리티, 리츠/부동산, 게임, CDMO, 화장품, 엔터/미디어.
- [ ] StockEasy 자체를 무단 수집 소스로 고정하지 말고, 우선은 테마명 alias/표시명/RS UI 참고로 둔다. 실제 구성종목 데이터는 네이버/키움 등 수집 가능한 source에 귀속한다.

### T-1. 키움 테마 REST API 연동 (후속 TODO)

- [ ] 키움 REST API 사용 신청/인증 설정을 별도 config로 분리한다. 허용 IP, 토큰 발급/갱신, 호출 제한 정책을 문서화한다.
- [ ] `ka90001`(테마그룹별요청)으로 키움 테마 목록을 수집한다.
- [ ] `ka90002`(테마구성종목요청)으로 테마별 구성 종목을 수집한다.
- [ ] 수집 결과는 `source="KIWOOM"`, `category_type="theme"`으로 저장하고, 네이버 테마와 OR 병합할 수 있도록 `raw_group_id`, `raw_name`, `normalized_name`, `code`, `name`, `collected_at`을 보존한다.
- [ ] 네이버/키움 동일 테마명 차이는 alias 테이블로 정규화한다. 자동 병합보다 명시 alias를 우선한다.
- [ ] 키움 호출 실패 시 기존 성공 캐시를 유지하고, 테마 주도주 화면에는 source와 마지막 갱신 시각을 노출한다.

주요 후보 파일: `services/kiwoom_theme_service.py`, `repositories/theme_classification_repository.py`, `config/kiwoom_config.yaml`

---

## P3. 유지보수성

> 완료(git): 3-5 tick-size 단일화(`get_tick_size()` 단일 소스 + parity 테스트). 3-6 silent skip 방지(IndicatorService 계산 오류 ERROR+metric+alert, 전략 per-code fail-rate metric).

### 3-4. 전략 공통 lifecycle/state contract

- [~] active strategy lifecycle 7단계(`get_watchlist`/`filter_candidates`/`evaluate_entries_bounded`/`evaluate_exits_bounded`/`emit_metrics`) 분해 — **보류**(현재 `scan`/`check_exits`에 묻혀 있어 분해 자체가 대형 리팩토링). 완료: checklist 테스트로 활성 7전략 최소 contract 누락 자동 탐지. 공통 흐름이 더 쌓이면 재승격.

주요 파일: `interfaces/live_strategy.py`, `tests/unit_test/strategies/test_live_strategy_lifecycle_contract.py`

---

## 해외주식 전략 적용 (VBO 일봉 PoC / 어댑터 통합)

결론: 일봉 셋업형 전략만 적용 가능(해외 일봉 API 존재), 장중/실시간 전략은 분봉·랭킹·웹소켓 부재로 불가. 첫 대상 = `LarryWilliamsVBOStrategy`(일봉 셋업). 제약: **해외 주문 TR은 실전(TTTS6036U 등)만, 모의 주문 TR 없음** → dry-run 검증 전 실주문 배선 금지.

- 완료: Phase 1 데이터 어댑터(`get_recent_daily_ohlcv`/`get_current_price` exchange 분기 + `OverseasCandidateService`), Phase 2 `OverseasDailyVBOBacktest`(일봉 근사), Phase 3 `MarketClock.for_us_equities()` + `OverseasVBODryRunService` + `OverseasDryRunTask`(주문 경로 없는 dry-run, 한국장 after-market 스케줄러 재사용). (#549·#550)
- [~] **Phase 4 주문/사이징**: 자동 전략 컴포넌트 완료 — `OverseasOrderExecutionService`(`place_overseas_limit_order` 지정가 연결, `live_enabled=False` 구조적 실주문 잠금 + would-be 레코드), `OverseasPositionSizingService`(고정 USD 슬롯÷지정가 floor + `max_qty`/`available_usd` cap), FX 환율(`extract_fx_krw_per_usd` 잔고 관용 추출 + `_overseas_fx_provider` 배선 → dry-run KRW 환산 노출), 일봉 기반 exit(`decide_daily_exit` stop/eod). 테스트 잠금 완료. 별도 웹 수동 해외 지정가 주문은 존재하며, 실전 모드에서는 `overseas_stock.allow_live_trading=true`와 `REAL` 확인 문자열 없이는 broker 호출 전 차단된다.
  - 남은 것(Phase 5 소관): scheduler/factory가 sizing→order_execution 자동 연결(canary auto-fire) + `live_enabled=True` 전환 — dry-run 검증 + canary 후로 게이팅.
- [ ] **Phase 5 안전/canary**: `get_overseas_balance`/`ccnl` reconcile(`OverseasReconcileService` scaffolding 존재), risk gate/kill switch/canary USD 확장, 실전 소액 canary, canary auto-fire 배선 + `live_enabled` 전환.
- [x] 3d(후속): 미국장 마감(ET) 정밀 트리거. `AfterMarketLoop`에 timezone/cron 파라미터화(기본 KST 유지) + mcs 미주입 시 클럭 날짜 폴백 → `OverseasDryRunTask`를 America/New_York 16:30 트리거로 전환, `service_container`가 `MarketClock.for_us_equities()` + `mcs=None` 배선. (#585)

주요 파일: `brokers/korea_investment/korea_invest_overseas_stock_api.py`, `brokers/broker_api_wrapper.py`, `services/overseas_order_execution_service.py`, `services/overseas_position_sizing_service.py`, `services/overseas_reconcile_service.py`, `services/stock_query_service.py`, `view/web/bootstrap/{service_container,strategy_factory}.py`, `config/tr_ids_config.yaml`

---

## Pool B 튜닝 관찰 (조건부 — 후보 부족 재발 시)

- [ ] 거래대금 기준 50억 → 30억 추가 완화 검토.
- [ ] 정배열 조건을 Pool B 전용 `current > ma_20d` 중심으로 완화 검토.

---

## 시스템 트레이더 관점 리뷰 (R-1~R-6, 2026-06-08)

20년차 시스템 트레이더 관점 코드 검토. 우선순위: 수익성 신뢰도 > 실전 리스크 > 성능/유지보수.

### R-1. 생존편향 — 노출·PnL 정량화 완료, 결론 도출 [해소]

- 결론: **노출은 크나 이 시스템의 의무 손절이 PnL 생존편향을 방어한다.** 2025-01~2026-06 유동성 부실 상폐 ≈15종이 고점 대비 -40~-100% 붕괴(survivor-only 유니버스에 0% 표본)지만, 실제 전략 파라미터(손절-5%/트레일8%) 멀티데이 시뮬에서 생존편향 PnL GAP ≈ -0.15%p/거래(무시 가능) — 손절이 -90% 정리매매 *전에* 청산하기 때문. 미보호(손절없음·보유)에서만 avg -60%/거래. → **손절 규율 자체가 생존편향 방어선**(끄거나 느슨하게 하면 즉시 치명적).
- 완료(코드/데이터): PIT universe provider/wrapper + 상폐 OHLCV store + run_backtest `--pit-universe`/`--delisted-ohlcv-dir` 배선(#552), `MultiDayDailyBreakoutBacktest`, FDR 데이터 생성. 상세 리포트 `data/survivorship/survivorship_exposure_report.md`.
- 잔여(선택, 코드 후속 없음): forward gap(전일 종가→당일 시가) 정량 측정, 전략별 실제 숫자는 데이터-ops로 재실행 시 산출. `StrategyLogReportService` 자동 통합은 결론이 "손절 유지"로 수렴해 운영 가치 낮음(보류).

### R-2. 전략 상관 / 단일 regime 집중 [심각 — 부분 해소]

- 활성 7전략 전부 long-only 모멘텀/돌파/눌림목 → 단일 "상승/추세 regime 베팅", 약세·횡보장 동시 손실 위험.
- 완료: 전략 간 실현수익률 상관행렬 + regime별 성과 분해를 일일 리포트에 노출(매수 시점 `market_regime` journal persist, 고상관 클러스터·regime 집중도 경고).
- [ ] 자금 확대 전 비상관 엣지(역추세/숏/저변동 등) 1개 이상 도입 여부 정책 결정.

주요 파일: `services/market_regime_service.py`, `services/strategy_log_report_service.py`

### R-3. 포트폴리오 총위험(heat) 집계 [중대 — 해소]

- 완료: 전 포지션 합산 open-risk 한도를 `PositionSizingService`에 도입(수량 축소→소진 시 차단, profile별 1%/3%/6%). 상관 가중 heat은 검토 후 보류(현재 flat full-sum이 더 보수적, 분산 크레딧은 한도 완화라 자본 보호와 역방향).
- 재승격 조건: flat full-sum을 분산 크레딧 모델로 바꾸기로 정책 결정할 때 상관 가중 함께 설계.

### R-4. 오버나이트 갭 + 스톱 gap-through [중대 — 해소]

- 완료: 백테스트 `OrderType.STOP` 갭 관통 보수 체결 모델(매도=`min(stop,open)`), 전략별 오버나이트 노출·실현 멀티세션 보유 리포트 노출.
- 잔여(별도): 실제 익일 시가 갭(forward gap)의 정량 측정은 종목별 OHLCV 조인 필요 → 미구현. R-1 잔존 리스크(halt 후 극단 갭다운)와 동일 맥락.

### R-6. 비용 모델 — capacity/시장충격 [경미, 관찰]

- [ ] (관찰) 전략별 후보 종목 평균 거래대금 분포 + 주문 규모 대비 시장충격 추정을 리포트에 노출할지 검토. (`max_top_of_book_participation_pct` 이미 존재)

> R-5 증권거래세율: 0.20%(0.002) 현행 정확값 확인 — 변경 없음 [해소].

---

## StrategyScheduler 코드 리뷰 (S-1~S-10, 2026-06-12) — 거의 완료

S-1(stop 강제청산 데드 패스)~S-8 버그/수명/구조 수정 완료, S-3/S-10은 의도된 설계 확인 후 주석 명시, S-9 prune 통합/trace_id 영속화/get-holds 핫패스/인터페이스 승격 완료. (git)

- **보류** — S-9 EventShadowManager 등 god class 분리(scheduler shadow 블록 ≈330줄): 테스트 ~26곳이 내부 표면 직접 잠금 + shim 비용 + shadow 코드 거취가 **P2 2-4 PR-3 판정**에 달림 → parity 판정 후 재평가. (Go=배선 전 분리 선행 / No-Go=shadow 삭제로 종결)

---

## 바로 착수 추천 순서

1. **외부 운영·데이터 확보 후 진행**
   - 실 KIS 계정 REST/WebSocket 유량 한도 재확인 → 필요 시 운영값 조정 (P2 2-2)
   - 실전 submit/signing notice raw fixture 확보 → mapper 회귀 보강 (P0 0-1)
   - 장중 프로그램매매 WebSocket 샘플 캡처 + 한국장 microstructure fixture (P1 1-5)

2. **운영 관찰·블로커 (최우선)**
   - **WebSocket price 피드 무틱 ≈55% 근본 원인 해소** (P2 2-4) — 라이브 실시간 데이터 품질 블로커. 2026-06-19 진단 `a1_kis_no_send` 우세 + 2026-06-22 실험으로 종목/상품군/계정 단위 KIS측 미전송 확정. 다음은 **KIS 에스컬레이션**(코드 수정 아님). 해소 전까지 shadow 수집 불가.
   - profitability gate를 우회하지 않고 shadow/paper/canary journal로 전략별 실전 근거 축적 (P1 1-6)

3. **정책 결정 후 진행**
   - DSR hard threshold 및 PBO/DSR profitability gate 운영 기준 확정 (P1 1-7, canary 데이터 후)
   - 자금 확대 전 비상관 엣지 도입 여부 (R-2)

4. **조건부 트리거 — 재발 시**
   - Pool B 거래대금 50→30억 / 정배열 `current > ma_20d` 완화

5. **정책 합의 후 재승격 (보류)**
   - S-9 god class 분리 (P2 2-4 PR-3 판정 후)
   - active strategy lifecycle 7단계 분해 (P3 3-4)
   - VBO `scan()` 잔여 순차 bounded 전환 (P2 2-6, 실익 marginal)
   - tiered force-exit window / RiskGate 실패 주문 cap 정책 / 전략별 min trading value·market cap 하한 / 매도 RiskGate 우회·KillSwitch auto-trigger / volatility hard gate / 성과 저하 자동 해제·수량 축소 / WebSocket health probe 자동화 / 레거시 전략(MomentumStrategy 등) 백테스트 통합 여부

---

## 완료 기준

표기: `[x]` 완료, `[~]` 부분 완료/진행 필요, `[ ]` 미완료

- [x] 실전 모드에서 주문 접수만으로 보유/체결이 확정되지 않는다. (`SUBMITTED` 유지)
- [x] 모든 주문은 Risk Gate 통과 전 broker API를 호출하지 않는다.
- [x] 서비스 재시작 후 미체결 주문·잔고를 복원/reconcile한다. (`restore_state_from_broker`/`reconcile_orders_with_broker`)
- [x] paper/real URL·TR ID·토큰·계좌 분기가 테스트로 검증된다.
- [x] 장애·데이터 지연·websocket 끊김·reconcile 실패 시 신규 주문 차단 또는 경고 전환. (Kill Switch/Risk Gate/data quality/watchdog/reconcile alarm)
- [~] 전략 성과는 수수료·세금·슬리피지 반영 순수익 기준으로 추적된다.
  - 완료: 비용 유틸·체결 품질 로그·슬리피지 추적, 성과 지표 net PnL/return 기본값, `BacktestExecutionSimulator` 비용 포함 리포트, 활성 period runner 표준 journal 통합, MFE/MAE 합산.
  - 진행 필요: `MomentumStrategy` 등 비활성/레거시 독립 백테스트 경로까지 동일 체결 리포트/장부 통합할지 결정.
