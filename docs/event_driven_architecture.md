# Event-Driven Architecture Plan (P2 2-4)

> 상태: 설계안 (구현 전)
> 작성일: 2026-05-17
> 관련 TODO: `todo_list.md` P2 2-4

## 1. 목표

폴링 기반 `StrategyScheduler._loop()`을 점진적으로 이벤트 기반 평가로 전환하여:

- **반응 지연 단축**: `interval_minutes` 주기(현재 1~5분) 대신 체결 이벤트 도착 즉시 평가 후보 검토.
- **API 호출 절감**: 같은 데이터를 반복 폴링하지 않고, snapshot 업데이트 시점에만 평가.
- **전략 자원 충돌 완화**: 평가가 시간 균등 분포로 옮겨가 `STAGGER_INTERVAL_SEC` 의존도 감소.

**비범위 (Non-goals)**
- 폴링 루프 완전 제거. 시간 기반 강제 청산(`force_exit_on_close`), 원장 대사(`_run_reconciliation`), 미체결 주문 폴(`_poll_active_orders_if_due`), 비활성/저빈도 전략은 폴링 유지.
- 백테스트 경로 변경. `BacktestPeriodRunner`/`BacktestExecutionSimulator`는 결정론적 bar-by-bar 진행을 유지.
- 신규 WebSocket 채널 도입. 기존 `H0STCNT0`(체결가) / `H0STASP0`(호가) / `H0STCNI0`(체결통보) 범위 내.

## 2. 현재 아키텍처 (Polling)

```
                 ┌─────────────────────────────┐
                 │ StrategyScheduler._loop()   │
                 │   LOOP_INTERVAL_SEC=1       │
                 │   while market_open:        │
                 │     for cfg in strategies:  │
                 │       if elapsed >= interval│
                 │         run_strategy(cfg)   │
                 └────────────┬────────────────┘
                              │ 매 N분마다
                              ▼
                  ┌───────────────────────┐
                  │ Strategy.execute(...) │  ← 종목 리스트 전체 평가
                  │   StrategyExecutor    │     (liquidity → stage → signal)
                  └───────────────────────┘

(병렬 흐름)
WebSocket → on_realtime_message_callback → PriceStreamService.on_price_tick()
                                              └ snapshot 캐시 갱신만 수행
                                                (전략 평가 트리거 없음)
```

**현재의 문제**
- WebSocket 캐시는 풍부하지만 전략은 다음 폴링 주기까지 활용 불가 → 신호 지연.
- 후보 종목 50개 × 1분 주기 = 분당 50회 평가가 시간차 없이 묶여 실행 (STAGGER_INTERVAL_SEC=60초로 강제 분산).
- 활성 후보군이 줄어든 시점에도 동일 주기로 풀스캔.

## 3. 목표 아키텍처 (Event-Driven Hybrid)

폴링 루프는 유지하되, **HIGH 우선순위 종목**에 한해 이벤트 트리거 평가를 추가하는 **하이브리드** 모델.

```
WebSocket realtime tick
  └→ KoreaInvestWebSocketAPI.on_realtime_message_callback
      └→ PriceStreamService.on_price_tick()
          ├→ snapshot 캐시 갱신 (기존)
          ├→ DataQualityService.validate_price_tick() (기존)
          └→ [신규] EventDispatcher.publish(PriceTickEvent)
                    │
                    ▼
          ┌─────────────────────────────────┐
          │ StrategyEventRouter             │
          │   subscribers: {code → [cfg]}   │  ← SubscriptionPolicy와 동일 카테고리 키 재사용
          │   debounce/throttle per (code,  │     ("strategy_oneil" 등)
          │      strategy)                  │
          └────────────┬────────────────────┘
                       │ 매칭된 전략만
                       ▼
          ┌──────────────────────────────┐
          │ Strategy.evaluate_single(    │  ← 단일 종목 평가 (신규 contract)
          │   code, snapshot, ctx)       │     기존 execute()는 폴링용으로 유지
          └────────────┬─────────────────┘
                       │ TradeSignal
                       ▼
          (기존 경로 그대로) RiskGate → OrderExecution → KillSwitch hook
```

