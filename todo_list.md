# Investment Trading App - 남은 To-Do

최종 업데이트: 2026-06-28 (S-9 god class 분리 착수 반영)

이 문서는 **현재 남은 실행 항목**만 추린 목록이다. 완료된 구현 상세·완료 체크·과거 세션 요약은 git/PR과 리포트 파일로 추적하고 본 문서에서 제거한다.

정리 원칙:

- 실전 계좌 보호와 주문 안정성을 최우선으로 둔다.
- 이미 적용된 항목은 새 기능으로 다시 넣지 않고, 검증/고도화 과제로만 남긴다.
- 후보군 관리는 기존 `OneilUniverseService` / `SubscriptionPolicy` 공통 파이프라인 확장 과제로 본다.
- 주문/브로커/스케줄러 변경은 테스트 hang 가이드와 paper/real 분기 검증을 함께 적용한다.
- `VolumeBreakoutStrategy`, `VolumeBreakoutLiveStrategy`, `TraditionalVolumeBreakoutStrategy`, `GapUpPullbackStrategy`, `ProgramBuyFollowStrategy`, `MomentumStrategy`는 비활성/레거시이므로 신규 연결·개선 우선순위에서 제외한다.

---

## 착수 가능성 요약

순수 코드로 지금 착수 가능한 항목은 제한적이다. 대부분의 P0~P2 잔여는 외부 데이터 확보·KIS 운영 액션에 의존한다.

- **코드 착수 가능 (정책 합의 선행)**
  - **P1 1-6 paper/소액 canary journal 축적** — 무틱 블로커와 독립. journal은 `virtual_trade_service.get_standard_journal_records` ← polling scan + REST 가격 경로로 채워져 틱 비의존. 라이브 런타임 시간만 필요(무틱에 막히는 건 "shadow" 하위 요소뿐, 2-4 parity와 동일 의존).
  - **S-9 god class 분리 / 3-4 lifecycle 분해** — 보류 해제(정책 합의 시).
- **외부 액션 대기 (블로커)**: P2 2-4(KIS 에스컬레이션), P0 0-1(fixture), P1 1-5(microstructure 캡처), P2 2-2(KIS 유량 한도 재확인), P1 1-7(canary 후 정책), 해외 Phase 5(canary 게이팅).
- **종결**: T-1 키움 테마 REST — **드롭**. 네이버 테마(`ThemeClassificationCollectorService`) 자동 수집으로 분류 데이터 충분, 키움 추가 소스 불필요. (멀티소스 병합 인프라는 `StockClassificationRepository`에 잔존하나 신규 소스 연결 계획 없음.)

---

## P0. 실전 손실 방지

### 0-1. 실전 KIS `inquire-daily-ccld` 응답 필드 검증 [외부 데이터 의존]

- [~] 실전 submit response/체결통보(signing notice) raw fixture를 확보해 `BrokerOrderResponseMapper` 회귀를 보강한다.
  - 남은 것: 취소/거부 실전 row 포함 raw fixture 추가 확보 시 회귀 고정.

주요 파일: `common/broker_order_response_mapper.py`, `services/order_execution_service.py`, `services/fill_reconciliation_service.py`, `tests/fixtures/kis/`

---

## P1. 전략 수익성 검증

### 1-5. 백테스트 검증 확장 [blocked — 장중 캡처 의존]

- [blocked] 실제 replay fixture를 통과 케이스까지 확장 — 장중 프로그램매매 WebSocket 샘플 미확보. 해제 조건: 장중 `scripts.capture_backtest_microstructure`로 후보 종목 샘플 확보 → replay overlay.
- [ ] 한국장 실전 microstructure fixture(bid/ask book·잔량·체결강도·프로그램매매 overlay)로 체결 모델 보정 + 시장가/최유리/지정가별 fill quality가 live journal과 얼마나 벌어지는지 리포트.

주요 파일: `services/backtest_execution_simulator.py`, `services/backtest_replay_context.py`, `scripts/run_backtest.py`, `tests/fixtures/backtest/`

