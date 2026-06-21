# strategy_scheduler.py 분할 설계안

작성일: 2026-06-21 · 상태: 제안(미착수)

> 이 문서는 코드 변경이 아니라 분할 **계획**이다. 실제 추출은 별도 PR로,
> 각 단계마다 행위 보존을 보장하는 characterization 테스트를 먼저 깔고 진행한다.

## 1. 문제

- `scheduler/strategy_scheduler.py` = **2,411줄 / 단일 God class `StrategyScheduler`** (82개 메서드).
- `CODEBASE_SUMMARY.md`에서 이미 "영향 범위가 커서 조심해야 하는 영역"으로 분류됨.
  장중 동작 전체(전략 실행·강제청산·원장 대사·signal history·실행 간격)가 한 클래스에 응집.
- 단일 파일 비대화로 인한 비용:
  - 한 메서드 수정이 무관한 책임에 회귀를 일으킬 위험.
  - `AGENTS.md` hang 패턴(백그라운드 task·sleep·mock)과 직결되어 테스트 설계가 어려움.
  - 신규 작업자가 변경 지점을 좁히기 어렵다.

## 2. 이미 추출된 선례 (이 패턴을 따른다)

- `scheduler/strategy_scheduler_store.py` → `StrategySchedulerStore` (274줄)
  - SQLite 영속화(signal history, scheduler state, keyed value, 레거시 CSV/JSON 마이그레이션)를
    **컴포지션으로 분리**해 둔 상태. 스케줄러는 이 store에 위임한다.
- 분할 방향은 이와 동일하게 **상속이 아닌 컴포지션** — 협력 객체를 `StrategyScheduler`에 주입(DI)하고
  `StrategyScheduler`는 오케스트레이션(루프/순서/상태 보유)만 남긴다.
- `StrategyScheduler`의 **공개 API는 그대로 유지**한다
  (`register`, `start`, `stop`, `start_strategy`, `stop_strategy`, `update_max_positions`,
  `get_status`, `restore_state`, `clear_saved_state`, `get_signal_history`,
  `create_subscriber_queue`, `remove_subscriber_queue`, `close`).
  호출부(`task/background/intraday/strategy_scheduler_task_adapter.py`,
  `view/web/routes/scheduler.py`, `view/web/web_app_initializer.py`)는 변경하지 않는다.

## 3. 책임 군집 (현재 메서드 → 제안 모듈)

현 메서드를 명명/라인 인접도 기준으로 묶으면 아래 7개 응집 단위가 보인다.

### A. 코어 오케스트레이션 (분할 후 `StrategyScheduler`에 잔류)
`__init__`, `register`, `start`, `stop`, `_loop`, `close`, `get_status`,
`update_max_positions`, `start_strategy`, `stop_strategy`,
`_is_scan_time_window_blocked`, `_get_order_cutoff_time`, `_is_after_order_cutoff`
→ 루프·생명주기·시간창 판정. 다른 협력 객체를 호출만 한다.

### B. 전략 실행기 → `SignalExecutionService` (가장 큰 덩어리, ~L446-1180)
`_run_strategy`, `_stamp_signals`, `_execute_signals_concurrently`, `_execute_signal`,
`_execute_signal_inner`, `_check_live_expansion_gate`, `_log_signal_to_order_latency`,
`_should_emit_strategy_signal_notification`, `_estimate_return_rate_from_hold`,
`_log_position_limit_rejections`, `_rollback_rejected_buy_states`,
`_exclude_order_policy_blocked_code`,
+ payload/kwargs 빌더(`_strategy_notification_payload`, `_virtual_trade_log_kwargs`,
`_market_regime_log_kwargs`, `_signal_price_policy_kwargs`)
→ 신호 생성→게이트→주문→알림/로그까지의 실행 경로. 단일 책임으로 가장 명확.

### C. 강제청산 → `ForceLiquidationService` (~L1507-1700, 1507 전후)
`_force_liquidate_strategy`, `_get_force_liquidation_holdings`,
`_has_active_buy_order_for_force_exit`, `_get_broker_position_map_for_force_exit`,
`_normalize_broker_position_map`, `_parse_position_qty`, `_sync_force_exit_done_date`,
`_clear_force_exit_position_state`, `_prune_disabled_force_exit_state`
→ 장 마감 전 강제 청산 + 관련 상태 정리.

