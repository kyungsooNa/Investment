# Reconcile Failure Policy

최종 업데이트: 2026-05-10

## 배경 / Scope

이 문서는 `OpeningPositionReconcileService.reconcile_once()` 및 관련 reconcile 흐름이 실패했을 때
시스템이 어떻게 동작해야 하는지를 정의한다.

적용 범위:
- **Opening reconcile**: 장 시작 직후 1회 실행 (`task/background/intraday/opening_position_reconcile_task.py`)
- **After-market reconcile**: 장 종료 후 실행 (`task/background/after_market/after_market_reconcile_task.py`)
- **Polling reconcile**: `OrderExecutionService.poll_active_orders_once()` 의 주문 상태 polling

---

## 운영 매트릭스

| 시나리오 | 현재 동작 | 신규 주문 | KillSwitch | 비고 |
|---|---|---|---|---|
| broker balance fetch 실패 (1회) | 경고 로그 + `record_api_failure` 호출 | **허용** | 트립 안 함 (카운터 +1) | `max_consecutive_api_errors` 미만이면 트립 없음 |
| broker balance fetch 실패 (연속 N회) | 경고 + `record_api_failure` → KillSwitch 트립 | **차단** | **트립** | N = `KillSwitchConfig.max_consecutive_api_errors` |
| `reconcile_with_broker` 내부 예외 | 예외 로그, force_close 미적용, error 반환 | **허용** | 트립 안 함 | VirtualTradeService 내부 예외 |
| force_close 시도 중 `log_sell_async` 실패 | 경고 로그 + notification emit | **허용** (해당 종목 외) | 트립 안 함 | 종목 단위 격리 — 전체 차단 없음 |
| reconcile task가 윈도우 내 1회도 실행 못 함 | `_last_checked_date` 미설정 → 다음 윈도우 재시도 | **허용** | 트립 안 함 | 다음 장 시작 직후 재시도 |

---

## KillSwitch 연동 규칙

### 카운터 기반 트립

- `OpeningPositionReconcileService.reconcile_once()` 에서 broker balance 조회 실패 시:
  ```python
  await self._kill_switch.record_api_failure("opening_reconcile: <msg>")
  ```
  이 호출은 `KillSwitchService._consecutive_api_errors` 를 +1한다.

- 성공 시 카운터 초기화:
  - reconcile이 성공하면 `KillSwitchService.record_api_success()` 를 호출해 카운터를 0으로 리셋한다.
  - **현재 구현**: 성공 시 `record_api_success` 는 자동 호출되지 않는다. 추후 호출 시점 추가를 고려할 수 있다.

- 임계치: `KillSwitchConfig.max_consecutive_api_errors` (설정 파일에서 관리)

### 트립 해제

- KillSwitch trip 상태에서 주문은 `check_orders_allowed()` 가 `(False, reason)` 을 반환한다.
- 수동 해제: 관리자가 `KillSwitchService.reset_trip()` 을 호출하거나, 재시작 시 `kill_switch_state.json` 의 `is_tripped` 를 `false` 로 수정한다.

---

## 알림 채널

- reconcile 실패 및 KillSwitch trip 발생 시:
  - `KillSwitchService._notif.emit(...)` 으로 `notification_service` 에 위임
  - 텔레그램 reporter 연동: `notification_service` 설정에 따라 자동 전달

---

## 수동 복구 절차

1. 트립 원인 파악: 로그에서 `[KillSwitch] trip` 메시지 및 `reason` 확인
2. broker API 장애 여부 확인 (한국투자증권 상태 페이지 or 로그)
3. KillSwitch 해제:
   ```bash
   # data/kill_switch_state.json 편집
   { "is_tripped": false, "consecutive_api_errors": 0, ... }
   ```
   또는 관리자 API가 있다면 `/api/kill-switch/reset` 엔드포인트 호출
4. 서비스 재시작 없이 runtime reset: `KillSwitchService.reset_trip()` 직접 호출 (관리 콘솔 또는 debug endpoint)

---

## 테스트 매핑

`tests/integration_test/test_it_reconcile_failure_policy.py` 의 테스트 함수는 위 매트릭스 행과 1:1 매핑된다.

| 매트릭스 행 | 테스트 함수명 |
|---|---|
| broker balance fetch 실패 (1회) | `test_single_broker_fetch_failure_records_api_failure` |
| broker balance fetch 실패 + 연속 N회 → KillSwitch 트립 | `test_consecutive_broker_fetch_failures_trip_kill_switch` |
| `reconcile_with_broker` 내부 예외 | `test_reconcile_with_broker_exception_does_not_trip_kill_switch` |
| force_close 시도 중 매도 실패 | `test_force_close_sell_failure_emits_notification` |
| reconcile task 윈도우 미실행 | `test_reconcile_task_window_miss_allows_retry_next_window` |
