# Investment Trading App - 남은 To-Do

최종 업데이트: 2026-05-26 (main 최신 코드 리뷰 반영 — canary/profile/API budget/broker mapper/event-driven 잔여 리스크 재정리)

이 문서는 현재 남은 실행 항목만 추린 목록이다. 완료된 구현 상세, 완료 체크 항목, 과거 세션 요약은 제거한다. 100% 종료된 섹션(`[x]` only, follow-up 없음)은 git history 로 추적하고 본 문서에서 삭제한다.

정리 원칙:

- 실전 계좌 보호와 주문 안정성을 최우선으로 둔다.
- 이미 적용된 항목은 새 기능으로 다시 넣지 않고, 검증/고도화 과제로만 남긴다.
- 후보군 관리는 신규 구축 과제가 아니라 기존 `OneilUniverseService` / `SubscriptionPolicy` 구조의 공통 파이프라인 확장 과제로 본다.
- 주문/브로커/스케줄러 변경은 테스트 hang 가이드와 paper/real 분기 검증을 함께 적용한다.
- `VolumeBreakoutStrategy`, `VolumeBreakoutLiveStrategy`, `TraditionalVolumeBreakoutStrategy`, `GapUpPullbackStrategy`, `ProgramBuyFollowStrategy`는 현재 사용하지 않는 전략이므로 신규 연결/개선 우선순위에서 제외한다.

남은 실행 영역 요약:

- P0 0-1: 실전 체결 이력 확보 후 broker order mapper 분리 + fixture 회귀 잠금.
- P0 0-7: ~~canary 5% 노출 제한을 코드/config profile로 분리하고 real 기본 30%와 운영 혼동을 제거.~~ ✅ 완료 (2026-05-27).
- P0 0-8: 라이브 전략 일봉 지표에서 당일 미완성 봉 제외 옵션을 도입한다.
- P0 0-9: 라이브 손절/익절 판단을 gross PnL이 아닌 비용 반영 net PnL 기준으로 통일한다.
- P0 0-10: 같은 사이클의 신규 BUY에 대해 pending/reserved cash 및 종목별 전 전략 합산 노출을 반영한다.
- P0 0-11: kill switch 및 sync fallback state 저장을 atomic write로 통일한다.
- P1 1-5: 한국장 microstructure fixture 로 체결 모델 보수성 검증.
- P1 1-6: 실전 journal/shadow/paper/live 성과 데이터로 profitability gate의 실제 통과 근거 확보.
- P1 1-7: multiple testing proxy를 formal walk-forward / purged validation / PBO·Deflated Sharpe급 검증으로 확장할지 결정.
- P2 2-2: ~~실제 KIS 계정별 REST/WebSocket 유량 한도 재확인 후 budget 기본값 보정.~~ 전역 normal 8/s + emergency 2/s 기본값 보정 완료. 계정별 공식 한도 재확인은 운영 전 외부 확인으로 유지.
- P2 2-4: VBO shadow 5거래일 jsonl 수집 → polling parity 비교. event-driven live order는 별도 승인 전 No-Go.
- P2 2-5: 전략 scan의 종목별 현재가 REST 호출을 batch quote / WebSocket snapshot 중심으로 줄인다.
- P3 3-4: active strategy lifecycle contract 최소 공통 단계 강제 여부 재설계(현재 보류).
- P3 3-5: backtest/live 호가단위 tick-size 로직을 단일 utility로 통일한다.
- P3 3-6: 지표/전략 경로의 광범위 `except Exception` silent skip에 alert/metric hook을 붙인다.
- Pool B 튜닝: 후보 부족 재발 시 거래대금/정배열 조건 완화 검토.
- 완료 기준의 전략 성과 `[~]`: `MomentumStrategy` 등 비활성 백테스트 경로의 표준 journal 통합 여부 결정.

---

## P0. 실전 손실 방지

### 0-1. 실전 KIS `inquire-daily-ccld` 응답 필드 검증

- [blocked] 실제 체결 이력이 있는 실전 계좌 응답을 캡처한다. (현재 실전 계좌 체결 이력 부재)
- [blocked] 민감정보를 제거한 fixture를 추가한다. (실전 응답 확보 후 진행)
- [blocked] paper fixture와 real fixture의 필드 차이를 회귀 테스트에 반영한다. (실전 응답 확보 후 진행)
- [blocked] 주문번호, 종목코드, 매수/매도 구분, 주문수량, 누적체결수량, 미체결수량, 평균체결가, 취소/거부 필드 매핑을 확정한다. (실전 응답 확보 후 진행)
- [~] 주문 접수 응답의 broker order number mapper를 실전/모의 fixture 기반으로 확장한다.
  - 적용 완료: 주문번호 추출 실패 시 raw payload 보존 + `FillReconciliationService.on_broker_order_no_missing` → 운영자 CRITICAL 알림 + 다음 reconcile 사이클까지 신규 주문 차단.
  - 후속(blocked): 실전/모의 fixture 확보 후 별도 `BrokerOrderResponseMapper` 클래스로 분리하고 submit response / order query / signing notice / real response / paper response를 각각 normalize한다.
  - 테스트 고정 대상: `ordno`, `order_no`, `odno`, `ORDNO`, `ORDER_NO`, `ODNO`, `ODER_NO`, `주문번호`, 중첩 `output` payload, 체결통보 payload 키.
  - 리뷰 판단: 현재 구조는 추출 실패 시 신규 주문을 막는 방어는 충분하지만, submit response mapper가 `OrderStateMachine` 내부 helper에 가까워 독립 mapper와 raw fixture 회귀 테스트가 아직 필요하다.

