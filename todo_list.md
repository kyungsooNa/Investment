# Investment Trading App - 남은 To-Do

최종 업데이트: 2026-05-21 (P1 실전 확대 gate 런타임 연결)

이 문서는 현재 남은 실행 항목만 추린 목록입니다. 완료된 구현 상세, 완료 체크 항목, 과거 세션 요약은 제거했습니다.

정리 원칙:

- 실전 계좌 보호와 주문 안정성을 최우선으로 둔다.
- 이미 적용된 항목은 새 기능으로 다시 넣지 않고, 검증/고도화 과제로만 남긴다.
- 후보군 관리는 신규 구축 과제가 아니라 기존 `OneilUniverseService` / `SubscriptionPolicy` 구조의 공통 파이프라인 확장 과제로 본다.
- 주문/브로커/스케줄러 변경은 테스트 hang 가이드와 paper/real 분기 검증을 함께 적용한다.
- `VolumeBreakoutStrategy`, `VolumeBreakoutLiveStrategy`, `TraditionalVolumeBreakoutStrategy`, `GapUpPullbackStrategy`, `ProgramBuyFollowStrategy`는 현재 사용하지 않는 전략이므로 신규 연결/개선 우선순위에서 제외한다.

---

## P0. 실전 손실 방지

백테스트에서 이긴 전략이 실거래에서도 같은 방식으로 기록, 체결, 정산, 차단되는지 검증하는 영역입니다. 실전 투입 전 최우선입니다.

### 0-0. 정적 리뷰 기반 주문 안전성 재점검

2026-05-19 ~ 05-20 외부 정적 리뷰 항목 5건을 모두 적용. 상세는 PR #419 ~ #423 본문과 git log 참조.

