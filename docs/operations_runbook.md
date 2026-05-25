# Operations Runbook

> 실전(또는 실계좌 canary) 운영 시 사용하는 점검·장애 대응·복구 절차 문서.
> 실계좌 canary 진입/승격 조건은 [docs/canary_procedure.md](canary_procedure.md) 참고.
> Kill Switch 트립 매트릭스의 행 단위 동작 정의는 [docs/reconcile_failure_policy.md](reconcile_failure_policy.md) 참고.

## Scope

- 대상: 실전 또는 canary 모드의 자동매매 운영자
- 범위: 장 시작 전 점검 → 장중 장애 대응 → Kill Switch 복구 → 배포 체크 → 사고 리포트
- 비대상: 백테스트 절차, 전략 튜닝, 시스템 아키텍처 설명 (`CODEBASE_SUMMARY.md`)

## Runtime 진입점

운영자는 목적에 따라 다음 runtime을 분리 실행할 수 있다. 자세한 경계는 todo `3-2` 항목 참고.

| Runtime | Entrypoint | 역할 | 스케줄러 |
| --- | --- | --- | --- |
| WEB | `web_app.py` | 일반 웹 UI + 운영자 대시보드 | TRADING + BATCH 포함 (기본 단일 프로세스) |
| TRADING | `trading_runtime.py` | 장중 전략/주문 실행 전용 | TRADING ON, BATCH OFF |
| BATCH | `batch_runtime.py` | 장마감 데이터 수집/리포트 전용 | BATCH ON, TRADING OFF |
| Admin | `admin_runtime.py` | 운영자 수동 점검(WEB surface, scheduler OFF) | TRADING/BATCH OFF |

## 장 시작 전 체크리스트

매 거래일 09:00 KST 이전에 수행한다. 실패 항목이 있으면 정상 복구 또는 명시적인 우회 결정 전까지 자동매매 진입을 보류한다.

| # | 항목 | 점검 위치 | 합격 조건 | 실패 시 조치 |
| --- | --- | --- | --- | --- |
| 1 | 토큰 상태 | `data/token_*.json`, 운영자 대시보드 토큰 위젯 | 만료까지 6시간 이상 잔여 | 토큰 재발급 후 runtime 재기동 |
| 2 | 잔고/예수금 | `/api/balance` 또는 운영자 대시보드 | 전일 정산 후 예상 예수금과 일치 | 한국투자증권 HTS 잔고와 교차 확인 — 불일치 시 자동매매 보류 |
| 3 | 보유 포지션 | `/api/positions`, `data/trade_journal.csv` | journal HOLD ≡ broker 잔고 | `OpeningPositionReconcileService.reconcile_once()` 트리거(09:00 직후 자동 실행). 불일치 잔여 시 [docs/opening_position_reconcile_policy.md](opening_position_reconcile_policy.md) 절차 |
| 4 | 데이터 연결 | 로그 `services.market_data_service` 최근 1분 이내 응답 | 현재가 조회 RTT < 1s | broker_api_wrapper Circuit Breaker open 여부 확인. open 이면 회복 대기 |
| 5 | WebSocket | `WebSocketWatchdogTask` 로그, `streaming_logger.log_reconnect` | 구독 종목 수 > 0, 마지막 수신 시각 < 5s | `force_reconnect(trigger="manual")` 수동 호출 또는 watchdog가 `market_open` 트리거로 자동 재연결할 때까지 대기 |
| 6 | Kill Switch 상태 | `data/kill_switch_state.json` | `is_tripped == false` | 트립 상태면 아래 *Kill Switch 발동 시 절차* 따름 |
| 7 | Risk Gate 설정 | `config/config.yaml` 의 `risk_gate.*` | `max_pending_orders`, `max_order_amount`, `strategy_loss_limit` 값이 현재 운영 단계(canary/full)에 맞게 설정 | canary 단계는 `canary_procedure.md` 의 한도로 재설정 후 재시작 |
| 8 | Event shadow | `logs/strategies/event_shadow/YYYYMMDD.jsonl` 직전 거래일 파일 | 직전일 폴링 신호와 ≥ 95% 일치 | 차이 분석 후 PR-3 진입 보류 |

## 장중 장애 대응 매트릭스

발생 시각·증상·트리거 로그 라인을 모아 *사고 리포트 템플릿* 에 기록한다.