주요 파일:

- `brokers/korea_investment/korea_invest_account_api.py`
- `brokers/korea_investment/korea_invest_trading_api.py`
- `brokers/broker_api_wrapper.py`
- `services/order_execution_service.py`
- `services/fill_reconciliation_service.py`

---

### 0-7. Canary profile과 real exposure 한도 분리

- [x] canary 운영용 profile을 코드/config에 명시적으로 추가한다.
  - 적용 완료: `AppConfig.operating_profile` (`canary` | `real_limited` | `real_full`, default `canary`). `RiskGateCanaryOverrides`(5% / 2 pending / 1M order), `PositionSizingCanaryOverrides`(0.25% / 1.5%) 신규 dataclass. 기존 `real_mode_overrides`는 의미상 `real_limited` overlay로 재정의 (값/동작 변경 없음).
- [x] predeploy / pre-market health check / runbook에서 현재 profile이 `canary`인지 표시하고, canary 단계에서 `real_limited` 또는 `real_full` 설정이면 경고 또는 차단한다.
  - 적용 완료: `PreDeployCheckService.check_operating_profile` 신규 (canary=PASS, 그 외=WARN). `check_real_mode_policy_strictness`는 profile 별 임계 분기 (canary=5%/2/0.25%/1.5%, real_limited=30%/5/0.5%/3.0%, real_full=SKIPPED).
  - 후속 (선택): `pre_market_health_check_task` 별도 surfacing은 미반영. 현재 PredeployCheckService 경로로만 표시.
- [x] `real_limited`(예: 총 노출 30%)와 `real_full`은 canary 성과 데이터와 별도 승인 후에만 사용할 수 있도록 운영 문서에 명시한다.
  - 적용 완료: `docs/canary_procedure.md` 운영 한도 표를 3-tier (canary/real_limited/real_full) 로 확장. `config/config.yaml.example` 에 `operating_profile`, `risk_gate.canary_overrides`, `position_sizing.canary_overrides` 명시.

주요 파일:

- `config/config.yaml`
- `config/config.yaml.example`
- `services/risk_gate_service.py`
- `services/position_sizing_service.py`
- `docs/canary_procedure.md`
- `docs/predeploy_checklist.md`

---

### 0-8. 라이브 일봉 지표에서 당일 미완성 봉 제외

- [ ] `IndicatorService.get_rsi()`, `get_moving_average()`, `calculate_atr()`에 `exclude_today: bool = False` 또는 동등 옵션을 추가한다.
- [ ] 라이브 전략 호출 경로는 기본적으로 당일 미완성 봉을 제외하도록 변경한다. 특히 RSI(2), 5MA/200MA exit, ATR 기반 sizing 경로를 우선 고정한다.
- [ ] 차트/리포트 UI처럼 장중 현재 봉 표시가 필요한 경로는 기존 동작을 유지하거나 명시적으로 `exclude_today=False`를 사용한다.
- [ ] 테스트: 장중 `MarketDataService.get_ohlcv("D")`가 today row를 병합해도 라이브 지표 호출은 전일 확정 봉까지만 사용함을 검증한다.

판단:

- 코드 검토 결과, `MarketDataService.get_ohlcv()`는 장중 `_fetch_today_ohlcv()`로 today row를 붙인다. `get_chart_indicators()`는 마지막 row를 제외하지만 RSI/MA/ATR 개별 지표는 최신 row를 다시 병합해 반환하므로 미완성 봉 노이즈가 라이브 신호에 들어갈 수 있다.
- 이는 미래 데이터 lookahead라기보다 장중 미확정 캔들 기반 false-positive/false-negative 리스크로 본다.

주요 파일:

- `services/market_data_service.py`
- `services/indicator_service.py`
- `strategies/rsi2_pullback_strategy.py`
- `services/position_sizing_service.py`
- `tests/unit_test/services/test_indicator_service.py`

---

### 0-9. 라이브 exit 판단을 net PnL 기준으로 통일

- [ ] 활성 라이브 전략의 손절/익절 판단에서 `pnl_pct = (current - buy_price) / buy_price * 100` gross 비교를 비용 반영 net return 비교로 전환한다.
- [ ] `TransactionCostUtils`의 수수료/세금 기본값을 라이브 exit 판단에서도 공통 사용하되, 설정값으로 override 가능한지 검토한다.
- [ ] `TradeSignal.reason`, strategy log, notification에는 gross/net 중 무엇을 표시하는지 명확히 남긴다.
- [ ] 테스트: 동일 가격 경로에서 gross 기준으로는 미발동이지만 net 기준으로는 손절/익절 임계에 도달하는 케이스를 전략별 최소 1개 고정한다.

