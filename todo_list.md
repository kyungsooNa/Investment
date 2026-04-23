# Investment Trading App - Refined To-Do List

## 2026-04-23 Update - Order FSM / Execution Notice / Polling

### Done This Session
- [x] Added `OrderExecutionReport` as a common event model for WebSocket execution notices and order-query polling.
- [x] Added domestic stock execution notice parsing for `H0STCNI0` (real) and `H0STCNI9` (paper).
- [x] Added `htsid` based execution-notice subscribe/unsubscribe delegation through WebSocket API, KIS client, broker wrapper, and streaming service.
- [x] Registered `signing_notice` handling in `WebAppContext` so execution notices can update `OrderExecutionService`.
- [x] Added idempotent FSM event application: duplicate event prevention, partial-fill accumulation, `FILLED`, `CANCELED`, and `REJECTED` transitions.
- [x] Added KIS `inquire-daily-ccld` endpoint, TR IDs, params, account API method, client delegation, and broker wrapper delegation.
- [x] Added `OrderExecutionService.poll_active_orders_once()` to reconcile currently active orders via order-query polling.
- [x] Removed scheduler-side forced `FILLED` transition after API order success so actual execution notice/polling can finalize order state.
- [x] Added unit tests for execution notice parsing/subscription, FSM event application, polling reconciliation, and KIS delegation paths.
- [x] Verified direct impact scope: `375 passed`.
- [x] Verified syntax/import health with `compileall`.
- [x] Added bounded lifecycle management for `_processed_execution_events`.
- [x] Added out-of-order regression coverage for partial fill followed by late acceptance notice.
- [x] Wired `poll_active_orders_once()` into the strategy scheduler loop as a lightweight periodic reconciliation caller.
- [x] Hardened signing-notice parsing for short/drifted payloads and escalated missing real-trading `htsid`.
- [x] Hardened `from_order_query()` rejected/canceled edge cases for missing quantity fields.

### Remaining Work
- [ ] Decide whether virtual trade records should be written on API order acceptance or only after actual `FILLED` execution.
  - Current state: scheduler still writes virtual trade records after API order success.
  - Safer target: write/update virtual trades from `OrderExecutionService` when execution events confirm fills.
- [ ] Validate real KIS `inquire-daily-ccld` response fields in both paper and real environments.
  - Confirm order number, stock code, side, order qty, cumulative fill qty, remaining qty, average fill price, cancel/reject fields.
- [ ] Add cancellation API integration using `broker_order_no`.
  - Target FSM methods already exist conceptually (`mark_order_canceled`, `OrderState.CANCELED`), but broker cancel request flow is not wired.
- [ ] Add an operation-level fallback policy for missed WebSocket notices.
  - Example: run polling after order submit until terminal state, then slow down or stop polling for that order.
- [ ] Add notification/logging for orders stuck in `SUBMITTED` or `PARTIAL_FILLED` beyond a threshold.
- [ ] Investigate full `tests/unit_test` timeout separately.
  - Direct impact tests pass, but full suite hit the 5-minute command timeout during this session.
  - Check the hang guide patterns first: retry queue with plain dict mocks and background `asyncio.create_task()` loops not stopped.

### Recommended Next Order
1. Move strategy virtual-trade persistence from "API accepted" toward "execution confirmed".
2. Validate KIS polling fields with captured paper-trading responses.
3. Implement cancel request path and map cancel responses back into FSM.
4. Add an operation-level fallback policy for missed WebSocket notices.
5. Add stuck-order notification/logging for long-lived `SUBMITTED` / `PARTIAL_FILLED`.
6. Investigate full `tests/unit_test` timeout separately.

최종 업데이트: 2026-04-23

이 문서는 현재 코드베이스와 `CODEX_WORKFLOW.md`, `CODEBASE_SUMMARY.md` 기준으로 다시 정리한 실행용 To-Do다.

정리 원칙:

