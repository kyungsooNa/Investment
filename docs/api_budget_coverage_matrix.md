# API Budget Coverage Matrix

최종 업데이트: 2026-05-27

이 문서는 broker API 호출이 어떤 `ApiBudgetLimiter` category와 lane을 사용하는지 고정한다. 실제 KIS 계정별 유량 한도 숫자는 운영 전 별도 재확인이 필요하며, 아래 기본값은 실전 보호용 보수 운영값이다.

## 기본 유량 정책

2026-05-27 공개 자료 재확인 결과:

- 한국투자증권 공식 GitHub 샘플 저장소는 `EGW00201` 초당 거래건수 초과와 모의투자 REST 제한이 낮다는 점을 안내하지만, 계정별 숫자 표를 공개 README에 명시하지 않는다.
- 공개 운영 글의 수치는 실전 약 20/s, 모의 약 1~2/s, 과거 개인 10/s 등으로 갈린다.
- 따라서 코드 기본값은 계정별 확인 전까지 개인 실전 10/s 가능성에도 맞는 보수값으로 둔다.

기본값:

- normal 전역 bucket: `8/s`
- emergency 전역 bucket: `2/s`
- 총합: normal 8/s + emergency 2/s = 최대 10/s 수준

category별 rate limit은 endpoint군 간 burst를 나누기 위한 상한이며, 전체 합산 호출량은 `_global` bucket이 다시 제한한다.

## 런타임 원칙

- 조회성 REST API는 `ClientWithRetryQueue`를 통해 retry queue와 budget limiter를 모두 지난다.
- 주문/WebSocket API는 멱등성 또는 연결 상태 문제 때문에 retry queue는 우회하지만, budget limiter는 직접 지난다.
- `OrderExecutionService.sell_all_stocks(..., mode=ClearanceMode.EMERGENCY)`는 `emergency_scope()` 안에서 주문을 제출하므로 `order_submit`/`order_cancel` emergency lane을 사용한다.
- emergency lane이 정의되지 않은 category는 normal lane으로 fallback한다.
- `_global` normal bucket은 모든 normal 호출의 전체 합산 RPS를 제한한다.
- `_global.emergency` bucket은 긴급 청산 호출의 전체 합산 RPS를 normal traffic과 분리한다.

## Coverage Matrix

| Operation | Method | Category | Execution path | Lane |
| --- | --- | --- | --- | --- |
| 현재가 REST | `get_current_price` | `quotation_price` | retry queue | normal |
| 일봉/기간 OHLCV REST | `inquire_daily_itemchartprice` | `quotation_ohlcv` | retry queue | normal |
| 당일 분봉 REST | `inquire_time_itemchartprice` | `quotation_ohlcv` | retry queue | normal |
| 일별 분봉 REST | `inquire_time_dailychartprice` | `quotation_ohlcv` | retry queue | normal |
| 체결강도/체결 REST | `get_current_conclusion` | `quotation_conclusion` | retry queue | normal |
| 계좌/잔고 REST | `get_account_balance` | `account_balance` | retry queue | normal |
| 체결/미체결 대사 REST | `inquire_daily_ccld` | `account_reconciliation` | retry queue | normal |
| 미체결 대사 REST | `inquire_unfilled_orders` | `account_reconciliation` | retry queue | normal |
| 체결 이력 대사 REST | `inquire_filled_history` | `account_reconciliation` | retry queue | normal |
| 주문 제출 REST | `place_stock_order` | `order_submit` | direct budget only | normal |
| 주문 취소 REST | `cancel_stock_order` | `order_cancel` | direct budget only | normal |
| WebSocket 연결 | `connect_websocket` | `websocket_connect` | direct budget only | normal |
| WebSocket 연결 해제 | `disconnect_websocket` | `websocket_connect` | direct budget only | normal |
| 실시간 현재가 구독 | `subscribe_realtime_price` | `websocket_subscribe` | direct budget only | normal |
| 실시간 현재가 구독 해제 | `unsubscribe_realtime_price` | `websocket_subscribe` | direct budget only | normal |
| 실시간 호가 구독 | `subscribe_realtime_quote` | `websocket_subscribe` | direct budget only | normal |
| 실시간 호가 구독 해제 | `unsubscribe_realtime_quote` | `websocket_subscribe` | direct budget only | normal |
| 프로그램매매 구독 | `subscribe_program_trading` | `websocket_subscribe` | direct budget only | normal |
| 프로그램매매 구독 해제 | `unsubscribe_program_trading` | `websocket_subscribe` | direct budget only | normal |
| 통합 현재가 구독 | `subscribe_unified_price` | `websocket_subscribe` | direct budget only | normal |
| 통합 현재가 구독 해제 | `unsubscribe_unified_price` | `websocket_subscribe` | direct budget only | normal |
| 체결통보 구독 | `subscribe_order_notice` | `websocket_subscribe` | direct budget only | normal |
| 체결통보 구독 해제 | `unsubscribe_order_notice` | `websocket_subscribe` | direct budget only | normal |
| Emergency 전체청산 주문 제출 | `place_stock_order` | `order_submit` | direct budget only | emergency |
| Emergency 전체청산 주문 취소 | `cancel_stock_order` | `order_cancel` | direct budget only | emergency |

## 테스트 고정

- `tests/unit_test/core/retry_queue/test_client_with_retry_queue.py::TestApiBudgetCoverageMatrix`가 위 매트릭스와 런타임 라우팅 상수의 일치를 검증한다.
- `tests/unit_test/core/retry_queue/test_api_budget_limiter.py::test_opening_burst_load_keeps_order_reconcile_and_emergency_lanes_available`가 장초반 조회 burst 중에도 `account_reconciliation`, normal `order_submit`, emergency `order_submit` lane이 독립적으로 진입하는지 검증한다.
- `tests/unit_test/core/retry_queue/test_api_budget_limiter.py::test_global_rate_limiter_caps_total_rps_across_categories`가 서로 다른 category 호출도 `_global` bucket으로 전체 합산 제한되는지 검증한다.
- `tests/unit_test/core/retry_queue/test_api_budget_limiter.py::test_emergency_global_rate_bucket_is_independent_from_normal_global_bucket`가 emergency global bucket이 normal global bucket과 분리되는지 검증한다.
- `services.predeploy_check_service.PreDeployCheckService.check_api_budget_limiter()`는 `_global` 및 `_global.emergency` bucket 누락을 WARN으로 보고한다.
- `ApiBudgetLimiter.snapshot()`은 `rate_wait_total`과 `rate_wait_seconds_total`을 노출해 사전 throttle이 실제로 작동했는지 운영 상태 API에서 관측할 수 있다.