판단:

- backtest/virtual 성과 경로에는 비용 반영 유틸이 있으나, 라이브 `check_exits()`의 손절/익절 트리거는 대부분 gross PnL을 직접 계산한다. 비용만큼 백테스트 trigger와 라이브 trigger의 기준이 어긋날 수 있다.

주요 파일:

- `utils/transaction_cost_utils.py`
- `strategies/first_pullback_strategy.py`
- `strategies/high_tight_flag_strategy.py`
- `strategies/oneil_squeeze_breakout_strategy.py`
- `strategies/oneil_pocket_pivot_strategy.py`
- `strategies/rsi2_pullback_strategy.py`
- `strategies/larry_williams_vbo_strategy.py`
- `strategies/larry_williams_channel_breakout_strategy.py`

---

### 0-10. pending/reserved cash와 cross-strategy same-symbol 노출 반영

- [ ] `PositionSizingService.adjust_buy_qty()` 또는 상위 scheduler/order layer에 같은 사이클 내 accepted BUY의 reserved cash 누적기를 도입한다.
- [ ] 종목별 보유 금액 계산에 broker snapshot의 현재 보유뿐 아니라 active/pending BUY 주문과 같은 배치의 accepted BUY를 합산한다.
- [ ] 동일 전략 중복 보유 차단은 유지하되, 전 전략 합산 동일 종목 노출 상한을 별도 hard-block 또는 qty cap으로 추가할지 결정한다.
- [ ] 테스트: 같은 종목이 여러 전략에서 동시에 BUY 신호로 들어올 때, snapshot 기준 한도를 초과하지 않도록 두 번째 이후 주문 수량이 줄거나 차단되는지 검증한다.

판단:

- 현재 `PositionSizingService`는 `snapshot.available_cash`와 `snapshot.positions[code]`를 기준으로 수량을 줄인다. 하지만 같은 scheduler batch에서 아직 broker/account snapshot에 반영되지 않은 pending 주문 reservation은 차감하지 않는다.
- 리뷰의 “7전략 7배 노출” 표현은 과장 소지가 있다. broker snapshot에 이미 잡힌 보유는 차감된다. 실제 남은 리스크는 동시/미체결 BUY와 전 전략 same-symbol 합산 노출이다.

주요 파일:

- `services/position_sizing_service.py`
- `services/risk_gate_service.py`
- `scheduler/strategy_scheduler.py`
- `services/order_submission_coordinator.py`
- `services/backtest_execution_simulator.py`

---

### 0-11. 상태 파일 저장 atomic write 통일

- [ ] `StrategyStateIO._write_atomic()` 또는 공통 `AtomicJsonFile` 유틸을 추출해 kill switch 상태 저장에 적용한다.
- [ ] 전략 `_save_state()`의 sync fallback 경로(`open(..., "w")`, `json.dump`)도 같은 atomic utility를 사용하도록 바꾼다.
- [ ] 테스트: 저장 중 예외가 발생해도 기존 JSON 파일이 truncate 되지 않는 회귀 테스트를 추가한다.

판단:

- async 전략 저장 경로는 `StrategyStateIO.save_atomic()`으로 보호된다. 반면 `KillSwitchService._save_state()`와 일부 전략 sync fallback 경로는 truncate-write라 프로세스 강제 종료 시 상태 파일 손상 가능성이 남아 있다.

주요 파일:

- `utils/strategy_state_io.py`
- `services/kill_switch_service.py`
- `strategies/first_pullback_strategy.py`
- `strategies/high_tight_flag_strategy.py`
- `strategies/oneil_squeeze_breakout_strategy.py`
- `strategies/oneil_pocket_pivot_strategy.py`
- `strategies/rsi2_pullback_strategy.py`
- `strategies/larry_williams_channel_breakout_strategy.py`

---

## P1. 전략 수익성 검증

### 1-5. 백테스트 검증 확장

- [blocked] 실제 replay fixture를 통과 케이스까지 확장한다. (장중 프로그램매매 WebSocket 샘플 미확보)
  - 차단 사유: 장중 후보 종목의 프로그램매매 WebSocket 샘플을 실시간으로 캡처해야 하며, 장 마감 후에는 재생성 불가.
  - 차단 해제 조건: 장중에 `scripts.capture_backtest_microstructure`로 후보 종목 WebSocket 샘플 확보 → replay fixture overlay로 결합.
  - 선택 작업(차단 해제 후): 필요 시 `20260506`, `20260511`, `20260504`, `20260416` 표본 fixture를 추가 생성한다.