### D. 이벤트/엑싯 섀도 → `EventShadowCoordinator` (자기완결적, ~L1180-1506 ≈ 320줄)
`_refresh_event_shadow_subscriptions`, `_tick_ingest_snapshot_for`,
`_sync_event_shadow_price_subscriptions`, `_event_shadow_category_key`,
`_build_shadow_evaluator`, `_event_shadow_date_str`, `_record_event_shadow_signal`,
`_record_event_shadow_status`, `_flush_event_shadow_journal`,
`_exit_shadow_category_key`, `_exit_shadow_subscriber_name`,
`_refresh_exit_shadow_subscriptions`, `_build_exit_shadow_evaluator`
→ 메서드 이름 접두사가 일관되고 라인이 연속 → **추출 1순위(가장 저위험)**.

### E. 원장 대사 → `ReconciliationService` (~L1715, _poll L371)
`_run_reconciliation`, `_poll_active_orders_if_due`, `_source_for_signal`,
`_resolve_strategy_sell_price`, `_extract_best_bid`, `_clear_reconciled_position_state`
→ 당일 1회 원장 대사 + 미체결 주문 폴링.

### F. 포지션 상태 → `PositionStateTracker` (~L1889-2175)
`_get_signal_net_qty`, `_get_latest_open_buy_record`, `_is_valid_strategy_code`,
`_get_strategy_position_state`, `_persist_strategy_position_state`,
`_has_open_position_evidence`, `_prune_stale_position_state`,
`_prune_position_state_without_evidence`, `_build_strategy_state_holding`,
`_get_strategy_holdings`, `_get_all_current_positions`,
`_current_signal_date_prefix`, `_signal_record_on_date`
→ 전략별 포지션 상태 생성/검증/정리/조회.

### G. signal history & pub/sub (대부분 이미 store에 위임)
`_record_signal`, `_append_signal_db`, `get_signal_history`, `_load_signal_history`,
`_save_scheduler_state`, `restore_state`, `clear_saved_state`
→ `StrategySchedulerStore` 위임 비중이 크므로 **신규 모듈 불필요**. 잔존 로직만 정리.
`create_subscriber_queue`, `remove_subscriber_queue`, `_notify_subscribers`
→ 소규모 → `SubscriberHub`로 뽑을 수 있으나 ROI 낮음(선택).

## 4. 권장 추출 순서 (저위험 → 고위험)

| 순서 | 추출 대상 | 근거 | 위험 |
|---|---|---|---|
| 1 | D. EventShadowCoordinator | 라인 연속·접두사 일관·진단용(매매 경로 밖) | 낮음 |
| 2 | C. ForceLiquidationService | 경계 명확, 호출 진입점 1곳(`_loop`) | 중간 |
| 3 | F. PositionStateTracker | 순수 상태 로직 다수, mock 용이 | 중간 |
| 4 | E. ReconciliationService | 브로커/가격 의존 → mock 설계 주의 | 중간 |
| 5 | B. SignalExecutionService | 가장 크고 핵심 경로 → 마지막에, 위 추출로 의존성 축소된 후 | 높음 |

각 단계 후 `StrategyScheduler`는 해당 협력 객체를 보유하고 위임. 한 PR = 한 군집.

## 5. 테스트 전략 (단계 불변식)

- **추출 전**: 대상 군집의 외부 관찰 가능 행위를 고정하는 characterization 테스트를 먼저 추가
  (없으면 작성). `-n0` 단독 실행으로 먼저 통과 확인.
- **추출 후**: 동일 테스트가 그대로 통과(행위 보존)해야 한다. 공개 API 시그니처 불변.
- `AGENTS.md` hang 가이드 준수:
  - background task `start()` 시 `finally`에서 `await stop()`.
  - mock 반환값은 `ResCommonResponse`.
  - `asyncio.sleep` 패치 범위가 실제 호출 모듈까지 덮는지 확인.
- 회귀 검증: `tests/unit_test/scheduler/*`, `tests/integration_test/test_it_order_fsm_e2e.py`,
  `test_it_reconcile_*`, `test_strategies_logging_integration.py`.

## 6. 명시적 비목표

- 동작/타이밍/주문 정책 변경 금지 — **순수 구조 분할**.
- 공개 API·호출부 변경 금지.
- 한 번에 전체 분할 금지 — 위 순서대로 단계 PR.

## 7. 기대 효과

- 2,411줄 단일 파일 → 코어 오케스트레이터(~600-800줄) + 5개 협력 모듈(각 200-400줄).
- 변경 지점 국소화, 테스트 mock 표면 축소, 신규 작업자 진입 비용 감소.