| 시나리오 | 트리거/로그 | 자동 동작 | 운영자 수동 조치 |
| --- | --- | --- | --- |
| API 일시 오류 (1회) | `record_api_failure` 카운터 +1 | 카운터 누적, 다음 주문 허용 | 모니터링만 |
| API 연속 오류 (≥ `max_consecutive_api_errors`) | `[KillSwitch] trip — 연속 API 오류 N회` | Kill Switch 트립, 신규 주문 차단 | 아래 *Kill Switch 발동 시 절차* |
| 주문 지연 (개별 종목) | `OrderExecutionService` 재시도 로그 (`config.order_execution.order_max_retries`, 기본값 3) | 재시도 정책 내 자동 재시도 | 재시도 모두 실패 시 해당 종목 거래 일시 중단. broker 응답 코드 확인 |
| 미체결 시간 한도 초과 | `FillReconciliationService.reconcile_orders_with_broker()` 결과 | 미체결 주문 식별, journal 갱신 | canary 한도(`canary_procedure.md`) 초과면 자동매매 일시 정지 |
| Stale data (price gap) | `WebSocketWatchdogTask` `price_data_gap_*` trigger | `force_reconnect()` | 재연결 후에도 gap 지속 시 종목 구독 재등록 |
| WebSocket receive task 사망 | `reconnect_trigger = "receive_task_dead"` | watchdog 자동 재연결 + `AlertSource.WEBSOCKET_WATCHDOG` 알림 | 5분 내 재연결 실패 시 manual `force_reconnect` |
| 시장 시간 외 주문 시도 | `is_market_open_now()` 또는 `is_market_operating_hours()` False | 주문 거부, 로그 경고 | 정상 — 모의 환경/배포 직후 검증 외에는 발생 안 함 |
| 장 마감 강제 청산 발동 | 마감 30분 전 (`FORCE_EXIT_MINUTES_BEFORE = 30`) | `force_exit_on_close=True` 전략의 보유 포지션 청산 | 청산 완료 확인 후 `_force_exit_done` 누락 종목 점검 |
| 원장 대사 실패 | `OpeningPositionReconcileService` error | 다음 윈도우 재시도 | 매트릭스 [docs/reconcile_failure_policy.md](reconcile_failure_policy.md) 행 단위 절차 적용 |
| Data quality 알림 | `AlertSource.DATA_QUALITY` | 알림 발송 (자동매매는 유지) | 원인 (예: 시세 누락, 일봉 비정상) 식별 후 필요 시 manual stop |

## Kill Switch 발동 시 절차

Kill Switch 가 트립되면 신규 주문은 즉시 차단된다 (`check_orders_allowed() == (False, reason)`). 단, `force_exit_on_close=True` 전략의 마감 청산은 유지된다.

1. **사실 확인**
   - `data/kill_switch_state.json` 의 `is_tripped`, `trip_reason`, `trip_timestamp`, `trip_metadata` 확인
   - 운영자 대시보드 `/operator` 의 `kill_switch:global` 알림 확인
2. **원인 분류**
   - `연속 API 오류 N회` — broker 측 장애. 한국투자증권 공지 확인
   - `데이터 품질` — `AlertSource.DATA_QUALITY` 와 동반. 원인 종목/지표 식별
   - `risk gate exposure` — 전략 노출 한도 초과. 전략 자본/한도 재검토
   - `manual` — 운영자 또는 외부 시스템이 명시적으로 트립
3. **현재 보유 포지션 보호**
   - 마감 30분 전이면 `force_exit_on_close` 전략은 청산 진행 — 별도 조치 불필요
   - 마감 30분 외이면 운영자가 수동 청산 필요 여부 판단 (HTS 또는 admin runtime 의 `/api/order`)
4. **복구 시도**
   - 단순 broker 장애였다면 회복 대기 후 *재개 조건* 충족 여부 확인
   - 데이터 품질 이슈는 원인 해소 (예: 시세 캐시 갱신, 종목 코드 재로드) 후 재시도
5. **트립 해제**
   - 운영 콘솔에서 `KillSwitchService.reset_trip()` 호출 (admin runtime 의 디버그 엔드포인트 또는 별도 CLI)
   - 또는 `data/kill_switch_state.json` 을 `{"is_tripped": false, "consecutive_api_errors": 0, ...}` 로 수정 후 runtime 재기동
   - reconcile alarm 도 함께 트립된 경우 `reset_reconcile_alarm()` 과 `OrderStateMachine.clear_safe_transition_mismatch()` 도 함께 호출 (todo `132` 후속)

## 재개 조건

다음 조건이 **모두 충족**될 때까지 자동매매 재개를 보류한다.

- [ ] 트립 원인의 근본 해소가 로그 또는 외부 채널에서 확인됨
- [ ] 직전 1시간 내 `record_api_failure` 호출 0건
- [ ] 보유 포지션과 broker 잔고가 일치 (`OpeningPositionReconcileService.reconcile_once` 결과 `quantity_mismatches == []`)
- [ ] WebSocket 마지막 수신 시각 < 5s
- [ ] Kill Switch 트립 사유와 동일한 알림이 5분 내 재발생하지 않음
- [ ] 사고 리포트의 *원인/조치/재발 방지* 항목 기록 완료

## 배포 체크리스트

코드 변경을 운영 환경에 반영할 때 사용한다. canary 단계에서는 *모든* 항목을 수동 점검한다. 3\~8번은 `scripts/run_predeploy_check.py` 로 자동화되어 있다.

### 자동 점검 (권장)

```powershell
# live 점검: broker 호출 포함. 실전/모의 환경의 토큰·base URL·계좌 조회까지 확인
python -m scripts.run_predeploy_check

# 모의투자 broker 로 점검
python -m scripts.run_predeploy_check --paper

# CI 또는 broker 호출이 불가능한 환경: 정적 점검만 수행
python -m scripts.run_predeploy_check --offline

# 결과를 JSON 으로 받아 후속 자동화에 연결
python -m scripts.run_predeploy_check --json
```