- [~] 실전 체결 품질과 괴리가 큰 백테스트 가정을 별도 고도화 과제로 분리한다.
  - 적용 완료: `BacktestBar`에 `trading_value`, `is_halted`, `vi_triggered`, `upper_limit_price`, `lower_limit_price`, `bid`, `ask` 추가. `OrderType.BEST_LIMIT` enum 추가. day-order 자동 취소 contract 명시.
  - 개선 방향: 실전 성과 판단용 runner에서는 next-bar 기본값, 호가/spread/부분체결/취소 fixture를 우선 추가한다.
- [~] 체결 모델을 한국 주식 실전 제약 기준으로 더 보수화한다.
  - 적용 완료: `opening_market_slippage_bonus_pct`, `liquidity_slippage_buckets`, microstructure 차단(VI/상하한가/거래정지), BEST_LIMIT, bid/ask spread (`spread_pct`).
  - 후보(추가): 호가단위, 부분체결, 미체결 후 취소, 매도 체결 실패.
- [ ] 한국장 실전 microstructure fixture로 체결 모델을 보정한다.
  - 남은 것: bid/ask book, 잔량, 체결강도, 프로그램매매 overlay를 fixture로 수집하고, 시장가/최유리/지정가별 fill quality가 live journal과 얼마나 벌어지는지 리포트한다.

주요 파일:

- `services/backtest_execution_simulator.py`
- `services/backtest_replay_context.py`
- `strategies/backtest_data_provider.py`
- `strategies/strategy_executor.py`
- `scripts/run_backtest.py`
- `tests/fixtures/backtest/`

---

### 1-6. 실전 수익성 데이터 확보와 profitability gate 운영

- [ ] shadow / paper / 소액 canary journal을 표준 포맷으로 누적하고, 전략별 profitability gate 통과 근거를 리포트한다.
  - 최소 유지 기준: 실전 override의 `min_trades=100`, `profit_factor>=1.3`, `payoff_ratio>=1.2`, `win_rate>=40%`, `max_drawdown<=12%`, regime별 최소 거래 수 30.
  - 필수 조건: parameter stability, Monte Carlo, regime balance, multiple testing adjustment를 운영 편의상 낮추지 않는다.
- [ ] gate 미통과 또는 journal provider 부재 시 신규 진입이 fail-close 되는지 predeploy/checklist 테스트로 다시 고정한다.
- [ ] 전략별 `entry_reason`, `invalidation_price`, `stop_loss_price`, `target_price`, `trailing_rule`, `expected_holding_period_days`, `confidence`, `required_data`, `config_hash`가 journal 분석까지 이어지는지 샘플 리포트로 검증한다.

판단:

- 코드 구조는 수익성 검증 준비 단계까지 왔지만, 실제 수익 가능성은 아직 실전 journal과 shadow/paper/live 성과 데이터로 증명해야 한다.

주요 파일:

- `services/strategy_live_expansion_gate_service.py`
- `services/strategy_profitability_gate_service.py`
- `scheduler/strategy_scheduler.py`
- `common/types.py`
- `services/strategy_log_report_service.py`

### 1-7. Multiple testing / 과최적화 방어 고도화

- [ ] 현재 proxy 기반 multiple testing 방어를 자금 확대 전 formal 검증으로 확장할지 결정한다.
  - 현재 판단: adjusted Sharpe proxy와 PBO-like proxy는 canary 전 검토에는 유용하지만 formal Deflated Sharpe 또는 formal PBO 구현은 아니다.
  - 후보: walk-forward validation, purged validation, formal PBO, Deflated Sharpe, 전략/필터 조합별 ablation 결과 자동 리포트.
- [ ] 전략 수와 필터 조합이 늘어나는 경우, canary 통과와 자금 확대 기준을 분리한다.
  - canary: 현재 proxy + 보수적 risk limit + live journal 관찰.
  - 자금 확대: formal 검증 + 실전 성과 데이터 + regime별 일관성 확인.

주요 파일:

- `services/multiple_testing_bias_service.py`
- `services/strategy_ablation_service.py`
- `services/strategy_profitability_gate_service.py`
- `docs/`

---

## P2. 시스템 성능

### 2-2. API 호출 최적화