- [x] 시장가 매수(`price == 0`) RiskGate 우회 차단 — `market_buy_reference_price_provider` 주입 (2026-05-19, PR #419)
- [x] `BrokerOrderSubmitter.submit_with_retry()` `market_clock=None` 시 `asyncio.sleep` backoff fallback (2026-05-19)
- [x] 실전 모드 RiskGate fail-close 전환 — `RiskGateFailOpenConfig(paper=True, real=False)` (2026-05-19, PR #421)
- [x] `get_current_price` 전역 캐시 제거 — `cache_config.yaml::enabled_methods`에서 제외 (2026-05-20)
- [x] 전략 state load/save atomic write + per-path lock + 명시적 `load_state` await — `utils/strategy_state_io.py` (2026-05-20)

주요 파일:

- `services/risk_gate_service.py`
- `services/broker_order_submitter.py`
- `services/order_submission_coordinator.py`
- `services/order_policy_service.py`
- `config/cache_config.yaml`
- `strategies/oneil_pocket_pivot_strategy.py`
- `strategies/high_tight_flag_strategy.py`
- `strategies/first_pullback_strategy.py`
- `strategies/oneil_squeeze_breakout_strategy.py`
- `tests/unit_test/services/test_risk_gate_service.py`
- `tests/unit_test/services/test_broker_order_submitter.py`
- `tests/unit_test/services/test_stock_query_service.py`
- `tests/unit_test/strategies/`

### 0-1. 실전 KIS `inquire-daily-ccld` 응답 필드 검증

- [blocked] 실제 체결 이력이 있는 실전 계좌 응답을 캡처한다. (현재 실전 계좌 체결 이력 부재)
- [blocked] 민감정보를 제거한 fixture를 추가한다. (실전 응답 확보 후 진행)
- [blocked] paper fixture와 real fixture의 필드 차이를 회귀 테스트에 반영한다. (실전 응답 확보 후 진행)
- [blocked] 주문번호, 종목코드, 매수/매도 구분, 주문수량, 누적체결수량, 미체결수량, 평균체결가, 취소/거부 필드 매핑을 확정한다. (실전 응답 확보 후 진행)
- [ ] 주문 접수 응답의 broker order number mapper를 실전/모의 fixture 기반으로 확장한다.
  - 검토 결과: 부분 타당. 현재 `OrderStateMachine.extract_broker_order_no()`는 `ordno`, `order_no`, `odno`만 확인한다. `inquire-daily-ccld`/미체결 조회 복원 쪽은 `ODNO`/`주문번호`도 일부 처리하지만, 최초 주문 응답 mapper는 더 좁다.
  - 개선 방향: raw broker response payload를 journal/diagnostic에 보존하고, 주문번호 추출 실패 시 reconcile alarm 또는 운영자 알림으로 승격한다.

주요 파일:

- `brokers/korea_investment/korea_invest_account_api.py`
- `brokers/korea_investment/korea_invest_trading_api.py`
- `brokers/broker_api_wrapper.py`
- `services/order_execution_service.py`
- `tests/unit_test/`
- `tests/integration_test/`

### 0-2. 백테스트와 실거래 journal schema 표준화

- [x] 모든 저장 경로가 표준 journal schema를 직접 저장하도록 정리한다.
  - 완료된 부분: `BacktestExecutionReport` 기반 기간 백테스트 journal이 전략 신호 사유(`decision_reason`)와 체결 봉 기준 `MFE`/`MAE`를 표준 schema로 보존한다.
  - 완료된 부분: 레거시 `VolumeBreakoutStrategy.backtest_open_threshold_intraday()` 저장 경로가 왕복 1행(`ROUND_TRIP`) 대신 `BacktestExecutionReport` 기반 `BUY FILLED` + `SELL SOLD` 표준 journal을 저장한다.
  - 완료된 부분: 활성 전략 period runner의 매도 journal이 매수 체결 봉과 매도 체결 봉을 합산한 보유기간 기준 `MFE`, `MAE`를 기록한다.
  - 완료된 부분: `MarkToMarketBarProvider` contract(`services/backtest_period_runner.py`)와 `BacktestPeriodRunner` 통합으로, 주입 시 중간 보유일 일중 high/low를 MFE/MAE에 합산한다(미주입 시 기존 동작 유지).
  - 완료된 부분: `StockQueryDailyMtmBarProvider`가 `StockQueryService.get_recent_daily_ohlcv` 기반 일봉 high/low를 `MarkToMarketBarProvider` contract로 제공하고, `scripts/run_backtest.py` 운영 백테스트에 wiring한다.
- [x] backtest-vs-live 괴리 리포트를 운영 판단 지표로 승격한다.
  - 완료된 부분: `StrategyLogReportService`가 live/backtest 표준 journal을 `analyze_strategy_performance_degradation()`에 넣고, `StrategyLogReportTask`가 after-market 성과 저하 후보를 `AlertSource.STRATEGY_PERF`로 알림한다.
  - 완료된 부분: `compare_trade_journals()`의 매칭/미매칭/체결가 괴리 신호를 성과 저하 후보 `backtest_live_divergence` metadata와 `backtest_live_divergence` reason에 추가 연결한다.
- [x] 비용 포함 순수익(`cost`, `net_pnl`, `net_return`)을 기본 성과 기준으로 사용한다.

주요 파일:

- `strategies/backtest_data_provider.py`
- `services/strategy_log_report_service.py`
- `task/background/after_market/strategy_log_report_task.py`
- `repositories/*`
- `utils/transaction_cost_utils.py`

### 0-3. 체결 시뮬레이터와 포트폴리오 장부

- [x] 체결 시뮬레이터를 분리한다: 지정가/시장가, 당일 고가·저가 도달 여부, 거래량 기반 부분체결, 미체결, current/next bar 정책을 명시한다.
- [x] 포트폴리오 단위 현금/보유/예약주문 장부를 만든다. 동시 신호 발생 시 자금 부족, 전략별 max positions, 우선순위 정렬을 재현한다.
- [x] 리스크 게이트와 포지션 사이징을 백테스트에서도 동일하게 호출하거나, 동일 contract의 dry-run 구현으로 검증한다.
- [x] 활성 전략군용 기간 백테스트 runner를 만든다.

주요 파일:

- `services/backtest_execution_simulator.py`
- `services/backtest_period_runner.py`
- `services/backtest_replay_adapter.py`
- `scripts/run_backtest.py`
- `tests/unit_test/services/test_backtest_execution_simulator.py`
- `tests/unit_test/services/test_backtest_period_runner.py`
- `tests/unit_test/services/test_backtest_replay_adapter.py`
- `tests/unit_test/scripts/test_run_backtest.py`
- `strategies/debug/strategy_debug_runner.py`
- `strategies/debug/rejection_report.py`
- `scripts/run_strategy_debug.py`
- `utils/transaction_cost_utils.py`
- `common/trade_journal_schema.py`

### 0-4. Kill Switch 손익 hook 연결 검증

- [x] `KillSwitchService.record_trade_result(profit_won, code, strategy, account_balance_won)` 가 모든 매도 체결/정산 경로에서 호출되는지 검증한다.
- [x] `KillSwitchService.record_strategy_trade_result(strategy_name, pnl_won)` 를 전략 매도 정산 시점에서 호출한다.
  - 현재 상태: 메서드는 구현되어 있으나 호출 지점이 없어 전략 KS가 실제 손익을 인식하지 못함.
  - 연결 후보: `OrderExecutionService` 체결 확정 콜백 또는 `VirtualTradeService` 매도 정산 시점.
  - 전제 조건: P0 `inquire-daily-ccld` 실전 응답 확보 후 체결 확정 시점이 명확해지면 실전 경로까지 연결.
- [x] 매도 체결 완료 → 실현손익 계산 → KillSwitch 기록 → 전략별 연속 손실/일손실 반영 → 한도 초과 시 전략·주문 차단 흐름을 회귀 테스트로 고정한다.

주요 파일:

- `services/kill_switch_service.py`
- `services/order_execution_service.py`
- `services/virtual_trade_service.py`
- `tests/unit_test/services/test_kill_switch_service.py`
- `tests/unit_test/services/test_order_execution_service.py`

### 0-5. 주문 상태와 잔고 대사 E2E 검증

- [x] 실전 fixture 기반으로 주문 접수, 부분체결, 전체체결, 미체결, 취소/거부 상태 전이를 검증한다.
- [x] 주문 접수만으로 보유/체결을 확정하지 않는 기존 FSM 동작을 실전 응답 fixture로 재검증한다.
- [x] 재시작 후 미체결 주문과 잔고 restore/reconcile 결과가 신규 주문 차단 또는 경고 상태로 이어지는지 end-to-end 검증을 보강한다.
- [x] reconcile task 실패 자체가 주문 차단 또는 명시 경고 상태로 이어지는 정책을 운영 매트릭스로 확정한다.
- [x] `OrderStateMachine.safe_transition()` mismatch escalation을 정책화한다.
  - 검토 결과: 부분 타당. `safe_transition()`은 invalid transition/key error를 warning + mismatch count 증가 + no-op으로 처리한다. `FillReconciliationService`에는 mismatch alarm과 2회 연속 mismatch 처리 일부가 있으나, safe transition 실패 자체의 주문별 escalation 정책은 명확하지 않다.
  - 완료된 부분: 같은 주문 1회 warning, 2회 force reconcile flag, 3회 critical 알림 + 수동 해제 필요 reconcile alarm으로 신규 주문 차단을 테스트로 고정한다.
  - 후속 검토: 운영 reset API/CLI가 `reset_reconcile_alarm()`과 `OrderStateMachine.clear_safe_transition_mismatch(order_key)`를 함께 호출하도록 노출한다.

---

## P1. 전략 수익성 검증

전략을 더 추가하기보다 현재 전략의 기대값, MDD, 승률, 손익비, 시장 국면별 성과를 먼저 검증합니다.



### 1-0. 실거래 투입 전 전략별 수익성 통과 기준

- [x] 전략별 “돈 버는 기준선”을 명시하고, 기준 미달 전략은 실계좌 자동주문 대상에서 제외한다.
  - 검토 결과: 타당. 현재 P1에 walk-forward, Monte Carlo, 국면별 성과 검증은 있으나 “통과/탈락 기준”이 약하다. 실전 투입 판단에는 절대 수익보다 비용·슬리피지·미체결 반영 후에도 기대값이 남는지가 핵심이다.
  - 최소 기준 후보: 전략별 충분한 표본 수, out-of-sample Profit Factor, 평균 손익비, MDD, 비용 반영 후 기대값, 슬리피지 2배 스트레스, KOSPI/KOSDAQ 대비 초과수익, 하락장 손실 제한.
  - 산출물: 전략별 `거래 수 / CAGR 또는 기간수익률 / MDD / PF / 승률 / 평균손익비 / 비용·슬리피지 후 성과 / 시장 국면별 성과` 표.
  - 1차 완료: `services/strategy_profitability_gate_service.py` 추가. 표준 journal의 SOLD 기록 기준으로 `min_trades`, Profit Factor, payoff ratio, win rate, 평균 net return, total net PnL, MDD, Monte Carlo ruin probability/worst MDD, regime별 손익 기준을 평가해 `pass` / `fail` / `insufficient_sample`을 반환한다.
  - 1차 완료: `scripts/run_backtest.py --profitability-gate` 옵션 추가. 일반 기간 백테스트는 전체 journal, walk-forward는 test phase journal만 기준선 판정에 사용하고 console/json 출력에 `profitability_gate`를 포함한다.
  - 완료된 부분: `StrategyLiveExpansionGateService`를 추가하고 `StrategyScheduler`에 연결했다. paper/dry-run은 허용하고, real 모드에서는 수익성 gate 결과가 없거나 미통과인 전략의 신규 scan/BUY 주문을 fail-closed로 차단한다. 기존 보유 청산/force-exit 경로는 차단하지 않는다.
- [x] P1 수익성 검증을 실전 확대 gate로 승격한다.
  - 정책: P0 주문 안정성 완료 후에도, P1 수익성 기준을 통과하지 못하면 full-auto/자금 확대 금지. 허용 범위는 backtest, paper/shadow, 제한적 소액 canary까지로 둔다.
  - 완료된 부분: `StrategyFactory`가 표준 journal provider(`VirtualTradeService.get_standard_journal_records`)와 `strategy_profitability_gate` 설정을 사용해 live expansion gate를 scheduler에 주입한다. 향후 backtest gate 결과 저장소가 별도 구축되면 provider만 교체한다.

### 1-1. 과최적화 방지 검증

- [x] 전략별 ablation test를 추가한다.
  - 예: O'Neil PP/BGU의 smart money, execution strength, market timing, BGU/PP 조건을 하나씩 제거해 실제 기여도를 확인한다.
  - 완료된 부분: `services/strategy_ablation_service.py`(`AblationVariant`/`AblationPreset`/`apply_config_overrides`/`ForceMarketTimingOkUniverseWrapper`/`compute_ablation_summary`) 추가. `scripts/run_backtest.py`에 `--ablation`/`--ablation-variants` 옵션과 baseline 대비 metric delta 출력(console/JSON) 연결.
  - 완료된 부분: 활성 7개 전략 전부에 preset 추가 — `oneil_pocket_pivot`(5 variants), `oneil_squeeze_breakout`(4), `high_tight_flag`(5), `first_pullback`(5), `larry_williams_vbo`(4), `rsi2_pullback`(3), `larry_williams_channel_breakout`(4). 각 preset 은 가능하면 `disable_smart_money`/`disable_execution_strength`/`disable_market_timing`/`disable_volume_filter` 패턴을 따르고, 전략 고유 게이트(예: HTF `relax_pattern_check`, FirstPullback `widen_pullback_range`, RSI2 `disable_minervini_stage2`/`disable_rsi_oversold`, CB `disable_adx_filter`/`disable_rs_rating`)는 별도 variant 로 둔다.
- [x] parameter stability surface를 리포트한다.
  - 특정 임계값 하나에서만 성과가 튀는 전략은 실전 후보에서 제외하거나 canary로만 둔다.
  - 완료된 부분: `services/parameter_stability_service.py`(`StabilitySweepDimension`/`StabilitySweepPreset`/`compute_stability_summary`) 추가. baseline 주변 5-point sweep 의 `total_net_pnl` 기준 raw surface 와 `stable`/`spike`/`cliff`/`edge` 분류(가벼운 정책: 인접점 sign-flip 또는 ≥80% 하락 시 cliff, 양쪽 ≥50% 하락 + baseline/이웃평균 ≥2.0 시 spike) 를 출력.
  - 완료된 부분: 활성 7개 전략 전부에 `strategies/<key>_parameter_stability.py` preset(각 3 dimension × 5 sweep 점) 추가 — `oneil_pocket_pivot`(pp_ma_proximity_upper_pct/bgu_gap_pct/execution_strength_min), `oneil_squeeze_breakout`(volume_breakout_multiplier/execution_strength_min/osb_max_extension_pct), `high_tight_flag`(pole_min_surge_ratio/flag_max_drawdown_pct/volume_breakout_multiplier), `first_pullback`(pullback_upper_pct/rapid_surge_pct/execution_strength_min), `larry_williams_vbo`(k_value/confidence_threshold/program_buy_ratio), `rsi2_pullback`(rsi_threshold/hard_stop_pct/take_profit_ma_period), `larry_williams_channel_breakout`(adx_threshold/volume_multiplier/rs_rating_min).
  - 완료된 부분: `scripts/run_backtest.py` 에 `--parameter-stability`/`--parameter-stability-dimensions` 옵션, sweep 점마다 `AblationVariant(config_overrides={dim.parameter: value})` 를 합성해 기존 `make_runner(variant=...)` 재사용. console/JSON 출력에 baseline 대비 dimension 별 metric 표 + stability flag 노출.
  - 완료된 부분: hard gate 통합 — `strategy_profitability_gate` 가 parameter stability summary의 `spike`/`cliff` dimension을 차단 사유(`parameter_stability_<flag>:<dimension>`)로 반영한다. 기본값은 report 결과가 있을 때만 적용하며, `block_parameter_stability_flags=[]` 로 report-only 운용도 가능하다.
- [ ] purged/embargo cross-validation 또는 종목·기간 누수 방지 규칙을 walk-forward 검증에 추가할지 검토한다.
- [ ] Deflated Sharpe / PBO 같은 다중 전략 실험 착시 방지 지표를 도입할지 검토한다.
- [ ] regime-balanced validation을 추가한다.
  - 상승장 데이터에만 최적화된 전략이 횡보/하락장에서 손실을 키우지 않는지 분리 검증한다.

### 1-2. 포트폴리오 단위 리스크 확장

- [ ] 총 노출 한도 외에 포트폴리오 집중도 리스크를 추가한다.
  - 후보: 섹터/테마 집중도, KOSPI/KOSDAQ 비중, 전략 간 상관관계, 시장 베타, 당일 신규 진입 횟수, 연속 손절 후 쿨다운, 장초반/장마감 별도 리스크.
  - 검토 결과: 타당. HTF, VBO, Pocket Pivot이 모두 강한 모멘텀 종목군을 고르면 전략은 여러 개여도 포트폴리오 관점에서는 같은 베팅이 될 수 있다.
- [ ] 전략 간 중복 신호/동일 종목/동일 테마 진입을 포트폴리오 의사결정 단계에서 리포트한다.
  - 1차는 hard block보다 “동시 노출 경고 + journal metadata”로 시작한다.

### 1-3. `TradeSignal` / `PositionSizingService` 수량 contract 표준화

- [x] 전략은 매수 사유, 기준가, 손절 기준, 신뢰도만 생성하고 최종 수량은 `PositionSizingService`가 전담하는 구조로 정리한다.
- [x] 향후 `TradeSignal` 확장 후보를 검토한다: `stop_price`, `risk_pct`, `confidence`.
  - 결정: 본 PR에서는 미도입. `stop_loss_pct`/`atr_multiplier`가 이미 존재해 stop 거리 계산에 충분. 후속 PR에서 `Optional[...]` 추가 시 backward-compat 유지 가능.
- [x] 확장 필드는 즉시 구현하지 않고 sizing 구조 개편 시 backward-compatible migration 계획을 함께 세운다.
  - 계획: 모든 신규 필드는 `Optional[...] = None`으로 추가, 기존 47개 construction site 영향 없음.
- [x] 기존 `qty` 의미를 “전략 상한”으로 유지할지 “요청 수량 없음/무제한”을 표현할지 결정하고 테스트로 고정한다.
  - 결정: `qty: Optional[int] = None`. `None`=sizing 단독 결정, `int`=전략의 자발적 상한. 테스트로 invariant 고정 완료.

주요 파일:

- `common/types.py`
- `services/position_sizing_service.py`
- `scheduler/strategy_scheduler.py`

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
- [~] O'Neil/Minervini 계열 전략 성과를 KOSPI 상승장, KOSDAQ 상승장, 지수 횡보장, 지수 하락장, 거래대금 증가 장세로 분리해 리포트한다.
  - `common/trade_journal_schema.py` 에 `market_regime` 필드 + `SCHEMA_VERSION = 2`. 정규화 함수에 `market_regime` 파라미터(service 호출 금지, record/metadata fallback).
  - `services/regime_performance_service.py` 신규 (순수 함수): KOSPI Bull / KOSDAQ Bull / SIDEWAYS / BEAR / TRADING_VALUE_SURGE 5개 버킷 + MDD(signal_time 정렬 누적 net_pnl).
  - `GET /api/strategies/performance-by-regime?strategy=&from_date=&to_date=` 엔드포인트 추가.
  - 거래대금 급증 장세 버킷은 market-wide aggregate contract 미준비로 1차에서 정의만 유지 — 항상 빈 결과. 후속 PR에서 데이터 소스 확정 후 활성화.

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
- [ ] 실전 체결 품질과 괴리가 큰 백테스트 가정을 별도 고도화 과제로 분리한다.
  - 검토 결과: 부분 타당. `BacktestExecutionSimulator`는 지정가/시장가, current/next bar, 슬리피지, 거래량 기반 부분체결, 비용을 이미 다루지만, bid/ask spread, 호가잔량, market impact, VI/상하한가/거래정지, 미체결 후 취소 정책은 아직 명시 contract가 아니다.
  - 개선 방향: 실전 성과 판단용 runner에서는 next-bar 기본값, 호가/spread/부분체결/취소 fixture를 우선 추가한다.
- [ ] 체결 모델을 한국 주식 실전 제약 기준으로 더 보수화한다.
  - 후보: 호가단위, 부분체결, 미체결 후 취소, 거래대금 bucket별 슬리피지, 9:00~9:10 장초반 체결 악화, VI/상하한가/거래정지, 시장가/지정가/최유리 주문 차이, 매도 체결 실패.

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

성능 개선의 핵심은 후보군 관리 신규 구축이 아니라, 이미 있는 후보군/구독 정책을 전체 전략과 실시간 snapshot 구조에 연결하는 것입니다.

### 2-1. 후보군/구독 정책 공통 파이프라인 확장

- [x] `OneilUniverseService`의 Pool A, Pool B, Watchlist 병합 구조를 O'Neil 계열뿐 아니라 전체 전략 공통 후보군 파이프라인으로 확장할지 설계한다.
  - 결정: generic `CandidateUniverseService` 및 `RankingCacheService` 미도입. 모든 활성 전략(HTF, FirstPullback, LarryWilliamsVBO, RSI2Pullback, LarryWilliamsCB, OSB, PP)이 이미 `OneilUniverseService.get_watchlist()`를 사용하며 `strategy_oneil` 카테고리로 구독 등록됨. O'Neil Pool A/B 구조는 O'Neil 전용 필드를 동반하므로 일반화 불필요.
  - 행동: `VolumeBreakoutLiveStrategy`, `TraditionalVolumeBreakoutStrategy`, `ProgramBuyFollowStrategy` 3개 전략을 스케줄러 등록에서 제거함 (2026-05-05). 전략 클래스 파일과 테스트는 유지.
- [x] `SubscriptionPolicy`의 보유종목, 전략 감시종목, UI 관심종목 우선순위와 참조 카운팅을 전체 전략 구독 정책에 일관 적용한다.
  - 결과: 스케줄러에 등록된 모든 활성 전략이 `get_watchlist()` → `sync_subscriptions("strategy_oneil", MEDIUM)` 경로로 이미 일관 적용됨. `test_get_watchlist_syncs_subscriptions`로 검증됨.
- [x] 기존 후보 부족 관찰 항목은 Pool B 튜닝 후보로 유지하고, 신규 후보군 구축 과제로 중복 등록하지 않는다.

주요 파일:

- `services/oneil_universe_service.py`
- `services/subscription_policy.py`
- `services/price_subscription_service.py`
- `repositories/streaming_stock_repo.py`

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
  - 검토 결과: 타당. 개별 전략의 chunk size/semaphore와 `ForegroundScheduler`는 존재하지만, current price/OHLCV/account/order API 전체를 전략 간 공유하는 전역 rate limiter는 없다. 여러 전략이 동시에 scan/check_exits를 수행하면 순간 호출량이 합산된다.
  - 개선 방향: `ApiBudget` 또는 broker wrapper 레벨 limiter를 shared dependency로 주입하고, 조회/계좌/주문 카테고리별 rate를 분리한다.
  - 테스트: 여러 전략이 동시에 호출해도 카테고리별 limiter가 적용되고 주문 API 우선순위가 보존되는지 검증.
  - 완료된 부분: `core.retry_queue.api_budget_limiter.ApiBudgetLimiter` 추가. `BrokerAPIWrapper`가 shared limiter를 생성해 `ClientWithRetryQueue`에 주입하고, retry queue의 최초 호출/재시도 호출이 모두 limiter를 거치도록 연결했다.
  - 1차 정책: 조회성 API는 `quotation` 기본 동시성 8, 계좌 조회 API는 `account` 기본 동시성 2로 분리. 주문/WebSocket 메서드는 기존 제외 목록을 유지해 budget limiter와 retry queue를 우회한다.
  - 검증: retry queue 단위 88개, retry queue 통합 22개, broker wrapper 단위 33개, 전체 단위 4798개, 전체 통합 233개 통과.
- [x] 활성 전략의 exit check도 bounded gather 또는 순차/우선순위 정책으로 통일한다.
  - 검토 결과: 타당. `FirstPullbackStrategy`, `HighTightFlagStrategy`, `LarryWilliamsChannelBreakoutStrategy` 등 일부 전략은 holdings 전체에 대해 `asyncio.gather()`를 수행한다. VBO/레거시 일부는 순차 처리다.
  - 완료된 부분: `utils/async_concurrency.py::bounded_gather()` 헬퍼 신규 + 단위 테스트 7개. 활성 6개 전략(`FirstPullback`, `HighTightFlag`, `LarryWilliamsChannelBreakout`, `OneilPocketPivot`, `OneilSqueezeBreakout`, `Rsi2Pullback`)의 holdings exit gather를 `bounded_gather(..., limit=_EXIT_CONCURRENCY=15, return_exceptions=True)`로 교체했다. entry chunk_size(10)보다 높여 청산 경로에 우선순위를 부여한다.
  - 미적용: VBO는 이미 sequential `for hold in holdings`로 더 보수적이므로 외과수술적 변경 원칙에 따라 유지.
- [ ] VBO range cache 갱신을 bounded concurrency로 바꾸고 precompute 경로를 검토한다.
  - 검토 결과: 타당. `LarryWilliamsVBOStrategy._refresh_range_cache()`가 후보 코드를 순차 순회하며 `get_recent_daily_ohlcv()`를 호출한다.
  - 개선 방향: semaphore 기반 bounded gather 또는 장 시작 전/Watchlist 생성 시 range precompute.
- [x] VBO fallback 후보의 unknown liquidity를 통과시키지 않는다.
  - 검토 결과: 타당. universe 미주입 fallback에서 `avg_5d_tv=0`을 넣고, validity filter는 `avg_5d_tv > 0 and avg_5d_tv < min`일 때만 탈락시킨다.
  - 완료된 부분: fail-closed reject로 전환. `_passes_validity_filter`에서 `avg_5d_tv <= 0`이면 `avg_trading_value_unknown` reason으로 차단. fallback 경로(`_load_pool_b` API 응답 기반)는 거래대금 검증 없이는 매수 신호를 만들 수 없다. 단위/통합 테스트로 회귀 고정.
- [ ] scan cycle 단위 데이터 공유와 성능 계측을 강화한다.
  - 후보: 동일 종목 current price/OHLCV/conclusion/program trading memoization, strategy scan latency, candidate count, API calls per scan, cache hit ratio, REST fallback ratio, rejected reason distribution, signal-to-order latency, order-to-fill latency.
  - 목표: 성능 개선을 감이 아니라 병목 지표 기반으로 진행한다.
- [ ] 전략별 universe 적합성을 비교한다.
  - 검토 결과: 타당. 활성 전략 다수가 `OneilUniverseService` watchlist를 공유하는 것은 운영상 단순하지만, RSI2 같은 mean-reversion 성격이나 VBO 단기 변동성 전략에 O'Neil universe가 항상 맞는지는 별도 검증이 필요하다.
  - 산출물: Oneil universe vs generic liquidity universe vs strategy-specific prefilter 성과 비교, universe exclusion report.

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

### 2-3. Market snapshot 표준화

- [x] 기존 `PriceStreamService`의 `get_cached_price()` / `cache_price_snapshot()` 구조를 중복 구현하지 않고, 전략 공통 snapshot contract로 승격할 방법을 설계한다.
- [x] WebSocket / REST / DB snapshot 입력을 한곳에서 표준화해 현재가, 거래량, 거래대금, 고가, 저가, 체결강도, 데이터 시각을 같은 형태로 제공한다.
- [x] 전략은 REST 직접 조회보다 snapshot provider를 우선 참조하고, stale/missing reason을 rejected reason으로 남긴다.

주요 파일:

- `services/price_stream_service.py`
- `services/execution_flow_service.py`
- `services/data_quality_service.py`
- `view/web/web_app_initializer.py`

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
- [ ] PR-3 선행: event-driven signal sink를 명확히 한다.
  - 검토 결과: 타당. `PriceStreamService.on_price_tick()`은 `loop.create_task(router.on_price_tick(...))`로 호출하고 반환된 signal list를 소비하지 않는다. 현재 PR-2 shadow mode는 의도적으로 실주문을 막기 때문에 문제 없지만, live 전환 전에는 signal queue/order intent bus 같은 명시 sink가 필요하다.
  - 개선 방향: `StrategyEventRouter`가 list 반환에 의존하지 않고 `await signal_sink.publish(signal)` 또는 `await signal_queue.put(signal)`로 전달하도록 contract를 정한다.
- [ ] PR-3 선행: EventRouter throttle을 threshold crossing 또는 signal debounce 정책으로 보강한다.
  - 검토 결과: 부분 타당. 현재 throttle은 evaluator 실행 전 `(strategy, code)` 단위 0.5초 차단이다. 돌파 조건 crossing tick이 throttle window 안에 들어오면 평가 자체가 지연될 수 있다.
  - 개선 방향: trigger price crossing은 evaluator 실행을 허용하고, 중복 signal 발행만 debounce하는 방향을 검토한다.
- [ ] PR-4+: 단계적 확장.

구현 결정 사항 (`docs/event_driven_architecture.md` §9, 2026-05-18 확정):

- [x] (Q1) `(strategy, code)` event throttle = 0.5초 (`StrategyEventRouter(throttle_sec=...)`).
- [x] (Q2) Stale snapshot 임계 = 5초 (`StrategyEventRouter(stale_snapshot_sec=...)`).
- [x] (Q3) Shadow mode 운영 기간 = VBO 1주 (5 거래일).
- [x] (Q4) `signal_source` 저장 = `metadata` JSON 키 (DB schema 변경 없음).

---

## P3. 유지보수성

기능이 늘어난 만큼 초기화 조립부와 주문 실행부의 책임을 줄여 테스트와 리팩토링 누락 위험을 낮춥니다.

### 3-1. `WebAppContext` / `web_app_initializer` 분리

- [x] 환경 로드, 브로커 생성, 서비스 조립, 전략/태스크 등록, 알림 설정, 스케줄러 초기화를 단계별 bootstrap으로 분리한다.
  - `ConfigBootstrap`, `BrokerBootstrap`, `ServiceContainer`, `SchedulerBootstrap`, `StrategyFactory`, `WiringPhase` 추출 완료 (`view/web/bootstrap/`).
  - `WebAppContext` 의 5개 초기화 메서드 (`load_config_and_env`, `_bootstrap_broker`, `_bootstrap_services`, `_bootstrap_schedulers`, `initialize_scheduler`) 모두 신규 모듈에 위임만 한다.
  - `web_app_initializer.py` 1238라인 → 약 530라인 (57% 감소).
  - `WebBootstrap` (전체 orchestrator) 는 도입하지 않음 — `initialize_services()` 가 이미 14줄 thin orchestrator 라 추가 모듈마트 분리 시 의미있는 커플링 감소 없음.
- [x] `BrokerFactory`, `ServiceContainer`, `StrategyFactory`, `SchedulerBootstrap`, `WebBootstrap`, `ConfigBootstrap` 분리 후보를 검토한다.
  - `ConfigBootstrap`, `BrokerBootstrap` (`BrokerFactory` 역할), `ServiceContainer`, `SchedulerBootstrap`, `StrategyFactory` 5개 도입.
  - `WebBootstrap` 은 비용 대비 효익이 낮아 won't fix.
- [x] 후주입 방식 서비스 연결을 줄이고, 누락 시 테스트에서 빨리 드러나도록 생성 contract를 명확히 한다.
  - `WiringPhase` 추출 (`view/web/bootstrap/wiring_phase.py`) — 후주입 14개 wire 를 한곳에 모음. `ServiceContainer.run()` → `WiringPhase.run()` 순서로 위임.
  - 직접 속성 변경 3곳을 setter 로 교체: `MarketDataService.set_data_quality_service`, `MinerviniStageService.set_minervini_update_task`, `MinerviniUpdateTask.set_daily_price_collector_task`.
  - 14개 wire 마다 단위 테스트 추가 (`tests/unit_test/view/web/bootstrap/test_wiring_phase.py`) — 누락 시 즉시 실패한다.
  - 진성 순환 의존 자체의 생성자 주입 전환은 consumer API 변경을 수반하므로 별도 PR 로 분리한다 (이번 영역 외).

주요 파일:

- `view/web/web_app_initializer.py`
- `view/web/web_main.py`
- `view/web/bootstrap/config_bootstrap.py` (신규)
- `view/web/bootstrap/broker_bootstrap.py` (신규)
- `view/web/bootstrap/service_container.py` (신규)
- `view/web/bootstrap/scheduler_bootstrap.py` (신규)
- `view/web/bootstrap/strategy_factory.py` (신규)
- `view/web/bootstrap/wiring_phase.py` (신규)
- `tests/unit_test/view/web/test_web_app_initializer.py`
- `tests/unit_test/view/web/bootstrap/test_config_bootstrap.py` (신규)
- `tests/unit_test/view/web/bootstrap/test_broker_bootstrap.py` (신규)
- `tests/unit_test/view/web/bootstrap/test_service_container.py` (신규)
- `tests/unit_test/view/web/bootstrap/test_scheduler_bootstrap.py` (신규)
- `tests/unit_test/view/web/bootstrap/test_strategy_factory.py` (신규)
- `tests/unit_test/view/web/bootstrap/test_wiring_phase.py` (신규)

### 3-2. 웹 / 운영 / 전략 runtime 경계 분리

- [x] 웹 화면/API, 장중 전략/주문 실행, 장마감 데이터 수집/리포트, 수동 운영 점검 runtime 경계를 명확히 한다.
  - 1차 완료: `view/web/bootstrap/runtime_mode.py` 의 `RuntimeMode` enum (WEB / TRADING / BATCH / ALL) 로 15 개 task (`SchedulerBootstrap` 등록 14 + `StrategyFactory` 등록 1) 의 runtime 소유권 명시.
  - 1차 완료: `SchedulerBootstrap.run()` 을 `_register_web_tasks`/`_register_trading_tasks`/`_register_batch_tasks`/`_register_websocket_watchdog` 4 개 그룹 메서드로 분리. websocket_watchdog 은 WEB | TRADING 양쪽 mode 에서 1 회만 등록.
  - 1차 완료: `StrategyFactory.build()` 가 TRADING 비활성 시 즉시 return (StrategyScheduler / 7 개 전략 / Adapter 모두 미생성).
  - 2차 완료: `ServiceContainer` 가 runtime mode 에 따라 task 생성을 일부 분기한다. BATCH 단독은 realtime chain(`StreamingService`, `PriceStreamService`, `PriceSubscriptionService`, `WebSocketWatchdogTask`)과 WEB/TRADING task를 만들지 않고, WEB 단독은 realtime chain + `NotificationQueueTask`만 유지하며 장중/장마감 task를 만들지 않는다. `WiringPhase` 는 realtime chain 이 없는 BATCH 단독 컨텍스트를 no-op 으로 통과한다.
  - 완료: `TRADING` 단독 `ServiceContainer` contract 테스트 추가. TRADING 은 realtime chain + 장중 task(`PreMarketHealthCheckTask`, `OpeningPositionReconcileTask`, `CacheWarmupTask`)를 만들고, WEB 알림 task와 장마감 task는 만들지 않는다.
  - 완료: `web_app.py`, `trading_runtime.py`, `batch_runtime.py`, `admin_runtime.py` 진입점 추가. 각 진입점은 `runtime_entrypoint.py` 를 통해 `RUNTIME_MODE` 를 명시하고 기존 FastAPI bootstrap 으로 위임한다. admin runtime 은 현재 WEB surface 를 사용하되 TRADING/BATCH scheduler 를 비활성화한 운영 점검 모드로 정의한다.
  - 결정: `OrderExecutionService`, `StockQueryService`, `OneilUniverseService`, `RankingTask` 같은 도메인/조회 서비스는 mode 별 task 등록과 별개인 공통 조립 계층으로 유지한다. API surface, 전략 생성, background task 등록이 mode boundary 를 담당한다.
- [x] 웹 서버 초기화가 모든 after-market task와 장중 scheduler를 직접 끌어안지 않도록 분리한다.
  - 1차 완료: `web_main.py` lifespan 이 `RUNTIME_MODE` env (default `ALL` = 현행 동작 100% 유지) 를 읽어 `WebAppContext(runtime_mode=...)` 로 주입. mode 별로 task 등록과 StrategyScheduler 생성을 분기.
  - 1차 완료: `BackgroundScheduler` / `ForegroundScheduler` 객체 생성은 mode 와 무관하게 항상 수행 (foreground middleware 가 rate-limit 경합 제어에 의존).
  - 1차 완료: 가격 구독 초기화 (`_initialize_price_subscriptions`) 는 WEB | TRADING 에서만 호출. BATCH 단독에서는 생략.
  - 안전성 정책: `restore_state_from_broker` / `reconcile_orders_with_broker` 는 WEB | TRADING 어느 한쪽이라도 켜져 있으면 항상 호출 (`/api/order` 가 WEB 에서 살아 있는 한 stale 주문 상태 위험 차단). "WEB_ONLY = read-only" 정책 확정 시에만 TRADING 단독 gate 로 좁힐 수 있음.
  - 완료: 별도 runtime 진입점 파일을 제공해 프로세스 매니저가 WEB/TRADING/BATCH/Admin 을 분리 실행할 수 있게 했다. cross-runtime IPC/event bus 는 현재 단일 DB/log 기반 운영에 불필요하므로 3-2 완료 범위에서 제외하고, 다중 프로세스 간 실시간 상태 공유가 필요해질 때 별도 P3/P4 항목으로 승격한다.

분리된 진입점:

- `web_app.py` — 화면/API
- `trading_runtime.py` — 장중 전략/주문 실행
- `batch_runtime.py` — 장마감/데이터 수집/리포트
- `admin_runtime.py` — 수동 조작/점검 (현재 WEB surface + TRADING/BATCH 비활성)

### 3-3. 주문 서비스 역할 분리

- [x] `OrderExecutionService`에서 validator, risk, sizing, submit, state machine, fill reconciliation, execution quality reporting 책임을 단계적으로 분리한다.
  - PR #412 (5 phase) 완료. `OrderExecutionService` 2006 → 697줄 (-1309, 65% 축소). facade 패턴 유지로 view/web/routes/, scheduler/, web_app_initializer.py 변경 0줄.
- [x] 주문 수량 상한, 중복 주문 방지 키, 같은 전략/같은 종목 동시 재진입 방지, 체결 확인/잔고 반영 분리 기준을 명확히 한다.
  - 양방향 차단 (buy 진행 중 sell, sell 진행 중 buy) 은 `OrderStateMachine.lookup_by_side` 로 분리.
  - intent 중복은 `OrderStateMachine.intent_to_order_key` / `register_intent`.
  - 체결 확인/잔고 반영은 `FillReconciliationService.apply_execution_report` / `handle_signing_notice` 로 분리.
- [x] 분리 전후로 기존 주문 안전장치 테스트가 그대로 통과하도록 단위 테스트를 먼저 보강한다.
  - Phase 0 에서 양방향 차단 + 동시 호출 직렬화 2개 테스트 선보강. 모든 Phase 종료 후 단위 4615 + 통합 233 GREEN.

도입된 서비스 (분리 후보 vs 실제):

- `ExecutionQualityReporter` ✅ (`services/execution_quality_reporter.py`, 281줄)
- `BrokerOrderSubmitter` ✅ (`services/broker_order_submitter.py`, 170줄) — 원안의 `OrderSubmitter`
- `OrderStateMachine` ✅ (`services/order_state_machine.py`, 311줄)
- `FillReconciliationService` ✅ (`services/fill_reconciliation_service.py`, 860줄)
- `OrderSubmissionCoordinator` ✅ (`services/order_submission_coordinator.py`, 350줄) — 원안에는 없던 코디네이터. validator(`OrderValidator`)·`RiskGateAdapter`·`PositionSizingAdapter` 별도 클래스 추출 없이 Coordinator 가 `data_quality_service`, `order_policy_service`, `risk_gate_service`, `position_sizing_service` 를 직접 호출하는 방식으로 흐름만 분리. 별도 어댑터 도입 비용 대비 효익이 낮아 won't fix.

### 3-4. 전략 공통 lifecycle/state contract

- [ ] 활성 전략의 공통 scan/check_exits/state save/load 패턴을 base class 또는 helper로 추출할지 설계한다.
  - 검토 결과: 타당. O'Neil/HTF/First Pullback/RSI2/Larry Williams 계열에 watchlist 조회, market timing, candidate filtering, chunked entry, unbounded exit, state save/load 패턴이 반복된다.
  - 개선 방향: 바로 대형 리팩토링하지 말고 `bounded_gather`, `ensure_state_loaded`, `save_state_atomic` 같은 작은 공통 helper부터 도입한다.
- [ ] `strategy_id`와 `display_name`을 분리한다.
  - 검토 결과: 타당. `strategy.name` 값이 한국어 display name(`오닐PP/BGU`, `오닐스퀴즈돌파` 등) 또는 영문 display name(`Larry Williams VBO`)으로 TradeSignal/RiskGate/log/config key에 사용된다.
  - 개선 방향: 설정·DB·journal·risk limit은 stable `strategy_id`, UI/로그 문구는 `display_name`을 사용하도록 backward-compatible migration을 설계한다.
- [x] 직전 거래일 계산을 `MarketCalendarService` 기준으로 통일한다.
  - 검토 결과: 타당. `OneilUniverseService`, `OneilPocketPivotStrategy`, `FirstPullbackStrategy`, `MarketDataService` 일부 경로에서 `now - timedelta(days=1)`을 전일 기준으로 사용한다.
  - 완료된 부분: `common.date_utils.previous_trading_day_str(now, holidays=None)` sync helper 추가. 4개 사용처(`OneilUniverseService._analyze_surge_candidate`, `OneilPocketPivotStrategy._check_entry`, `FirstPullbackStrategy._check_entry`, `MarketDataService.get_ohlcv`)를 helper로 통일. 주말(토/일) 우회 + optional `holidays` set 지원. 단위 테스트로 weekday/주말/공휴일/시각 무관/`date` 입력 회귀 고정. `MarketCalendarService` async 메서드 추가는 의존성 주입 부담이 커서 보류 — 호출자가 휴장일을 인지할 때 `holidays` 인자로 전달하도록 했다.
- [ ] `TradeSignal` contract를 분석/운영 기준으로 확장한다.
  - 후보 필드: `signal_id`, `strategy_id`, `entry_reason`, `invalidation_price`, `stop_loss`, `target` 또는 trailing rule, `expected_holding_period`, `confidence`, `required_data`.
  - 검토 결과: 타당. 현재 `TradeSignal`은 `reason`, `strategy_name`, `stop_loss_pct`, `atr_multiplier`, `volatility_20d_annualized`를 갖지만, 중복 신호 식별과 사후 분석에 필요한 표준 필드는 아직 부족하다.
- [ ] 활성/실험/레거시 전략의 디렉터리 또는 registry 경계를 명확히 한다.
  - 후보 구조: `strategies/active`, `strategies/experimental`, `strategies/deprecated`, `strategies/legacy`.
  - 1차 대안: 파일 이동 없이 strategy registry metadata로 active/experimental/deprecated 상태를 명시하고, 실행 가능한 전략은 config에서 명시적으로만 로드한다.
- [ ] 설정 변경 통제를 도입한다.
  - 후보: config version, trade journal 내 config hash, 장 시작 전 config diff log, production config lock, dry-run config validation.
  - 목표: 어떤 설정으로 어떤 주문이 나갔는지 사후 재현 가능하게 만든다.

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
- [~] 성과 악화 시 신규 진입 중단, 수량 축소, paper mode 전환, 관리자 알림 후보로 표시한다.
  - 1차 구현은 soft signal만 산출하며 실제 자동 차단, 수량 변경, paper 전환은 실행하지 않는다.
  - 후보 metadata에 recommended_actions를 포함한다.
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

### 4-2. Rejected reason 리포트

- [x] 거래량 부족, 거래대금 부족, Stage Guard 탈락, RS Rating 부족, 시장 타이밍 OFF, RiskGate 차단, 현금 부족, 동일 종목 재진입 차단, 호가/체결강도 불량을 rejected reason으로 표준화한다.
  - `services/strategy_log_report_service.py` `_REASON_KR`에 `insufficient_trading_value`, `rs_rating_low`, `market_timing_off`, `risk_gate_blocked`, `insufficient_cash`, `duplicate_entry_blocked`, `stage_blocked` 추가
  - `strategies/debug/rejection_collector.py` `CAPTURED_EVENTS`에 `liquidity_blocked` 추가
  - `strategies/debug/rejection_report.py` `_STAGE_LABELS`·`_event_label()` 업데이트
  - `strategies/strategy_executor.py` reason 문자열 → canonical code 교체 (`min_trading_value_won` → `insufficient_trading_value`, `min_avg_volume` → `insufficient_volume`)
- [x] 전략별/일자별 rejected reason 분포를 리포트한다.
  - `services/rejection_distribution_service.py` 신규: `record()` / `get_distribution()` / `flush_to_file()` / `attach_to_strategy_logger()`
  - `task/background/after_market/strategy_log_report_task.py`: 장 마감 후 `flush_to_file()` 호출
  - `view/web/web_app_initializer.py`: 서비스 초기화 및 `attach_to_strategy_logger()` 연결
  - 저장 경로: `logs/strategies/rejections/YYYYMMDD.jsonl`
- [x] `run_strategy_debug`와 운영 대시보드에서 rejected reason을 같은 필드명으로 표시한다.
  - `view/web/routes/strategy_report.py` 신규: `GET /api/strategies/rejected-reasons?strategy=&date=YYYYMMDD`
  - 필드명 `reason_code`, `count`, `label_kr`, `strategy`로 `format_json()` 규약과 통일

### 4-3. 운영 대시보드와 알림

- [x] OperatorAlertService(dedup·전이 이벤트), 운영자 대시보드 페이지(/operator), Kill Switch·Risk Gate·Reconcile·WebSocket Watchdog를 operator_alert_service에 연결
- [x] 알림은 신규 차단, 위험도 상승, 자동 해제/복구를 구분해 중복 발송을 줄인다.
- [x] 전략별 성과 저하 알림 — `StrategyLogReportTask`에서 `AlertSource.STRATEGY_PERF`로 연결
- [x] 데이터 품질 차단 알림 — `DataQualityService` 위반율 집계(N건/M초) 후 임계값 초과 시 `AlertSource.DATA_QUALITY`로 연결
  - 기본값: 동일 reason 위반 60초 내 5건 이상, 동일 reason 알림 cooldown 60초.
  - `WebAppContext`에서 `DataQualityService`에 `operator_alert_service`를 주입한다.

### 4-4. 실전 운영 Runbook / Canary 절차

- [ ] 실전 운영 runbook을 작성한다.
  - 포함: 장 시작 전 체크리스트(토큰, 잔고, 포지션, 데이터 연결, WebSocket), 장중 장애 대응(API 오류, 주문 지연, 미체결, stale data), Kill Switch 발동 후 절차, 재개 조건, 배포 체크리스트, 사고 리포트 템플릿.
- [ ] 실계좌 canary 절차를 문서화한다.
  - 정책: P0 완료 + P1 기준선 통과 전 full-auto 금지. 소액 canary는 종목 수/주문금액/일손실/연속 손실/미체결 시간 한도를 별도로 낮게 둔다.
  - 산출물: canary 진입 조건, 중단 조건, 관찰 기간, 승격 조건.
- [ ] 배포 전 dry-run 운영 점검을 자동화한다.
  - 후보: config validation, broker token/account/env consistency, WebSocket subscription health, latest trading date, account snapshot freshness, event shadow status.

---

## Strategy Log 남은 작업

### Pool B 튜닝 관찰

- [ ] 후보 부족 현상이 재발하면 거래대금 기준을 50억에서 30억으로 추가 완화할지 검토한다.
- [ ] 후보 부족 현상이 재발하면 정배열 조건을 Pool B 전용으로 `current > ma_20d` 중심으로 완화할지 검토한다.

---

## 바로 착수 추천 순서

1. P0/P1 백테스트 신뢰도 (대부분 `[blocked]` — 실전 fixture 미확보)
   - 실전 체결 이력 fixture 확보 및 민감정보 제거 (blocked)
   - 실전 fixture 기반 주문번호, 종목코드, 매수/매도, 체결/미체결/취소/거부 필드 매핑 확정 (blocked)
   - 장중 후보 종목의 프로그램매매 WebSocket 캡처 샘플 확보 (blocked)
   - 실제 replay fixture에 캡처 overlay를 결합해 통과 케이스 고정 (blocked)

2. P1 전략 수익성
   - 전략별 수익성 통과 기준선 정의
   - 비용·세금·슬리피지·미체결 반영 후 성과표 작성
   - 과최적화 방지(ablation, parameter stability, regime-balanced validation)
   - 포트폴리오 집중도/전략 상관 리스크 리포트
   - 전략별 universe 적합성 비교

3. P2 시스템 성능
   - 전역 API budget limiter 도입
   - VBO range cache bounded parallel 처리
   - exit check bounded gather 통일
   - event-driven shadow parity/false positive/full gate 차이 로그화
   - scan/API/cache/fallback/latency metric 정리

4. P3/P4 유지보수와 운영 품질
   - 전략별 성과 저하 감지 지표 집계 (완료)
   - `WebAppContext` 분리 (완료)
   - ServiceContainer / Factory 도입 (완료)
   - `OrderExecutionService` 역할 분리 (완료 — PR #412)
   - 남은 항목: 전략 state/lifecycle contract, `strategy_id`/`display_name` 분리, 직전 거래일 계산 통일
   - TradeSignal contract, config hash/version, 운영 runbook/canary 절차

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

## HTF 전략 추가 개선 (차후 검토)

- [ ] **VCP 타이트함 검증**: 깃발 최근 3~5일 일변동폭(고가-저가) 평균이 깃대 구간 변동폭 대비 현저히 축소되었는지 `_detect_pole_and_flag`에 추가
- [ ] **돌파 확인 지연**: pole_high 돌파 후 3~5분간 가격 유지 확인 로직 (현재는 +0.5% 버퍼로 대체)
- [ ] **깃발 기간 20MA 지지**: 횡보 구간 최저점이 20일선을 심각하게 훼손하지 않았는지 확인
- [ ] **이격도 모니터링**: 매수 시그널 발생 시 `(current/pole_high - 1) * 100`을 metrics 로그·대시보드에 노출