### 1-6. 실전 수익성 데이터 확보와 profitability gate 운영 [라이브 축적 — 코드 착수 가능]

- [ ] shadow / paper / 소액 canary journal을 표준 포맷으로 누적하고, 전략별 profitability gate 통과 근거를 리포트한다. (paper/canary는 WS 틱 비의존이라 무틱 블로커와 독립적으로 축적 가능. shadow 하위 요소만 2-4와 동일 의존.)
  - 최소 유지 기준: 실전 override `min_trades=100`, `profit_factor>=1.3`, `payoff_ratio>=1.2`, `win_rate>=40%`, `max_drawdown<=12%`, regime별 최소 거래 30. parameter stability·Monte Carlo·regime balance·multiple testing 보정을 운영 편의로 낮추지 않는다.

주요 파일: `services/strategy_live_expansion_gate_service.py`, `services/strategy_profitability_gate_service.py`, `scheduler/strategy_scheduler.py`, `services/strategy_log_report_service.py`

### 1-7. Multiple testing / 과최적화 방어 고도화 [canary 데이터 후]

- [~] formal PBO/DSR의 profitability gate **hard 적용 정책** 결정 — PBO/adjusted-Sharpe hard gate 옵션과 real-mode overlay(`require_multiple_testing_adjustment=True`, `multiple_testing_max_pbo_probability=0.5`)는 구현됨. DSR hard threshold만 미정 → canary 성과 축적 후 enforce 기준 확정.
- [ ] 전략 수·필터 조합 증가 시 canary 통과 기준과 자금 확대 기준 분리(자금 확대 = formal 검증 + 실전 성과 + regime 일관성).
- 우선순위 낮음: purged K-fold 별도 CV 러너(walk-forward+embargo·CSCV embargo로 실질 커버).

주요 파일: `services/multiple_testing_bias_service.py`, `services/strategy_ablation_service.py`, `scripts/run_backtest.py`

---

## P2. 시스템 성능

### 2-2. API 호출 최적화 [외부 — 운영 직전 재확인]

- [~] API budget limiter 운영 정책 — 동시성/rate 분리·emergency overlay·전역 8/s 등 구현 완료.
  - 남은 작업(외부): 실제 KIS 계정별 REST/WebSocket 유량 한도 숫자를 공식 포털/계정 공지로 **운영 직전 재확인** → 필요 시 `_global` 8/s 운영값 조정. 공개 자료가 갈리므로 코드 기본값은 보수값 유지.

주요 파일: `core/retry_queue/api_budget_limiter.py`, `docs/api_budget_coverage_matrix.md`

### 2-4. Polling → event-driven 전환 [최우선 블로커 — KIS 에스컬레이션]

- **[최우선 블로커] WebSocket price 피드 무틱 ≈55%**: 구독 종목 절반 이상이 종일 `subscribed_no_tick` → shadow parity 수집 불가 + 라이브 실시간 데이터 품질 문제. **이 레포의 코드 작업은 종결**(무틱 종목 격리 구현 완료). 진단 확정: 종목·상품군·계정 단위 **KIS측 프레임 미전송**(`a1_kis_no_send`).
  - 근거: 2026-06-19 로그 진단(`reports/no_tick_diagnosis_20260619.md`) + 2026-06-22 운영 실험 A~D(`reports/no_tick_operational_experiment_analysis_20260622_live.md`) — subscribe/ack/quality_reject 전부 0, received 5 vs no_tick 18. 보통주 일부만 0틱(종목 단위), ETF/우선주 전부 0틱(상품군 단위), 격리해도 0틱 지속(계정 단위), refresh 무효.
  - **다음 액션(코드 아님)**: ① B군 — KIS에 ETF/우선주 WS 지원 여부 확인. ② C군 — 무틱 보통주를 runner 출력 첨부해 KIS 에스컬레이션.