- [~] API budget limiter 운영 정책을 실전 한도 기준으로 보정한다.
  - 적용 완료: endpoint별 category(`quotation_price`/`quotation_ohlcv`/`quotation_conclusion`/`account_balance`/`account_reconciliation`/`order_submit`/`order_cancel`/`websocket_*`) 동시성/rate 분리, emergency overlay lane, coverage matrix 코드/문서/테스트 고정, 장초반 부하 invariant 테스트, `rate_wait_total`/`rate_wait_seconds_total` 관측성.
  - 강제 주입 검증 완료: 모든 REST 호출은 `BrokerAPIWrapper → ClientWithRetryQueue(proxy) → call_api()` 경로를 거치며, services/scheduler/task에서 raw `httpx.AsyncClient` 직접 사용 없음. `KoreaInvestApiBase`는 Account/Quotations/Trading API의 부모 클래스로만 import되어 외부 직접 호출 경로 없음. `_BUDGET_ONLY_METHOD_CATEGORIES` 우회 가능성은 `test_coverage_matrix_includes_live_operation_paths` / `test_coverage_matrix_matches_runtime_method_routing` / `test_coverage_matrix_marks_emergency_sell_as_emergency_lane` / `test_coverage_matrix_categories_are_configured_in_default_limiter` 4종 테스트로 잠금. `bounded_gather()` 같은 호출 묶음 단위 제한은 전역 limiter를 대체하지 못한다는 원칙은 유효.
  - 적용 완료(2026-05-27): `ApiBudgetLimiter`에 `_global` normal bucket(8/s)과 `_global.emergency` bucket(2/s)을 추가해 category가 달라도 전체 합산 RPS를 제한한다. 개인 실전 10/s 가능성에도 맞는 보수 기본값이며, emergency liquidation은 normal traffic과 분리된 2/s bucket을 사용한다.
  - 적용 완료(2026-05-27): predeploy 점검에서 `_global` 및 `_global.emergency` bucket 누락을 WARN으로 보고한다.
  - 남은 작업(외부): 실제 KIS 계정별 REST/WebSocket 유량 한도 숫자는 공식 포털/계정 공지로 운영 직전 재확인한다. 공개 자료는 실전 20/s, 모의 1~2/s, 과거 개인 10/s 등으로 갈리므로 코드 기본값은 보수값 유지.
  - 적용 완료(2026-05-27): current price/OHLCV/account/balance/order submit/order status/reconcile/websocket subscribe/emergency liquidation priority lane을 모두 섞은 end-to-end 통합 시나리오 테스트 추가. `ClientWithRetryQueue → ApiRequestQueue → ApiBudgetLimiter → fake client` 전체 경로에서 shared `_global` bucket과 `_global.emergency` bucket 동작을 검증한다.
  - 운영 기준: 429 또는 "초당 거래건수를 초과하였습니다" 응답 후 retry하는 사후 대응만으로는 부족하므로, 장초반 다전략 동시 실행 전에 사전 제한이 동작해야 한다.

주요 파일:

- `core/retry_queue/api_budget_limiter.py`
- `core/retry_queue/client_with_retry_queue.py`
- `core/api_priority.py`
- `docs/api_budget_coverage_matrix.md`

### 2-4. Polling에서 event-driven으로 점진 전환

- [~] PR-2.5: VBO shadow 운영 관찰 시작.
  - 적용 완료: `StrategyFactory`에서 VBO `StrategySchedulerConfig(..., event_driven_shadow=True)` 활성화 (실주문은 `enabled=False`로 차단).
  - 적용 완료: parity 분석 도구 `scripts/analyze_event_shadow_parity.py` (matched / shadow_only / polling_only / duplicates / lead_time_seconds / price_divergence / missed_pnl / per_date) + `tests/unit_test/scripts/test_analyze_event_shadow_parity.py` 회귀 잠금.
  - 남은 작업: 5거래일 동안 `logs/strategies/event_shadow/YYYYMMDD.jsonl` 을 수집한 뒤 위 도구로 리포트 생성.
  - 검증 기준: shadow 신호와 기존 polling 신호의 시간/종목/가격 괴리를 비교해 실주문 전환 가능 여부를 판정한다.
  - 추가 기준: polling 대비 신호 선행 시간, fast path false positive, false negative, full gate parity, missed trade PnL, duplicate signal rate. (모두 위 스크립트 출력에 포함)
  - VBO 특이점: `evaluate_single()` shadow fast path는 execution strength/program-buy를 의도적으로 생략하므로, fast path 통과(=`shadow_only` + `matched`)와 full gate 최종 통과(=`matched` + `polling_only`)의 분리는 현재 스크립트의 `shadow_only` / `matched` / `polling_only` 분류로 간접 표현된다.
- [ ] event-driven signal은 별도 승인 전 shadow/latency 측정용으로만 운영한다.
  - 운영 원칙: 실주문은 polling scheduler + full gate 통과 경로만 허용한다.
  - No-Go 사유: `StrategySignalSink`는 protocol 수준이고, VBO fast path는 일부 full safeguard를 생략한다.
- [ ] exit fast-path는 entry event-driven 전환보다 별도 우선순위로 검토한다.
  - 목표: 손절 조건만이라도 WebSocket price snapshot 기반 shadow 판정으로 latency와 false-positive를 먼저 측정한다.
  - 실주문 전환 조건: 5거래일 이상 shadow journal에서 기존 polling exit과 괴리, 선행 시간, 중복률, 누락률을 리포트한 뒤 별도 승인.
- [blocked] PR-3: PR-2.5 관찰 결과 양호 시 VBO 실 적용 + OSB shadow 진입.
- [ ] PR-4+: 단계적 확장. (HighTightFlag 등 OHLCV 별도 조회 필요 전략에 적용할지 재평가)

구현 결정 사항 (`docs/event_driven_architecture.md` §9, 2026-05-18 확정):

- (Q1) `(strategy, code)` event throttle = 0.5초.
- (Q2) Stale snapshot 임계 = 5초.
- (Q3) Shadow mode 운영 기간 = VBO 1주 (5 거래일).
- (Q4) `signal_source` 저장 = `metadata` JSON 키 (DB schema 변경 없음).
- (Q5) Throttle vs debounce 분리: trigger crossing은 evaluator 평가 허용, signal publish만 debounce.

