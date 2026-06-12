# Investment Trading App - 남은 To-Do

최종 업데이트: 2026-06-12 (StrategyScheduler 코드 리뷰 S-1~S-10 추가)

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
- P2 2-6: 라이브 핫패스 성능 follow-up. ~~WebSocket 틱 루프 상수 캐싱/로그 lazy 처리~~ ✅, ~~장마감 배치 `iterrows()` 축소~~ ✅, ~~HTTP/token 미세 정합성 개선(fallback Limits·Timeout / token singleflight / retry jitter)~~ ✅, ~~VBO `check_exits()` bounded 전환~~ ✅ (2026-06-11). 남은 것: 활성 전략 `scan()` 순차 후보 처리 bounded 전환 — 실전 진입 경로라 별도 승인 후 진행(VBO scan은 실익 marginal로 보류 결정).
- P3 3-4: active strategy lifecycle contract 최소 공통 단계 강제 여부 재설계(현재 보류).
- P3 3-5: backtest/live 호가단위 tick-size 로직을 단일 utility로 통일한다.
- P3 3-6: ~~IndicatorService 계산 경로의 광범위 `except Exception` silent skip에 ERROR log/metric/alert hook을 붙인다.~~ ✅ 완료. 전략 레이어 per-code fail-rate metric도 `scan_metrics`/`exit_metrics`에 반영 완료.
- Pool B 튜닝: 후보 부족 재발 시 거래대금/정배열 조건 완화 검토.
- 완료 기준의 전략 성과 `[~]`: `MomentumStrategy` 등 비활성 백테스트 경로의 표준 journal 통합 여부 결정.
- 시스템 트레이더 관점 리뷰(R-1~R-6, 2026-06-08): 생존편향·전략 상관/regime 집중·총위험 미집계·갭 체결 등 백테스트 신뢰도/실전 리스크 신규 발견. 자금 확대 전 R-1~R-3 우선 해소 권장. (R-5 거래세율은 검토 결과 0.20% 정확 → 해소, 변경 없음. 하단 "시스템 트레이더 관점 리뷰" 섹션)
- StrategyScheduler 코드 리뷰(S-1~S-10, 2026-06-12): `stop()` 강제청산 데드 패스, 매도 병렬 에러 처리 비대칭, 이력 트림 vs 복구 충돌 등 버그 + 수명/구조 개선. S-1/S-2/S-4~S-8 수정 완료, S-3/S-10은 의도된 설계 확인 후 주석 명시로 종결. S-9는 부분 진행(prune 통합/trace_id 영속화/import 정리) — getter 부수효과는 의도된 계약으로 판명되어 철회, god class 분리는 P2 2-4 parity 판정 후 재평가로 보류. (하단 "StrategyScheduler 코드 리뷰" 섹션)

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
  - 적용 완료(일일 리포트 DSR 섹션, 2026-06-08): DSR 스레드를 운영 가시성까지 완성. `StrategyLogReportService._build_multiple_testing_section`이 live journal(`get_standard_journal_records`)을 `compute_strategy_window_metrics`로 전략별 집계 → `compute_multiple_testing_bias_summary`로 돌려 formal Deflated Sharpe(확률/best SR/기대최대/표본) + adjusted Sharpe proxy + PBO proxy + 편향 경고를 일일 HTML 리포트에 노출. 전략 <2개 또는 DSR/proxy/경고 모두 부재 시 섹션 생략. active/inactive 양쪽 조립 경로 배선. 테스트: `test_strategy_log_report_multiple_testing.py` 4종. 단위 5806 / 통합 240 passed. (P1 1-6 #501 signal_metadata 섹션과 동일 패턴)
  - 후속(미구현): formal PBO(CSCV — config별 T×N period 수익률 행렬 필요, 데이터 파이프라인 선행), walk-forward / purged validation(백테스트 러너 변경), ablation 자동 리포트(현재 `run_backtest.py --ablation` 오프라인 console 전용·persist 없음 → 백테스트 실행+persist+집계 선행).
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
  - 수집 현황(2026-06-09 진단): `event_shadow/20260609.jsonl` 1개 파일만 존재하고 내용은 전부 `record_status`(`subscriptions_refreshed`)뿐, parity 비교용 entry(`event_shadow`)/exit(`event_shadow_exit`) 신호는 0건. 같은 날 polling VBO는 `buy_signal_generated` 4건(403870/093370/023530/160980) 정상 생성됨. 근본 원인 = **WebSocket 유령 구독**: 돌파 종목들이 `subscribed_no_tick`(로컬 active인데 KIS tick 0건) 상태였고, shadow는 websocket tick에만 의존하므로 `evaluate_single` 미실행 → 기록 0건. (polling은 REST 가격이라 영향 없음.) 즉 5거래일 카운트는 사실상 0일이며, 아래 구독 ACK 수정 후 재시작해야 유효 수집이 됨.
  - 구독 ACK 수정(2026-06-09, `fix/ws-subscription-ack-confirm`): 유령 구독 근본 원인은 `send_realtime_request`가 `ws.send()` 프레임 전송만으로 `True`를 반환하고 `SubscriptionPolicy._active_codes_price`가 그 위에서 ACK 미확인으로 active 마킹하던 것. 재연결 churn(receive_task_dead/hourly reconnect) 중 KIS 미등록 구독이 active로 고착되어 `_rebalance`가 재구독하지 않았다. 수정: `_do_subscribe`(UNIFIED_PRICE)가 전송 성공 후 KIS 등록 ACK 확정(`wait_unified_price_ack` → `KoreaInvestWebSocketAPI.wait_for_subscription_ack`, `_subscribed_items`/pending future 기반, `SUBSCRIBE_ACK_TIMEOUT_SEC=2.0`)을 받은 경우에만 active로 마킹. 미확정이면 active 미마킹 → 다음 rebalance 재구독(자가 치유). `send_realtime_request` 반환 의미는 불변(브로커 테스트 100+ 보호), ACK 대기는 `_EXCLUDED_METHODS`로 budget queue 우회. 단위 5841 / 통합 240 passed.
  - 수집 현황(2026-06-10 재진단): `20260609.jsonl`/`20260610.jsonl` 두 파일 모두 `subscriptions_refreshed` status 만 있고 entry/exit 신호 0건. 4종목 모두 router 구독은 정상(`added_codes` 확인)이나 그 다음 단계에서 **3중 원인**으로 각기 막힘: (1) **403870/160980** = `subscribed_no_tick` 종일(09:13~15:2x) → `on_price_tick` 미호출(틱 0). (2) **093370** = polling 이 09:11(진입창 오픈 1분 후) 매수 → 공유 `_bought_today` 게이트로 shadow 영구 차단(ACK 수정으로도 안 풀림 = 구조적 결합). (3) **023530** = 틱 정상·목표가 09:26 돌파·shadow 는 체결강도 게이트 생략·polling 매수는 10:07 → 09:26~10:07 모든 게이트 열렸는데 0건 = ①②로 설명 안 되는 event 디스패치/스냅샷 갭. event_router 는 `PriceStreamService` 에 정상 주입됨(service_container.py:329).
  - 진단 계측 추가(2026-06-10): `evaluate_single` 의 게이트별 None 반환을 `_shadow_eval_stats[code]` Counter 로 누적(`evaluated`/`reject_not_candidate`/`reject_outside_window`/`reject_already_bought`/`reject_range_missing`/`reject_bad_snapshot`/`reject_invalid_open`/`reject_invalid_price`/`reject_below_target`/`signal`), `scan()` 종료 시 `shadow_eval_stats` 이벤트로 1회 요약 로깅, 날짜 변경 시 초기화. 다음 장에서 **틱 미수신(evaluated≈0) vs 게이트 탈락(reject_invalid_open 등)** 을 023530 류 케이스로 즉시 구분 가능. gate5 를 open/price 로 분리(`reject_invalid_open` 가 023530 open-누락 가설 직격). 테스트: `test_larry_williams_vbo_evaluate_single.py` 4종 추가. 단위(VBO 75) / 통합 240 passed. (전략 1파일 한정, 신규 배선·서비스 결합 없음)
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
- [x] 활성 전략 `check_exits()` 순차 루프를 계산 경로별로 점검하고, 보유 종목 수가 늘어나는 경로는 bounded 처리로 통일한다. (2026-06-11)
  - 확인: `sell_all_stocks()`에는 이미 `SAFE_SEQUENTIAL` / `BOUNDED_PARALLEL` / `EMERGENCY` 모드가 있으며, `EMERGENCY` unbounded gather는 `emergency_scope()`를 사용하는 의도적 경로다. 리뷰의 "exit unbounded gather"는 이 경로보다 전략별 `check_exits()` 순차 계산 개선으로 재정의한다.
  - 우선 대상: `LarryWilliamsVBOStrategy`, `ProgramBuyFollowStrategy`, `VolumeBreakoutLiveStrategy`, `TraditionalVolumeBreakoutStrategy`의 순차 holdings 루프. 단, 현재 웹 등록 활성 전략은 VBO 중심으로 먼저 적용한다.
  - 적용 완료: `LarryWilliamsVBOStrategy.check_exits()`에 `_EXIT_CONCURRENCY=15` + `bounded_gather()` 적용. 기존 오버나이트/EOD/net 손절 판정은 `_check_single_exit()`로 분리해 동등성 유지.
  - 검증 기준: 손절/익절/시간청산 신호 생성 결과가 기존과 동등하고, per-code 예외가 다른 보유 종목 exit 판단을 막지 않는다. (충족: VBO 전략 단위 39 passed / 단위 5876 passed / 통합 240 passed)
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

## 시스템 트레이더 관점 리뷰 (2026-06-08 추가, 코드 기반)

20년차 시스템 트레이더 관점에서 "이 시스템이 돈을 벌 수 있는가 / 실전 자본을 지키는가 / 유지보수 가능한가"를 실제 코드로 검토한 결과. 기존 todo 항목과 중복되지 않는 신규 발견만 추린다. 우선순위는 **수익성 신뢰도 > 실전 리스크 > 성능/유지보수** 순.

### R-1. 생존편향(Survivorship bias) — 백테스트 신뢰도 최우선 [심각]

- 증거: 백테스트/유니버스가 현재 상장 종목 리스트(`data/stock_code_list.csv`, 2026-02 시점 단일 스냅샷)에서 파생되고, 코드베이스 전체에서 상장폐지 종목을 편입/처리하는 경로(point-in-time universe, 상폐 OHLCV)가 없다(`delist`/`survivor`/`as_of` grep → 유니버스 파이프라인에 부재). `OneilUniverseService`/`generic_liquidity_universe_service`도 현재 리스트 기반 필터.
- 왜 치명적인가: 한국 중소형 모멘텀/돌파(KOSDAQ 다수)에서 **상장폐지(감사의견 거절·부실·횡령·합병)된 종목이 백테스트에서 통째로 제외**된다. 정확히 돌파 후 -100%로 가는 종목들이 표본에서 빠져 수익률·승률·profit factor가 **체계적으로 과대평가**된다. profitability gate가 통과해도 그 근거 자체가 오염될 수 있다.
- [ ] point-in-time 유니버스(일자별 실제 상장 종목) + 상장폐지 종목 OHLCV(상폐 직전까지)를 백테스트 데이터에 편입한다. 일자별 유니버스 스냅샷(상장/상폐일 기반) 구축.
- [ ] 상폐 종목 편입 전/후 백테스트 성과 격차를 리포트해 기존 결과의 과대평가 폭을 정량화한다.
- 데이터 소스 검증(2026-06-08, FDR 0.9.110): 현재 코드는 pykrx가 아닌 **FinanceDataReader**를 씀(`services/stock_sync_service.py`, `task/.../daily_price_collector_task.py`, `ohlcv_update_task.py`). 네트워크 프로브 결과:
  - `fdr.DataReader(code, start, end)` OHLCV 조회 **정상**(005930 41행, 장기 이력 1233행). → 상폐 코드 목록만 있으면 OHLCV 백필은 FDR로 가능.
  - `fdr.StockListing('KRX')` **HTTP 404**, `StockListing('KRX-DELISTING')` **0행** → 일자별 상장 구성·상폐 목록을 FDR로 못 가져옴(스크레이퍼 깨짐). pykrx도 불안정.
  - **진짜 병목**: "어느 종목이 언제 상장/상폐됐는가" 신뢰 소스 부재. 이게 없으면 백필 대상 코드도, 편향 정량화도 불가. → 1순위 sub-task = **상폐 종목+상폐일+일자별 구성 신뢰 소스 확보**(예: KRX data.krx.co.kr 상장폐지 목록 수동 export, 또는 대체 소스). 외부 데이터 의사결정 필요.
- 적용 완료(2026-06-11, 데이터 소스 재검증/감사 스크립트): `scripts/audit_delisting_universe.py` 추가. 현재 환경의 FDR 0.9.110에서는 `StockListing('KRX-DELISTING')`가 다시 정상 동작(전체 4,139행)하고, `DataReader(code, exchange='KRX-DELISTING')` 상폐 종목 OHLCV도 조회 가능하다. 스크립트는 FDR 상폐 목록을 `Market in {KOSPI,KOSDAQ}` + `SecuGroup == 주권`으로 필터하고, KIND `상장폐지현황` POST 결과(시장별 1/2)와 이름+상폐일 기준 대조한다. 2026-01-01~2026-06-11 실측: FDR filtered 44건, KIND 45건, matched 40건, FDR-only 4건, KIND-only 5건. 불일치 대부분은 표기 차이(`에스케이` vs `SK`, `IHQ` vs `아이에이치큐`)와 필터 제외 대상(리츠/투자회사)로 보이며, 다음 단계는 이 audit 결과를 기반으로 point-in-time universe snapshot schema/백필 파이프라인을 설계하는 것.
- 관련: `services/oneil_universe_service.py`, `services/generic_liquidity_universe_service.py`, `services/stock_sync_service.py`, `task/background/after_market/daily_price_collector_task.py`, `task/background/after_market/ohlcv_update_task.py`, `data/stock_code_list.csv`, `data/ohlcv_extracted.csv`, `scripts/run_backtest.py`

### R-2. 전략 상관 / 단일 regime 집중 — "7전략 분산"의 착시 [심각]

- 증거: 활성 등록 7개 전략(`first_pullback`/`high_tight_flag`/`larry_williams_channel_breakout`/`larry_williams_vbo`/`oneil_pocket_pivot`/`oneil_squeeze_breakout`/`rsi2_pullback`)이 **전부 long-only 모멘텀/돌파/눌림목 계열**(RSI2 mean-reversion도 long). 숏·헤지·마켓뉴트럴·비상관 자산 없음.
- 왜 위험한가: 사실상 **단일 "상승/추세 regime 베팅"**이다. 강세장에서 동시 수익, 약세·횡보장에서 **동시 손실**. 전략 수가 분산처럼 보이지만 실제 포트폴리오 상관이 높아 drawdown이 합산된다. multiple testing(1-7)으로 과최적화는 방어해도 regime 집중 자체는 별개 리스크.
- [x] 전략 간 실현수익률 상관행렬을 journal로 산출해 리포트(1-7 DSR 섹션 옆)에 노출하고, 고상관 클러스터를 명시한다. (2026-06-09) 계산은 기존 `services/strategy_correlation_service.py::compute_strategy_correlation_summary`(이미 gate에서 사용 중)가 있어 재구축 없이 **리포트 노출만** 추가. `StrategyLogReportService._build_strategy_correlation_section`이 live journal 일별 net_return으로 최고 상관쌍 + 고상관(≥0.8) 클러스터 + 경고를 노출. active/inactive 양쪽 배선. **부수 수정**: DSR `multiple_testing_section`이 active(정상) 리포트 경로 body append에서 누락돼 inactive 경로에만 나오던 #505 버그를 함께 바로잡음. 테스트: `test_strategy_log_report_correlation.py` 4종. 단위 5810 / 통합 240 passed.
- [~] regime별(상승/하락/횡보) 전략군 성과를 분해해 "전 전략이 같은 regime에서만 작동"하는지 정량 확인한다. (`market_regime_service` 활용)
  - 적용 완료(journal-time 캡처, 2026-06-09): 사용자 결정에 따라 매수 시점 `market_regime`({kospi,kosdaq,stock_market})을 journal에 persist. 조사 결과 계산(`regime_performance_service.compute_performance_by_regime`)과 전략별 웹 엔드포인트(`GET /strategies/performance-by-regime?strategy=`)는 **이미 존재**했으나 trade에 `market_regime`을 채우는 곳이 없어 dark 상태였다. (1) `VirtualTradeRepository`에 `market_regime` JSON 컬럼+migration+log_buy 파라미터+읽기 dict 역직렬화(1-6 `required_data` 패턴), (2) `StrategyScheduler._market_regime_log_kwargs`가 scan-warm된 cached snapshot+`is_kosdaq`로 dict 구성→매수 log에 stamp, (3) `OneilUniverseService.market_regime_service` property로 공유 instance를 scheduler에 주입(`strategy_factory`). 이제 기존 엔드포인트가 populated 버킷을 받는다. 테스트: repo 6종 + scheduler 5종. 단위 5821 / 통합 240 passed.
  - 특성(prospective): journal-time 캡처라 **배선 후 매수분부터** regime이 채워진다. 과거 trade 소급 분류는 별도(retroactive) 작업.
  - 적용 완료(일일 리포트 regime 분해 섹션, 2026-06-11): 웹 엔드포인트로만 노출되던 per-strategy regime 성과를 일일 HTML 리포트에 노출. 순수 함수 `regime_performance_service.compute_strategy_regime_decomposition`(기존 `compute_performance_by_regime`를 전략별로 묶어 dominant primary 버킷 + regime 집중도 산출, SURGE overlay 는 by_bucket 노출하되 primary total/dominant 미합산) + `StrategyLogReportService._build_regime_decomposition_section`. live journal(`get_standard_journal_records`)로 전략별 주력 국면·버킷별 승률/평균순익 + "N/M 전략이 같은 국면에 몰림" 집중도(≥70%면 ⚠️ 단일 regime 집중)를 노출. regime 채워진 SOLD 전략 <2개면 생략. active/inactive 양쪽 배선(상관/DSR/오버나이트 섹션과 동일 패턴). 테스트: `test_regime_performance_service.py` 5종(분해/집중도/HOLD·regime 누락 제외/SURGE 미합산) + `test_strategy_log_report_regime.py` 4종. 단위 5873 / 통합 240 passed.
- [ ] 자금 확대 전 비상관 엣지(역추세/숏 가능 시점/저변동 등) 1개 이상 도입 여부를 정책 결정한다.
- 관련: `services/market_regime_service.py`, `services/strategy_log_report_service.py`, P1 1-1 상관 follow-up과 통합

### R-3. 포트폴리오 총위험(heat) 집계 부재 — 사이징의 구멍 [중대]

- 증거: `PositionSizingService`는 per-trade `risk_qty = total_equity × per_trade_risk_pct / 주당리스크`로 종목별 사이징하지만(L170), **전 전략·전 포지션 합산 open-risk(heat) 한도가 없다**. RiskGate의 `max_total_exposure_pct`는 notional(노출금액) cap이지 risk cap이 아니다(L504-505, positions 합산은 금액 기준).
- 왜 위험한가: R-2의 고상관 7전략이 각자 per-trade risk를 소진하면, 상관 drawdown 시 **합산 open-risk가 per-trade의 배수**로 누적된다. 개별 종목은 "1% 리스크"여도 동시 10포지션이 같이 무너지면 포트폴리오는 10% 리스크. notional cap만으로는 이 위험을 막지 못한다.
- [x] 전 포지션 합산 open-risk(Σ 진입가–stop 거리 × 수량 / total_equity) 한도를 RiskGate 또는 PositionSizing에 도입하고, 초과 시 신규 진입 차단/축소한다. (2026-06-09) `PositionSizingService`에 도입(수량 축소 우선, 소진 시 차단). 스냅샷에 종목별 stop이 없어 기존 보유 open-risk를 `Σ(평가금 × |default_stop_loss_pct|)` proxy로 추정(모델 A), 신규 후보는 정확한 `per_share_risk×qty`. reservation overlay 활성 시 같은 사이클 예약도 합산. `_calc_portfolio_heat_qty`가 `heat_limited` 후보로 기존 `min()` 사이징에 합류, budget 소진 시 reason `portfolio_heat_exhausted`. profile별 한도 `max_portfolio_open_risk_pct`(canary 1% / real_limited 3% / paper·real_full 6%, 0이면 비활성). 테스트: `test_position_sizing_service.py` 5종(scale-down/exhausted/disabled/reservation 합산/profile 분기). 단위 5839 / 통합 240 passed.
- [x] 상관 가중 heat(고상관 클러스터는 위험 합산)을 검토한다. (R-2 상관행렬과 연계) → **검토 완료, 보류 (2026-06-10)**.
  - 검토 결론: 현재 `_calc_portfolio_heat_qty`의 기존 보유 open-risk 추정은 `Σ(평가금 × default_stop)` — **분산 크레딧 없이 전 포지션을 완전 상관(worst-case)으로 단순 합산**한다. 그 위에 상관 가중을 얹으면 (1) 고상관 클러스터는 이미 full-sum이라 no-op, (2) 저상관 포지션은 분산 크레딧을 줘 한도를 **완화(loosening)** 하는 방향 → 자본 보호를 조이는 작업이 아니다.
  - 추가 비용: `PositionSizingService`에 상관 provider + 포지션→전략 귀속(journal `get_holds_by_strategy`) 두 의존성 신규 주입 필요. 상관행렬은 SOLD 5일 overlap이 있어야 산출되므로 canary 초기(자본 적을 때)엔 dark. 비용 대비 보호 이득 없음.
  - 재승격 조건: flat full-sum 모델을 분산 크레딧 모델로 바꾸기로 정책 결정할 때(=한도를 의도적으로 완화할 때) 함께 설계한다. 그 전까지는 보수적 flat sum 유지.
- 관련: `services/position_sizing_service.py`, `services/risk_gate_service.py`

### R-4. 오버나이트 갭 + 스톱 gap-through 가정 [중대]

- 증거: `force_exit_on_close` 기본 False(`StrategySchedulerConfig`) → VBO 외 대부분 전략이 **멀티데이 보유**(오버나이트). 한국장 일일 ±30% 가격제한에서 갭다운은 stop_loss를 관통할 수 있다.
- 왜 위험한가: 백테스트 체결 모델이 갭다운 시에도 stop 가격에 체결됐다고 가정하면 **실제 손실을 과소평가**한다(실전은 시가 갭 체결 → 손실 확대). 0-9 net PnL 통일/1-5 microstructure와 직접 연결되는 체결 현실성 문제.
- [x] 백테스트에서 stop_loss가 전일 종가→당일 시가 갭을 관통하면 stop 가격이 아닌 **시가(또는 더 보수적 값) 체결**로 모델링한다. (2026-06-10)
  - 적용 완료: `BacktestExecutionSimulator`에 `OrderType.STOP` 추가. STOP은 발동이 이미 결정된 시장가성 주문이라 항상 체결되며, `_base_fill_price`가 갭 관통 시 보수 체결로 모델링한다 — 매도 stop=`min(stop, bar.open)`(갭다운 관통 시 시가, 아니면 stop), 매수 stop=`max(stop, bar.open)`(갭업 관통 시 시가). market 슬리피지/스프레드/호가단위 내림은 시장가 경로와 동일 적용.
  - 기존 버그 해소: 손절 청산이 `OrderType.LIMIT`로 모델링돼 갭다운 시 `bar.high < stop`이면 **미체결(손실 누락)**, `bar.high >= stop`이면 **stop 가격 체결(과소 손실)** 되던 두 경로 모두 차단.
  - 배선: `BacktestPeriodRunner._signal_to_order`가 SELL 청산 reason에 전략 공통 토큰 `손절`/`스탑`이 있으면 STOP으로 분류(`_is_stop_exit_reason`). 익절/일반 SELL은 LIMIT 유지.
  - 테스트: `test_backtest_execution_simulator.py`(stop 매도 갭다운 시가 체결/무갭 stop 체결/market 슬리피지/매수 stop 갭업 4종), `test_backtest_period_runner.py::test_period_runner_stop_loss_exit_fills_at_gap_open`(손절 청산일 갭다운→시가 체결, 미체결 아님). 단위 5846 passed.
- [x] 전략별 오버나이트 노출 한도/익일 갭 리스크 지표를 리포트에 노출할지 검토한다. → **노출 구현 완료 (2026-06-10)**.
  - 적용 완료: 순수 함수 `services/overnight_exposure_service.py::compute_overnight_exposure_summary`(strategy_correlation_service 패턴) + `StrategyLogReportService._build_overnight_exposure_section`. live journal 로 (1) **현재 노출**(status==HOLD, 장 마감 후 남은 포지션=익일 갭 노출) 전략별 종목 수·보유 경과일, (2) **실현 멀티세션 보유**(SOLD 중 매수일≠매도일) 전략별 건수·평균보유일·평균/최저 순익(사후 downside proxy)을 일일 HTML 리포트에 노출. 당일 청산(intraday)은 제외, 노출 0이면 섹션 생략. active/inactive 양쪽 조립 경로 배선(상관/DSR 섹션과 동일 패턴).
  - 범위 밖(명시): **실제 익일 시가 갭(전일 종가→당일 시가)의 정량 측정은 종목별 OHLCV 조인이 필요해 forward gap 은 미구현**. 본 섹션은 노출 *규모*와 실현 다운사이드만 보여준다. forward gap 측정은 별도(미래) 작업.
  - 테스트: `test_overnight_exposure_service.py` 7종(open/realized/intraday 제외/정렬/빈입력/미파싱일), `test_strategy_log_report_overnight.py` 4종(섹션 노출/생략 조건). 단위+통합 6104 passed.
- 관련: `services/overnight_exposure_service.py`, `services/strategy_log_report_service.py`, `services/backtest_execution_simulator.py`, `scheduler/strategy_scheduler.py`, P1 1-5와 통합

### R-5. 증권거래세율 — 검토 결과 0.20% 현행 정확값, 변경 없음 [해소]

- 당초 리뷰 가정(2025~ 0.15% 인하 → `TAX_RATE=0.002` stale)은 **오류였다.** 사용자 확인 결과 매도 거래세는 **0.20%(0.002)가 현행 정확값**이다(2024년 금투세 폐지 등으로 당초 예정된 인하가 그대로 적용되지 않음). 따라서 `TAX_RATE` 변경하지 않는다.
- 조치: 2026-06-09 0.0015로 바꿨던 변경을 원복(브랜치 폐기, main 미반영). `utils/transaction_cost_utils.py`는 `TAX_RATE=0.002` 유지.
- [x] 세율 정확성 확인 — 0.20% 유지로 종결.
- 관련: `utils/transaction_cost_utils.py`

### R-6. 비용 모델 단순성 — 최소수수료/유동성 비용 부재 [경미, 관찰]

- 증거: `TransactionCostUtils.calculate_cost`는 정률 수수료+세금만 계산(50줄). 슬리피지/스프레드/시장충격은 백테스트 시뮬레이터에만 있고 live net-PnL 회계 util에는 없다.
- 판단: 회계용 util로는 적정하나, 저유동성 중소형주를 다루는 전략 특성상 **capacity(체결 가능 규모)와 시장충격**이 실전 수익을 깎는 핵심 변수다. 2-5/1-5에서 다루는 체결 현실성과 함께, 전략별 평균 거래대금 대비 주문비중(이미 `max_top_of_book_participation_pct` 존재)을 capacity 리포트로 surfacing할지 검토.
- [ ] (관찰) 전략별 후보 종목 평균 거래대금 분포와 주문 규모 대비 충격 추정을 리포트에 노출할지 검토.
- 관련: `utils/transaction_cost_utils.py`, `services/position_sizing_service.py`(`_calc_top_of_book_qty`), P2 1-5

판단 요약:

- 코드 엔지니어링(리스크 게이트·사이징·reconcile·atomic write·체결 시뮬)은 개인 시스템치고 상당히 견고하다.
- 그러나 "돈을 버는가"의 핵심 근거인 **백테스트 신뢰도는 R-1(생존편향)·R-4(갭 체결)로 과대평가 가능성**이 있고, **R-2(regime 집중)·R-3(총위험 미집계)는 실전 drawdown을 백테스트보다 키울 수 있다.** 자금 확대 전 R-1~R-3을 우선 해소하지 않으면 1-6/1-7 gate 통과도 "오염된 표본 위의 통과"가 될 위험이 있다.

---

## StrategyScheduler 코드 리뷰 (2026-06-12 추가, `scheduler/strategy_scheduler.py` 2,299줄 전수 분석)

`scheduler/strategy_scheduler.py` 단일 파일 코드 리뷰 결과. 기존 todo 항목(P2 2-4 shadow, P3 3-6 metric 등)과 중복되지 않는 신규 발견만 추린다. 우선순위는 **실전 청산 누락 방지 > 에러 가시성 > 수명/구조** 순. 작업 브랜치: `fix/strategy-scheduler-audit`.

### S-1. `stop()` 강제청산 데드 패스 [버그, 최우선]

- 증거: `stop()`이 모든 전략의 `cfg.enabled = False`를 먼저 설정한 뒤 `stop_strategy(name, perform_force_exit=True)`를 호출하는데, `stop_strategy`의 청산 조건은 `perform_force_exit and cfg.enabled and cfg.force_exit_on_close`라 **항상 거짓**. 주석 의도("save_state=False → 청산 수행")와 달리 `stop()` 경로의 강제청산은 한 번도 실행되지 않는다. 기존 테스트는 `stop_strategy`를 mock으로 대체해 호출 인자만 검증하므로 미탐지.
- 왜 위험한가: 당일청산 전략(`force_exit_on_close=True`) 보유 중 스케줄러를 수동 정지하면 청산 없이 포지션이 방치되어 오버나이트 갭 리스크(R-4)에 노출된다.
- [x] 재현 테스트 작성: `stop(save_state=False)` 시 `_force_liquidate_strategy` 호출 0회로 데드 패스 재현 확인 (`test_strategy_scheduler_audit_fixes.py::test_stop_performs_force_exit_for_enabled_force_exit_strategy`).
- [x] 수정 (2026-06-12): `stop()`의 enabled 일괄 비활성화 선행 루프 제거. 비활성화는 `stop_strategy()`가 전략별 청산 판단 후 수행 — 데드 패스 원인을 주석으로 명시.
- [x] 회귀 확인: `stop(save_state=True)`(재시작 경로)는 여전히 청산하지 않음 (`test_stop_with_save_state_skips_force_exit`).

### S-2. `scan()` 반환값 None 가드 불일치 [버그]

- 증거: `_run_strategy`에서 stamping 루프는 `buy_signals or []`로 방어하지만, `scan_metrics`의 `len(buy_signals)`와 pyramiding 분기 리스트 컴프리헨션은 `buy_signals`를 직접 사용. 전략이 None을 반환하면 TypeError.
- [x] 수정 (2026-06-12): scan/check_exits 직후 `list(signals or [])` 1회 정규화. 재현 테스트로 `len(None)` TypeError 확인 후 수정 (`test_run_strategy_handles_none_scan_result`).

### S-3. 주문 컷오프가 check_exits까지 차단 [설계 확인 필요 — 정책 결정 대기]

- 증거: 메인 루프의 컷오프(`ORDER_CUTOFF_MINUTES_BEFORE_CLOSE=20`) 도달 시 `continue`로 전략 실행 전체를 건너뛰어, **마지막 20분 동안 손절 체크가 돌지 않는다.** force_exit 전략은 마감 30분 전에 청산되어 무관하지만, 오버나이트 보유 전략은 종가 급락에 무방비.
- 결정 필요: (a) 의도된 설계라면 상수 주석에 명시, (b) 아니라면 컷오프 이후에도 check_exits 경로만 허용하도록 분리. 신규 진입 차단은 유지.
- [x] 확인 결과 (2026-06-12): **의도된 설계로 판정 (옵션 a)** — 컷오프(15:40 설정−20분=15:20)는 KRX 종가 동시호가 시작과 일치한다. 동시호가 구간은 연속체결이 없어 현재가 기반 exit 판단이 무의미하고, 당일청산 전략은 FORCE_EXIT(마감 30분 전=15:10)가 선행 청산한다. `ORDER_CUTOFF_MINUTES_BEFORE_CLOSE` 상수 주석에 명시. 오버나이트 전략의 종가 부근 청산(동시호가 참여)이 필요해지면 별도 재승격.

### S-4. 매도/매수 병렬 신호 실행의 에러 처리 비대칭 [버그]

- 증거: 매도는 `as_completed` + 직접 await라 한 신호 예외 시 나머지 await가 중단되고(태스크 방치), 매수는 `gather(return_exceptions=True)` 결과를 검사하지 않아 예외를 조용히 삼킨다.
- 왜 위험한가: 매도 실패는 포지션 잔존 = 실전 손실 경로인데 로그조차 안 남을 수 있다.
- [x] 수정 (2026-06-12): `_execute_signals_concurrently()` helper로 매도/매수 통일 — `gather(return_exceptions=True)` + 신호별 예외 ERROR 로그. 매도 예외 시에도 scan 단계까지 진행 (`test_sell_signal_exception_does_not_abort_run_strategy`, `test_buy_signal_exception_is_logged`).

### S-5. `MAX_HISTORY=200` 트림과 이력 기반 복구 로직 충돌 [버그]

- 증거: `_get_force_liquidation_holdings` / `_get_signal_net_qty` / `_has_open_position_evidence`는 `_signal_history`에 당일 전체 이력이 있다고 가정하지만, 메모리 이력은 200건으로 잘린다. 신호가 많은 날 force-exit 복구가 보유 종목을 놓칠 수 있다.
- [x] 수정 (2026-06-12): `_record_signal()` helper로 기록 경로 단일화(메인 + sizing-skip 중복 제거 겸) — 트림 시 당일 레코드 보존, 과거 날짜만 잘림 (`test_history_trim_preserves_today_records`, `test_history_trim_drops_past_date_records_first`).
- [x] `trace_id` 확인 결과 (2026-06-12): store `signal_history` 테이블에 trace_id 컬럼 자체가 없어 복원 불가 → **S-9에서 해소** (컬럼 추가 + ALTER 마이그레이션 + 복원 배선, 같은 날 2차 작업).

### S-6. 날짜 키 set 무한 증가 [수명]

- 증거: `_strategy_failure_alert_keys`(날짜 포함 튜플 키), `_reconciled_dates`(YYYY-MM-DD)는 과거 날짜 항목을 비우는 곳이 없어 장기 구동 시 누적된다.
- [x] 수정 (2026-06-12): `_sync_force_exit_done_date()`에서 날짜 전환 시 `_strategy_failure_alert_keys`(키[0]≠당일 YYYYMMDD)와 `_reconciled_dates`(당일 외) purge (`test_date_rollover_purges_stale_date_keys`).

### S-7. `stop_strategy`가 event router 구독을 해제하지 않음 [수명]

- 증거: `stop_strategy`는 가격 구독 카테고리만 제거하고 `_event_router` 구독과 `_event_shadow_subscriptions`/`_exit_shadow_subscriptions` 항목은 남긴다. 비활성 전략의 shadow evaluator가 계속 틱을 받아 평가를 수행한다.
- [x] 수정 (2026-06-12): `stop_strategy()`에서 entry(`name`)/exit(`name__exit`) router 구독 해제 + 추적 dict pop. 기존 가격 구독 카테고리 제거와 동일 위치 (`test_stop_strategy_unsubscribes_event_shadow_router`).

### S-8. 중복 블록 추출 [구조]

- 증거: ① 신호 stamping(created_at/signal_id/strategy_id/config_hash)이 exit/scan 경로에 ~20줄씩 복제. ② sizing-skip 경로가 메인 경로의 "record 생성→trim→DB→SSE→알림" 시퀀스를 통째로 중복.
- [x] 수정 (2026-06-12): `_stamp_signals(signals, strategy)` 추출(exit/scan 공통), record/notify 중복은 S-5의 `_record_signal()`이 흡수. 동작 동등성은 기존 `test_strategy_scheduler_signal_stamping.py` 등 green 유지로 검증.

### S-9. 대형 구조 개선 [부분 진행 완료 — 잔여는 보류, 브랜치 refactor/scheduler-s9]

- [x] `_prune_disabled_force_exit_state`/`_prune_stale_position_state` 중복 통합 (2026-06-12): 공통 구현 `_prune_position_state_without_evidence(cfg, repo_holdings, allow_signal_history)` 추출, 기존 두 메서드는 가드+플래그만 다른 wrapper 로 유지. 동작 동등성은 기존 prune 테스트로 잠금.
- [x] trace_id 영속화/복원 (2026-06-12, S-5 잔여 해소): store `signal_history`에 `trace_id` 컬럼 추가(신규 DDL + 기존 DB `PRAGMA table_info`→ALTER 마이그레이션, 1-6 `_ensure_trade_columns` 패턴), append/load/load_for_date 배선, `_load_signal_history` 복원. 테스트 5종(영속/부재 기본값/날짜조회/레거시 DB 마이그레이션/복원).
- [x] 미사용 `field` import 제거 (2026-06-12).
- **철회** — `_get_strategy_holdings` getter 부수효과 제거: 조사 결과 `get_status()` 경유 prune 은 **의도된 계약**이었다 (`test_get_status_prunes_disabled_force_exit_strategy_state_*` 등 4개 테스트가 명시적으로 잠금 — 웹 폴링을 stale state janitor 트리거로 사용). 리뷰 판단(C4)이 틀렸으므로 변경하지 않고 `_get_strategy_holdings` docstring 에 의도를 명시.
- **보류** — EventShadowManager 등 god class 분리: 테스트 ~26곳이 scheduler 내부 표면을 직접 잠그고(특히 `scheduler._event_router = ...`/`._event_shadow_journal = ...` **생성 후 재할당** 패턴) 호환 유지에 property setter 등 shim 7~8개가 필요해 순복잡도가 증가한다. 또한 shadow 코드는 P2 2-4 parity 결과에 따라 실주문 승격/폐기가 갈리므로 지금 분리하면 재작업 위험. parity 판정(PR-3 진입 여부) 후 재평가.
- **보류** — LiveStrategy 인터페이스 승격(`_position_state`/`_save_state`/`_bought_today` 정식 메서드화), 동기 DB/CSV 호출 to_thread 전환: 전략 7개 + 호출 경로 전반을 건드려 단독 PR 필요. 인터페이스 승격은 P3 3-4 lifecycle contract 재설계와 함께 다루는 것이 외과적.

### S-10. 경미 [관찰/소규모]

- [x] 확인 결과 (2026-06-12): 주문 성공 알림의 `CRITICAL`은 **의도된 설계** — Telegram `route_levels.STRATEGY=[warning,error,critical]`이 INFO를 통과시키지 않아, 실주문 성공 push 보장에 CRITICAL이 필요하다(`notification_queue_task._should_send_external`). 코드 주석으로 명시, 레벨 변경 없음.
- [x] 수정 (2026-06-12): `_execute_signal_inner` BUY 분기 `else:` 직후 주석 들여쓰기 정렬, `get_signal_history` 타입 힌트 `Optional[str]`.

진행 로그:

- 2026-06-12: 섹션 작성. S-1부터 TDD로 착수.
- 2026-06-12: S-1/S-2/S-4/S-5/S-6/S-7/S-8/S-10(일부) 수정 완료. 신규 테스트 9건(`test_strategy_scheduler_audit_fixes.py`) — 수정 전 7건이 의도된 사유로 실패함을 확인 후 구현. 단위 5885 / 통합 240 passed. 남은 것: S-3(정책), S-9(별도 승인), S-10 CRITICAL 레벨(사용자 확인).
- 2026-06-12 (2차): S-3/S-10 코드 근거 조사로 종결 — 컷오프=동시호가 시작(15:20) 일치, CRITICAL=Telegram route_levels 통과 요건. 둘 다 의도된 설계로 판정하고 주석만 추가(동작 무변경). 남은 것: S-9만 (별도 승인 후 단독 PR).
- 2026-06-12 (3차, S-9): prune 중복 통합 + trace_id 영속화/복원 + 미사용 import 정리 완료. getter 부수효과 제거는 TDD 중 기존 테스트 4건이 의도된 계약임을 발견해 **철회**(docstring 명시로 대체). god class 분리는 shim 비용·P2 2-4 갈림길 근거로 보류. 브랜치 `refactor/scheduler-s9` (#519 위 stacked).

---

## 바로 착수 추천 순서

남은 항목은 외부 데이터/운영 관찰 대기 작업과 코드 수정으로 바로 진행 가능한 작업으로 나뉜다. 실전 자본 보호에 직접 닿는 코드 수정 항목을 우선 정리한다.

1. **외부 운영 확인 후 즉시 진행**
   - 실제 KIS 계정별 REST/WebSocket 유량 한도 재확인 → 필요 시 `_global` 8/s + `_global.emergency` 2/s 운영값 조정 (P2 2-2; 공통 HTTP 경로 강제 주입과 보수 기본값은 적용 완료)

2. **코드 수정으로 바로 진행 가능**
   - ~~WebSocket 틱 루프 TR_ID 캐싱 + debug lazy logging 적용 (P2 2-6)~~ ✅ 완료 (2026-05-31)
   - `LarryWilliamsVBOStrategy.scan()` 잔여 순차 후보 처리 bounded 전환 (P2 2-6) — 2026-05-31 검토 후 **보류**(실익 marginal·실전 진입 경로). 재개 시 2-pass 권장.
   - ~~활성 전략 `check_exits()` 순차 holdings 루프 bounded 통일 (P2 2-6)~~ ✅ 완료 (2026-06-11, VBO 중심 적용)
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