- [ ] (블로커 해소 후) `event_shadow`/`event_shadow_exit` 5거래일 jsonl 수집 → `scripts/analyze_event_shadow_parity.py`로 entry/exit parity 리포트 → PR-3 진입 판정.
- [ ] event-driven signal은 별도 승인 전 shadow/latency 측정용으로만 운영(실주문은 polling + full gate 경로만). VBO fast path는 execution strength/program-buy 생략.
- [blocked] PR-3: 관찰 양호 시 VBO 실 적용 + OSB shadow 진입. / PR-4+: 단계적 확장.

구현 결정(`docs/event_driven_architecture.md` §9): event throttle 0.5s / stale snapshot 5s / shadow 운영 1주(5거래일) / `signal_source`=metadata JSON 키 / trigger crossing은 평가 허용·publish만 debounce.

주요 파일: `services/strategy_event_router.py`, `services/event_shadow_journal_service.py`, `services/price_stream_service.py`, `services/streaming_service.py`, `brokers/korea_investment/korea_invest_websocket_api.py`, `task/background/intraday/websocket_watchdog_task.py`, `scheduler/strategy_scheduler.py`

### 2-6. 라이브 핫패스 성능 — 잔여 [보류]

- [~] 활성 전략 `scan()` 잔여 순차 후보 처리를 `bounded_gather`로 전환 — **보류**(저비용 단계 대다수 탈락 + 전역 limiter 8/s 직렬화로 실익 marginal). 재개 시 2-pass(돌파 후보만 `execution_strength` bounded)가 외과적.

주요 파일: `strategies/larry_williams_vbo_strategy.py`

---

## 테마/분류 데이터

네이버 테마(주 소스)는 `ThemeClassificationTask` 자동 수집 가동 중(BATCH 모드 장마감 후, 기본 7일 간격). 수동 트리거 `POST /api/background/theme-classification/force-update`. 분류 데이터는 네이버 단일 소스로 충분 — 키움 등 추가 소스 연동 계획 없음(T-1 드롭).

### T-0. StockEasy 섹터RS taxonomy 참고 (선택)

- [ ] StockEasy 종합 RS 화면(`stockeasy.intellio.kr/stock-analysis`)의 섹터/테마 분류를 네이버 테마 alias/표시명 후보 참고자료로 정리한다. StockEasy 자체를 무단 수집 소스로 고정하지 말고, 실제 구성종목 데이터는 네이버 등 수집 가능한 source에 귀속한다.
  - 주요 후보: 반도체소재, 지주사, 메모리, 비메모리/팹리스, 전력기기, 반도체장비, 보험, 건설, 테스트소켓, 유통, 로봇/자동화, 미용기기, 산업기계, 완성차, SW/AI, 자동차부품, 증권, 우주항공, 배터리셀, 통신, 원자력, 양극재, 신재생, 전자장비, 조선기자재, 조선, 타이어, 바이오신약, 방위산업, 음극재/소재, 은행, 정유/화학, 철강/비철, 의료기기, 해운, 여행/레저, 음식료, 패션/의류, 제약, 인터넷/플랫폼, 유틸리티, 리츠/부동산, 게임, CDMO, 화장품, 엔터/미디어.

---

## 해외주식 전략 적용 (VBO 일봉)

결론: 일봉 셋업형 전략만 적용 가능(해외 일봉 API 존재), 장중/실시간 전략은 불가. 첫 대상 = `LarryWilliamsVBOStrategy`. 제약: **해외 주문 TR은 실전(TTTS6036U 등)만, 모의 주문 TR 없음** → dry-run 검증 전 실주문 배선 금지. Phase 1~4(데이터 어댑터·일봉 백테스트·dry-run·주문/사이징) 완료, 자동 전략 경로 `live_enabled=False` 잠금.

- [ ] **Phase 5 안전/canary**: `get_overseas_balance`/`ccnl` reconcile(`OverseasReconcileService` scaffolding 존재), risk gate/kill switch/canary USD 확장, 실전 소액 canary, canary auto-fire 배선 + `live_enabled=True` 전환 — dry-run 검증 + canary 게이팅.