주요 파일:

- `services/strategy_event_router.py`
- `services/event_shadow_journal_service.py`
- `interfaces/live_strategy.py`
- `strategies/larry_williams_vbo_strategy.py`
- `scheduler/strategy_scheduler.py`
- `view/web/bootstrap/service_container.py`
- `view/web/bootstrap/strategy_factory.py`
- `docs/event_driven_architecture.md`

---

### 2-5. 전략 scan 현재가 조회 batch/snapshot 최적화

- [ ] 활성 전략 scan에서 종목별 `get_current_price()` REST fallback이 많이 발생하는 경로를 측정하고, `price_lookup_stats_delta`를 전략별 리포트에 포함한다.
- [ ] 후보군 현재가는 우선 WebSocket snapshot을 사용하고, snapshot 누락분은 `get_multi_price()` 30종목 batch로 보강하는 helper를 설계한다.
- [ ] 전략별로 개별 REST 호출이 필요한 전체 필드(`per`, `pbr`, `stck_llam` 등)와 단순 현재가만 필요한 경로를 분리한다.
- [ ] 테스트: 60개 후보 scan에서 REST current price 호출이 60회가 아니라 batch 2회 이하로 떨어지는 경로를 mock으로 검증한다.

판단:

- 리뷰의 “평균 87초 stale” 수치는 현재 `StockQueryService`의 5초 WebSocket snapshot TTL과 맞지 않아 그대로 채택하지 않는다. 다만 전략 scan이 batch quote를 적극 사용하지 않고 종목별 현재가 조회로 쉽게 fallback되는 구조는 성능/latency 리스크다.

주요 파일:

- `services/stock_query_service.py`
- `services/market_data_service.py`
- `brokers/korea_investment/korea_invest_quotations_api.py`
- `strategies/`
- `scheduler/strategy_scheduler.py`

---

## P3. 유지보수성

### 3-4. 전략 공통 lifecycle/state contract

- [~] 활성 전략의 공통 scan/check_exits/state save/load 패턴을 base class 또는 helper로 추출할지 설계한다.
  - 결정: scan/check_exits 대형 base class 추출은 현재 반복 제거 대비 리스크가 커서 보류한다. 공통 흐름이 더 쌓이면 별도 설계 항목으로 재승격한다. `larry_williams_vbo`는 state 파일이 없어 마이그레이션 대상이 아니다.
- [~] active strategy lifecycle contract를 최소 공통 단계로 강제할지 재설계한다.
  - 후보 단계: `load_state` → `get_watchlist` → `filter_candidates` → `evaluate_entries_bounded` → `evaluate_exits_bounded` → `save_state` → `emit_metrics`.
  - 목표: 대형 base class가 부담되면 helper/protocol/checklist 테스트로라도 active strategy별 누락을 탐지한다.
  - 적용 완료(2026-05-25): checklist 테스트 방식으로 활성 7개 전략 최소 contract 누락 자동 탐지.
  - 후속(보류): 7단계 분해(`get_watchlist`/`filter_candidates`/`evaluate_entries_bounded`/`evaluate_exits_bounded`/`emit_metrics`)는 별도 PR. 현재는 `scan`/`check_exits` 안에 묻혀 있어 분해 자체가 대형 리팩토링.

주요 파일:

- `interfaces/live_strategy.py`
- `strategies/`
- `tests/unit_test/strategies/test_live_strategy_lifecycle_contract.py`

---

### 3-5. tick-size 로직 단일화

- [x] `BacktestExecutionSimulator.tick_size()`를 `utils.korea_invest_price_utils.get_tick_size()`로 대체하거나 공통 utility를 broker-neutral 이름으로 승격한다.
  - 적용 완료: `BacktestExecutionSimulator.tick_size()`의 중복 호가단위 표를 제거하고 `get_tick_size()` 단일 소스로 위임. 메서드 시그니처는 기존 테스트/`round_to_tick` 호환을 위해 유지.
- [x] backtest BUY/SELL tick rounding과 live 주문 가격 보정의 방향성 차이(매수 올림/매도 내림 vs live 지정가 내림)를 의도적으로 문서화한다.
  - 적용 완료: `round_to_tick()`에 주석으로 명시(backtest=보수적 체결가 매수 올림/매도 내림, live `adjust_price`=항상 내림).
- [x] 테스트: tick boundary fixture를 단일 테스트 데이터로 backtest/live utility 양쪽에 적용한다.
  - 적용 완료: `TICK_SIZE_FIXTURE` 단일 경계 데이터로 `sim.tick_size()`와 `get_tick_size()` parity 검증 (`test_backtest_and_live_share_single_tick_size_table`).

판단:

- (해소) backtest와 live의 호가단위 표 중복을 단일 소스로 통일했다. 향후 KRX/NXT 변경 시 `get_tick_size()` 한 곳만 바꾸면 되고, parity 테스트가 재중복을 차단한다.