- 이미 적용된 내용은 신규 도입 과제가 아니라 적용 범위 점검/고도화 과제로 적는다.
- 큰 주제는 바로 착수 가능한 단위로 쪼갠다.
- 우선순위는 실전 운영 위험도, 장애 가능성, 회귀 영향도를 기준으로 둔다.
- 가능하면 `route/service`부터 해결하고, 조립부(`web_app_initializer.py`) 수정은 뒤로 미룬다.

---

## Tier 1. 운영 안정성 / 실전 안전장치

실거래 계좌 보호, 운영 중 장애 방지, 중복 주문/데이터 누락 방지가 최우선이다.

- [ ] 구독 종목 복구 지연 개선
  - 목표: 프로그램매매/실시간 구독 복구 정보를 tick 단위가 아니라 배치 단위로 저장해 DB 쓰기 부담과 복구 지연을 줄인다.
  - 대상:
    - `services/program_trading_stream_service.py`
    - `repositories/program_trading_repo.py`

- [ ] 스트리밍 데이터 수신 누락 원인 정리 및 수정
  - 목표: 현재가 조회 실패 종목, 웹소켓 구독 누락, 재구독 실패를 구분해서 진짜 누락 원인을 고친다.
  - 대상:
    - `services/streaming_service.py`
    - `services/price_stream_service.py`
    - `services/price_subscription_service.py`
    - `brokers/korea_investment/korea_invest_websocket_api.py`
    - `task/background/intraday/websocket_watchdog_task.py`
  - 장중 실검증 체크리스트:
    - 실제 장중에 `python main.py --web` 실행 후 종목 2~3개를 구독한다.
    - 체결가가 정상 수신될 때 `PriceStreamService` 캐시와 관련 스트리밍 로그가 갱신되는지 확인한다.
    - WebSocket 수신 중단을 유도한 뒤 `price_data_gap_*` trigger로 재연결이 발생하는지 확인한다.
    - 재연결 이후 동일 종목의 체결가 수신이 다시 살아나는지 확인한다.
    - PT 데이터는 오는데 체결가가 없을 때 `not_subscribed` 또는 `subscribed_no_tick` 로그가 기대대로 남는지 확인한다.
    - 현재가 REST 조회 실패 상황에서 `rest_failed` 로그가 남는지 확인한다.
    - 장 마감 후 조용한 상태를 장애로 오탐하지 않는지 로그를 함께 점검한다.


- [ ] 주문 상태 기계(FSM) 도입
  - 목표: `PENDING_SUBMIT -> SUBMITTED -> PARTIAL_FILLED -> FILLED/CANCELED` 같은 명시적 상태 전이로 중복 주문과 race condition을 줄인다.
  - 대상:
    - `common/types.py`
    - `services/order_execution_service.py`
    - 필요 시 `scheduler/strategy_scheduler.py`

- [ ] 계좌 보호용 킬 스위치 추가
  - 목표: 일손실 한도, 연속 손실, 비정상 응답 반복, 체결 이상 시 자동으로 주문/전략 실행을 막는다.
  - 대상:
    - `services/order_execution_service.py`
    - `scheduler/strategy_scheduler.py`
    - `services/notification_service.py`

- [ ] 포지션 사이징 고도화
  - 목표: ATR 또는 변동성 기반으로 진입 수량을 동적으로 제한하고 계좌 노출을 제어한다.
  - 대상:
    - `services/order_execution_service.py`
    - `services/indicator_service.py`
    - 필요 시 신규 유틸 모듈

- [ ] 주문 추적용 Trace ID 파이프라인 구축
  - 목표: 신호 생성부터 주문 전송, 체결, 취소, 알림까지 하나의 ID로 추적 가능하게 만든다.
  - 대상:
    - `core/loggers/app_logger.py`
    - `core/loggers/json_formatter.py`
    - `services/order_execution_service.py`
    - `scheduler/strategy_scheduler.py`

---

## Tier 2. 구조 안정화 / 유지보수성

이 프로젝트는 기능보다 조립 구조와 생명주기 리스크가 커서, 운영 안정성을 위해 구조 정리가 필요하다.

