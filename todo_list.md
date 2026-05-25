# Investment Trading App - 남은 To-Do

최종 업데이트: 2026-05-25 (최신 main 엄격 리뷰 재반영)

이 문서는 현재 남은 실행 항목만 추린 목록입니다. 완료된 구현 상세, 완료 체크 항목, 과거 세션 요약은 제거했습니다. 100% 종료된 섹션(`[x]` only, follow-up 없음)은 git history 로 추적하고 본 문서에서 삭제합니다.

정리 원칙:

- 실전 계좌 보호와 주문 안정성을 최우선으로 둔다.
- 이미 적용된 항목은 새 기능으로 다시 넣지 않고, 검증/고도화 과제로만 남긴다.
- 후보군 관리는 신규 구축 과제가 아니라 기존 `OneilUniverseService` / `SubscriptionPolicy` 구조의 공통 파이프라인 확장 과제로 본다.
- 주문/브로커/스케줄러 변경은 테스트 hang 가이드와 paper/real 분기 검증을 함께 적용한다.
- `VolumeBreakoutStrategy`, `VolumeBreakoutLiveStrategy`, `TraditionalVolumeBreakoutStrategy`, `GapUpPullbackStrategy`, `ProgramBuyFollowStrategy`는 현재 사용하지 않는 전략이므로 신규 연결/개선 우선순위에서 제외한다.

최신 리뷰 반영 요약:

- 실전 데이터 수집 대기만 남은 상태가 아니다. 수익성 gate 기본 정책, canary 리스크 한도, 주문 정책 기본값은 코드/운영 정책으로 더 보수화해야 한다.
- API budget limiter는 구조가 있으나 실제 KIS 유량 한도, endpoint별 coverage matrix, scan/order/reconcile/emergency 동시 부하 검증이 남아 있다.
- broker 주문번호 추출은 reconcile alarm으로 방어하지만, 응답 유형별 mapper와 raw fixture 테스트가 아직 필요하다.
- `TradeSignal` contract는 확장됐지만 모든 BUY signal producer가 stop/invalidation/target/confidence/required_data를 일관되게 채우는 단계는 남아 있다.

---

## P0. 실전 손실 방지

백테스트에서 이긴 전략이 실거래에서도 같은 방식으로 기록, 체결, 정산, 차단되는지 검증하는 영역입니다. 실전 투입 전 최우선입니다.

### 0-1. 실전 KIS `inquire-daily-ccld` 응답 필드 검증

- [blocked] 실제 체결 이력이 있는 실전 계좌 응답을 캡처한다. (현재 실전 계좌 체결 이력 부재)
- [blocked] 민감정보를 제거한 fixture를 추가한다. (실전 응답 확보 후 진행)
- [blocked] paper fixture와 real fixture의 필드 차이를 회귀 테스트에 반영한다. (실전 응답 확보 후 진행)
- [blocked] 주문번호, 종목코드, 매수/매도 구분, 주문수량, 누적체결수량, 미체결수량, 평균체결가, 취소/거부 필드 매핑을 확정한다. (실전 응답 확보 후 진행)
- [~] 주문 접수 응답의 broker order number mapper를 실전/모의 fixture 기반으로 확장한다.
  - 검토 결과: 부분 타당. 현재 `OrderStateMachine.extract_broker_order_no()`는 `ordno`, `order_no`, `odno`만 확인한다. `inquire-daily-ccld`/미체결 조회 복원 쪽은 `ODNO`/`주문번호`도 일부 처리하지만, 최초 주문 응답 mapper는 더 좁다.
  - 개선 방향: raw broker response payload를 journal/diagnostic에 보존하고, 주문번호 추출 실패 시 reconcile alarm 또는 운영자 알림으로 승격한다.
  - 완료된 부분(2026-05-24): `FillReconciliationService.on_broker_order_no_missing(result, stock_code, order_key)` 신규 메서드 추가 — `_reconcile_alarm = True` + `_critical_alarm_manual_required = True` 설정, `logger.error` 에 raw payload(`result.data` repr) 포함, `NotificationCategory.TRADE` + `NotificationLevel.CRITICAL` 운영자 알림 emit(`alert_type=broker_order_no_missing`, metadata 에 stock_code/order_key/rt_cd/msg1/raw_data 보존). `BrokerOrderSubmitter` 에 `on_missing_broker_order_no_fn` 콜백 파라미터 추가, success 응답에서 broker_no 추출 실패 시 raw payload 로그 + 콜백 호출(콜백 예외는 흡수해 주문 흐름 차단 방지). `OrderExecutionService` 가 `_fill_reconciliation` 을 `_broker_submitter` 보다 먼저 생성하고 콜백 wire. 다음 reconcile 사이클까지 신규 주문이 차단되며 운영자가 raw payload 로 누락 필드 진단 가능. 단위 5333, 통합 235 통과.
  - 후속(blocked): 실전/모의 fixture 확보 후 별도 broker response mapper 클래스로 분리하고 `ODNO`(대문자), `주문번호`(한글), 중첩 payload, 체결통보 payload 키를 주문번호 추출 테스트로 고정한다.

주요 파일:

- `brokers/korea_investment/korea_invest_account_api.py`
- `brokers/korea_investment/korea_invest_trading_api.py`
- `brokers/broker_api_wrapper.py`
- `services/order_execution_service.py`
- `tests/unit_test/`
- `tests/integration_test/`

### 0-2. Canary 기본 리스크/주문 정책 보수화

- [x] 실전 canary 전용 config/profile을 분리하거나 기본 운영값을 낮춘다.
  - 리뷰 기준: 현재 `PositionSizingConfig.per_trade_risk_pct=1.5`, `max_per_position_pct=5.0`, `RiskGateConfig.max_total_exposure_pct=95.0`, `max_pending_orders=10`은 검증 전 자동매매 canary에는 공격적이다.
  - canary 목표값: `per_trade_risk_pct=0.25~0.5`, `max_per_position_pct=2.0~3.0`, `max_total_exposure_pct=20.0~35.0`, `max_pending_orders=3~5`.
  - 완료된 부분(2026-05-25): nested overlay 패턴 채택(`RiskGateFailOpenConfig`/`DataQualityConfig` 선례 일관). 신규 BaseModel 3종: `PositionSizingRealOverrides{per_trade_risk_pct=0.5, max_per_position_pct=3.0}`, `RiskGateRealOverrides{max_total_exposure_pct=30.0, max_pending_orders=5}`. 각 부모 config에 `real_mode_overrides: <Type>RealOverrides = Field(default_factory=...)` 필드 추가. paper 동작 보존(top-level 필드 그대로), real 모드에서만 자동 적용. yaml 명시값이 우선해 production 등급 운영자는 overlay 풀 수 있음. `config/config.yaml.example`에 commented overlay 블록 포함.
- [x] 실전 BUY 주문 정책을 canary 기준으로 fail-close에 가깝게 보수화한다.
  - 리뷰 기준: 현재 `OrderPolicyConfig.allow_market_buy=True`, `min_trading_value_won=0`, `min_market_cap_won=0`, `max_top_of_book_participation_pct=100.0`은 초기 실전 운용 기준으로 느슨하다.
  - canary 목표값: `allow_market_buy=false`, `max_market_slippage_pct=0.3~0.5`, `max_spread_pct=0.3~0.5`, `min_trading_value_won`/`min_market_cap_won` 전략별 하한 설정, `max_top_of_book_participation_pct=5~15`.
  - 완료된 부분(2026-05-25): `OrderPolicyRealOverrides{allow_market_buy=False, max_market_slippage_pct=0.5, max_spread_pct=0.5, max_top_of_book_participation_pct=10.0}` 추가. real 모드에서 시장가 매수 fail-close, 슬리피지/스프레드 0.5% 제한, 1호가 잔량 대비 최대 10% 참여. `min_trading_value_won`/`min_market_cap_won`은 todo 명시대로 전략별 하한이라 overlay 미포함(`RiskGateStrategyLimitConfig` 경로 유지).
  - 후속(blocked → 정책 합의 후): 전략별 `min_trading_value_won`/`min_market_cap_won` 하한 설정은 별도 PR에서 진행.
- [x] `PreDeployCheckService`가 real mode에서 canary profile 또는 production profile의 리스크/주문 정책이 과도하게 느슨하면 WARN/FAIL을 내도록 검증한다.
  - 완료된 부분(2026-05-25): `check_real_mode_policy_strictness()` 신규 등록. paper 모드 → SKIPPED. real 모드에서는 effective override 값을 canary 임계값(각 RealOverrides의 default)과 비교: `value > canary*1.5` → FAIL, `canary < value ≤ canary*1.5` → WARN. `allow_market_buy=True`는 real 모드에서 즉시 FAIL. `run_all()`에 등록되어 배포 전 자동 점검. 6건 단위 테스트(skipped/pass/warn/fail/market_buy_fail/fail_priority_over_warn) + 통합 테스트 8 checks expectation 갱신.
- [x] config 변경 후 `RiskGateService`, `PositionSizingService`, `OrderPolicyService`, web order path, scheduler order path의 paper/real 분기 회귀 테스트를 추가한다.
  - 완료된 부분(2026-05-25): 서비스 분기 로직 일관 패턴 적용. `RiskGateService`는 기존 `env` + `_is_real_mode()` 보유 → `_effective_max_total_exposure_pct()` / `_effective_max_pending_orders()` 헬퍼 추가. `PositionSizingService` / `OrderPolicyService` 생성자에 `env: Optional[Any] = None` keyword-only 인자 추가(기존 호출자 무변동) + `_is_real_mode()` + `_effective_*()` 헬퍼. 직접 `self._cfg.X` 읽기를 effective 헬퍼 호출로 교체. `view/web/bootstrap/service_container.py` 두 서비스 호출부에 `env=getattr(ctx.broker, "env", None)` 전달. 단위 테스트 추가: PositionSizing 8건(real_mode 분기 5 + risk_qty shrinks 1 + 기타), RiskGate 7건(effective_max 2종 paper/real 각 1 + user yaml + exposure/pending 차단 회귀 2 + 기타), OrderPolicy 4건(paper/real defaults + user yaml + market_buy 차단). 단위 5520 + 통합 235 통과.

주요 파일:

- `config/config_loader.py`
- `services/position_sizing_service.py`
- `services/risk_gate_service.py`
- `services/order_policy_service.py`
- `services/predeploy_check_service.py`
- `tests/unit_test/services/`

### 0-6. Emergency 전체청산 모드