주요 파일:

- `services/backtest_execution_simulator.py`
- `utils/korea_invest_price_utils.py`
- `brokers/korea_investment/korea_invest_trading_api.py`
- `tests/unit_test/services/test_backtest_execution_simulator.py`
- `tests/unit_test/utils/test_korea_invest_price_utils.py`

---

### 3-6. 광범위 예외 처리의 silent skip 방지

- [ ] `IndicatorService`의 계산/캐시 경로에서 `except Exception`으로 `UNKNOWN_ERROR` 또는 `None`만 반환하는 지점을 분류한다.
- [ ] 데이터 schema/type 오류는 최소 ERROR log + metric counter로 집계하고, 반복 발생 시 `OperatorAlertService`로 알림을 올린다.
- [ ] 전략 scan/check_exits의 per-code 예외는 전체 전략 중단을 막되, 전략별 fail-rate metric에 반영한다.
- [ ] 테스트: 지표 계산 중 schema 오류가 발생하면 전략이 조용히 skip만 하지 않고 alert/metric hook이 호출되는지 검증한다.

판단:

- per-code 장애를 흡수하는 운영 안정성은 유지해야 한다. 다만 지표/전략 계산 실패가 단순 “데이터 없음 → 진입 skip”으로만 보이면 신호 손실이 장기간 누적될 수 있다.

주요 파일:

- `services/indicator_service.py`
- `services/operator_alert_service.py`
- `strategies/`
- `services/strategy_log_report_service.py`

---

## Strategy Log 남은 작업

### Pool B 튜닝 관찰

- [ ] 후보 부족 현상이 재발하면 거래대금 기준을 50억에서 30억으로 추가 완화할지 검토한다.
- [ ] 후보 부족 현상이 재발하면 정배열 조건을 Pool B 전용으로 `current > ma_20d` 중심으로 완화할지 검토한다.

---

## 바로 착수 추천 순서

남은 항목은 외부 데이터/운영 관찰 대기 작업과 코드 수정으로 바로 진행 가능한 작업으로 나뉜다. 실전 자본 보호에 직접 닿는 코드 수정 항목을 우선 정리한다.

1. **외부 운영 확인 후 즉시 진행**
   - 실제 KIS 계정별 REST/WebSocket 유량 한도 재확인 → 필요 시 `_global` 8/s + `_global.emergency` 2/s 운영값 조정 (P2 2-2; 공통 HTTP 경로 강제 주입과 보수 기본값은 적용 완료)

2. **코드 수정으로 바로 진행 가능**
   - 라이브 일봉 지표에서 당일 미완성 봉 제외 옵션 추가 (P0 0-8)
   - 라이브 exit 판단을 net PnL 기준으로 통일 (P0 0-9)
   - pending/reserved cash와 전 전략 same-symbol 노출 반영 (P0 0-10)
   - kill switch/state sync fallback atomic write 통일 (P0 0-11)

3. **운영 관찰 진행 중**
   - VBO shadow 5거래일 jsonl 수집 → `scripts/analyze_event_shadow_parity.py` 로 parity 리포트 생성 → PR-3 진입 판정 (P2 2-4 PR-2.5)
   - 손절 전용 exit fast-path shadow/latency 측정 설계 (P2 2-4 후속)
   - profitability gate는 우회하지 않고 shadow/paper/canary journal로 전략별 실전 근거를 축적 (P1 1-6)

4. **외부 데이터 확보 후 진행 가능 (blocked)**
   - 실전 KIS `inquire-daily-ccld` 체결 이력 응답 캡처 → fixture 회귀 (P0 0-1)
   - broker order number mapper 별도 클래스 분리 + fixture 테스트 (P0 0-1)
   - 장중 후보 종목 프로그램매매 WebSocket 샘플 캡처 → replay fixture overlay (P1 1-5)
   - 한국장 microstructure fixture 로 체결 모델 보수성 검증 (P1 1-5)
   - VBO 실 적용 + OSB shadow 진입 (PR-3, P2 2-4 — PR-2.5 결과 양호 조건)

5. **조건부 트리거 — 재발 시 진행**
   - Pool B 거래대금 50→30억 완화 검토
   - Pool B 정배열 조건을 `current > ma_20d` 중심 완화 검토