- [ ] `WebAppContext` 비대화 해소
  - 목표: 브로커 생성, 서비스 조립, 전략 등록, 태스크 초기화를 단계적으로 분리한다.
  - 방향:
    - broker bootstrap
    - service bootstrap
    - strategy/task bootstrap
  - 대상:
    - `view/web/web_app_initializer.py`

- [ ] 서비스 초기화 순서 의존 제거
  - 목표: 후주입 방식으로 서로 얽힌 서비스 의존성을 줄이고 테스트 누락 위험을 낮춘다.
  - 대상:
    - `view/web/web_app_initializer.py`
    - 관련 서비스 파일 전반

- [ ] background task 공통 lifecycle 점검 및 표준화
  - 목표: `start()`, `stop()`, `cancel`, long sleep, restore 경로를 공통 규약으로 맞추고 hang 리스크를 줄인다.
  - 대상:
    - `task/background/*`
    - `scheduler/strategy_scheduler.py`
    - 관련 테스트

- [ ] `BrokerAPIWrapper` 테스트 안전장치 표준화
  - 목표: retry/cache wrapper 때문에 발생하는 hang 패턴을 fixture/helper로 표준화한다.
  - 대상:
    - `brokers/broker_api_wrapper.py`
    - `tests/unit_test/**`
    - `tests/integration_test/**`
    - `tests/**/conftest.py`


- [ ] 실행 진입점 문서 정합성 정리
  - 목표: 문서상 CLI/Web 설명과 실제 `main.py` 동작의 차이를 정리한다.
  - 대상:
    - `main.py`
    - `AGENTS.md`
    - `CODEBASE_SUMMARY.md`
    - `README` 또는 관련 문서

---

## Tier 3. 성능 / 병목 해소

이미 적용된 최적화는 유지하되, 실제 병목이 남아 있는 구간만 좁혀서 개선한다.

- [ ] DB WAL 적용 범위 점검 및 벌크 쓰기 도입
  - 상태:
    - WAL 자체는 일부 저장소/캐시에 이미 적용되어 있음.
  - 목표:
    - tick/스트림/리포트성 데이터의 잦은 쓰기를 batch insert 또는 queue flush 방식으로 줄인다.
  - 대상:
    - `core/cache/db_cache.py`
    - `repositories/program_trading_repo.py`
    - 관련 DB writer 코드

- [ ] 실시간 지표 계산의 buffer 구조 개선
  - 상태:
    - `orjson`은 이미 여러 핵심 경로에 적용되어 있음.
  - 목표:
    - Pandas append/concat 의존 구간이 있으면 `deque` 또는 circular buffer로 교체한다.
  - 대상:
    - `services/indicator_service.py`
    - `strategies/strategy_executor.py`
    - 필요 시 `scheduler/worker/worker_pool.py`

- [ ] I/O와 CPU 연산 격리 검토
  - 목표: 전략 대량 평가나 지표 재계산이 event loop를 막는지 측정한 뒤, 필요한 구간만 `ProcessPoolExecutor`로 분리한다.
  - 대상:
    - `strategies/strategy_executor.py`
    - `scheduler/worker/worker_pool.py`
    - `scheduler/strategy_scheduler.py`

- [ ] HTTP 세션 재사용 실태 점검
  - 목표: 주문/조회 시 세션 재생성이 반복되는지 먼저 확인하고, 필요할 때만 keep-alive/pool 전략을 강화한다.
  - 대상:
    - `brokers/korea_investment/korea_invest_api_base.py`
    - `brokers/korea_investment/korea_invest_client.py`
    - `view/web/web_app_initializer.py`

---

## Tier 4. 전략 / 백테스트 / 실행 분리

전략 품질 향상도 중요하지만, 실전 실행 엔진과 강하게 얽혀 있으면 확장과 검증이 어려워진다.

- [ ] 전략 신호와 실행 책임 분리
  - 목표: 전략은 시그널만 발행하고, 실행 서비스가 실제 주문/가상주문/기록 여부를 결정하도록 분리한다.
  - 대상:
    - `scheduler/ticket_queue/message_broker.py`
    - `strategies/strategy_executor.py`
    - `services/order_execution_service.py`