- 동작: 모든 점검을 끝까지 실행한 뒤 PASS/FAIL/WARN/SKIPPED 표를 출력한다. FAIL 이 1건이라도 있으면 exit 1.
- 점검 항목은 아래 표의 # 3\~8 과 동일하다. WebSocket subscription health 는 streaming watchdog 도입 전까지 SKIPPED 로 노출된다 (todo `4-4` item 3 후속).
- 자세한 구현은 [services/predeploy_check_service.py](../services/predeploy_check_service.py) 및 [scripts/run_predeploy_check.py](../scripts/run_predeploy_check.py).

| # | 항목 | 점검 방법 | 합격 조건 |
| --- | --- | --- | --- |
| 1 | 단위 테스트 | `pytest tests/unit_test -v` | 전부 통과 |
| 2 | 통합 테스트 | `pytest tests/integration_test -v` | 전부 통과 |
| 3 | config validation | 자동: `run_predeploy_check` `config_validation` | pydantic 검증 통과 |
| 4 | broker token/account/env consistency | 자동: `run_predeploy_check` `broker_env_consistency` | `is_paper_trading` 와 base URL, websocket URL 호스트가 일관 |
| 5 | WebSocket subscription health | 자동(예정): `run_predeploy_check` `websocket_subscription_health` — watchdog 도입 전까지 dry-run 1분 후 `streaming_logger` 수동 확인 | 마지막 수신 시각 < 5s |
| 6 | latest trading date | 자동: `run_predeploy_check` `latest_trading_date` | 직전 영업일과 일치 (`--offline` 시 SKIPPED) |
| 7 | 계좌 스냅샷 freshness | 자동: `run_predeploy_check` `account_snapshot_freshness` | `/api/balance` 응답 < 30s (`--offline` 시 SKIPPED) |
| 8 | Event shadow status | 자동: `run_predeploy_check` `event_shadow_status` | 최신 shadow 로그가 3일 이내. ≥ 95% 일치율 검증은 manual (후속 자동화 대상) |
| 9 | Kill Switch state file 보존 | `data/kill_switch_state.json` 백업 또는 미존재 (정상 출발) | 합격 |
| 10 | 사후 모니터링 30분 | 배포 후 30분간 에러 로그 0건, 신규 주문 정상 흐름 | 합격 |

## 사고 리포트 템플릿

장중 자동매매를 중단했거나 Kill Switch 트립, 원장 불일치, 미체결 한도 초과 등 운영 사건 발생 시 작성한다. 별도 issue 또는 `docs/incidents/YYYYMMDD-<slug>.md` 에 저장한다.

```markdown
# Incident: <짧은 제목>

- 발생 일시 (KST): YYYY-MM-DD HH:MM
- 감지 일시 (KST): YYYY-MM-DD HH:MM
- 복구 일시 (KST): YYYY-MM-DD HH:MM
- 운영 단계: canary / full / paper
- 영향 범위: <전략명 / 종목수 / 추정 손실 / 미체결 건수>

## 증상

<로그/대시보드/외부 신호 요약. 가능하면 timestamp 와 로그 라인 인용.>

## 트리거

- Alert: <AlertSource.KILL_SWITCH / DATA_QUALITY / WEBSOCKET_WATCHDOG / RISK_GATE / STRATEGY_PERF>
- Kill Switch trip_reason: <문자열 또는 N/A>
- 관련 로그 라인:
  - `...`

## 타임라인

- HH:MM — <이벤트>
- HH:MM — <이벤트>

## 원인 분석

<root cause. broker 장애 / 시세 누락 / risk gate 오설정 / 코드 회귀 등.>

## 즉시 조치

<운영자가 수행한 manual 조치. trip reset, force_reconnect, 종목 구독 재등록, 수동 청산 등.>

## 데이터 영향

- 주문 영향: <건수, 종목, 금액>
- 포지션 영향: <보유 변화>
- 가상매매 journal vs broker 잔고 불일치 여부: <Y/N + 항목>

## 재발 방지

<코드 수정 / config 조정 / runbook 보완 / 모니터링 추가 등 후속 작업.>

## 관련 작업/PR

- <링크 또는 todo_list 항목 번호>
```

## 관련 문서

- [docs/canary_procedure.md](canary_procedure.md) — 실계좌 canary 진입/승격 조건
- [docs/reconcile_failure_policy.md](reconcile_failure_policy.md) — broker reconcile 실패 매트릭스, KillSwitch 카운터 규칙
- [docs/opening_position_reconcile_policy.md](opening_position_reconcile_policy.md) — 장 시작 원장 대사 규칙
- [docs/risk_gate.md](risk_gate.md) — 주문 차단 Rule, fail-open 정책
- [docs/order_policy.md](order_policy.md) — 주문 결정/거부 규칙
- [docs/event_driven_architecture.md](event_driven_architecture.md) — 실시간 신호 평가 구조