주요 파일: `brokers/korea_investment/korea_invest_overseas_stock_api.py`, `brokers/broker_api_wrapper.py`, `services/overseas_order_execution_service.py`, `services/overseas_position_sizing_service.py`, `services/overseas_reconcile_service.py`, `services/stock_query_service.py`, `view/web/bootstrap/{service_container,strategy_factory}.py`, `config/tr_ids_config.yaml`

---

## 조건부 / 정책 결정 대기

### Pool B 튜닝 (후보 부족 재발 시)

- [ ] 거래대금 기준 50억 → 30억 추가 완화 검토.
- [ ] 정배열 조건을 Pool B 전용 `current > ma_20d` 중심으로 완화 검토.

### R-2. 전략 상관 / 단일 regime 집중 [엣지 도입 — Phase 1~3 완료, Phase 4 데이터 대기]

- 활성 7전략 전부 long-only 모멘텀/돌파/눌림목 → 단일 "상승/추세 regime 베팅". 상관행렬·regime 분해는 일일 리포트에 노출 완료.
- 비상관 엣지 = **인버스 ETF 레짐 슬리브**(KOSPI bear에서만 -1x 인버스 ETF 추세추종 매수, long-only와 음의 상관). 직접 숏(공매도)은 개인 비현실적이라 제외.
  - 완료: Phase 1 전략(#594) · Phase 2 다중 베어 사이클 백테스트(#595, 실데이터 46거래 복리 +20% MDD −11.8%) · Phase 3 factory 배선 `enabled=False`(#596, shadow/paper 관찰). 일봉+REST라 ETF 무틱(2-4) 무관.
- [ ] **Phase 4 (베어 paper 데이터 + 정책 후 구현)**: profitability gate는 전역 단일 config(per-strategy override 없음)라 인버스 슬리브가 표준 gate와 3중 충돌(① win_rate 0.35/0.40 vs 실측 34.8% ② min_trades 30/100 vs regime-conditional ③ regime-balance vs BEAR 단일). **방향 A 확정**: 추세추종 디코릴레이터엔 win_rate가 잘못된 기준 → gate에 per-strategy override 추가 + 인버스 전용 프로파일(win_rate 완화/제거, payoff·profit_factor·양(+)PnL 유지, min_trades 완화+≥2 독립 베어 에피소드, regime-balance 면제). 임계값 보정할 paper 베어 데이터가 없어 **다음 베어장 paper 축적 후 구현**(선구현 금지). 이후 canary → `enabled=True` 전환.

주요 파일: `strategies/inverse_etf_regime_strategy.py`, `strategies/inverse_etf_regime_backtest.py`, `services/strategy_profitability_gate_service.py`, `services/market_regime_service.py`, `view/web/bootstrap/strategy_factory.py`

### R-6. 비용 모델 — capacity/시장충격 [관찰]

- [ ] (관찰) 전략별 후보 종목 평균 거래대금 분포 + 주문 규모 대비 시장충격 추정을 리포트에 노출할지 검토. (`max_top_of_book_participation_pct` 이미 존재)

### 보류 — 정책 합의 후 재승격

- [x] **S-9 god class 분리** — `EventShadowManager`(`scheduler/event_shadow_manager.py`)로 shadow 블록 ~327줄 추출 완료(2026-06-28). scheduler 2432→2097줄. entry/exit 구독·evaluator·journal record/flush·teardown 이동, 테스트 ~44곳 receiver를 `_event_shadow_manager`로 갱신(동작 불변, unit 6505 + integration 245 green). 분리로 2-4 PR-3 No-Go(삭제) 시에도 단일 파일 제거로 종결 가능.
- [ ] **3-4 active strategy lifecycle 7단계 분해**(`get_watchlist`/`filter_candidates`/`evaluate_entries_bounded`/`evaluate_exits_bounded`/`emit_metrics`) — 현재 `scan`/`check_exits`에 묻혀 있어 대형 리팩토링. checklist 테스트는 적용 완료. 공통 흐름이 더 쌓이면 재승격.
- [ ] 기타: tiered force-exit window / RiskGate 실패 주문 cap 정책 / 전략별 min trading value·market cap 하한 / 매도 RiskGate 우회·KillSwitch auto-trigger / volatility hard gate / 성과 저하 자동 해제·수량 축소 / WebSocket health probe 자동화 / 레거시 전략 백테스트 통합 여부.

주요 파일: `interfaces/live_strategy.py`, `tests/unit_test/strategies/test_live_strategy_lifecycle_contract.py`

---

## 바로 착수 추천 순서

1. **운영 관찰·블로커 (최우선, 코드 아님)**
   - WebSocket 무틱 ≈55% — **KIS 에스컬레이션**(ETF/우선주 WS 지원 확인 + 무틱 보통주 계정 단위 문의). 해소 전까지 shadow 수집 불가. (P2 2-4)
   - profitability gate 우회 없이 shadow/paper/canary journal로 전략별 실전 근거 축적 (P1 1-6, 라이브 축적)
2. **데이터 + 정책 대기**
   - R-2 인버스 ETF 슬리브 Phase 4(추세추종 게이트 프로파일) — 다음 베어장 paper 데이터 축적 후 구현
3. **외부 운영·데이터 확보 후**
   - KIS REST/WebSocket 유량 한도 재확인 (P2 2-2) · 실전 submit/signing fixture (P0 0-1) · 장중 microstructure 캡처 (P1 1-5)
4. **정책 결정 후**
   - DSR hard threshold 및 PBO/DSR gate 운영 기준 확정 (P1 1-7, canary 후)
5. **조건부 트리거 — 재발 시**
   - Pool B 거래대금 50→30억 / 정배열 완화

---

## 운영 안전 기준 현황

표기: `[x]` 완료, `[~]` 부분 완료/진행 필요, `[ ]` 미완료

- [x] 실전 모드에서 주문 접수만으로 보유/체결이 확정되지 않는다. (`SUBMITTED` 유지)
- [x] 모든 주문은 Risk Gate 통과 전 broker API를 호출하지 않는다.
- [x] 서비스 재시작 후 미체결 주문·잔고를 복원/reconcile한다. (`restore_state_from_broker`/`reconcile_orders_with_broker`)
- [x] paper/real URL·TR ID·토큰·계좌 분기가 테스트로 검증된다.
- [x] 장애·데이터 지연·websocket 끊김·reconcile 실패 시 신규 주문 차단 또는 경고 전환.
- [~] 전략 성과는 수수료·세금·슬리피지 반영 순수익 기준으로 추적된다.
  - 진행 필요: `MomentumStrategy` 등 비활성/레거시 독립 백테스트 경로까지 동일 체결 리포트/장부 통합할지 결정.

---

## 해소된 리뷰 결론 (참고 — 코드 후속 없음)

> 상세는 git/PR·리포트 파일로 추적. 결론만 보존.

- **R-1 생존편향 [해소]**: 의무 손절이 PnL 생존편향을 방어(손절-5%/트레일8% 멀티데이 시뮬 GAP ≈ -0.15%p/거래). 손절 규율 자체가 방어선. 상세 `data/survivorship/survivorship_exposure_report.md`.
- **R-3 포트폴리오 heat [해소]**: 전 포지션 합산 open-risk 한도 도입(profile별 1%/3%/6%). 재승격 조건: flat full-sum을 분산 크레딧 모델로 바꾸기로 정책 결정할 때 상관 가중 함께 설계.
- **R-4 갭 스톱 [해소]**: 백테스트 `OrderType.STOP` 갭 관통 보수 체결(매도=`min(stop,open)`). 잔여: 실제 forward gap 정량 측정은 종목별 OHLCV 조인 필요(R-1과 동일 맥락, 미구현).
- **R-5 증권거래세율 [해소]**: 0.20%(0.002) 현행 정확값 — 변경 없음.
- **S-1~S-10 StrategyScheduler 리뷰**: S-1~S-8 버그/수명/구조 수정 완료, S-3/S-10 의도된 설계 확인, S-9는 위 "보류" 항목으로 이관.