**핵심 원칙**
1. **이벤트는 트리거일 뿐, 평가 로직은 공유**. `Strategy.evaluate_single(code, snapshot)`은 `Strategy.execute([code])`의 단일 종목 fast-path; 동일한 필터(`_apply_liquidity_filter`, `_apply_stage_guard`)를 호출.
2. **이중 실행 차단**. `evaluate_single` 결과로 신호가 났더라도 같은 `(strategy, code)`가 그 분 내 폴링 경로에서 다시 평가되면 cooldown으로 차단 (`_last_evaluated[(strategy, code)]`).
3. **이벤트가 끊겨도 폴링이 안전망**. 30초 이상 tick 없으면 폴링 루프가 평가하여 시그널을 놓치지 않음.

## 4. 컴포넌트별 변경

### 4-1. 신규: `services/strategy_event_router.py`

```python
class StrategyEventRouter:
    def __init__(self, logger, market_clock, throttle_sec: float = 0.5):
        self._subscribers: dict[str, list[StrategyConfig]] = {}  # code → strategies
        self._last_dispatched: dict[tuple[str, str], float] = {}  # (strategy, code) → ts

    def subscribe(self, code: str, cfg: StrategySchedulerConfig) -> None: ...
    def unsubscribe(self, code: str, strategy_name: str) -> None: ...
    async def on_price_tick(self, code: str, snapshot: dict) -> None:
        # throttle, market_open 체크, kill switch 체크 후
        # 매칭된 전략의 evaluate_single을 asyncio.create_task로 디스패치
```

- `PriceStreamService.on_price_tick()` 마지막에 `event_router.on_price_tick(stock_code, snapshot)` 호출 추가.
- `StrategyScheduler`가 register 시점에 후보 종목을 `event_router.subscribe()`로 등록.

### 4-2. 신규 contract: `LiveStrategy.evaluate_single(code, snapshot, ctx) -> Optional[TradeSignal]`

- 기본 구현(`LiveStrategy`)은 `await self.execute([code])`를 호출하고 결과에서 해당 코드 신호만 추출 — 점진 마이그레이션 안전망.
- 우선순위 전략부터 오버라이드하여 snapshot 활용 fast-path 구현 (4-4 참고).

### 4-3. `StrategyScheduler` 변경

- `__init__`에 `event_router: Optional[StrategyEventRouter] = None` 추가.
- `register(cfg)`에서 `cfg.event_driven: bool = False` 플래그 확인 후 router에 등록.
- `_run_strategy(cfg)` 내부 watchlist sync 시점에 router 구독 codes 갱신.
- `_execute_signal_inner`는 호출 경로(polling/event) 표시를 metadata에 기록하여 로그 추적성 확보.

### 4-4. 단계적 적용 우선순위

| 단계 | 전략 | 이유 | 검증 |
|---|---|---|---|
| 0 | (해당 없음) | Router/이벤트 인프라만 도입 | event flow unit test |
| 1 | `LarryWilliamsVBOStrategy` | 단순 가격 돌파 → snapshot만으로 평가 가능 | live shadow mode 1주 |
| 2 | `OneilSqueezeBreakoutStrategy` | 가격 + 거래량 (snapshot에 누적량 존재) | live shadow mode 1주 |
| 3 | `HighTightFlagStrategy` | 가격 + 거래량 + 깃대 패턴 | OHLCV 별도 조회 필요 — fast-path 보류 후 재평가 |
| 4 | 기타 전략 | 적용 효익 재평가 | — |

**0단계 우선 착수 권고**. 1단계 이후는 결과 보고 후 결정.

### 4-5. 변경 없음

- `RiskGateService`, `KillSwitchService`, `OrderExecutionService`, `PositionSizingService`, `SubscriptionPolicy`: 동일 contract.
- 백테스트 경로 전체.
- 비활성 전략군.

## 5. 이벤트 흐름 (전체)

```
체결 이벤트 (KIS H0STCNT0)
  ↓
KoreaInvestWebSocketAPI._handle_message
  ↓ on_realtime_message_callback
PriceStreamService.on_price_tick
  ├ DataQualityService.validate_price_tick (기존)
  ├ _latest_prices / market_snapshot 캐시 갱신 (기존)
  ├ SSE queue publish (기존)
  └ [신규] StrategyEventRouter.on_price_tick
       ├ throttle check ((strategy, code) → last_dispatched)
       ├ market_open / kill_switch 게이트
       └ for cfg in subscribers[code]:
            asyncio.create_task(_evaluate_one(cfg, code, snapshot))
                ↓
            cfg.strategy.evaluate_single(code, snapshot, ctx)
                ↓ TradeSignal (or None)
            StrategyScheduler._execute_signal
                ↓
            RiskGateService.validate_order  (기존)
                ↓
            OrderExecutionService.submit_buy_order  (기존)
                ↓ 체결 콜백
            KillSwitchService.record_strategy_trade_result  (기존)
            VirtualTradeRepository.log_buy  (기존)
```