- [x] `sell_all_stocks()`에 운영 목적별 청산 모드를 분리한다.
  - 최신 main 재확인(2026-05-25): 반영 완료. `services/order_execution_service.py::ClearanceMode`에 `SAFE_SEQUENTIAL`, `BOUNDED_PARALLEL`, `EMERGENCY`가 있고, `sell_all_stocks(exchange, *, mode=..., bounded_concurrency=...)`가 모드별 dispatcher를 사용한다.
  - 검증된 범위: 장중/장마감 분기, 일부 주문 실패 시 집계, bounded concurrency, emergency 동시 실행, paper/real 공통 기본 동작을 단위/통합 테스트로 고정했다.
  - 완료된 부분(2026-05-24): `services/order_execution_service.py`에 `ClearanceMode(str, Enum)` enum(`SAFE_SEQUENTIAL`/`BOUNDED_PARALLEL`/`EMERGENCY`) 추가. `sell_all_stocks(exchange, *, mode=ClearanceMode.SAFE_SEQUENTIAL, bounded_concurrency=3)` 시그니처로 확장(mode/bounded_concurrency keyword-only → 기존 positional 호출 보호). default가 기존 순차 동작이라 [view/web/routes/balance.py:77](view/web/routes/balance.py#L77) 호출부와 기존 단위 테스트 6건 무변동. 신규 helper `_dispatch_clearance()`는 모드별 분기(순차 / `asyncio.Semaphore(N)` / `asyncio.gather`), `_submit_one_sell()`은 종목별 매도 후 success/실패 dict로 정규화하며 예외 흡수 포함. 신규 단위 테스트 6건: `test_sell_all_stocks_default_mode_is_sequential`, `test_sell_all_stocks_safe_sequential_explicit`, `test_sell_all_stocks_bounded_parallel_respects_concurrency`, `test_sell_all_stocks_emergency_runs_concurrently`, `test_sell_all_stocks_emergency_aggregates_partial_failures`, `test_sell_all_stocks_market_closed_blocks_all_modes`(parametrize 3 모드). `conftest.fast_sleep` autouse가 `asyncio.sleep`을 mock해 yield되지 않으므로 `_make_concurrency_tracker()`는 `asyncio.Event` 기반 결정적 동시성 검증으로 작성. 단위 5346(이전 5338 → +8), 통합 235 통과.
  - 후속(정책 합의 필요): 매도 청산 경로의 RiskGate 우회 정책, KillSwitch 청산 예외, KillSwitchService auto-trigger 등 신규 호출처 추가는 별도 PR에서 진행한다.
  - 완료된 부분(2026-05-25, API budget 우선순위): emergency 청산이 일반 주문/조회 traffic 과 분리된 별도 lane 으로 진입하도록 `ApiBudgetLimiter` 에 카테고리별 emergency overlay lane 을 추가했다. `core/api_priority.py` 신규 — `ContextVar` 기반 `emergency_scope()` context manager 로 호출 체인 시그니처를 건드리지 않고 priority 전파. `ApiBudgetLimiter.acquire(category, *, priority="normal")` 로 시그니처 확장(keyword-only, 기존 호출자 무변동). 기본 emergency lane 적용은 `order_submit`/`order_cancel` 두 카테고리(각 limit=1, rate=2.0/sec — normal lane 과 독립 semaphore + 독립 rate bucket). 정의되지 않은 카테고리는 normal lane fallback. `ClientWithRetryQueue.budgeted_direct` 가 `current_priority()` 읽어 limiter 에 전달. `OrderExecutionService._dispatch_clearance` 의 EMERGENCY 분기를 `with emergency_scope():` 로 감싸 `asyncio.gather` 의 자식 Task 가 emergency priority 를 contextvar 로 상속한다. `snapshot()` 응답에 emergency lane 정의 시 `"emergency": {...}` 키 추가. 신규 단위 테스트 19건: api_priority 7, limiter emergency lane 7(default 정의/snapshot 노출/snapshot 생략/normal 점유 시 분리 진입/fallback/카운터 분리/rate bucket 독립), client priority propagation 4, order execution emergency wiring 3(EMERGENCY 시 emergency_scope 활성/SAFE_SEQUENTIAL normal/BOUNDED_PARALLEL normal). 기존 `RecordingLimiter` 테스트 mock 5건은 `**kwargs` accept 로 forward-compat 화. 단위 5492, 통합 235 통과.

---

## P1. 전략 수익성 검증

전략을 더 추가하기보다 현재 전략의 기대값, MDD, 승률, 손익비, 시장 국면별 성과를 먼저 검증합니다.



### 1-1. Profitability gate 기본 정책 강화

- [x] 실전 확대용 `StrategyProfitabilityGateConfig` 기본값 또는 real-mode profile을 더 엄격하게 조정한다.
  - 현재 기본값: `min_trades=30`, `min_profit_factor=1.2`, `min_payoff_ratio=1.0`, `min_win_rate=0.35`, `max_mdd_pct=20.0`, `min_regime_trade_count=5`, `require_parameter_stability=False`.
  - canary/full-auto 승격 후보값: 전략별 `min_trades=100~300`, `min_profit_factor=1.3~1.5`, `min_payoff_ratio>=1.2`, `max_mdd_pct=10~12`, `min_regime_trade_count>=30`, `require_parameter_stability=True`.
  - 완료된 부분(2026-05-25): nested overlay 패턴 채택(`PositionSizingRealOverrides`/`RiskGateRealOverrides`/`OrderPolicyRealOverrides` 선례 일관). 신규 `StrategyProfitabilityGateRealOverrides` BaseModel — canary 기본값 `{min_trades=100, min_profit_factor=1.3, min_payoff_ratio=1.2, min_win_rate=0.4, max_mdd_pct=12.0, min_regime_trade_count=30, require_parameter_stability=True}`. `StrategyProfitabilityGateConfig` 에 `real_mode_overrides: StrategyProfitabilityGateRealOverrides = Field(default_factory=...)` 추가. paper/backtest 동작 보존(top-level 필드 그대로), real 모드에서만 자동 적용. yaml 명시값이 우선해 운영자가 production 등급으로 풀 수 있음(`tests/unit_test/config/test_config_loader.py::test_strategy_profitability_gate_real_mode_overrides_accept_user_yaml_values` 검증). `config/config.yaml.example` 에 commented overlay 블록 포함.
- [x] Monte Carlo, regime performance, parameter stability, benchmark beta/correlation 등 필수 검증 데이터가 없는 경우 real mode 신규 BUY 진입을 warning이 아니라 block으로 처리할지 정책을 확정하고 구현한다.
  - 최소 정책: paper/backtest에서는 warning 허용, real mode live expansion gate에서는 missing evidence를 block reason으로 승격.
  - 완료된 부분(2026-05-25): `StrategyProfitabilityGateConfig`(dataclass + Pydantic) 양쪽에 `require_monte_carlo: bool = False`, `require_regime_balance: bool = False` 추가(`require_parameter_stability` 는 기존 유지). paper 동작 보존을 위해 default False. `_append_monte_carlo_failures()` 가 `monte_carlo=None` 일 때 cfg 임계값이 정의되어 있고 `require_monte_carlo=True` 이면 `monte_carlo_unavailable` 을 `blocking_reasons` 로 승격(이전엔 warning). `_compute_regime_balance_gate()` 가 `balanced_pass=False` 일 때 `require_regime_balance=True` 이면 `regime_balance_incomplete` 을 `blocking_reasons` 로 승격(호출부에서 `blocking_reasons.extend(regime_balance.get("blocking_reasons", []))` 로 전파). canary overlay 에서 `require_monte_carlo=True`/`require_regime_balance=True`/`require_parameter_stability=True` 기본값으로 real 모드 fail-close 정착. market_beta/strategy_correlation 은 warning 성격이 강해 본 PR 에서는 별도 escalation 미적용(필요 시 후속 PR 에서 동일 패턴 확장).
- [x] `StrategyLiveExpansionGateService`와 `StrategyScheduler` 경로에서 stricter gate block reason이 `profitability_gate_blocked` 및 세부 reason으로 관측되는지 단위/통합 테스트로 고정한다.
  - 완료된 부분(2026-05-25): `StrategyLiveExpansionGateService.__init__` 이 `profitability_gate_config.real_mode_overrides` 를 추출해 `_apply_real_overrides(base, overrides)` 헬퍼로 effective config 계산. paper 모드는 `_is_paper_trading()` early-return 으로 overlay 우회. `_apply_real_overrides` 는 dataclass `fields(...)` 와 `hasattr(overrides, name)` 조합으로 RealOverrides BaseModel 에 선언된 필드만 정확히 덮어쓴다(`model_config = {"extra": "allow"}` 라도 Pydantic 미선언 필드는 `hasattr=False`). evaluate 결과의 `blocking_reasons` 가 `decision.details.blocking_reasons` 로 그대로 노출되어, 기존 `scheduler._check_live_expansion_gate()` 와 `scheduler._execute_signal_inner()` 의 BUY 차단 경로(`event=signal_rejected reason=profitability_gate_blocked gate_reason=profitability_gate_fail`)에 신규 reason 자동 반영. 새 단위 테스트 11건: `test_apply_real_overrides_returns_base_when_no_overrides`, `test_apply_real_overrides_only_overlays_declared_fields`, `test_live_expansion_gate_applies_real_overrides_to_escalate_block`(missing monte_carlo → block 승격), `test_live_expansion_gate_paper_mode_bypasses_real_overrides`, `test_live_expansion_gate_real_overrides_tighten_min_trades`, `test_profitability_gate_warns_when_monte_carlo_unavailable_by_default`, `test_profitability_gate_blocks_when_monte_carlo_required_and_missing`, `test_profitability_gate_require_monte_carlo_passes_when_evidence_provided`, `test_profitability_gate_blocks_when_regime_balance_required_and_incomplete`, `test_strategy_profitability_gate_real_mode_overrides_defaults_are_canary_friendly`, `test_strategy_profitability_gate_real_mode_overrides_accept_user_yaml_values`. 기존 scheduler 차단 회귀(`test_run_strategy_allows_exits_but_skips_scan_when_live_gate_blocks_entries`, `test_execute_buy_signal_blocks_order_when_live_gate_blocks_entries`, `test_execute_sell_signal_ignores_live_gate`)는 reason 코드 추상화 덕에 변경 없이 통과. 단위 5531(이전 5520 → +11), 통합 235 통과.
- [x] gate 기준 변경이 기존 journal sample 부족 상태에서 모든 전략을 의도치 않게 영구 차단하지 않도록 수동 override/runbook 절차를 문서화한다.
  - 완료된 부분(2026-05-25): `docs/canary_procedure.md` 에 "Profitability Gate 수동 Override Runbook (P1 1-1)" 섹션 추가. canary overlay 기본값 표, paper/real 동작 원칙, 차단 reason 별 권장 완화(`insufficient_trades`/`monte_carlo_unavailable`/`parameter_stability_unavailable`/`regime_balance_incomplete`), 변경 PR 작성 규칙(`config/config.yaml` 직접 수정 금지·review 필수·사유/적용 기간/원복 일자 명시·자동 issue 등록), yaml 예시, 배포·검증·원복 절차 명문화. KillSwitch 와의 책임 경계 명시(overlay 완화는 표본 부족 차단 해제용이지 손실 차단 우회 용도가 아님).

주요 파일:

- `config/config_loader.py`
- `services/strategy_profitability_gate_service.py`
- `services/strategy_live_expansion_gate_service.py`
- `scheduler/strategy_scheduler.py`
- `docs/canary_procedure.md`
- `tests/unit_test/services/`
- `tests/integration_test/`

### 1-2. Multiple testing / overfitting 방어 강화

- [ ] 현재 `MultipleTestingBiasService`의 lightweight report-only 성격을 실전 승격 기준으로 충분한지 재평가한다.
- [ ] 전략/필터/파라미터를 여러 개 실험한 뒤 상위 성과만 고르는 착시를 줄이기 위해 Deflated Sharpe 또는 PBO 유사 지표를 추가한다.
- [ ] walk-forward 결과를 전략 승격 gate에 연결한다. 단순 리포트가 아니라 기간별 안정성 미달 시 real mode 신규 진입을 막는 정책을 검토한다.
- [ ] purged/embargo split 또는 같은 종목·인접 기간 누수 방지용 validation contract를 정의한다.
- [ ] ablation 결과를 승격 기준에 반영한다. 필터 제거/완화 시 성과가 크게 무너지지 않거나, 특정 필터가 실제로 기여하는지 확인한다.

주요 파일:

- `services/multiple_testing_bias_service.py`
- `services/strategy_profitability_gate_service.py`
- `services/strategy_ablation_service.py`
- `scripts/run_backtest.py`
- `tests/unit_test/services/`

### 1-4. 시장 국면별 전략 성과 분해

- [x] 시장 상태를 상승/하락/횡보로 분류하는 기준을 정의한다.
  - 신규 `MarketRegimeService` (KOSPI/KOSDAQ ETF 단기 MA 추세 기반 4-state → bull/bear/sideways 3-state 매핑).
  - 기존 `OneilUniverseService._check_etf_ma_rising` 로직을 추출, `is_market_timing_ok` 시그니처 보존.
- [x] 코스피/코스닥 지수 기반 전략 ON/OFF 조건을 추가한다.
  - 기존 5개 전략(FirstPullback, HighTightFlag, OneilPocketPivot, OneilSqueezeBreakout, RSI2Pullback)이 이미 `is_market_timing_ok` 게이트 사용 — `MarketRegimeService` 위임으로 자동 반영.
  - 누락된 LarryWilliamsVBO, LarryWilliamsCB에 동일 hard gate 추가 (`_position_state` 변경 전 위치, `reason=market_timing_off` 로깅).
- [x] 변동성 기반 진입 제한을 검토한다.
  - 1차 PR에서는 hard gate 미도입 — 리포트 컬럼으로 먼저 검증 후 도입 결정. 후속 PR에서 `RiskGateConfig.max_volatility_pct` 추가 검토.
  - 완료된 부분: 20일 종가 수익률 표준편차(annualized) 유틸 `utils/volatility_utils.py` 추가, `STANDARD_TRADE_JOURNAL_FIELDS`에 `volatility_20d_annualized` 필드 + `SCHEMA_VERSION = 3` 승격, `normalize_virtual_trade`/`normalize_backtest_trade`/`normalize_backtest_decision`/`normalize_backtest_execution` 4개 모두 Optional 인자 + record/metadata fallback 지원. `compute_performance_by_regime`가 버킷별 `volatility_sample_count`, `avg_volatility_20d_annualized`, `median_volatility_20d_annualized`를 산출해 `GET /api/strategies/performance-by-regime` JSON에 자동 노출.
  - 완료된 부분: `TradeSignal.volatility_20d_annualized` Optional 필드 추가. `BacktestPeriodRunner._rejected_signal_record` / `_execution_record` 양쪽이 signal의 변동성을 journal record로 전파(BUY 체결, SELL 정산, risk gate 차단, position sizing skip 4개 경로 단위 테스트 검증).
  - 완료된 부분: 활성 7개 전략(`FirstPullbackStrategy`, `HighTightFlagStrategy`, `LarryWilliamsChannelBreakoutStrategy`, `LarryWilliamsVBOStrategy`, `OneilPocketPivotStrategy`, `OneilSqueezeBreakoutStrategy`, `Rsi2PullbackStrategy`)이 `utils.volatility_utils.annualized_return_std`로 OHLCV → 변동성을 계산해 `TradeSignal.volatility_20d_annualized`에 채운다. 비활성 3개 전략(`ProgramBuyFollow`, `TraditionalVolumeBreakout`, `VolumeBreakoutLive`)에도 동일 hook 적용.
  - 완료된 부분: `VirtualTradeRepository`에 `volatility_20d_annualized REAL` 컬럼 + ALTER TABLE migration, `log_buy()` 인자 추가, INSERT/SELECT 매핑 완료 (`test_virtual_trade_repository_volatility.py` 검증).
  - 완료된 부분: `strategy_log_report_service._build_volatility_section()` 추가, HTML 일일 리포트 body에 변동성 섹션 통합 (`test_strategy_log_report_volatility.py` 검증).
  - 후속 검토(P1 별도 항목): 리포트 누적 후 hard gate(`RiskGateConfig.max_volatility_pct`) 도입 여부 결정.
- [x] 장 초반/후반 타임 필터를 전략 실행 전에 적용한다.
  - `StrategySchedulerConfig.skip_minutes_after_open` / `skip_minutes_before_close` 필드 추가.
  - `_is_scan_time_window_blocked()` 가 `cfg.strategy.scan()` 만 skip (force_exit/check_exits 영향 없음).
- [x] O'Neil/Minervini 계열 전략 성과를 KOSPI 상승장, KOSDAQ 상승장, 지수 횡보장, 지수 하락장, 거래대금 증가 장세로 분리해 리포트한다.
  - `common/trade_journal_schema.py` 에 `market_regime` 필드 + `SCHEMA_VERSION = 2`. 정규화 함수에 `market_regime` 파라미터(service 호출 금지, record/metadata fallback).
  - `services/regime_performance_service.py` 신규 (순수 함수): KOSPI Bull / KOSDAQ Bull / SIDEWAYS / BEAR / TRADING_VALUE_SURGE 5개 버킷 + MDD(signal_time 정렬 누적 net_pnl).
  - `GET /api/strategies/performance-by-regime?strategy=&from_date=&to_date=` 엔드포인트 추가.
  - 완료된 부분(2026-05-25, TRADING_VALUE_SURGE 활성화): `_classify_bucket()` → `_classify_buckets()` (plural, list 반환)로 확장해 **cross-cutting overlay** 모델 구현. 같은 record 가 index 버킷(KOSPI_BULL/KOSDAQ_BULL/SIDEWAYS/BEAR)과 TRADING_VALUE_SURGE 양쪽에 동시 집계 가능 — "KOSPI 상승장 중 거래대금 급증 구간 성과 vs 일반 상승장 성과" 비교를 위해. `market_regime` metadata에 `trading_value_surge: bool` 필드 추가 (Optional, 키 누락 시 False로 backward-compat). Producer-side helper `is_trading_value_surge(current, baseline, threshold_pct=30.0)` 추가 — baseline 0/None 시 보수적으로 False, threshold 정확 도달 시 True (`>=` 비교). 모듈 docstring에 cross-cutting 모델·input contract 명시. 단위 테스트 10건 추가: overlay 동작 4건(both buckets / flag False / index 없이 surge 단독 / backward-compat 키 누락), `is_trading_value_surge` helper 7건(above/below/exact threshold / custom threshold / None inputs / zero baseline / invalid input). 단위 5695(이전 5685 → +10), 통합 235 통과.
  - 후속(blocked → 데이터 소스 wiring): producer 측에서 KOSPI/KOSDAQ 시장 거래대금 일일 aggregate를 수집하고 `MarketRegimeService.get_regime_snapshot()` 등에서 `is_trading_value_surge(current, ma_5d)` 결과를 `RegimeSnapshot.trading_value_surge`로 채워 journal `market_regime` metadata에 전달하면 자동 활성화. 데이터 소스 후보: pykrx `get_market_trading_value_by_date()` 일별 시장 거래대금 또는 KIS API 시장 통계.

주요 파일:

- `services/market_regime_service.py` (신규)
- `services/regime_performance_service.py` (신규)
- `services/oneil_universe_service.py` (`MarketRegimeService` 위임)
- `strategies/larry_williams_vbo_strategy.py`
- `strategies/larry_williams_channel_breakout_strategy.py`
- `scheduler/strategy_scheduler.py`
- `common/trade_journal_schema.py`
- `view/web/routes/strategy_report.py`

### 1-5. 백테스트 검증 확장

- [x] `--date YYYYMMDD` / 기간 기반 과거 시점 재현용 market clock과 replay 데이터 주입 구조를 만든다.
- [x] 실시간 API 응답 대신 과거 OHLCV/체결강도/프로그램매매 데이터를 공급하는 `BacktestStockQueryService` 또는 data replay adapter를 추가한다.
- [x] `run_strategy_debug`는 미매수 사유 진단, `run_backtest`는 기간 수익률/포트폴리오 검증으로 역할을 분리한다.
- [x] walk-forward 검증을 추가한다. 기간을 train/tune/test로 나누고, 파라미터 튜닝 구간과 검증 구간을 분리한다.
- [x] 몬테카를로 시뮬레이션을 추가한다. trade 결과 순서를 섞어 최악 MDD, 연속 손실, ruin probability를 계산한다.
- [x] 활성 전략별 fixture parity를 period runner와 strategy debug runner 양쪽에서 검증한다.
- [blocked] 실제 replay fixture를 통과 케이스까지 확장한다. (장중 프로그램매매 WebSocket 샘플 미확보)
  - 차단 사유: 장중 후보 종목의 프로그램매매 WebSocket 샘플을 실시간으로 캡처해야 하며, 장 마감 후에는 재생성 불가.
  - 차단 해제 조건: 장중에 `scripts.capture_backtest_microstructure`로 후보 종목 WebSocket 샘플 확보 → replay fixture overlay로 결합.
  - 선택 작업(차단 해제 후): 필요 시 `20260506`, `20260511`, `20260504`, `20260416` 표본 fixture를 추가 생성한다.
- [~] 실전 체결 품질과 괴리가 큰 백테스트 가정을 별도 고도화 과제로 분리한다.
  - 검토 결과: 부분 타당. `BacktestExecutionSimulator`는 지정가/시장가, current/next bar, 슬리피지, 거래량 기반 부분체결, 비용을 이미 다루지만, bid/ask spread, 호가잔량, market impact, VI/상하한가/거래정지, 미체결 후 취소 정책은 아직 명시 contract가 아니다.
  - 개선 방향: 실전 성과 판단용 runner에서는 next-bar 기본값, 호가/spread/부분체결/취소 fixture를 우선 추가한다.
  - Phase 1 완료(2026-05-24): `BacktestExecutionSimulator` docstring에 "UNFILLED/PARTIAL 잔여 = day order 자동 취소(이월 없음)" contract 명시. 호출자가 다음 봉 재시도하려면 별도 주문을 새로 만들어야 한다. 코드 동작 변경 없음(기존 `BacktestPeriodRunner._execute_signal` 흐름이 이미 단봉 단위 시뮬레이션).
  - Phase 2 완료(2026-05-24): `BacktestBar.trading_value: float | None = None` Optional 필드 추가로 거래대금 입력 contract 제공(누락 시 `volume * close` fallback). 호출자/bar_provider 무변동.
  - Phase 3 완료(2026-05-24): `BacktestBar`에 `is_halted`, `vi_triggered`, `upper_limit_price`, `lower_limit_price` Optional 4개 필드 추가로 한국 주식 미시구조 차단 contract 제공. 호출자/bar_provider 무변동.
  - Phase 4 완료(2026-05-24): `OrderType.BEST_LIMIT` enum 값 추가로 시장가/지정가/최유리지정가 세 주문 타입 contract 명확화. 호출자/bar_provider 무변동.
  - Phase 5 완료(2026-05-24): `BacktestBar.bid`/`ask` Optional 필드와 `BacktestExecutionPolicy.spread_pct` 추가로 bid/ask spread 모델 contract 제공. 호출자/bar_provider 무변동.
- [~] 체결 모델을 한국 주식 실전 제약 기준으로 더 보수화한다.
  - 후보: 호가단위, 부분체결, 미체결 후 취소, 거래대금 bucket별 슬리피지, 9:00~9:10 장초반 체결 악화, VI/상하한가/거래정지, 시장가/지정가/최유리 주문 차이, 매도 체결 실패.
  - Phase 1 완료(2026-05-24): `BacktestExecutionPolicy.opening_market_slippage_bonus_pct: float = 0.0` 추가. 시장가 주문 + `market_price_field == "open"` 조합일 때 base `market_slippage_pct` 위에 가산해 한국 주식 시가(동시호가 직후) 변동을 stylized fact로 반영. default 0 이라 기존 백테스트 결과 무영향. 지정가 주문과 `market_price_field != "open"` 케이스는 bonus 비적용. 단위 4건 추가(`test_opening_market_slippage_bonus_*`) 검증 단위 5303(이전 5299 → +4), 통합 235.
  - Phase 2 완료(2026-05-24): `BacktestExecutionPolicy.liquidity_slippage_buckets: tuple[tuple[float, float], ...] = ()` 추가. `((threshold, bonus_pct), ...)` 형식으로 거래대금이 threshold 미만인 모든 bucket bonus 중 최댓값을 시장가 슬리피지에 가산. `BacktestBar.trading_value` 누락 시 `volume * close` fallback, 둘 다 없으면 bonus 0. base/opening bonus와 합산. 지정가 주문은 비적용. default `()` 이라 기존 백테스트 결과 무영향. 단위 6건 추가(`test_liquidity_slippage_*`) 검증 단위 5309(이전 5303 → +6), 통합 235.
  - Phase 3 완료(2026-05-24): `BacktestBar`에 `is_halted: bool = False`, `vi_triggered: bool = False`, `upper_limit_price: float | None = None`, `lower_limit_price: float | None = None` 추가. `BacktestExecutionSimulator._market_microstructure_block()` helper가 `invalid_qty` 직후/가격 평가 이전에 차단을 결정한다. 정책: 거래정지/VI 발동 = 매수·매도 모두 UNFILLED, `close >= upper_limit_price` = BUY만 차단(상한가 잠금 시 매도 가능), `close <= lower_limit_price` = SELL만 차단(하한가 잠금 시 매수 가능). reason 코드 `"halted"`/`"vi_triggered"`/`"upper_limit_blocked"`/`"lower_limit_blocked"`. default 값으로 기존 백테스트 결과 무영향. 단위 7건 추가 검증 단위 5316(이전 5309 → +7), 통합 235.
  - Phase 4 완료(2026-05-24): `OrderType.BEST_LIMIT = "BEST_LIMIT"` enum 값과 `BacktestExecutionPolicy.best_limit_slippage_pct: float = 0.0` 추가. `_base_fill_price()`는 BEST_LIMIT을 MARKET과 동일하게 `market_price_field` 기반 즉시 체결로 모델링한다(매수 최유리 = 매도 1호가 즉시 체결). `_apply_market_slippage()` 분기로 BEST_LIMIT은 `best_limit_slippage_pct`만 적용하고 MARKET 한정 `opening_market_slippage_bonus_pct`/`liquidity_slippage_buckets`는 무시한다(1호가 한계 체결이라 시장가보다 슬리피지 작음). microstructure 차단(VI/상하한가/거래정지)은 동일 적용. default 0이라 기존 백테스트 결과 무영향. 단위 5건 추가(`test_best_limit_*`) 검증 단위 5321(이전 5316 → +5), 통합 235.
  - Phase 5 완료(2026-05-24): `BacktestBar.bid: float | None = None`/`ask: float | None = None`과 `BacktestExecutionPolicy.spread_pct: float = 0.0` 추가. `_apply_bid_ask_spread()` helper가 슬리피지 적용 후 fill_price에 half-spread 가/감산(BUY → +, SELL → -). `_effective_spread_pct()`는 bar.bid/ask가 둘 다 있으면 실제 `(ask - bid) / mid * 100`을 우선 사용하고, 누락 시 `policy.spread_pct` fallback. LIMIT 주문은 spread 무영향(가격 도달 검사로 체결). MARKET/BEST_LIMIT은 동일 적용. default 0이라 기존 백테스트 결과 무영향. 단위 6건 추가(`test_*_spread_*`/`test_bar_bid_ask_*`) 검증 단위 5327(이전 5321 → +6), 통합 235.
- [ ] 한국장 실전 microstructure fixture로 체결 모델을 보정한다.
  - 2026-05-24 최신 main 리뷰 반영: Phase 1~5로 계약과 보수적 옵션은 생겼지만, 실제 호가잔량 기반 부분체결, VI, 거래정지, 상하한가, 장초반 market impact를 실전 캡처로 검증한 것은 아니다.
  - 남은 것: bid/ask book, 잔량, 체결강도, 프로그램매매 overlay를 fixture로 수집하고, 시장가/최유리/지정가별 fill quality가 live journal과 얼마나 벌어지는지 리포트한다.

주요 파일:

- `strategies/debug/*`
- `scripts/run_strategy_debug.py`
- `strategies/backtest_data_provider.py`
- `strategies/strategy_executor.py`
- `services/backtest_replay_context.py`
- `tests/unit_test/strategies/test_oneil_pp_bgu_fixture_runner_parity.py`
- `tests/unit_test/strategies/debug/test_strategy_debug_runner.py`
- `tests/fixtures/backtest/oneil_pp_bgu_entry_cases.json`
- `tests/unit_test/strategies/test_oneil_pocket_pivot_fixture_cases.py`
- `tests/fixtures/backtest/rsi2_pullback_entry_cases.json`
- `tests/unit_test/strategies/test_rsi2_pullback_fixture_runner_parity.py`
- `tests/fixtures/backtest/larry_williams_channel_breakout_entry_cases.json`
- `tests/unit_test/strategies/test_larry_williams_channel_breakout_fixture_runner_parity.py`
- `tests/fixtures/backtest/oneil_squeeze_breakout_entry_cases.json`
- `tests/unit_test/strategies/test_oneil_squeeze_breakout_fixture_runner_parity.py`
- `tests/fixtures/backtest/larry_williams_vbo_entry_cases.json`
- `tests/unit_test/strategies/test_larry_williams_vbo_fixture_runner_parity.py`
- `tests/fixtures/backtest/high_tight_flag_entry_cases.json`
- `tests/unit_test/strategies/test_high_tight_flag_fixture_runner_parity.py`

---

## P2. 시스템 성능

API 호출 최적화와 polling → event-driven 전환을 통해 운영 안정성과 처리량을 개선합니다.

### 2-2. API 호출 최적화

- [x] `StrategyExecutor` Liquidity Filter의 `asyncio.gather()`에 semaphore 기반 동시성 제한을 추가한다.
  - 근거: `strategies/strategy_executor.py:88` `Semaphore(self._max_liquidity_concurrency)` 적용, `:106` gather, `:141` `async with sem:` 으로 REST 호출 보호.
  - snapshot-first chain도 동일 파일 `:127-139`에 구현됨 (PriceStreamService 신선 snapshot 우선 사용 → 없거나 stale 시 REST fallback → REST 응답을 `cache_price_snapshot()`으로 backfill).
  - 2026-05-19 재검토: 리뷰에서 “liquidity filter semaphore 불일치”를 지적했으나 현재 main 기준 REST fallback 구간은 `_lookup_liquidity()` 내부 semaphore로 제한되고, `test_liquidity_filter_respects_concurrency_limit`가 동시 실행 상한을 검증한다. 이 항목은 완료 유지.
- [x] 종목별 current price REST 호출을 최소화하고 WebSocket/stream snapshot을 우선 사용한다.
  - 완료: `StockQueryService.get_current_price()`에 snapshot-first 로직 추가. `price_stream_service` 주입 시 `get_cached_price()` 우선 참조 → stale/없음 시 REST fallback. `web_app_initializer.py`에서 `price_stream_service` 주입.
  - 구독 중이나 tick 미수신(snapshot=None) 케이스: `no_tick_fallback`으로 분류해 REST fallback. `_price_lookup_stats`에 `snapshot_hit/no_tick_fallback/stale_fallback/rest_fallback` 카운터 노출.
  - REST 성공 시 `cache_price_snapshot()`으로 backfill → 다음 호출 snapshot hit 유도.
  - 전략들은 호출 코드 변경 없이 자동으로 WebSocket 캐시 활용.
- [x] REST API는 snapshot 누락, 검증, 보정용으로 제한한다.
  - 완료: snapshot 없음/stale → REST fallback. `force_fresh=True` 시 snapshot 무시(주문/리스크/수동 조회 경로에서 사용).
  - `_price_lookup_stats` dict를 `price_lookup_stats` 이벤트 로그로 노출 가능.
- [x] 전략별 중복 조회를 제거하고 동일 종목 데이터는 공통 snapshot에서 읽도록 정리한다.
  - 완료: 전략들이 `StockQueryService.get_current_price()` 한 통로를 거치므로 snapshot-first 기본화로 자연 해결. REST backfill로 첫 호출 이후 동일 종목 재조회 시 캐시 hit.
- [x] 전략/서비스 전역 API budget limiter를 도입한다.
  - 최신 main 재확인(2026-05-25): 전역 limiter가 "없음"은 아니다. `core.retry_queue.api_budget_limiter.ApiBudgetLimiter`가 존재하고, `BrokerAPIWrapper`가 shared limiter를 생성해 retry queue에 주입한다.
  - 적용 범위: current price/OHLCV/체결강도/잔고/체결·미체결 대사 조회는 `ClientWithRetryQueue._budget_category_for_method()`로 카테고리화되어 최초 호출과 retry 호출 모두 limiter를 거친다.
  - 최신 반영(2026-05-25): 주문/WebSocket 메서드는 멱등성·상태 기반 동작 때문에 retry queue 제외는 유지하되, global budget limiter는 적용하도록 조정했다. 따라서 "전역 API limiter 미도입"이 아니라 "emergency 청산 우선순위 정책은 별도 설계 필요"로 본다.
  - 완료된 부분: `core.retry_queue.api_budget_limiter.ApiBudgetLimiter` 추가. `BrokerAPIWrapper`가 shared limiter를 생성해 `ClientWithRetryQueue`에 주입하고, retry queue의 최초 호출/재시도 호출이 모두 limiter를 거치도록 연결했다.
  - 1차 정책: 조회성 API는 `quotation` 기본 동시성 8, 계좌 조회 API는 `account` 기본 동시성 2로 분리. 주문/WebSocket 메서드는 기존 제외 목록을 유지해 budget limiter와 retry queue를 우회한다.
  - 검증: retry queue 단위 88개, retry queue 통합 22개, broker wrapper 단위 33개, 전체 단위 4798개, 전체 통합 233개 통과.
- [~] API budget limiter 운영 정책을 실전 한도 기준으로 보정한다.
  - 최신 main 재확인(2026-05-25): REST 조회/계좌 limiter는 endpoint 성격별로 세분화되었으나, 실제 KIS 공지 한도 재확인과 주문/WebSocket budget 정책은 아직 운영 결정이 필요하다.
  - 목표: `quotation_price`/`quotation_ohlcv`/`quotation_conclusion`/`account_balance`/`account_reconciliation` 기본값을 실전 계정 한도와 맞추고, 주문/청산/대사 경로가 조회성 scan 폭주에 밀리지 않는지 부하 테스트로 확인한다.
  - 완료된 부분(2026-05-25): `ApiBudgetLimiter` 기본 정책을 endpoint 성격별 category로 세분화했다. 동시성 budget은 `quotation_price=4`, `quotation_ohlcv=2`, `quotation_conclusion=3`, `quotation=4`, `account_balance=1`, `account_reconciliation=1`, `account=1`, `default=4`로 보수화하고, category별 초당 rate 예약 throttle(`rate_limit_per_sec`)을 추가했다. `ClientWithRetryQueue._budget_category_for_method()`가 current price / OHLCV / 체결강도 / 잔고 / 체결·미체결 대사 조회를 각각 분류한다. 주문/WebSocket은 기존처럼 retry queue와 budget limiter를 우회해 scan 조회 budget에 막히지 않는다. 회귀 테스트: endpoint 분류, rate snapshot, busy-loop 없는 rate 예약, quotation scan budget과 account reconciliation budget 독립성을 추가 검증. 공식 포털에는 2026-04-20 기준 REST/WebSocket 유량 공지가 노출되지만 상세 수치는 운영 전 재확인이 필요하므로, 기본값은 한도 단정이 아니라 실전 보호용 보수 운영값으로 둔다.
  - 완료된 부분(2026-05-25): order submit retry는 `config.order_execution.order_max_retries` / `order_retry_delay_sec`로 config화했다. 기본값은 기존 코드와 같은 `3` / `3`이라 기존 운영 동작은 유지된다.
  - 완료된 부분(2026-05-25): 부하 invariant 테스트를 추가했다. `quotation_price` burst가 같은 category 대기열을 만들더라도 `account_reconciliation`은 별도 budget으로 즉시 시작됨을 회귀로 고정했다.
  - 완료된 부분(2026-05-25): 운영 관측성 추가. `/api/system/operations/status` 응답에 `api_budget` snapshot(`limit`, `rate_limit_per_sec`, `active`, `acquired_total`, `max_observed_active`)을 노출하고, `PreDeployCheckService`에 `api_budget_limiter` 점검을 추가했다(미주입 시 SKIPPED, 필수 category 누락 시 WARN).
  - 완료된 부분(2026-05-25): 주문/WebSocket budget 정책 1차 반영. `place_stock_order`/`cancel_stock_order`는 retry queue를 우회하되 `order_submit`/`order_cancel` budget을 적용하고, WebSocket connect/subscribe/unsubscribe 계열은 `websocket_connect`/`websocket_subscribe` budget을 적용한다. 멱등성 없는 주문 재시도는 계속 제외한다.
  - 완료된 부분(2026-05-25, emergency 청산 우선순위): `ApiBudgetLimiter` 에 카테고리별 emergency overlay lane(`order_submit`/`order_cancel` 기본 적용) 과 `core/api_priority.py` ContextVar 기반 `emergency_scope()` 를 도입했다. `OrderExecutionService.sell_all_stocks(EMERGENCY)` 가 일반 주문/조회 lane 점유와 무관하게 별도 슬롯으로 진입한다. 자세한 변경 내역은 P0 0-6 완료 노트 참조.
  - 남은 작업: 실제 KIS 계정별 REST/WebSocket 유량 한도 재확인 후 기본값 보정.
  - 남은 작업: endpoint별 coverage matrix를 작성한다. 현재가, OHLCV, 체결강도, 계좌/잔고, 주문 제출, 주문 취소, 체결/미체결 대사, WebSocket connect/subscribe/unsubscribe, emergency sell 경로가 모두 어느 category/lane을 타는지 문서와 테스트로 고정한다.
  - 남은 작업: 장초반 후보군 scan + 현재가 fallback + 체결강도 조회 + 주문 + reconcile + emergency sell이 동시에 발생하는 synthetic 부하 테스트를 추가해 order/reconcile/emergency가 quotation burst에 밀리지 않는지 검증한다.
  - 남은 작업: 429 또는 "초당 거래건수 초과"를 만난 뒤 재시도하는 대응형 retry에만 의존하지 않도록, real-mode 운영값에서 사전 throttle이 먼저 작동하는지 관측 metric을 추가한다.
- [x] 활성 전략의 exit check도 bounded gather 또는 순차/우선순위 정책으로 통일한다.
  - 검토 결과: 타당. `FirstPullbackStrategy`, `HighTightFlagStrategy`, `LarryWilliamsChannelBreakoutStrategy` 등 일부 전략은 holdings 전체에 대해 `asyncio.gather()`를 수행한다. VBO/레거시 일부는 순차 처리다.
  - 완료된 부분: `utils/async_concurrency.py::bounded_gather()` 헬퍼 신규 + 단위 테스트 7개. 활성 6개 전략(`FirstPullback`, `HighTightFlag`, `LarryWilliamsChannelBreakout`, `OneilPocketPivot`, `OneilSqueezeBreakout`, `Rsi2Pullback`)의 holdings exit gather를 `bounded_gather(..., limit=_EXIT_CONCURRENCY=15, return_exceptions=True)`로 교체했다. entry chunk_size(10)보다 높여 청산 경로에 우선순위를 부여한다.
  - 미적용: VBO는 이미 sequential `for hold in holdings`로 더 보수적이므로 외과수술적 변경 원칙에 따라 유지.
- [x] VBO range cache 갱신을 bounded concurrency로 바꾸고 precompute 경로를 검토한다.
  - 검토 결과: 타당. `LarryWilliamsVBOStrategy._refresh_range_cache()`가 후보 코드를 순차 순회하며 `get_recent_daily_ohlcv()`를 호출한다.
  - 개선 방향: semaphore 기반 bounded gather 또는 장 시작 전/Watchlist 생성 시 range precompute.
  - 완료된 부분: PR #435에서 `_refresh_range_cache()`를 `bounded_gather(..., limit=_RANGE_CACHE_CONCURRENCY=10, return_exceptions=True)` 패턴으로 교체했다 (`strategies/larry_williams_vbo_strategy.py:438-457`).
  - 후속 검토(미진행): 장 시작 전/Watchlist 생성 시 range precompute. universe 빌드 경로에 hook을 끼우는 방식이 필요하며, scan 시점의 캐시 hit 보장 정책이 별도 결정 사항이라 본 항목에서는 분리.
- [x] VBO fallback 후보의 unknown liquidity를 통과시키지 않는다.
  - 검토 결과: 타당. universe 미주입 fallback에서 `avg_5d_tv=0`을 넣고, validity filter는 `avg_5d_tv > 0 and avg_5d_tv < min`일 때만 탈락시킨다.
  - 완료된 부분: fail-closed reject로 전환. `_passes_validity_filter`에서 `avg_5d_tv <= 0`이면 `avg_trading_value_unknown` reason으로 차단. fallback 경로(`_load_pool_b` API 응답 기반)는 거래대금 검증 없이는 매수 신호를 만들 수 없다. 단위/통합 테스트로 회귀 고정.
- [x] scan cycle 단위 데이터 공유와 성능 계측을 강화한다.
  - 후보: 동일 종목 current price/OHLCV/conclusion/program trading memoization, strategy scan latency, candidate count, API calls per scan, cache hit ratio, REST fallback ratio, rejected reason distribution, signal-to-order latency, order-to-fill latency.
  - 목표: 성능 개선을 감이 아니라 병목 지표 기반으로 진행한다.
  - 1차 완료(2026-05-22): 4종 메트릭(latency_ms / candidate_count / signal_count / rejected_reasons) structured log event 발행. `core/scan_rejection_counter.py::EntryRejectionCounter` (logging.Handler 상속) 신규 — `scheduler/strategy_scheduler.py:_run_strategy()`가 scan 직전 strategy logger 에 attach 하고, 직후 detach 해 한 cycle 동안의 entry_rejected reason 분포를 누적. 예외 시에도 detach 보장. `scan_metrics` event 가 4종 필드를 포함해 발행된다.
  - 1차 한계: `LOG_LEVEL=INFO`(prod) 환경에서 `_logger.debug({...})` 로 emit 되는 entry_rejected (예: FirstPullback) 는 logger 단계에서 필터링돼 카운트되지 않는다. 전 전략을 INFO 로 표준화하는 보강은 후속 PR.
  - 한계 해소(2026-05-22): `strategies/first_pullback_strategy.py` 4건 (L170 `no_surge_history`, L176 `ma_not_uptrending`, L188 `pullback_out_of_range`, L201 `volume_not_dry`) 의 `_logger.debug({"event": "entry_rejected", ...})` 호출을 `info` 로 표준화. 다른 전략은 이미 `info` (직접 호출 또는 helper) 사용 중이라 변동 없음. 회귀 방지로 `tests/unit_test/strategies/test_entry_rejected_logging_level_audit.py` 신규 — ast 기반 `strategies/*.py` 전역 audit 으로 `_logger.debug({"event": "entry_rejected", ...})` 0건 유지.
  - 2차 완료(2026-05-22): API/캐시 cycle delta 추가. `services/stock_query_service.py`에 `price_lookup_stats_snapshot() -> Dict[str, int]` public 메서드 추가, `scheduler/strategy_scheduler.py`가 scan 직전/직후 snapshot 캡처 → cycle 단위 변동 키만 추려 `scan_metrics.lookup_stats_delta` 필드로 노출. 10종 카운터(snapshot_hit / no_tick_fallback / stale_fallback / rest_fallback / force_fresh_bypass / full_output_required / stream_unavailable_fallback / conclusion_hit / conclusion_stale_fallback / conclusion_missing_fallback) 변동이 자동 포함된다. sqs 미주입 또는 메서드 부재 시 빈 dict.
  - 후속(2026-05-22, signal-to-order latency): `common/types.py::TradeSignal`에 `created_at: float | None = None` 필드 추가(후방호환). `scheduler/strategy_scheduler.py`가 `scan()`/`check_exits()` 직후 미stamp 신호에 현재 시각을 부여(이미 값이 있으면 보존), `_log_signal_to_order_latency(signal, tid)` helper 가 dry_run/실모드 양쪽에서 order placement 직전 `{"event": "signal_to_order_latency", "strategy_name", "code", "action", "latency_ms", "trace_id"}` log event 발행. `created_at` 미설정 신호는 skip(방어). 새 단위 9개(trade_signal_created_at 4 + scheduler signal_to_order_latency 5).
  - 후속(2026-05-24, order-to-fill latency): `services/fill_reconciliation_service.py::apply_execution_report()` 가 FILLED 전이 시 `{"event": "order_to_fill_latency", "order_key", "code", "side", "latency_ms", "trace_id"}` log event 발행. `was_not_filled_before` 가드로 동일 주문에 대한 중복 webhook 의 재emit 차단, `context.created_at`(OrderContext register 시각 = 주문 placement 직후) 기준 latency 계산. PARTIAL_FILLED 전이에서는 미발행. signal_to_order_latency 와 한 쌍으로 묶여 signal → 주문 placement → 체결 확정 구간을 분리 측정. 단위 테스트 3건 추가 (`test_fill_reconciliation_service.py`). 검증 단위 5299 (이전 5296 → +3), 통합 235.
  - Closed(2026-05-24): 본 항목은 [x] 종결. 추가 후속 두 건은 즉시 도입 시 복잡도 대비 benefit 불확실로 조건부 재개 트리거로 보관한다.
    - **current_price/OHLCV memoization** — 재개 트리거: scan_metrics `lookup_stats_delta` 또는 OHLCV REST 호출 로그에서 동일 cycle 내 동일 (code, limit, end_date) 중복 호출 비중이 운영 관측으로 ≥ 20% 로 측정될 때. 현재 아키텍처는 `STAGGER_INTERVAL_SEC` 로 전략 staggered 실행 + `OneilUniverseService._watchlist_date` 일별 캐시 + `StockQueryService` snapshot-first 체인이 이미 중복 차단 → 추가 memoization 효과가 미미할 가능성. 도입 시점에 cycle scope key 는 `(code, limit, end_date or "")` 권장.
    - **in-memory aggregator + web API 노출** — 재개 트리거: 운영 대시보드 UI(웹 또는 외부) 도입 결정. 현재는 structured log event(`scan_metrics`, `signal_to_order_latency`, `order_to_fill_latency`, `execution_quality`) 가 모두 emit 되므로 로그 분석 도구로 동일한 정보 추출 가능. endpoint 신설은 소비자 부재 상태에서 dead code 가 됨.
  - 검증(2차): 새 단위 5개(stock_query_service_stats_snapshot 3 + scheduler scan_metrics 2). 전체 단위 5002 + 통합 233 통과.
- [x] 전략별 universe 적합성을 비교한다.
  - 검토 결과: 타당. 활성 전략 다수가 `OneilUniverseService` watchlist를 공유하는 것은 운영상 단순하지만, RSI2 같은 mean-reversion 성격이나 VBO 단기 변동성 전략에 O'Neil universe가 항상 맞는지는 별도 검증이 필요하다.
  - 산출물: Oneil universe vs generic liquidity universe vs strategy-specific prefilter 성과 비교, universe exclusion report.
  - Phase 0 완료(2026-05-23): `services/generic_liquidity_universe_service.py`(`GenericLiquidityUniverseService` — 5일 평균 거래대금 + 시가총액 임계만 적용하는 OneilUniverseService 대체 contract) 추가. `scripts/run_backtest.py::_build_ablation_overrides()`에 `universe_overrides["universe_type"] == "generic_liquidity"` 분기 추가 (`force_market_timing_ok`와 조합 가능). `strategies/larry_williams_vbo_ablation.py`에 `universe_generic_liquidity` variant 1개 추가. 단위 테스트 14건 (서비스 9 + ablation overrides 5) 추가. 운영 사용: `python scripts/run_backtest.py --strategy larry_williams_vbo --ablation larry_williams_vbo --ablation-variants universe_generic_liquidity --dates ...`.
  - Phase 1 완료(2026-05-24): 나머지 6개 활성 전략(`oneil_pocket_pivot`, `oneil_squeeze_breakout`, `high_tight_flag`, `first_pullback`, `rsi2_pullback`, `larry_williams_channel_breakout`) 의 ablation preset 에 `universe_generic_liquidity` variant 일괄 추가. `tests/unit_test/scripts/test_run_backtest_ablation_smoke.py` 에 7개 활성 전략 전체를 parametrize 로 잠그는 회귀 테스트 추가. PP preset 의 기존 enum 테스트도 동기화.
  - Phase 2 완료(2026-05-24): `services/strategy_ablation_service.py::compute_universe_exclusion_summary()` (pure function) 추가. baseline/variant 의 SOLD 거래 종목을 set-difference 로 분할(`variant_only_codes`, `baseline_only_codes`, `shared_codes`)하고 variant_only 종목에 대해 거래 수/총 net PnL/승패/per-code 집계를 산출한다. `scripts/run_backtest.py::_run_ablation_for_result()` 가 `result.ablation["universe_exclusion"]` 키로 첨부하고 `_format_universe_exclusion_console_lines()` 가 콘솔 표를 출력한다. 단위 테스트 9건 추가 (pure function 6 + wiring 1 + formatter 2).
  - Phase 1 잔여 완료(2026-05-24): strategy-specific prefilter variant 2종 정의. `services/rsi2_mean_reversion_universe_service.py`(`Rsi2MeanReversionUniverseService` — 거래대금/시총 baseline + 20일 annualized 변동성 ≥ 0.30) + `services/vbo_volatility_universe_service.py`(`VboVolatilityUniverseService` — 동 baseline + 20일 변동성 ≥ 0.35) 추가. `scripts/run_backtest.py::_build_ablation_overrides()` 에 `universe_type ∈ {"rsi2_mean_reversion","vbo_volatility"}` 분기 추가 (`min_volatility_20d_annualized`/`min_avg_trading_value_5d`/`min_market_cap`/`max_watchlist` override 지원, `force_market_timing_ok` 결합 가능). `strategies/rsi2_pullback_ablation.py` 에 `universe_rsi2_mean_reversion` variant, `strategies/larry_williams_vbo_ablation.py` 에 `universe_vbo_volatility` variant 각 1개 추가. 단위 테스트 30건 추가 (RSI2 service 11 + VBO service 12 + ablation overrides wiring 7). 운영 사용 예: `python scripts/run_backtest.py --strategy rsi2_pullback --ablation rsi2_pullback --ablation-variants universe_rsi2_mean_reversion --dates ...`. 검증 단위 5296 (이전 5266 → +30), 통합 235.

주요 파일:

- `strategies/strategy_executor.py`
- `services/stock_query_service.py`
- `services/price_stream_service.py`
- `view/web/web_app_initializer.py` (price_stream_service 주입)
- `services/order_execution_service.py`, `services/risk_gate_service.py` (`force_fresh=True` 명시)
- `strategies/larry_williams_vbo_strategy.py`
- `strategies/first_pullback_strategy.py`
- `strategies/high_tight_flag_strategy.py`
- `strategies/larry_williams_channel_breakout_strategy.py`

### 2-4. Polling에서 event-driven으로 점진 전환

- [x] 체결 이벤트 수신 → snapshot 업데이트 → 후보군 상태 갱신 → 전략 조건 평가 → RiskGate → 주문 → 체결/잔고/손익 대사 흐름을 목표 아키텍처로 문서화한다.
  - 산출물: `docs/event_driven_architecture.md` (2026-05-17). hybrid 모델(폴링 안전망 유지 + HIGH 우선순위 종목 event-trigger 추가) 전체 흐름·컴포넌트·검증·리스크 정리됨.
- [x] 우선순위 높은 전략부터 polling loop를 event-triggered 평가로 옮길 수 있는지 검토한다.
  - `docs/event_driven_architecture.md` §4-4 단계적 적용 표: 0단계(인프라) → 1단계 `LarryWilliamsVBOStrategy` shadow → 2단계 `OneilSqueezeBreakoutStrategy` shadow → 3단계 `HighTightFlagStrategy`는 OHLCV 별도 조회 필요로 보류 → 4단계 재평가.

구현 PR (별도 항목):

- [x] PR-1: Event router 인프라 도입 (2026-05-18 완료).
  - 신규: `services/strategy_event_router.py` (`StrategyEventRouter` — subscribe/unsubscribe/on_price_tick + throttle/stale/market_open/kill_switch 게이트)
  - `services/price_stream_service.py`: `event_router` 생성자 인자 + `set_event_router()` setter + `on_price_tick` 끝에서 `asyncio.create_task(router.on_price_tick(code, snapshot, snapshot_ts=...))` 디스패치 (running loop 없으면 silently skip).
  - 신규 단위 테스트 20개 (`test_strategy_event_router.py` 15 + `test_price_stream_service_event_router.py` 5).
  - PoC 전략 미연결. `LiveStrategy.evaluate_single` 기본 구현은 PR-2 에서 첫 전략 도입 시 함께 추가 (현재 base 인터페이스 변경 0).
  - 검증: 단위 4635 GREEN (이전 4615 → +20), 통합 233 GREEN.
- [x] PR-2: VBO shadow mode (2026-05-19 완료).
  - `interfaces/live_strategy.py`: `evaluate_single(code, snapshot) -> Optional[TradeSignal]` 기본 구현(None) + `current_candidate_codes() -> List[str]` 기본 구현([]) 추가.
  - `strategies/larry_williams_vbo_strategy.py`: 두 메서드 오버라이드. evaluate_single 은 시간/후보/range/bought_today/snapshot.open·price 게이트만 적용 — execution_strength·program_buy 는 폴링 안전망에 위임.
  - `services/event_shadow_journal_service.py`: `record()` + `flush_to_file()` (`logs/strategies/event_shadow/YYYYMMDD.jsonl`).
  - `scheduler/strategy_scheduler.py`: `StrategySchedulerConfig.event_driven_shadow: bool = False` + `event_router`/`event_shadow_journal` 생성자 인자 + `_refresh_event_shadow_subscriptions(cfg)` (scan 직후 호출, 후보 집합 diff 로 router 구독 갱신). subscribe evaluator wrapper 는 항상 None 반환 (실 주문 차단 보장).
  - 와이어업: `view/web/bootstrap/service_container.py` 가 `StrategyEventRouter` + `EventShadowJournalService` 인스턴스화 후 `PriceStreamService(event_router=...)` 생성자 주입. `view/web/bootstrap/strategy_factory.py` 가 `StrategyScheduler(event_router=..., event_shadow_journal=...)` 주입.
  - 신규 단위 테스트 21개: shadow journal 5 + VBO evaluate_single 10 + scheduler 구독 6.
  - 검증: 단위 4662 GREEN (이전 4635 → +27), 통합 233 GREEN.
  - 운영 활성화 절차: `StrategyFactory` 에서 VBO `StrategySchedulerConfig(..., event_driven_shadow=True)` 로 설정만 변경하면 즉시 shadow 모드 동작. 1주 운영 후 `logs/strategies/event_shadow/YYYYMMDD.jsonl` 와 폴링 신호 비교로 PR-3 진입 판단.
- [~] PR-2.5: VBO shadow 운영 관찰 시작.
  - 코드 대조 결과: `StrategyEventRouter`, `EventShadowJournalService`, `LarryWilliamsVBOStrategy.evaluate_single()`, scheduler 구독 lifecycle, ServiceContainer/StrategyFactory 주입은 존재한다.
  - 완료된 부분: `StrategyFactory` 의 VBO `StrategySchedulerConfig(..., event_driven_shadow=True)` 를 활성화했다. `enabled=False` 는 유지되어 실주문은 발생하지 않는다.
  - 남은 작업: 5거래일 동안 `logs/strategies/event_shadow/YYYYMMDD.jsonl` 을 수집한다.
  - 검증 기준: shadow 신호와 기존 polling 신호의 시간/종목/가격 괴리를 비교해 실주문 전환 가능 여부를 판정한다.
  - 추가 기준: polling 대비 신호 선행 시간, fast path false positive, false negative, full gate parity, missed trade PnL, duplicate signal rate.
  - VBO 특이점: `evaluate_single()` shadow fast path는 execution strength/program-buy를 의도적으로 생략하므로, fast path 통과와 full gate 최종 통과를 분리 기록해야 한다.
- [blocked] PR-3: PR-2.5 관찰 결과 양호 시 VBO 실 적용 + OSB shadow 진입.
- [x] PR-3 선행: event-driven signal sink를 명확히 한다.
  - 검토 결과: 타당. `PriceStreamService.on_price_tick()`은 `loop.create_task(router.on_price_tick(...))`로 호출하고 반환된 signal list를 소비하지 않는다. 현재 PR-2 shadow mode는 의도적으로 실주문을 막기 때문에 문제 없지만, live 전환 전에는 signal queue/order intent bus 같은 명시 sink가 필요하다.
  - 개선 방향: `StrategyEventRouter`가 list 반환에 의존하지 않고 `await signal_sink.publish(signal)` 또는 `await signal_queue.put(signal)`로 전달하도록 contract를 정한다.
  - 완료된 부분: `services/strategy_signal_sink.py` 신규 — `SignalSink` Protocol + `NullSignalSink` 기본 no-op 구현. context dict 표준 키(`signal_source="event"`, `strategy_name`, `code`, `snapshot_ts`)를 모듈 docstring에 정의했다.
  - 완료된 부분: `services/strategy_event_router.py`에 `signal_sink: Optional[SignalSink] = None` 생성자 인자 추가. `on_price_tick()`이 non-None 평가 신호마다 `await sink.publish(signal, context=...)`를 호출하고, publish 예외는 흡수해 다른 evaluator 흐름을 보존한다. `List[TradeSignal]` 반환은 호환 유지.
  - 완료된 부분: `view/web/bootstrap/service_container.py`에서 `StrategyEventRouter(..., signal_sink=None)` 명시. live consumer 주입은 PR-3 본 작업에서 진행하며 shadow 운영은 변동 없음.
  - 검증: `test_strategy_signal_sink.py` 신규 2 + `test_strategy_event_router.py` 추가 3 (publish/예외 흡수/None 신호 미호출). 전체 단위 4982 + 통합 233 통과.
- [x] PR-3 선행: EventRouter throttle을 threshold crossing 또는 signal debounce 정책으로 보강한다.
  - 검토 결과: 부분 타당. 현재 throttle은 evaluator 실행 전 `(strategy, code)` 단위 0.5초 차단이다. 돌파 조건 crossing tick이 throttle window 안에 들어오면 평가 자체가 지연될 수 있다.
  - 개선 방향: trigger price crossing은 evaluator 실행을 허용하고, 중복 signal 발행만 debounce하는 방향을 검토한다.
  - 완료된 부분: 두 단계로 분리(`docs/event_driven_architecture.md` §9 Q5, 2026-05-22 결정). `StrategyEventRouter`에 `signal_debounce_sec: Optional[float] = None` 신규 인자 추가, `_last_signal_dispatched` 상태와 `on_price_tick()` 결과 루프의 debounce 체크 통합. `unsubscribe()`도 debounce 상태 cleanup. 코드 기본값은 backward compat 유지 (`throttle_sec=0.5`, `signal_debounce_sec=None`).
  - 완료된 부분: 운영(`view/web/bootstrap/service_container.py`)에서 `throttle_sec=0.1, signal_debounce_sec=0.5`로 활성화. evaluator는 같은 tick burst만 흡수해 trigger crossing 보장, 같은 (strategy, code) 중복 publish/return은 0.5초 debounce.
  - 검증: `test_strategy_event_router.py` 추가 4개 (within-window 차단 / after-window 재발행 / None 비활성 backward compat / unsubscribe cleanup). 전체 단위 4986 + 통합 233 통과.
  - 완료된 부분(2026-05-25): crossing-aware bypass 시나리오 테스트 3건 추가 ([tests/unit_test/services/test_strategy_event_router.py](tests/unit_test/services/test_strategy_event_router.py)) — ① `test_crossing_tick_evaluated_under_operational_throttle_split` 운영 정책(throttle=0.1, debounce=0.5)에서 throttle 경과 후 도착한 crossing tick(`prev=9999, curr=10001`)이 evaluator 평가 + 신호 발행 진입을 잠근다, ② `test_crossing_tick_blocked_when_legacy_single_throttle_covers_window` 레거시 단일 throttle(0.5)에서 0.2초 뒤 crossing tick이 평가 자체 차단됨을 회귀로 고정해 throttle/debounce 분리 결정 근거를 명시한다, ③ `test_continuous_crossing_ticks_evaluated_but_signal_publish_debounced` trigger 위 연속 tick 5건(0.15s 간격)에서 evaluator는 매번 평가되고 publish만 debounce(첫·마지막 2건)되는 분리 contract를 잠근다. `_make_threshold_evaluator()` helper로 price-based evaluator 패턴 표준화. 단위 25(이전 22 → +3), 전체 5671 통과.
- [ ] PR-4+: 단계적 확장.

구현 결정 사항 (`docs/event_driven_architecture.md` §9, 2026-05-18 확정):

- [x] (Q1) `(strategy, code)` event throttle = 0.5초 (`StrategyEventRouter(throttle_sec=...)`).
- [x] (Q2) Stale snapshot 임계 = 5초 (`StrategyEventRouter(stale_snapshot_sec=...)`).
- [x] (Q3) Shadow mode 운영 기간 = VBO 1주 (5 거래일).
- [x] (Q4) `signal_source` 저장 = `metadata` JSON 키 (DB schema 변경 없음).

---

## P3. 유지보수성

활성 전략의 공통 실행 contract 표준화 — 신규 전략 추가 시 누락 위험을 낮춥니다.

### 3-4. 전략 공통 lifecycle/state contract

- [~] 활성 전략의 공통 scan/check_exits/state save/load 패턴을 base class 또는 helper로 추출할지 설계한다.
  - 검토 결과: 타당. O'Neil/HTF/First Pullback/RSI2/Larry Williams 계열에 watchlist 조회, market timing, candidate filtering, chunked entry, unbounded exit, state save/load 패턴이 반복된다.
  - 완료된 부분(2026-05-23): `larry_williams_cb` state 패턴을 `StrategyStateIO`로 통일했다.
  - 완료된 부분(2026-05-23): 4개 전략의 `schedule_save` 패턴을 통일하고, `rsi2` state migration 및 `StrategyStateIO` loop-aware 개선을 적용했다.
  - 완료된 부분(2026-05-23): `LiveStrategy.load_state()` 기본 no-op contract를 추가하고, scheduler stop 시 `StrategyStateIO.flush_pending()`으로 지연 저장을 정리한다.
  - 결정: scan/check_exits 대형 base class 추출은 현재 반복 제거 대비 리스크가 커서 보류한다. 공통 흐름이 더 쌓이면 별도 설계 항목으로 재승격한다. `larry_williams_vbo`는 state 파일이 없어 마이그레이션 대상이 아니다.
  - 2026-05-24 최신 main 리뷰 반영: 전략 실행 lifecycle 자체가 강하게 표준화된 상태는 아니므로 완료가 아니라 부분 반영으로 둔다.
- [~] active strategy lifecycle contract를 최소 공통 단계로 강제할지 재설계한다.
  - 후보 단계: `load_state` → `get_watchlist` → `filter_candidates` → `evaluate_entries_bounded` → `evaluate_exits_bounded` → `save_state` → `emit_metrics`.
  - 목표: 대형 base class가 부담되면 helper/protocol/checklist 테스트로라도 active strategy별 누락을 탐지한다.
  - 완료된 부분(2026-05-25): 7단계 분해 base class 도입은 보류(외과수술적 변경 원칙 — `scan`/`check_exits` 내부에 단계가 묻혀 있어 분해 리스크 > 표면화 가치). 대신 **checklist 테스트** 방식으로 활성 7개 전략의 최소 contract 누락을 자동 탐지한다. [interfaces/live_strategy.py](interfaces/live_strategy.py)에 `save_state()` default no-op hook 추가(load_state와 대칭, 호출자 await 표면 제공). 신규 [tests/unit_test/strategies/test_live_strategy_lifecycle_contract.py](tests/unit_test/strategies/test_live_strategy_lifecycle_contract.py)는 활성 7개 전략(`oneil_pocket_pivot`, `high_tight_flag`, `first_pullback`, `oneil_squeeze_breakout`, `rsi2_pullback`, `larry_williams_channel_breakout`, `larry_williams_vbo`)에 대해 다음을 parametrize 검증: ① `LiveStrategy` 상속, ② `name`/`strategy_id`/`display_name` non-empty str, ③ `scan`/`check_exits` async callable, ④ `load_state`/`save_state` async callable, ⑤ `evaluate_single` async callable, ⑥ `current_candidate_codes()` 동기 호출 + list 반환, ⑦ `load_state()` mock 환경 idempotent await. 전역 invariant 2건: `strategy_id`/`name` 충돌 없음. 65 case 통과(9 contract × 7 + 2 unique). 신규 전략 추가 시 contract 누락 자동 탐지 안전망 확립. 단위 5419(이전 5354 → +65), 통합 235 통과.
  - 최신 main 재확인(2026-05-25): 리뷰의 "공통 base lifecycle 미흡" 판단은 방향상 맞다. 다만 현재 todo 결론은 "즉시 base class 도입"이 아니라 "checklist contract로 누락 감지, 반복이 더 쌓이면 7단계 분해 재승격"이다.
  - 후속(보류): 7단계 분해(`get_watchlist`/`filter_candidates`/`evaluate_entries_bounded`/`evaluate_exits_bounded`/`emit_metrics`)는 별도 PR. 현재는 `scan`/`check_exits` 안에 묻혀 있어 분해 자체가 대형 리팩토링.
- [x] 운영 bootstrap에서 strategy state load/save barrier를 검증한다.
  - 시작 전: 모든 active strategy의 `load_state()` 완료 후 scan/scheduler 시작.
  - 종료 전: `StrategyStateIO.flush_pending()` 완료 후 프로세스 종료.
  - 실패 정책: state load 실패 시 paper/real별 fail-open/fail-close 정책을 문서화하고 테스트로 고정한다.
  - 완료된 부분(2026-05-25): bootstrap barrier 자체는 이미 [view/web/web_main.py:124](view/web/web_main.py#L124) `await ctx.ensure_strategy_states_loaded()`(initialize_scheduler 직후 + start_background_tasks 전)와 [view/web/web_main.py:150](view/web/web_main.py#L150) `await StrategyStateIO.flush_pending(timeout=5.0)`(종료 hook), [scheduler/strategy_scheduler.py:217](scheduler/strategy_scheduler.py#L217) `await StrategyStateIO.flush_pending()`(scheduler.stop()) 으로 wiring 되어 있었다. 이번 작업은 실패 정책을 추가했다. `WebAppContext.ensure_strategy_states_loaded()`가 `self.env.is_paper_trading`을 읽어 paper=fail-OPEN(기존 동작, error log 후 계속) / real=fail-CLOSE(`RuntimeError("실전 모드 bootstrap 차단: 전략 state load 실패 — {failed_names}")`)로 분기한다. `env=None`은 보수적으로 fail-OPEN. 정책 근거: P0 0-1의 `RiskGateFailOpenConfig(paper=True, real=False)`와 동일 패턴 — 실전에서 stale state로 신규 주문 위험이 모의 개발 흐름 차단 비용보다 크다. 모든 실패 전략은 한 번씩 await 시도된 뒤 마지막에 raise(다중 실패 전략 이름이 message에 모두 포함됨). 신규 단위 테스트 8건: `test_ensure_strategy_states_loaded_{no_scheduler_noop, all_success_paper, all_success_real, skips_strategies_without_load_state, paper_fails_open_on_load_error, real_fails_close_on_load_error, real_multiple_failures_in_error, no_env_defaults_to_fail_open}`. 단위 5354(이전 5346 → +8), 통합 235 통과.
- [x] `strategy_id`와 `display_name`을 분리한다.
  - 검토 결과: 타당. `strategy.name` 값이 한국어 display name(`오닐PP/BGU`, `오닐스퀴즈돌파` 등) 또는 영문 display name(`Larry Williams VBO`)으로 TradeSignal/RiskGate/log/config key에 사용된다.
  - 개선 방향: 설정·DB·journal·risk limit은 stable `strategy_id`, UI/로그 문구는 `display_name`을 사용하도록 backward-compatible migration을 설계한다.
  - Phase 1 완료(2026-05-23): `LiveStrategy` base class 에 `strategy_id` property 도입 (default = `self.name` fallback). 10개 활성 전략 (`first_pullback`, `high_tight_flag`, `larry_williams_cb`, `larry_williams_vbo`, `oneil_pocket_pivot`, `oneil_squeeze_breakout`, `program_buy_follow`, `rsi2_pullback`, `traditional_volume_breakout`, `volume_breakout_live`) 에 안정 영문 ID 지정. consumer 변경 없는 additive 변경. 단위 테스트 13건 (parametrize 10 + snake_case + unique + base fallback) 추가로 ID 잠금.
  - Phase 2 완료(2026-05-23): `StrategyIdentityResolver`와 virtual trade repository compat layer를 추가하고, KillSwitch JSON state/RiskGate consumer/scheduler signal stamping을 `strategy_id` 기준으로 전환했다.
  - Phase 3 완료(2026-05-23): `LiveStrategy.display_name` property를 추가하고, scheduler status API가 `strategy_id`/`display_name`을 함께 노출한다. 웹 scheduler UI는 표시명에 `display_name`을 사용하되 기존 filter key는 `name`으로 유지해 호환성을 보존한다.
- [x] 직전 거래일 계산을 `MarketCalendarService` 기준으로 통일한다.
  - 검토 결과: 타당. `OneilUniverseService`, `OneilPocketPivotStrategy`, `FirstPullbackStrategy`, `MarketDataService` 일부 경로에서 `now - timedelta(days=1)`을 전일 기준으로 사용한다.
  - 완료된 부분: `common.date_utils.previous_trading_day_str(now, holidays=None)` sync helper 추가. 4개 사용처(`OneilUniverseService._analyze_surge_candidate`, `OneilPocketPivotStrategy._check_entry`, `FirstPullbackStrategy._check_entry`, `MarketDataService.get_ohlcv`)를 helper로 통일. 주말(토/일) 우회 + optional `holidays` set 지원. 단위 테스트로 weekday/주말/공휴일/시각 무관/`date` 입력 회귀 고정. `MarketCalendarService` async 메서드 추가는 의존성 주입 부담이 커서 보류 — 호출자가 휴장일을 인지할 때 `holidays` 인자로 전달하도록 했다.
  - 2026-05-24 확인: 소스/전략/서비스 범위에서 `timedelta(days=1)` 전역 검색을 수행했다. 전략·universe의 직전거래일 계산 잔여 사용은 발견하지 못했고, 남은 사용은 `common.date_utils`/`MarketCalendarService` 내부 루프, calendar-day backfill, 테스트용 날짜 생성이다.
- [x] `TradeSignal` contract를 분석/운영 기준으로 확장한다.
  - 후보 필드: `signal_id`, `strategy_id`, `entry_reason`, `invalidation_price`, `stop_loss`, `target` 또는 trailing rule, `expected_holding_period`, `confidence`, `required_data`.
  - 검토 결과: 타당. 현재 `TradeSignal`은 `reason`, `strategy_name`, `stop_loss_pct`, `atr_multiplier`, `volatility_20d_annualized`를 갖지만, 중복 신호 식별과 사후 분석에 필요한 표준 필드는 아직 부족하다.
  - Phase 1 완료(2026-05-23): `TradeSignal` 에 10개 Optional 필드 추가 — `signal_id`, `strategy_id`, `entry_reason`, `invalidation_price`, `stop_loss_price`, `target_price`, `trailing_rule`, `expected_holding_period_days`, `confidence`, `required_data`. 모두 default None 이라 기존 호출자/소비자 변경 없음. `tests/unit_test/common/test_trade_signal_contract.py` 16건으로 contract 잠금 (minimal 호환, default None, to_dict 포함, 명시 할당, 기존 stop_loss_pct 와 stop_loss_price 공존).
  - Phase 2 완료(2026-05-23): scheduler 가 scan/check_exits 직후 `signal_id`를 자동 발급하고 `strategy_id`를 stamp 한다.
  - 완료된 부분(2026-05-23): scheduler 가 `config_hash`도 함께 stamp 한다.
  - 완료된 부분(2026-05-23): scheduler/order execution/risk gate가 `invalidation_price`/`stop_loss_price`를 주문 정책 입력으로 전달·검증한다. `PositionSizingService`는 `stop_loss_price`를 절대 손절가 기반 per-share risk로 반영한다. backtest replay는 `required_data` 누락 fixture를 차단한다.
- [x] 모든 활성 BUY signal producer가 확장된 `TradeSignal` 필드를 일관되게 채우도록 audit/test를 추가한다.
  - 필수 후보: `entry_reason`, `invalidation_price`, `stop_loss_price`, `target_price` 또는 `trailing_rule`, `expected_holding_period_days`, `confidence`, `required_data`.
  - 리뷰 기준: contract와 consumer는 확장됐지만, O'Neil/Minervini 계열 등 실제 전략의 BUY signal이 모든 필드를 항상 채운다고 보기 어렵다.
  - 완료 기준: 활성 7개 전략의 BUY signal fixture 또는 builder 단위 테스트에서 필수 필드가 누락되면 실패한다. 전략별로 목표가가 부적절한 경우에는 `trailing_rule` 또는 명시적 `target_unset_reason` 같은 표준 대체 contract를 둔다.
  - 운영 효과: 성과 악화 시 "진입 논리", "틀린 가격", "손절 기준", "목표/트레일링", "데이터 의존성"을 사후 분해할 수 있어야 한다.
  - 완료된 부분(2026-05-25): `tests/unit_test/strategies/test_active_buy_signal_contract.py` 신규 AST audit 테스트 추가. 활성 7개 전략의 모든 BUY `TradeSignal(...)` 호출이 `entry_reason`/`invalidation_price`/`stop_loss_price`/`expected_holding_period_days`/`confidence`/`required_data`와 `target_price` 또는 `trailing_rule`을 명시하지 않으면 실패하도록 고정했다. `FirstPullbackStrategy`, `HighTightFlagStrategy`, `LarryWilliamsChannelBreakoutStrategy`, `LarryWilliamsVBOStrategy`(polling + shadow), `OneilPocketPivotStrategy`, `OneilSqueezeBreakoutStrategy`, `RSI2PullbackStrategy`의 BUY 반환부에 전략별 손절가/무효화가/목표 또는 trailing rule/필수 데이터/신뢰도/보유기간 contract를 채웠다. 단위 5532, 통합 235 통과.
- [x] 활성/실험/레거시 전략의 디렉터리 또는 registry 경계를 명확히 한다.
  - 후보 구조: `strategies/active`, `strategies/experimental`, `strategies/deprecated`, `strategies/legacy`.
  - 완료된 부분(2026-05-23): 파일 이동 없이 `STRATEGY_STATUS_MAP` registry metadata로 7 active / 3 experimental 상태를 명시하고, resolver `get_status()`로 조회하도록 했다.
- [x] 설정 변경 통제를 도입한다.
  - 후보: config version, trade journal 내 config hash, 장 시작 전 config diff log, production config lock, dry-run config validation.
  - 목표: 어떤 설정으로 어떤 주문이 나갔는지 사후 재현 가능하게 만든다.
  - 완료된 부분(2026-05-23): `common/config_hashing.py` 추가 및 scheduler `config_hash` stamping 1차 적용.
  - 완료된 부분(2026-05-23): `virtual_trade_repository` DB schema/legacy migration/standard journal에 `config_hash`를 추가했다. `PreDeployCheckService`와 `scripts/run_predeploy_check.py --expected-config-hash`가 config 변경 후 hash diff를 WARN으로 보고한다.

---

## P4. 운영 품질

실전에서는 “왜 샀는지”뿐 아니라 “왜 안 샀는지”와 “언제 전략을 쉬게 해야 하는지”가 중요합니다.

### 4-1. 전략별 성과 저하 감지

- [x] `services/regime_performance_service.py` 와 같은 pure function 패턴으로 standard journal record 기반 분석 모듈을 추가한다.
  - 입력은 `get_standard_journal_records()` 와 동일한 record shape를 사용한다.
  - SOLD 거래만 대상으로 하며, 최근 N거래 기본값은 20건이다.
- [x] "최근 N거래" 정렬 기준을 확정한다.
  - 청산 시각 우선: `metadata.sell_date` → `metadata.exit_time` → `metadata.closed_at` → fallback `signal_time`.
  - 성과 저하는 결과 확정 시점 기준이므로 진입 시각보다 청산 시각을 우선한다.
- [x] 전략별 최근 20거래 손익, 승률, payoff ratio, profit factor, MDD, 연속 손실, MFE/MAE를 집계한다.
  - `payoff_ratio = avg_win_return / abs(avg_loss_return)`.
  - `profit_factor = sum_win_pnl / abs(sum_loss_pnl)` 이며 판정 기준은 profit factor를 우선한다.
  - MDD는 `mdd_amount`(누적 net_pnl peak-to-trough)와 `mdd_ratio`를 함께 산출한다.
  - `mdd_ratio`는 live/backtest가 같은 `capital_base_won` 분모 정의를 공유할 때만 baseline 비교에 사용한다.
- [x] 백테스트 기대값 대비 실거래 괴리를 전략별로 계산한다.
  - live sample 부족은 `insufficient_live`, baseline sample 부족은 `insufficient_baseline`으로 분리한다.
  - 표본 부족 시 hard 후보 판정은 하지 않고 리포트/metadata에만 표시한다.
- [x] 성과 악화 시 신규 진입 중단, 수량 축소, paper mode 전환, 관리자 알림 후보로 표시한다.
  - 1차 구현은 soft signal만 산출하며 실제 자동 차단, 수량 변경, paper 전환은 실행하지 않는다.
  - 후보 metadata에 recommended_actions를 포함한다.
  - 완료된 부분(2026-05-25, hard 신규 진입 차단 도입): 사용자 정책 합의 "신규 진입 차단만 + 수동 reset만"에 따라 BUY-only 자동 차단을 추가했다. ① `KillSwitchService.trip_strategy(name, reason, metadata=None, *, block_side="all")` — 새 keyword-only `block_side` 인자 ("all"|"buy", default "all"=기존 동작 보존, invalid → "all" fallback) 추가. trip metadata에 `block_side` 보존. ② `RiskGateService._check_strategy_kill_switch(strategy_name, side)` — trip_info의 `block_side` 읽어 `block_side="buy" + side=SELL`이면 통과시켜 성과 저하 전략의 보유 종목 graceful 청산 허용. legacy trip(metadata에 `block_side` 없음)은 "all" fallback으로 BUY/SELL 모두 차단(기존 의미 보존). force-exit SELL은 기존처럼 strategy KS 체크 자체를 우회. ③ `StrategyLogReportTask(..., auto_block_on_critical: bool = False)` — opt-in flag (default OFF로 backward-compat). `_emit_strategy_degradation_candidate_alert()`에서 `status == "critical_candidate"` AND not already tripped AND flag ON 시 `kill_switch.trip_strategy(strategy, reason=f"strategy_perf:{','.join(reasons)}", metadata={alert_type, report_date, candidate, auto_blocked: True}, block_side="buy")` 호출. trip 예외는 흡수해 운영자 알림 흐름 보존. alert metadata에 `auto_blocked_by_strategy_perf` 플래그 추가, `already_blocked_by_kill_switch`는 사전 트립일 때만 True. `degraded` (soft warning) status는 trip 대상 아님. ④ per-strategy 수동 reset route 추가 — `POST /kill-switch/reset-strategy/{strategy_id}` ([view/web/routes/kill_switch.py:50](view/web/routes/kill_switch.py#L50))로 `KillSwitchService.reset_strategy(strategy_id, operator)` 호출. 글로벌 KillSwitch reset(`/kill-switch/reset`)과 분리. 우선순위(KillSwitch hard stop > RiskGate hard block > StrategyPerf hard block > soft alert) 보존 — 성과 저하 자동 차단은 KillSwitch infra를 재사용하되 BUY-only로 격리. 신규 단위 테스트 13건: KillSwitch block_side 3 ([tests/unit_test/test_kill_switch_service.py](tests/unit_test/test_kill_switch_service.py)), RiskGate strategy KS side awareness 5 ([tests/unit_test/services/test_risk_gate_service.py](tests/unit_test/services/test_risk_gate_service.py) — all/buy + SELL graceful + legacy missing key fallback + force-exit bypass), StrategyLogReportTask auto-trip 5 ([tests/unit_test/task/background/after_market/test_strategy_log_report_task.py](tests/unit_test/task/background/after_market/test_strategy_log_report_task.py) — default off / critical trips with block_side=buy / degraded does not trip / already-tripped no double-trip / trip exception absorbed). 전체 단위+통합 5684 통과(이전 5671 → +13).
  - 후속(보류): hard 자동 차단 후 자동 해제(회복 조건 충족 시 자동 재개) 정책 — 사용자 선택은 "수동 reset만"이라 본 PR 범위에서 제외. PositionSizingService 수량 축소, paper mode 전환은 별도 정책 합의 후 별도 PR.
- [x] 운영자 알림은 `OperatorAlertService.report(AlertSource.STRATEGY_PERF, ...)` 경로를 우선 사용한다.
  - `AlertSource.STRATEGY_PERF = "STRATEGY_PERF"` 를 추가한다.
  - `operator_alert_service is not None` 이면 OperatorAlertService만 사용한다.
  - `operator_alert_service is None` 이고 `notification_service is not None` 일 때만 NotificationService fallback을 사용한다.
  - 둘을 동시에 호출하지 않는다.
- [x] 자동 차단 기준은 KillSwitch/RiskGate와 충돌하지 않도록 정책 우선순위를 정의한다.
  - 우선순위: `KillSwitch` hard stop > `RiskGate` 주문 직전 hard block > `StrategyPerf` soft alert.
  - `KillSwitchService.is_strategy_tripped(strategy_name)` 가 truthy이면 추가 차단 없이 `already_blocked_by_kill_switch=True`, `kill_switch_trip=<trip meta>` metadata로 알림만 보낸다.
- [x] 테스트 fixture를 `tests/fixtures/strategy_degradation/` 아래에 추가한다.
  - `recent_trades_live.json`
  - `recent_trades_backtest.json`
  - 필요 시 `expected_metrics.json`

주요 파일:

- `common/operator_alert_types.py`
- `services/strategy_performance_degradation_service.py`
- `services/strategy_log_report_service.py`
- `task/background/after_market/strategy_log_report_task.py`
- `services/kill_switch_service.py`
- `services/risk_gate_service.py`
- `tests/fixtures/strategy_degradation/`
- `tests/unit_test/services/test_strategy_performance_degradation_service.py`
- `tests/unit_test/task/background/after_market/test_strategy_log_report_task.py`

### 4-4. 실전 운영 Runbook / Canary 절차

- [x] 실전 운영 runbook을 작성한다.
  - 포함: 장 시작 전 체크리스트(토큰, 잔고, 포지션, 데이터 연결, WebSocket), 장중 장애 대응(API 오류, 주문 지연, 미체결, stale data), Kill Switch 발동 후 절차, 재개 조건, 배포 체크리스트, 사고 리포트 템플릿.
  - 산출물(2026-05-22): `docs/operations_runbook.md` — Runtime 진입점 표, 장 시작 전 체크리스트 8항목, 장중 장애 대응 매트릭스 10행, Kill Switch 발동 5단계 절차, 재개 조건 체크리스트, 배포 체크리스트 10항목, 사고 리포트 템플릿.
- [x] 실계좌 canary 절차를 문서화한다.
  - 정책: P0 완료 + P1 기준선 통과 전 full-auto 금지. 소액 canary는 종목 수/주문금액/일손실/연속 손실/미체결 시간 한도를 별도로 낮게 둔다.
  - 산출물: canary 진입 조건, 중단 조건, 관찰 기간, 승격 조건.
  - 산출물(2026-05-22): `docs/canary_procedure.md` — 진입 조건, 운영 한도표(canary vs full), 관찰 기간 단계, 중단 조건 매트릭스, 승격 조건 체크리스트.
- [x] 배포 전 dry-run 운영 점검을 자동화한다.
  - 후보: config validation, broker token/account/env consistency, WebSocket subscription health, latest trading date, account snapshot freshness, event shadow status.
  - 현황(2026-05-22): runbook *배포 체크리스트* 의 1~10번 수동 항목으로 우선 문서화함. 자동화는 별도 PR (코드 작업).
  - 산출물(2026-05-23): `services/predeploy_check_service.py` + `scripts/run_predeploy_check.py` 추가. `--offline`/`--paper`/`--json` 옵션, FAIL 시 exit 1. config_validation / broker_env_consistency / latest_trading_date / event_shadow_status / websocket_subscription_health / account_snapshot_freshness 6개 점검을 끝까지 실행 후 표 출력. 단위 테스트 32건 + 통합 테스트 2건 추가. runbook *배포 체크리스트* 에 자동 점검 사용법 반영.
  - 후속(보류): WebSocket subscription health 자동 점검은 streaming watchdog 의 probe 어댑터 도입 시 SKIPPED → PASS 로 활성화. event shadow ≥ 95% 일치율 비교는 폴링/shadow 신호 join 로직 필요 — 별도 작업으로 분리.

---

## Strategy Log 남은 작업

### Pool B 튜닝 관찰

- [ ] 후보 부족 현상이 재발하면 거래대금 기준을 50억에서 30억으로 추가 완화할지 검토한다.
- [ ] 후보 부족 현상이 재발하면 정배열 조건을 Pool B 전용으로 `current > ma_20d` 중심으로 완화할지 검토한다.

---

## 바로 착수 추천 순서

최신 main 리뷰 기준으로는 "실전 데이터 수집 대기" 외에도 즉시 가능한 코드/정책 작업이 다시 생겼습니다. 우선순위는 실전 손실 방지와 검증 gate 강화입니다.

1. **즉시 코드/정책 작업**
   - Profitability gate real-mode 기준 강화 + missing evidence block (P1 1-1)
   - Canary 리스크/주문 정책 보수화 (`position_sizing`, `risk_gate`, `order_policy`) (P0 0-2)
   - API budget endpoint coverage matrix + 장초반 synthetic 부하 테스트 (P2 2-2)
   - 활성 BUY signal 필수 필드 audit/test 추가 (P3 3-4)

2. **외부 데이터 확보 후 즉시 진행 가능**
   - broker order number mapper 별도 클래스 분리 + fixture 테스트 (P0 0-1)
   - 실제 replay fixture overlay 결합 → 통과 케이스 고정 (P1 1-5)
   - 한국장 microstructure fixture 로 체결 모델 보수성 검증 (P1 1-5)
   - VBO 실 적용 + OSB shadow 진입 (PR-3, P2 2-4)

3. **운영 데이터 축적 대기**
   - VBO shadow 5거래일 jsonl 수집 → polling 신호와 parity 비교 (P2 2-4 PR-2.5)
   - 실전 KIS `inquire-daily-ccld` 체결 이력 응답 캡처 (P0 0-1)
   - 장중 후보 종목 프로그램매매 WebSocket 샘플 캡처 (P1 1-5)

4. **검증 방법론 강화**
   - multiple testing 보정(PBO/Deflated Sharpe 유사 지표), purged/embargo validation, ablation 기준 승격 (P1 1-2)

5. **조건부 트리거 — 재발 시 진행**
   - Pool B 거래대금 50→30억 완화 검토
   - Pool B 정배열 조건을 `current > ma_20d` 중심 완화 검토

6. **코드 작업 가능하나 보류 결정된 항목 (필요 시 재승격)**
   - [x] 주문/WebSocket API budget 적용 1차 (완료: `place_stock_order`/`cancel_stock_order`/`subscribe_*` 가 retry queue 우회하되 `order_submit`/`order_cancel`/`websocket_subscribe` budget 적용)
   - [x] Emergency 청산 우선순위 정책화 (완료 2026-05-25: `core/api_priority.py` + `ApiBudgetLimiter` emergency overlay lane + `OrderExecutionService` EMERGENCY 스코프 wiring)
   - [x] 주문 submit retry 횟수 config화 (완료: `config.order_execution.order_max_retries` / `order_retry_delay_sec`, 기본값은 기존 `3` / `3` 유지)
   - active strategy lifecycle 7단계 base class 분해 (외과수술적 변경 원칙으로 보류)
   - 성과 저하 자동 해제 정책 (P4 4-1, 현재 수동 reset 만)
   - 성과 저하 시 수량 축소 / paper mode 자동 전환 (P4 4-1)
   - `MomentumStrategy` 등 비활성/레거시 백테스트 통합 여부 결정 (완료 기준)

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
  - 완료된 부분: 거래 비용 계산 유틸과 테스트가 있고, 체결 품질 로그/리포트에서 슬리피지를 추적한다.
  - 완료된 부분: 전략별 성과 지표 조회/집계 기본값이 비용 포함 순수익(`net PnL/return`) 기준으로 전환되었고, gross 기준은 `apply_cost=false` 명시 경로로 유지한다.
  - 완료된 부분: 백테스트용 `BacktestExecutionSimulator`가 명시적 시장가 슬리피지, 호가 단위 반올림, 수수료/거래세 포함 체결 리포트를 생성한다.
  - 완료된 부분: 활성 period runner와 레거시 `VolumeBreakoutStrategy.backtest_open_threshold_intraday()` 저장 경로는 `BacktestExecutionReport` 기반 표준 journal을 사용한다.
  - 완료된 부분: 활성 period runner는 `MarkToMarketBarProvider` contract 주입 시 중간 보유일 일중 high/low를 MFE/MAE에 합산한다(미주입 시 기존 동작 유지).
  - 진행 필요: `MomentumStrategy` 등 비활성/레거시 독립 백테스트 경로까지 동일 체결 리포트/포트폴리오 장부로 통합할지 결정한다.
  - 관련 파일: `utils/transaction_cost_utils.py`, `services/backtest_execution_simulator.py`, `services/order_execution_service.py`, `services/strategy_log_report_service.py`, `tests/unit_test/utils/test_transaction_cost_utils.py`, `tests/unit_test/services/test_backtest_execution_simulator.py`

- [x] 장애, 데이터 지연, websocket 끊김, reconcile 실패 시 신규 주문 차단 또는 경고 상태로 전환된다.
  - 완료된 부분: Kill Switch/Risk Gate 주문 차단, 데이터 품질 오류 주문 차단, websocket watchdog 재연결/경고, reconcile alarm 신규 주문 차단이 구현·테스트되어 있다.
  - 완료된 부분: reconcile 실패 운영 매트릭스가 `docs/reconcile_failure_policy.md` 에 정리되어 있고, `tests/integration_test/test_it_reconcile_failure_policy.py` 가 매트릭스 행을 1:1로 검증한다.
  - 완료된 부분: after-market reconcile task 오류/불일치 알림 경로는 `tests/unit_test/task/background/after_market/test_after_market_reconcile_task.py` 로 검증된다.
  - 관련 파일: `services/kill_switch_service.py`, `services/data_quality_service.py`, `services/risk_gate_service.py`, `services/order_execution_service.py`, `task/background/intraday/websocket_watchdog_task.py`, `task/background/after_market/after_market_reconcile_task.py`, `docs/reconcile_failure_policy.md`