- [ ] 백테스트용 가상 브로커/Mock 실행 엔진 도입
  - 목표: 실전 주문 흐름과 유사한 인터페이스로 전략 재사용성과 현실성을 높인다.
  - 대상:
    - `interfaces/strategy.py`
    - `strategies/backtest_data_provider.py`
    - `services/virtual_trade_service.py`

- [ ] 전략 후보군/필터 계층 고도화
  - 목표: 오닐, Minervini, RS, 신고가 기반 후보군 선별 로직을 전략과 분리해서 재사용 가능하게 정리한다.
  - 대상:
    - `services/oneil_universe_service.py`
    - `services/minervini_stage_service.py`
    - `services/rs_rating_service.py`
    - `services/newhigh_service.py`

- [ ] 전략 노후화 감지 및 자동 관망 전환
  - 목표: 전략별 최근 실거래 성과를 기준으로 경고 또는 자동 비활성화 기준을 둔다.
  - 대상:
    - `services/strategy_log_report_service.py`
    - `task/background/after_market/strategy_log_report_task.py`
    - 필요 시 `scheduler/strategy_scheduler.py`

---

## Tier 5. 테스트 / 관측성 / 운영성

실전 시스템은 기능 구현보다 장애를 빨리 찾고 안전하게 복구하는 능력이 중요하다.

- [ ] 네트워크 장애/결함 주입 테스트 추가
  - 목표: KIS API 500, rate limit, websocket 끊김, 지연 수신을 모의하는 테스트를 추가한다.
  - 대상:
    - `tests/integration_test/test_it_crash_handler.py`
    - `core/retry_queue/client_with_retry_queue.py`
    - 스트리밍 관련 테스트

- [ ] queue/backpressure 부하 테스트 추가
  - 목표: 순간적인 tick 폭주 시 OOM 없이 drop/backpressure가 동작하는지 검증한다.
  - 대상:
    - `scheduler/ticket_queue/message_broker.py`
    - `services/streaming_service.py`
    - 관련 테스트

- [ ] 운영 로그 블랙박스화
  - 목표: 주문 실패, 슬리피지, 재시도 폭주, 복구 이벤트를 짧은 시간 안에 추적 가능하게 로그 구조를 표준화한다.
  - 대상:
    - `core/loggers/app_logger.py`
    - `core/loggers/json_formatter.py`
    - `core/loggers/streaming_event_logger.py`

- [ ] 인증/인프라/UI 항목 분리 정리
  - 목표: 한 항목에 묶인 서로 다른 작업을 분리한다.
  - 세부:
    - 인증 강화: `view/web/routes/auth.py`
    - 컨테이너화: `Dockerfile` 신규 여부 검토
    - 가상매매 UI 개선: `view/web/templates/virtual.html`

---

## 이미 적용되어 있어 재정의한 항목

아래 항목은 "신규 도입"이 아니라 "적용 범위 점검/고도화"로 보는 것이 맞다.

- `orjson`
  - 이미 적용된 경로 예:
    - `brokers/korea_investment/korea_invest_api_base.py`
    - `brokers/korea_investment/korea_invest_websocket_api.py`
    - `core/loggers/json_formatter.py`
    - `scheduler/strategy_scheduler.py`

- SQLite WAL
  - 이미 적용된 경로 예:
    - `core/cache/db_cache.py`
    - 일부 repository 구현

- 인증/UI 일부 기반
  - 이미 존재:
    - `view/web/routes/auth.py`
    - `view/web/templates/virtual.html`
  - 아직 없음:
    - `Dockerfile`

---

## 지금 바로 착수 추천 순서

1. 스트리밍 수신 누락 원인 정리 및 수정
2. 주문 상태 기계(FSM) 도입 설계
3. `WebAppContext` 비대화 범위 정의
4. background task lifecycle 표준화

이 5개가 현재 코드베이스와 운영 리스크 기준으로 가장 투자 대비 효과가 크다.