6. **정책 합의 후 재승격 후보 (보류)**
   - formal walk-forward / purged validation / PBO / Deflated Sharpe 검증 도입 여부 결정 (P1 1-7)
   - active strategy lifecycle 7단계 base class 분해 (P3 3-4, 외과수술적 변경 원칙으로 보류)
   - tiered force-exit window(거래대금별 30/45/60분) 도입 여부 결정. 현재 `force_exit_on_close=True` 기본 등록은 VBO 중심이므로 전 전략 리스크로 일반화하지 않는다.
   - RiskGate daily cap은 broker retry마다 중복 증가하지 않는다. 다만 현재 구조상 broker 성공 전 `validate_order()`에서 일일 주문금액을 기록하므로, 실패 주문도 cap을 소모하는 정책이 의도인지 별도 결정한다.
   - 전략별 `min_trading_value_won` / `min_market_cap_won` 하한 설정 (P0 0-2 후속)
   - 매도 청산 경로의 RiskGate 우회 정책, KillSwitch 청산 예외, KillSwitchService auto-trigger 호출처 추가 (P0 0-6 후속)
   - market beta / strategy correlation missing evidence를 block reason으로 승격할지 결정 (P1 1-1 후속)
   - volatility hard gate(`RiskGateConfig.max_volatility_pct`) 도입 여부 결정 및 시장 거래대금 surge 데이터 소스 wiring (P1 1-4 후속)
   - 장 시작 전 / watchlist 생성 시 range precompute, 전 전략 entry rejection INFO 로그 표준화, latency 조건부 후속 재개 여부 결정 (P1 1-4 후속)
   - 성과 저하 자동 해제 정책 (P4 4-1 후속, 현재 수동 reset 만)
   - 성과 저하 시 수량 축소 / paper mode 자동 전환 (P4 4-1 후속)
   - WebSocket subscription health 자동 점검 probe 어댑터, event shadow ≥95% 일치율 비교 자동화 (P4 4-4 후속)
   - `MomentumStrategy` 등 비활성/레거시 백테스트 통합 여부 결정 (완료 기준의 전략 성과 `[~]` 항목)

---

## 완료 기준

표기: `[x]` 완료, `[~]` 부분 완료/진행 필요, `[ ]` 미완료

- [x] 실전 모드에서 주문 접수만으로 보유/체결이 확정되지 않는다.
  - 근거: 실전 모드에서는 `finalize_immediately=True`도 무시하고 `SUBMITTED` 상태를 유지한다.
  - 확인 파일: `services/order_execution_service.py`, `tests/unit_test/services/test_order_execution_service.py`

- [x] 모든 주문은 Risk Gate 통과 전 broker API를 호출하지 않는다.
  - 근거: `_submit_order_with_fsm()`에서 `RiskGateService.validate_order()` 차단 응답 시 broker 주문 호출 전에 반환한다.
  - 확인 파일: `services/order_execution_service.py`, `services/risk_gate_service.py`, `tests/unit_test/services/test_order_execution_service.py`

- [x] 서비스 재시작 후 미체결 주문과 잔고를 복원 또는 reconcile할 수 있다.
  - 근거: Web 시작 시 `restore_state_from_broker()`를 호출하며, 미체결/당일 체결 내역으로 주문 상태를 복원하고 `reconcile_orders_with_broker()`로 미체결/체결/잔고 불일치를 점검한다.
  - 확인 파일: `view/web/web_main.py`, `services/order_execution_service.py`, `tests/unit_test/services/test_order_execution_service.py`

- [x] paper/real URL, TR ID, 토큰, 계좌 분기가 테스트로 검증된다.
  - 근거: 환경 전환 시 URL/계좌/API 키/token provider 교체, TR ID paper/real 선택, 토큰 provider 위임 테스트가 있다.
  - 확인 파일: `brokers/korea_investment/korea_invest_env.py`, `brokers/korea_investment/korea_invest_trid_provider.py`, `tests/unit_test/brokers/korea_investment/`

- [~] 전략 성과는 수수료, 세금, 슬리피지를 반영한 순수익 기준으로 추적된다.
  - 적용 완료: 거래 비용 계산 유틸/체결 품질 로그/슬리피지 추적, 성과 지표 기본값을 net PnL/return 기준으로 전환(`apply_cost=false`로 gross 경로 유지), `BacktestExecutionSimulator` 슬리피지/호가 단위/수수료/세금 포함 리포트, 활성 period runner와 레거시 `VolumeBreakoutStrategy.backtest_open_threshold_intraday()` 표준 journal 통합, `MarkToMarketBarProvider` 주입 시 일중 high/low MFE/MAE 합산.
  - 진행 필요: `MomentumStrategy` 등 비활성/레거시 독립 백테스트 경로까지 동일 체결 리포트/포트폴리오 장부로 통합할지 결정한다.
  - 관련 파일: `utils/transaction_cost_utils.py`, `services/backtest_execution_simulator.py`, `services/order_execution_service.py`, `services/strategy_log_report_service.py`

- [x] 장애, 데이터 지연, websocket 끊김, reconcile 실패 시 신규 주문 차단 또는 경고 상태로 전환된다.
  - 적용 완료: Kill Switch/Risk Gate 주문 차단, 데이터 품질 오류 차단, websocket watchdog 재연결/경고, reconcile alarm 신규 주문 차단, reconcile 실패 운영 매트릭스(`docs/reconcile_failure_policy.md`), after-market reconcile task 알림 경로.
  - 관련 파일: `services/kill_switch_service.py`, `services/data_quality_service.py`, `services/risk_gate_service.py`, `services/order_execution_service.py`, `task/background/intraday/websocket_watchdog_task.py`, `task/background/after_market/after_market_reconcile_task.py`, `docs/reconcile_failure_policy.md`