## 6. 검증 전략

### 6-1. 단위 테스트

- `tests/unit_test/services/test_strategy_event_router.py` 신규
  - subscribe/unsubscribe ref-count
  - throttle (`(strategy, code)` 0.5초 내 중복 차단)
  - market_open=False 시 dispatch 없음
  - kill_switch trip 시 dispatch 없음 (force_exit 제외)
  - `evaluate_single`이 None 반환 시 신호 미발생

### 6-2. 통합 테스트

- `tests/integration_test/test_it_event_driven_flow.py` 신규
  - WebSocket mock → PriceStreamService → Router → 전략 1개 → RiskGate stub → 신호 기록까지 end-to-end
  - 이중 실행 차단: 같은 (strategy, code)가 router/scheduler 양쪽에서 트리거되어도 1회만 신호

### 6-3. Shadow mode

- 첫 적용 전략(`LarryWilliamsVBOStrategy`)은 1주간 **신호만 로깅, 주문 미발생** 모드로 운영.
- 폴링 경로 신호와 event 경로 신호를 같은 journal에 `signal_source=polling|event` 태그로 저장.
- 결과 비교: 동일 신호 발생 여부, 시간 차이, missed signal 여부.

## 7. 리스크와 완화

| 리스크 | 완화 |
|---|---|
| 이벤트 폭주로 평가 thread 포화 | `StrategyEventRouter` throttle + `asyncio.Semaphore` 동시성 상한 |
| WebSocket 끊김 시 시그널 누락 | 폴링 루프 안전망 유지 (event 경로는 *추가*, *대체* 아님) |
| Event/Polling 중복 신호 | `(strategy, code) → last_evaluated_ts` cooldown으로 분 단위 dedup |
| Snapshot stale 사용 | `PriceStreamService.get_subscription_age()`로 N초 이상 stale 시 router dispatch 차단 |
| 신호 폭증으로 RiskGate 부담 | RiskGate는 이미 token bucket; 추가 변경 불필요 |

## 8. 작업 분해 (Migration Tasks)

이 plan 자체가 2-4 첫 번째 체크박스를 충족. 후속 구현은 별도 PR.

1. **PR-1: Event router 인프라 도입**
   - `services/strategy_event_router.py` 신규
   - `PriceStreamService.on_price_tick()`에 router hook 추가 (router=None 시 no-op)
   - `LiveStrategy.evaluate_single()` 기본 구현 (execute 위임)
   - 단위 테스트
   - **PoC 전략 미연결**. 인프라만.

2. **PR-2: VBO shadow mode**
   - `LarryWilliamsVBOStrategy.evaluate_single()` 오버라이드
   - `StrategySchedulerConfig.event_driven_shadow: bool` 플래그
   - shadow signal을 별도 journal에 기록 (`signal_source=event_shadow`)
   - 1주 운영 후 결과 분석

3. **PR-3: VBO 실 적용 / OSB shadow**
   - PR-2 결과 양호하면 VBO `event_driven=True`로 승격
   - OSB는 shadow로 진입

4. **PR-4 이후**: 단계적 확장 (4-4 표 참고)

## 9. 결정 사항 (2026-05-18 확정)

- **(Q1) 이벤트 throttle 기본값**: 같은 `(strategy, code)` 최소 간격 **0.5초**. 변경은 `StrategyEventRouter(throttle_sec=...)` 생성자 인자.
- **(Q2) Stale snapshot 임계**: 마지막 tick으로부터 **5초**까지 dispatch 허용. 변경은 `StrategyEventRouter(stale_snapshot_sec=...)` 생성자 인자.
- **(Q3) Shadow mode 운영 기간**: VBO **1주** (거래일 기준 5일). 거래일 부족 시 연장은 별도 결정.
- **(Q4) `signal_source`**: 기존 `metadata` JSON에 `signal_source` 키로 포함. `VirtualTradeRepository` schema 변경 없음. 값: `"polling" | "event_shadow" | "event_live"`.
