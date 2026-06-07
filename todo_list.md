# Investment Trading App - 남은 To-Do

최종 업데이트: 2026-05-31 (성능 리뷰 follow-up 반영)

이 문서는 현재 남은 실행 항목만 추린 목록이다. 완료된 구현 상세, 완료 체크 항목, 과거 세션 요약은 제거한다. 100% 종료된 섹션(`[x]` only, follow-up 없음)은 git history 로 추적하고 본 문서에서 삭제한다.

정리 원칙:

- 실전 계좌 보호와 주문 안정성을 최우선으로 둔다.
- 이미 적용된 항목은 새 기능으로 다시 넣지 않고, 검증/고도화 과제로만 남긴다.
- 후보군 관리는 신규 구축 과제가 아니라 기존 `OneilUniverseService` / `SubscriptionPolicy` 구조의 공통 파이프라인 확장 과제로 본다.
- 주문/브로커/스케줄러 변경은 테스트 hang 가이드와 paper/real 분기 검증을 함께 적용한다.
- `VolumeBreakoutStrategy`, `VolumeBreakoutLiveStrategy`, `TraditionalVolumeBreakoutStrategy`, `GapUpPullbackStrategy`, `ProgramBuyFollowStrategy`는 현재 사용하지 않는 전략이므로 신규 연결/개선 우선순위에서 제외한다.

남은 실행 영역 요약:

- P0 0-1: 실전 체결 이력 fixture 회귀 잠금 완료, broker order mapper 분리 후속.
- P0 0-7: ~~canary 5% 노출 제한을 코드/config profile로 분리하고 real 기본 30%와 운영 혼동을 제거.~~ ✅ 완료 (2026-05-27).
- P0 0-8: ~~라이브 전략 일봉 지표에서 당일 미완성 봉 제외 옵션을 도입한다.~~ ✅ 완료 (2026-05-28, #478 + rollout 조사). 지표 API + RSI2 + ATR sizing 적용. rollout 전수 조사 결과 추가 오염 경로는 `LarryWilliamsChannelBreakout`(ADX/채널)뿐이라 `_confirmed_bars()`로 마감.
- P0 0-9: ~~라이브 손절/익절 판단을 gross PnL이 아닌 비용 반영 net PnL 기준으로 통일한다.~~ ✅ 완료 (2026-05-28, #479).
- P0 0-10: ~~같은 사이클/이전 사이클의 신규 BUY에 대해 pending/reserved cash와 동일 종목 전 전략 합산 노출을 qty cap에 반영한다.~~ ✅ 완료.
- P0 0-11: ~~kill switch 및 sync fallback state 저장을 atomic write로 통일한다.~~ ✅ 완료 (2026-05-28, #481).
- P1 1-5: 한국장 microstructure fixture 로 체결 모델 보수성 검증.
- P1 1-6: 실전 journal/shadow/paper/live 성과 데이터로 profitability gate의 실제 통과 근거 확보.
- P1 1-7: formal Deflated Sharpe Ratio 구현 완료(2026-06-08, proxy 병행). 남은 것은 formal PBO(CSCV)·walk-forward/purged·ablation 자동 리포트 + 수익률 모멘트 metric.
- P2 2-2: ~~실제 KIS 계정별 REST/WebSocket 유량 한도 재확인 후 budget 기본값 보정.~~ 전역 normal 8/s + emergency 2/s 기본값 보정 완료. 계정별 공식 한도 재확인은 운영 전 외부 확인으로 유지.
- P2 2-4: VBO shadow 5거래일 jsonl 수집 → polling parity 비교. event-driven live order는 별도 승인 전 No-Go.
- P2 2-5: ~~전략 scan의 종목별 현재가 REST 호출을 batch quote / WebSocket snapshot 중심으로 줄인다.~~ helper(`StockQueryService.prefetch_prices`) + rsi2 포함 활성 전략 7개 scan 배선 + 테스트 완료.
- P2 2-6: 라이브 핫패스 성능 follow-up. ~~WebSocket 틱 루프 상수 캐싱/로그 lazy 처리~~ ✅, ~~장마감 배치 `iterrows()` 축소~~ ✅, ~~HTTP/token 미세 정합성 개선(fallback Limits·Timeout / token singleflight / retry jitter)~~ ✅ (2026-05-31). 남은 것: 활성 전략 `scan()`/`check_exits()` 순차 후보 처리 bounded 전환 — 실전 진입/청산 경로라 별도 승인 후 진행(VBO scan은 실익 marginal로 보류 결정).
- P3 3-4: active strategy lifecycle contract 최소 공통 단계 강제 여부 재설계(현재 보류).
- P3 3-5: backtest/live 호가단위 tick-size 로직을 단일 utility로 통일한다.
- P3 3-6: ~~IndicatorService 계산 경로의 광범위 `except Exception` silent skip에 ERROR log/metric/alert hook을 붙인다.~~ ✅ 완료. 전략 레이어 per-code fail-rate metric도 `scan_metrics`/`exit_metrics`에 반영 완료.
- Pool B 튜닝: 후보 부족 재발 시 거래대금/정배열 조건 완화 검토.
- 완료 기준의 전략 성과 `[~]`: `MomentumStrategy` 등 비활성 백테스트 경로의 표준 journal 통합 여부 결정.

---

## P0. 실전 손실 방지

### 0-1. 실전 KIS `inquire-daily-ccld` 응답 필드 검증

- [x] 실제 체결 이력이 있는 실전 계좌 응답을 캡처한다. (2026-06-01, `001510`, KRX, 매수/매도 체결 2건)
- [x] 민감정보를 제거한 fixture를 추가한다. (`tests/fixtures/kis/inquire_daily_ccld_output1_real_20260601_001510.json`; 주문번호/지점/직원/IP/연락처 마스킹)
- [x] paper fixture와 real fixture의 필드 차이를 회귀 테스트에 반영한다. (`test_order_query_report_from_kis_inquire_daily_ccld_fixture`가 실전 fixture를 자동 discovery)
- [~] 주문번호, 종목코드, 매수/매도 구분, 주문수량, 누적체결수량, 미체결수량, 평균체결가 매핑을 실전 체결 fixture로 고정했다. 취소/거부 실전 row는 아직 미확보라 synthetic fixture 보강 상태를 유지한다.
- [x] 실전 KRX 조회에서 `EXCG_ID_DVSN_CD=KRX`를 보내도록 계좌 체결/미체결 조회 파라미터를 고정한다.
- [~] 주문 접수 응답의 broker order number mapper를 실전/모의 fixture 기반으로 확장한다.
  - 적용 완료: 주문번호 추출 실패 시 raw payload 보존 + `FillReconciliationService.on_broker_order_no_missing` → 운영자 CRITICAL 알림 + 다음 reconcile 사이클까지 신규 주문 차단.
  - 적용 완료: `BrokerOrderResponseMapper` 별도 클래스로 분리하고 submit response / order query / signing notice normalize 경로를 이 클래스로 통일했다. 기존 `OrderExecutionReport.from_order_query()` / `from_signing_notice()`와 `OrderStateMachine.extract_broker_order_no()`는 호환 wrapper로 유지한다.
  - 테스트 고정: `ordno`, `order_no`, `odno`, `ORDNO`, `ORDER_NO`, `ODNO`, `ODER_NO`, `주문번호`, 중첩 `output` payload, 실전 `inquire-daily-ccld` fixture row, 체결통보 payload 키.
  - 후속: 실전 submit response/signing notice raw fixture는 추가 확보 필요. 현재 submit/signing normalize는 대표 KIS shape 단위 테스트와 실전 order query fixture로 고정했다.
  - 리뷰 판단: 추출 실패 시 신규 주문을 막는 방어와 독립 mapper 구조는 확보했다. 남은 리스크는 실전 submit/signing payload 원본 표본 부족이다.

주요 파일:

- `brokers/korea_investment/korea_invest_account_api.py`
- `brokers/korea_investment/korea_invest_trading_api.py`
- `brokers/broker_api_wrapper.py`
- `services/order_execution_service.py`
- `services/fill_reconciliation_service.py`
- `common/broker_order_response_mapper.py`
- `utils/kis_inquire_daily_ccld_fixture_utils.py`
- `tests/fixtures/kis/inquire_daily_ccld_output1_real_20260601_001510.json`

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

### 0-8. 라이브 일봉 지표에서 당일 미완성 봉 제외 — ✅ 완료 (#478, 2026-05-28)

- [x] `IndicatorService.get_rsi()`, `get_moving_average()`, `calculate_atr()`에 `exclude_today: bool = False` 옵션을 추가했다.
- [x] 라이브 전략 호출 경로는 기본적으로 당일 미완성 봉을 제외하도록 변경한다.
  - 적용 완료: RSI(2) 경로(`RSI2PullbackStrategy`)와 ATR 기반 sizing(`PositionSizingService` `exclude_today=True`).
  - 적용 완료(rollout 조사 후): 나머지 활성 전략을 전수 조사한 결과, 당일 미확정 봉이 라이브 신호를 오염시키는 곳은 `LarryWilliamsChannelBreakoutStrategy` 하나뿐이었다. 유일하게 `get_ohlcv(period="D")`(장중 당일봉 병합)를 ADX(`calc_adx_sync`)/채널 하단(`_calc_channel_low`) 계산에 그대로 넘기던 경로를 `_confirmed_bars()`(마지막 행 date==오늘이면 제외)로 차단했다. 진입(`_check_entry`)·청산 trailing 갱신(`check_exits`) 양쪽 적용. 돌파 트리거(`current > item.high_20d`, 확정 watchlist 값)는 불변.
  - 조사 결과(미적용 사유): `RSI2Pullback`=이미 처리, `FirstPullback`=진입 `end_date=어제`/exit `get_recent_daily_ohlcv` DB-first 확정봉, `OneilSqueezeBreakout`=exit DB-first 확정봉, `OneilPocketPivot`=`today_candle` 의도적 병합, `HighTightFlag`=`get_recent_daily_ohlcv` DB-first 확정봉, `LarryWilliamsVBO`=전일 range only. 핵심: `get_recent_daily_ohlcv`는 장중 DB-first라 확정봉만 반환하고, `get_ohlcv`만 당일 미확정 봉을 붙인다.
  - 테스트 고정: `test_larry_williams_channel_breakout_strategy.py`의 `test_check_entry_excludes_today_bar_from_adx_and_channel`, `test_check_exits_trailing_update_excludes_today_bar`. 단위 5737 passed / 통합 240 passed.
- [x] 차트/리포트 UI 등 장중 현재 봉 표시가 필요한 경로는 기본값 `exclude_today=False`로 기존 동작을 유지한다.
- [x] 테스트: 장중 today row 병합 시에도 라이브 지표 호출이 전일 확정 봉까지만 사용함을 `test_indicator_service.py`에 고정했다.

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

### 0-9. 라이브 exit 판단을 net PnL 기준으로 통일 — ✅ 완료 (#479, 2026-05-28)

- [x] 활성 전략의 손절/익절 판단을 gross 비교에서 `TransactionCostUtils.net_return_pct()` 기반 net return 비교로 전환했다.
- [x] `TransactionCostUtils`의 수수료/세금 기본값을 라이브 exit 판단에서도 공통 사용한다 (`net_return_pct`/`calculate_net_pnl_won`).
- [x] gross/net 표시 구분을 strategy exit 경로/reason에 반영했다.
- [x] 테스트: gross 미발동·net 발동 경계 케이스를 전략 테스트에 고정했다 (`test_rsi2_pullback_strategy.py` 등).

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

### 0-10. pending/reserved cash와 cross-strategy same-symbol 노출 반영 — ✅ 완료

- [x] `PositionSizingService`에 같은 사이클 내 accepted BUY의 reserved cash 누적기(`_reservations`)를 도입하고 `available_cash`에서 차감한다.
- [x] 가용 현금 계산에서 broker snapshot 보유 + 같은 배치 accepted BUY reservation을 합산 차감한다.
- [x] 전 전략 합산 동일 종목 노출 상한은 qty cap으로 반영한다. `OrderExecutionService.get_pending_buy_exposure()`가 활성 BUY 미체결 잔량 노출을 계산하고, `PositionSizingService`가 이를 기존 보유분에 더해 `cap_qty`를 산정한다.
- [x] 테스트: 동시 BUY 시 reserved cash 차감으로 후속 주문 수량이 줄거나 차단되는 경로를 `test_position_sizing_service.py`에 고정했다.
- [x] 테스트: 이전 사이클 활성 BUY가 같은 종목 cap을 줄이거나 소진하는 경로와, terminal/sell/order exchange mismatch 제외 경로를 고정했다.

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

### 0-11. 상태 파일 저장 atomic write 통일 — ✅ 완료 (#481, 2026-05-28)

- [x] 공통 `utils/atomic_json.py`(`write_json_atomic`)를 추출해 `StrategyStateIO`와 `KillSwitchService`에 적용했다.
- [x] 활성 6개 전략의 sync fallback `_save_state()` 경로도 `write_json_atomic`을 사용하도록 바꿨다.
- [x] 테스트: 저장 중 예외 발생 시 기존 JSON이 truncate 되지 않음을 `test_atomic_json.py` / `test_kill_switch_service.py`에 고정했다.

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
- [~] gate 미통과 또는 journal provider 부재 시 신규 진입이 fail-close 되는지 predeploy/checklist 테스트로 다시 고정한다.
  - 적용 완료(checklist 테스트): 실제 `StrategyLiveExpansionGateService`를 scheduler에 주입한 end-to-end fail-close 잠금 추가. real 모드 + provider 부재 → `profitability_gate_unavailable`, real 모드 + journal 실적 부재 → `profitability_gate_missing` 둘 다 BUY 차단, paper 모드는 bypass. `test_strategy_scheduler.py`의 `test_real_gate_fail_closes_buy_when_journal_provider_absent`/`_when_strategy_missing_from_journal`/`test_real_gate_allows_buy_in_paper_mode_without_journal`. 기존 mock-gate 반응 테스트(스캔 skip/BUY reject/SELL 무영향)와 별개로 *실전 fail-close 조건 자체*를 고정. (테스트 전용, production 무변경)
  - 적용 완료(예방적 fail-open 가드): real 모드인데 scheduler에 gate가 미주입이면 `_check_live_expansion_gate`가 `not_applicable`=allowed로 fail-OPEN 하므로, `StrategyFactory.build()`가 실제 `StrategyLiveExpansionGateService`를 journal provider(`get_standard_journal_records`)와 함께 `dry_run=False`로 배선하는지 `test_strategy_factory.py::test_strategy_factory_wires_live_expansion_gate_with_journal_provider`로 고정. 배선 누락 시 회귀로 탐지. (테스트 전용, production 무변경)
  - 검토 후 미채택(대안): scheduler에서 real+gate=None을 fail-close로 바꾸는 안은 `dry_run=False`+gate 미주입 기존 테스트 수십 개가 BUY 진행을 전제하므로 비외과적. `PreDeployCheckService` 런타임 점검 안은 predeploy가 scheduler/gate 핸들을 갖고 있지 않아(현재 config/env/broker만 주입) 새 의존성 필요. 정적 배선 lock 테스트가 더 외과적이라 그쪽으로 마감.
- [~] 전략별 `entry_reason`, `invalidation_price`, `stop_loss_price`, `target_price`, `trailing_rule`, `expected_holding_period_days`, `confidence`, `required_data`, `config_hash`가 journal 분석까지 이어지는지 샘플 리포트로 검증한다.
  - 조사 결과(2026-05-31): 9개 필드 중 처음엔 **`config_hash`만** journal(`VirtualTradeRepository`)에 컬럼으로 persist되어 `get_standard_journal_records`/리포트까지 이어졌다. `invalidation_price`/`stop_loss_price`는 scheduler `_signal_price_policy_kwargs`로 **order 경로**(`OrderExecutionService`)에만, 나머지는 `log_buy` 시그니처/스키마에 없어 드롭됐다.
  - 적용 완료(price-policy 3필드, 2026-05-31): 사용자 결정에 따라 `invalidation_price`/`stop_loss_price`/`target_price`를 journal에 persist. `VirtualTradeRepository` trades 스키마 3 컬럼(REAL) + `_ensure_trade_columns` ALTER 마이그레이션 + `_SELECT`/`_INSERT`(12→15)/`_write`/`log_buy`·`log_buy_async` 파라미터, `log_order_failure` INSERT 튜플 정합. `_to_json_records`(df.to_dict 전 컬럼)→`normalize_virtual_trade` metadata 로 자동 surfacing. `StrategyScheduler._virtual_trade_log_kwargs`가 signal의 3필드를 journal 경로로 전달. 테스트: `test_virtual_trade_repository_volatility.py`(persist/standard-record metadata/DDL/migration/async), `test_strategy_scheduler.py::test_virtual_trade_log_kwargs_*`. 단위 5749 / 통합 240 passed.
  - 적용 완료(metadata 5필드, 2026-06-07): 사용자 결정에 따라 `entry_reason`/`trailing_rule`/`expected_holding_period_days`/`confidence`/`required_data`도 journal에 persist. `VirtualTradeRepository` trades 스키마 5 컬럼(TEXT/TEXT/INTEGER/REAL/TEXT — `required_data`는 JSON 직렬화) + `_ensure_trade_columns` 타입별 ALTER 마이그레이션 + `_SELECT`/`_INSERT`(15→20)/`_write`(`_opt_int`/`_opt_str` 헬퍼)/`log_buy`·`log_buy_async` 파라미터, `log_order_failure` INSERT 튜플 정합. `normalize_virtual_trade`의 `dict(trade).items()` metadata로 자동 surfacing. `StrategyScheduler._virtual_trade_log_kwargs`가 signal의 5필드를 None-omit으로 journal 경로로 전달(order용 `_signal_price_policy_kwargs`는 불변). 테스트: `test_virtual_trade_repository_volatility.py`(persist/standard-record metadata/DDL/migration/async 6종), `test_strategy_scheduler.py::test_virtual_trade_log_kwargs_*signal_metadata*` 2종, 기존 `test_log_buy_async_thread_execution` 시그니처 갱신. 단위 5788 / 통합 240 passed.

판단:

- 코드 구조는 수익성 검증 준비 단계까지 왔지만, 실제 수익 가능성은 아직 실전 journal과 shadow/paper/live 성과 데이터로 증명해야 한다.

주요 파일:

- `services/strategy_live_expansion_gate_service.py`
- `services/strategy_profitability_gate_service.py`
- `scheduler/strategy_scheduler.py`
- `common/types.py`
- `services/strategy_log_report_service.py`

### 1-7. Multiple testing / 과최적화 방어 고도화

- [~] 현재 proxy 기반 multiple testing 방어를 자금 확대 전 formal 검증으로 확장할지 결정한다.
  - 현재 판단: adjusted Sharpe proxy와 PBO-like proxy는 canary 전 검토에는 유용하지만 formal Deflated Sharpe 또는 formal PBO 구현은 아니다.
  - 후보: walk-forward validation, purged validation, formal PBO, Deflated Sharpe, 전략/필터 조합별 ablation 결과 자동 리포트.
  - 적용 완료(formal DSR, 2026-06-08): 사용자 결정에 따라 formal Deflated Sharpe Ratio(Bailey & López de Prado 2014)를 우선 구현. `multiple_testing_bias_service.py`에 `_compute_deflated_sharpe`(기존 `_compute_deflated_sharpe_proxy`는 불변 병행) 추가 — SR₀=trials 기반 기대 최대 Sharpe(across-strategy sharpe 분산 + Euler-Mascheroni 가중 E[max]) 대비 best Sharpe를 PSR 확률로 deflate. skew/kurtosis metric 부재 시 정규(0/3) fallback, sample 수는 `trade_count`(<2면 unavailable). `statistics.NormalDist`로 cdf/inv_cdf 처리. 신규 summary 키 `deflated_sharpe` + warning reason `deflated_sharpe_probability_below_threshold`. gate(`StrategyProfitabilityGateConfig`)에 `multiple_testing_min_deflated_sharpe_probability`/`_sample_size_metric`/`_skew_metric`/`_kurtosis_metric` opt-in 배선(기본 None → 동작 무변경). 테스트: `test_multiple_testing_bias_service.py` 4종(formal report/threshold warn/sample 부재 unavailable/skew·kurtosis 반영). 단위 5797 / 통합 240 passed.
  - 적용 완료(수익률 모멘트 metric, 2026-06-08): DSR 입력을 정규 가정 fallback에서 벗어나게 하기 위해 중앙 metrics 빌더 `compute_strategy_window_metrics`(`strategy_performance_degradation_service.py`)가 per-trade `net_return` 시계열에서 `sharpe_ratio`(mean/sample-stdev) + `return_skew`(표준화 3차 모멘트) + `return_kurtosis`(비초과, 정규=3.0)를 산출하도록 additive 키 3개 추가. 표본 부족 시 None(sharpe≥2/skew≥3/kurtosis≥4), 키 이름은 DSR 기본 metric 이름과 일치. `_empty_metrics`에도 None 기본값 추가. 기존 consumer(ablation/parameter_stability/gate)는 additive라 무영향. 테스트: `test_strategy_performance_degradation_service.py` 2종(모멘트 산출/소표본 None). 단위 5799 / 통합 240 passed.
  - 후속(미구현): formal PBO(CSCV — config별 T×N period 수익률 행렬 필요, 데이터 파이프라인 선행), walk-forward / purged validation(백테스트 러너 변경), ablation 자동 리포트.
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
  - 적용 완료(2026-05-31): event shadow 후보가 `StrategyEventRouter` 내부 구독만 갱신하고 실제 가격 WebSocket 구독을 요청하지 않던 배선 누락을 수정했다. `StrategyScheduler`가 `event_shadow_<strategy>` 카테고리로 `PriceSubscriptionService.sync_subscriptions()`를 호출해 후보 종목 tick을 실제 수신하도록 한다.
  - 남은 작업: 5거래일 동안 `logs/strategies/event_shadow/YYYYMMDD.jsonl` 을 수집한 뒤 위 도구로 리포트 생성.
  - 수집 현황(2026-05-31 확인): `logs/strategies/event_shadow/` 디렉터리 없음. 기존 기간은 유효 수집일로 계산하지 않고, 배선 수정 후 장중 운영일부터 5거래일을 다시 센다.
  - 검증 기준: shadow 신호와 기존 polling 신호의 시간/종목/가격 괴리를 비교해 실주문 전환 가능 여부를 판정한다.
  - 추가 기준: polling 대비 신호 선행 시간, fast path false positive, false negative, full gate parity, missed trade PnL, duplicate signal rate. (모두 위 스크립트 출력에 포함)
  - VBO 특이점: `evaluate_single()` shadow fast path는 execution strength/program-buy를 의도적으로 생략하므로, fast path 통과(=`shadow_only` + `matched`)와 full gate 최종 통과(=`matched` + `polling_only`)의 분리는 현재 스크립트의 `shadow_only` / `matched` / `polling_only` 분류로 간접 표현된다.
- [ ] event-driven signal은 별도 승인 전 shadow/latency 측정용으로만 운영한다.
  - 운영 원칙: 실주문은 polling scheduler + full gate 통과 경로만 허용한다.
  - No-Go 사유: `StrategySignalSink`는 protocol 수준이고, VBO fast path는 일부 full safeguard를 생략한다.
- [~] exit fast-path는 entry event-driven 전환보다 별도 우선순위로 검토한다.
  - 목표: 손절 조건만이라도 WebSocket price snapshot 기반 shadow 판정으로 latency와 false-positive를 먼저 측정한다.
  - 적용 완료(측정 경로, 2026-06-01): 손절 전용 exit shadow 구현. `LiveStrategy.evaluate_exit_single(code, snapshot, holding)` 인터페이스(default None) + VBO 구현(net 손절 트리거만 복제, 오버나이트/EOD 제외). `StrategyScheduler._refresh_exit_shadow_subscriptions`가 `event_driven_shadow=True` 전략의 보유 종목을 `{name}__exit` subscriber로 router 구독 → `evaluate_exit_single` 결과를 `EventShadowJournalService.record(signal_source="event_shadow_exit")`로 기록(실 주문 미발생), entry gate 무관하게 매 사이클 갱신. 가격 구독은 `event_shadow_exit_<strategy>` 카테고리. VBO가 entry+exit shadow pilot(기존 flag 공유). 단위 5760 / 통합 240 passed.
  - 적용 완료(parity exit 확장, 2026-06-01): `analyze_event_shadow_parity.py`에 `load_shadow_records(signal_source=...)` 필터 + CLI `--signal-source`(default `event_shadow`)/`--polling-action`(default `BUY`) 추가. 기본 실행은 entry 만 분석해 같은 jsonl 의 exit 레코드 교차 오염을 막고, exit 분석은 `--signal-source event_shadow_exit --polling-action SELL`. `compute_parity_report`는 action-agnostic 이라 불변. (분석 스크립트 한정, 실자본 경로 없음)
  - 남은 작업: 장중 운영으로 `event_shadow_exit` 레코드 5거래일 수집 → entry/exit 각각 parity 리포트 생성.
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

- [x] 활성 전략 scan에서 종목별 `get_current_price()` REST fallback이 많이 발생하는 경로를 측정하고, `price_lookup_stats_delta`를 전략별 리포트에 포함한다.
  - 적용 완료(기존): `StrategyScheduler._run_strategy()`가 scan 전후 `StockQueryService.price_lookup_stats_snapshot()` delta를 산출해 `scan_metrics` 로그에 `lookup_stats_delta`(전략별)로 포함한다.
- [x] 후보군 현재가는 우선 WebSocket snapshot을 사용하고, snapshot 누락분은 `get_multi_price()` 30종목 batch로 보강하는 helper를 설계한다.
  - 적용 완료: `StockQueryService.prefetch_prices(codes)` 신규. 신선 snapshot 보유 종목은 skip, 누락/stale만 `get_multi_price` ≤30종목 chunk로 보강 후 `cache_price_snapshot` backfill. 신규 stat 키 `batch_prefetch_call`/`batch_prefetch_backfill`/`batch_prefetch_skip_fresh`로 delta 리포트에 노출.
  - `get_multi_price`(FHKST11300006) 모의 지원 여부 미확정 → `korea_invest_quotations_api.py`의 "(실전 전용)" 단정 주석 제거. multi_price는 계속 사용하되, **연속 3회 실패 시 5분간 batch prefetch만 일시 중단하는 circuit-breaker**를 추가해 실전 전용이라도 매 scan 실패 호출이 누적되지 않게 했다. circuit open 동안에도 개별 `get_current_price` REST fallback은 정상 동작한다.
- [~] 전략별로 개별 REST 호출이 필요한 전체 필드(`per`, `pbr`, `stck_llam` 등)와 단순 현재가만 필요한 경로를 분리한다.
  - 기존: `get_current_price(allow_snapshot=...)` 파라미터로 snapshot에 없는 REST 전용 필드(per/pbr/eps) 요구 시 snapshot 우회 경로가 이미 분리되어 있다. 전략별로 어느 경로를 쓰는지 체계적 분류는 후속.
- [x] 테스트: 60개 후보 scan에서 REST current price 호출이 60회가 아니라 batch 2회 이하로 떨어지는 경로를 mock으로 검증한다.
  - 적용 완료: `test_stock_query_service_prefetch.py::test_prefetch_then_lookups_hit_snapshot_no_per_code_rest` (60종목 prefetch → batch 2회 + 60 get_current_price 전부 snapshot_hit + 개별 REST 0회), chunk[30,30]/신선 skip/best-effort/price_stream 미주입 no-op + **연속 실패 후 circuit open** 케이스 고정. 활성 전략 7개 scan의 async `prefetch_prices` mock/호출 검증 보정.
  - 검증: 단위 5714 passed / 통합 240 passed (통합 경고 2건, 결과는 통과).
- [x] 나머지 활성 전략 scan 배선: `rsi2_pullback`에 이어 `first_pullback`/`high_tight_flag`/`oneil_squeeze_breakout`/`oneil_pocket_pivot`/`larry_williams_channel_breakout`/`larry_williams_vbo`까지 후보군 확정 직후 `prefetch_prices` 호출을 추가했다.

판단:

- 리뷰의 “평균 87초 stale” 수치는 현재 `StockQueryService`의 5초 WebSocket snapshot TTL과 맞지 않아 그대로 채택하지 않는다. 다만 전략 scan이 batch quote를 적극 사용하지 않고 종목별 현재가 조회로 쉽게 fallback되는 구조는 성능/latency 리스크다.

주요 파일:

- `services/stock_query_service.py`
- `services/market_data_service.py`
- `brokers/korea_investment/korea_invest_quotations_api.py`
- `strategies/`
- `scheduler/strategy_scheduler.py`

---

### 2-6. 라이브 핫패스 성능 리뷰 follow-up

- [x] WebSocket 수신 루프의 틱당 고정 비용을 줄인다. (2026-05-31 완료)
  - 적용 완료: `_handle_websocket_message()`에서 (1) 미사용 REST 속성(`_websocket_url`/`_base_rest_url`/`_rest_api_key`/`_rest_api_secret`) 틱당 재대입 제거, (2) `realtime_price`/`unified_realtime_price`/`realtime_quote`/program trading TR_ID를 `_cache_realtime_tr_ids()`로 1회 캐싱(첫 메시지 lazy + 재연결 시 무효화→재해석), (3) debug 로그를 `%s` 파라미터화로 전환해 매 틱 `parsed_data` repr/깊은 dict 조회 제거.
  - 보존: `realtime_price`/`realtime_quote` 필수 키 누락 시 KeyError fail-fast 그대로 유지(상위 재연결 경로). 캐시 속성 bracket 접근으로 회귀 테스트 통과.
  - 테스트 고정: `test_handle_message_does_not_reassign_rest_attrs_per_tick`, `test_handle_message_caches_realtime_tr_ids`, `test_handle_message_uses_cached_tr_ids_after_config_mutation`, `test_cache_realtime_tr_ids_refreshes_on_reconnect`, `test_handle_message_debug_logging_is_lazy`. 단위 5729 passed / 통합 240 passed.
- [~] 활성 라이브 전략의 `scan()` 후보 처리 중 남은 순차 REST/보강 호출을 전략별로 재분류하고, 필요한 곳만 `bounded_gather`로 전환한다.
  - 우선 대상: `LarryWilliamsVBOStrategy.scan()` 후보 루프. 이미 `prefetch_prices()`와 range cache `bounded_gather()`는 적용되어 있지만, 후보별 현재가/체결강도/변동성 보강 호출은 순차 흐름이 남아 있다.
  - 검토 결과(2026-05-31, 보류): 저비용 단계(현재가는 `prefetch_prices` 덕에 대부분 snapshot hit)에서 대다수 후보가 탈락하고, 고비용 REST(`_get_execution_strength`)는 가격 게이트 통과한 돌파 후보(소수)에만 발생. 게다가 전역 `ApiBudgetLimiter`가 모든 REST를 8/s로 직렬화하므로 bounded 전환의 실익은 "동시 돌파 후보 多"일 때의 직렬 RTT 누적 감소뿐(marginal). 실전 진입 경로 동등성(순서/시그널 수/`_bought_today`) 검증 부담 대비 이득이 작아 보류. 재개 시 2-pass(돌파 후보만 `execution_strength` bounded)가 가장 외과적.
  - 주의: `MomentumStrategy.run()`과 `GapUpPullbackStrategy.run()`도 순차 N+1이 맞지만 현재 웹 라이브 스케줄러 등록 대상이 아니므로, 라이브 핫패스 최우선 항목으로 일반화하지 않는다.
  - 검증 기준: 결과 순서/시그널 수/거절 사유/`_bought_today` state 변화가 기존과 동등하고, 동시성 limit은 KIS budget limiter 기본값과 충돌하지 않는다.
- [ ] 활성 전략 `check_exits()` 순차 루프를 계산 경로별로 점검하고, 보유 종목 수가 늘어나는 경로는 bounded 처리로 통일한다.
  - 확인: `sell_all_stocks()`에는 이미 `SAFE_SEQUENTIAL` / `BOUNDED_PARALLEL` / `EMERGENCY` 모드가 있으며, `EMERGENCY` unbounded gather는 `emergency_scope()`를 사용하는 의도적 경로다. 리뷰의 "exit unbounded gather"는 이 경로보다 전략별 `check_exits()` 순차 계산 개선으로 재정의한다.
  - 우선 대상: `LarryWilliamsVBOStrategy`, `ProgramBuyFollowStrategy`, `VolumeBreakoutLiveStrategy`, `TraditionalVolumeBreakoutStrategy`의 순차 holdings 루프. 단, 현재 웹 등록 활성 전략은 VBO 중심으로 먼저 적용한다.
  - 검증 기준: 손절/익절/시간청산 신호 생성 결과가 기존과 동등하고, per-code 예외가 다른 보유 종목 exit 판단을 막지 않는다.
- [~] 장마감 배치의 `iterrows()` 사용처를 API 호출 전처리와 단순 포맷 변환으로 나눠 낮은 위험부터 개선한다.
  - 적용 완료(2026-05-31): 전체 종목 필터링 루프 3곳(`OneilUniverseService._generate_premium_watchlist()`, `RankingTask._load_all_stocks()`, `MinerviniUpdateTask._load_all_stocks()`)의 `iterrows()`를 컬럼 리스트 추출 후 `zip` 순회로 전환. 행마다 Series 생성하던 비용 제거. `row.get(col, "")` 시맨틱(컬럼 부재→"") 보존, 필터 순서/조건 불변.
  - 테스트 고정: ranking `test_load_all_stocks_preserves_df_order_after_filtering`/`test_load_all_stocks_empty_df_returns_empty`, minervini `test_load_all_stocks_preserves_order_and_all_markets`. 기존 ETF/우선주/스팩/빈코드/비대상시장 테스트 회귀 통과. 단위 5732 passed / 통합 240 passed.
  - 낮은 우선순위(미적용): FDR OHLCV 포맷 변환(`DailyPriceCollectorTask`, `OhlcvUpdateTask`)과 RS line 결과 변환(`RSRatingService`)은 장마감/소규모 변환 경로라 성능 영향이 작아 보류.
  - 검증 기준: 필터링 결과 tuple 목록이 기존과 동일하고 ETF/우선주/스팩 제외 조건이 유지된다. (충족)
- [~] HTTP/token 미세 정합성 개선은 별도 작은 PR로 묶는다.
  - 적용 완료(2026-05-31): (a) fallback `httpx.AsyncClient`에 shared client(`korea_invest_client`)와 동일한 `Limits(max_keepalive=50, max_conn=100, keepalive_expiry=30)`/`Timeout(10.0, connect=5.0)` 적용. (b) `TokenProvider.get_access_token()` double-checked `asyncio.Lock`(`_issue_lock`) singleflight — 동시 호출 중복 발급(EGW00133) 방지. (c) `call_api` 지수 백오프에 bounded jitter(`_RETRY_JITTER_FRACTION=0.25`) 추가 — 명시 delay(`e.delay>0`) 경로는 불변.
  - 보류(근거): JSON 이중 파싱(`_execute_request` + `_handle_response`의 `response.json()` 2회) 제거는 MagicMock 응답 테스트와 충돌(MagicMock이 임의 속성 자동 생성 → 응답 객체 속성 캐싱 불가)하고, 이중 EGW00123 처리 경로를 건드릴 위험 대비 이득이 미미(작은 JSON 2회 파싱=마이크로초)해 단순성 원칙상 보류. weakref 캐시 도입은 과한 복잡도.
  - 테스트 고정: `test_fallback_async_client_uses_shared_limits_and_timeout`, `test_get_access_token_singleflight_under_concurrency`(conftest의 `asyncio.sleep` 패치를 우회하는 raw yield로 실제 동시성 검증), `test_call_api_retry_backoff_applies_bounded_jitter`(`random.uniform` 고정 patch). 기존 429/500 retry 테스트는 jitter 범위 단언으로 갱신. 단위 5735 passed / 통합 240 passed.
  - 주의: `http2=True`는 KIS 지원 여부가 불확실하므로 측정/공식 확인 전 적용하지 않는다.
  - 검증 기준: token refresh, EGW00123 재시도, 429 retry, JSON parsing error 테스트가 기존 contract를 유지한다. (충족)

판단:

- 이번 성능 리뷰의 큰 방향은 맞지만, `MomentumStrategy`/`GapUpPullbackStrategy` 같은 레거시 전략을 현재 라이브 P1로 올리면 우선순위가 흐려진다.
- 라이브 등록 기준으로는 WebSocket 틱 루프와 VBO scan/check_exits 잔여 순차 처리부터 외과적으로 줄이는 것이 가장 비용 대비 효과가 크다.

주요 파일:

- `brokers/korea_investment/korea_invest_websocket_api.py`
- `strategies/larry_williams_vbo_strategy.py`
- `strategies/program_buy_follow_strategy.py`
- `strategies/volume_breakout_live_strategy.py`
- `strategies/traditional_volume_breakout_strategy.py`
- `task/background/after_market/ranking_task.py`
- `task/background/after_market/minervini_update_task.py`
- `services/oneil_universe_service.py`
- `brokers/korea_investment/korea_invest_api_base.py`
- `brokers/korea_investment/korea_invest_token_provider.py`

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

### 3-6. 광범위 예외 처리의 silent skip 방지 — ✅ 완료 (IndicatorService + StrategyScheduler)

- [x] `IndicatorService`의 계산 경로에서 `except Exception`으로 `UNKNOWN_ERROR`/`0.0`/`{}`만 반환하던 silent 지점 6개를 분류했다.
  - 대상: `_calculate_bollinger_bands_full` / `_calculate_rsi_series` / `_calculate_atr_full` / `_calculate_moving_average_full` / `calc_rs_sync` / `calc_adx_sync`. (이미 로깅하던 `get_relative_strength`/`_calculate_indicators_full`, 안전 재계산 fallback인 cache 경로, re-raise 경로는 제외)
  - 정상 "데이터 없음"은 `_get_ohlcv_data`에서 에러 응답으로 걸러지므로, 위 except에 도달하는 예외는 schema/type 등 비정상 계산 실패로 본다.
- [x] 데이터 schema/type 오류를 ERROR log + metric counter(`_record_calc_error` → `_calc_error_counts`)로 집계하고, window 내 반복(threshold/cooldown) 초과 시 `OperatorAlertService.report(AlertSource.INDICATOR, ...)`로 알림을 올린다. `get_calc_error_stats_delta()` accessor 추가. `service_container`에서 `operator_alert_service` 주입.
- [x] 전략 scan/check_exits의 per-code 예외는 전체 전략 중단을 막되, 전략별 fail-rate metric에 반영한다.
  - 적용 완료: `StrategyCalcFailureCounter`가 구조화 로그 중 `event` 이름에 `error`/`failed`/`exception`이 포함되고 `code`가 있는 per-code 실패만 집계한다.
  - `StrategyScheduler.scan_metrics`와 신규 `exit_metrics`에 `calc_failures`, `calc_failure_count`, `calc_failure_code_count`, `calc_failure_rate_pct`를 기록한다.
  - 전략 단위 장애나 state load/save처럼 `code`가 없는 이벤트는 실패율 분모와 맞지 않아 제외한다.
- [x] 테스트: 지표 계산 중 schema 오류 시 조용히 skip하지 않고 ERROR 로그 + 카운터 집계 + threshold 초과 alert hook 호출을 `test_indicator_service.py`에 고정했다.

판단:

- per-code 장애를 흡수하는 운영 안정성은 유지해야 한다. 다만 지표/전략 계산 실패가 단순 “데이터 없음 → 진입 skip”으로만 보이면 신호 손실이 장기간 누적될 수 있다.
- IndicatorService 쪽 silent skip과 전략 레이어 per-code fail-rate metric을 모두 해소했다. 남은 후속은 필요 시 `StrategyLogReportService`에서 일일 리포트로 surfacing 하는 정도다.

주요 파일:

- `services/indicator_service.py`
- `services/operator_alert_service.py`
- `core/scan_rejection_counter.py`
- `scheduler/strategy_scheduler.py`
- `tests/unit_test/core/test_scan_rejection_counter.py`
- `tests/unit_test/scheduler/test_strategy_scheduler_scan_metrics.py`

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
   - ~~WebSocket 틱 루프 TR_ID 캐싱 + debug lazy logging 적용 (P2 2-6)~~ ✅ 완료 (2026-05-31)
   - `LarryWilliamsVBOStrategy.scan()` 잔여 순차 후보 처리 bounded 전환 (P2 2-6) — 2026-05-31 검토 후 **보류**(실익 marginal·실전 진입 경로). 재개 시 2-pass 권장.
   - 활성 전략 `check_exits()` 순차 holdings 루프 bounded 통일 (P2 2-6) — 실전 청산 경로라 별도 승인 후 진행.
   - ~~장마감 배치 `iterrows()` 중 전체 종목 필터링 경로부터 벡터화/`itertuples()` 전환 (P2 2-6)~~ ✅ 완료 (2026-05-31, zip 순회). FDR/RS line 변환 경로는 영향 작아 보류.
   - ~~`KoreaInvestApiBase` fallback client Limits/Timeout + retry jitter + `TokenProvider` singleflight 정합성 개선 (P2 2-6)~~ ✅ 완료 (2026-05-31). JSON 이중 파싱 제거는 MagicMock 충돌/이득 미미로 보류.
   - ~~라이브 일봉 지표 당일 미완성 봉 제외 (P0 0-8)~~ ✅ #478 + rollout 조사 완료 (추가 오염은 `LarryWilliamsChannelBreakout` ADX/채널뿐 → `_confirmed_bars()`로 마감)
   - ~~라이브 exit net PnL 통일 (P0 0-9)~~ ✅ #479
   - ~~pending/reserved cash + 전 전략 same-symbol qty cap 반영 (P0 0-10)~~ ✅ 완료
   - ~~kill switch/state sync fallback atomic write 통일 (P0 0-11)~~ ✅ #481
   - ~~backtest/live tick-size 단일화 (P3 3-5)~~ ✅ 완료
   - ~~전략 scan 현재가 batch/snapshot 최적화 (P2 2-5)~~ ✅ helper + 활성 전략 7개 scan 배선 + 테스트 완료
   - ~~IndicatorService silent skip에 alert/metric hook + 전략 레이어 per-code fail-rate metric (P3 3-6)~~ ✅ 완료

3. **운영 관찰 진행 중**
   - VBO shadow 5거래일 jsonl 수집 → `scripts/analyze_event_shadow_parity.py` 로 parity 리포트 생성 → PR-3 진입 판정 (P2 2-4 PR-2.5)
     - 2026-05-31 배선 수정 후 장중 운영일부터 수집일 카운트 재시작.
   - ~~손절 전용 exit fast-path shadow/latency 측정 설계 (P2 2-4 후속)~~ ✅ 측정 경로 + parity exit 분류 구현 완료 (2026-06-01). 남은 것: 장중 `event_shadow_exit` 5거래일 수집 → 리포트.
   - profitability gate는 우회하지 않고 shadow/paper/canary journal로 전략별 실전 근거를 축적 (P1 1-6)

4. **외부 데이터 확보 후 진행 가능 (blocked)**
   - ~~실전 KIS `inquire-daily-ccld` 체결 이력 응답 캡처 → fixture 회귀 (P0 0-1)~~ ✅ 완료 (2026-06-01, `001510`)
   - broker order number mapper 별도 클래스 분리 + fixture 테스트 (P0 0-1)
   - 장중 후보 종목 프로그램매매 WebSocket 샘플 캡처 → replay fixture overlay (P1 1-5)
   - 한국장 microstructure fixture 로 체결 모델 보수성 검증 (P1 1-5)
   - VBO 실 적용 + OSB shadow 진입 (PR-3, P2 2-4 — PR-2.5 결과 양호 조건)

5. **조건부 트리거 — 재발 시 진행**
   - Pool B 거래대금 50→30억 완화 검토
   - Pool B 정배열 조건을 `current > ma_20d` 중심 완화 검토

6. **정책 합의 후 재승격 후보 (보류)**
   - ~~Deflated Sharpe formal 검증 도입~~ ✅ 완료 (2026-06-08, formal DSR). 남은 것: formal PBO(CSCV) / walk-forward / purged validation 도입 여부 결정 (P1 1-7)
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
