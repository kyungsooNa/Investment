# Canary Procedure

> 실계좌 소액 canary 운영 절차. 진입 조건 → 운영 한도 → 중단 조건 → 관찰 기간 → 승격 조건.
> 장중 장애 대응·복구·배포 체크리스트는 [docs/operations_runbook.md](operations_runbook.md) 참고.

## 배경 / Scope

- 자동매매 시스템이 paper/shadow 검증을 모두 통과한 뒤, **실계좌 자금으로 한정된 조건에서만 실행**하는 단계를 canary 라고 한다.
- canary 의 목적은 "실전 환경에서만 드러나는 broker/네트워크/체결/세금/슬리피지 issue 발견" 이다. 수익 검증이 아니다.
- 정책 원칙: **P0 (주문 안정성) 완료 + P1 (수익성 기준선) 통과 전에는 full-auto 금지.** 단계는 backtest → paper/shadow → 제한적 소액 canary → full-auto 순.
- todo_list `1-2 P0`, `2-1 P1`, `4-4 P4-4` 와 연계된다.

## 진입 조건

다음 항목이 **모두 충족**될 때까지 canary 진입을 보류한다.

- [ ] P0 (주문 안정성): broker reconcile, opening position reconcile, risk gate, order_policy 매트릭스가 통합 테스트로 고정되어 있다.
- [ ] P1 (수익성 기준선): 운영 후보 전략 각각이 paper/shadow 기준선을 통과했다. 단일 임계값에서만 성과가 튀는 전략은 제외하거나 canary 전용으로만 둔다 (todo `166`).
- [ ] backtest-vs-live 괴리 리포트(`docs` 또는 운영 채널)에 의미 있는 괴리가 없다 (todo `74`).
- [ ] Kill Switch 가 직전 1주일 운영(paper/shadow) 동안 트립되지 않았거나, 트립 원인이 모두 해소되고 사고 리포트가 작성되었다.
- [ ] [docs/operations_runbook.md](operations_runbook.md) 의 *배포 체크리스트* 1\~10 항목이 모두 합격.
- [ ] 운영자 대시보드 `/operator` 가 정상 동작하고 `OperatorAlertService` 알림 채널이 검증되었다.
- [ ] Event shadow log 가 직전 거래일 기준 폴링 신호와 ≥ 95% 일치 (활성 전략 한정).

## 운영 한도 (Canary 단계)

canary 단계는 full-auto 대비 한도를 별도로 낮게 둔다. 아래 값은 *초기 권장 기본값* 이며 운영 첫 주 결과를 바탕으로 재조정한다. 변경 시 본 문서와 `config/config.yaml` 의 `risk_gate.*` 를 동시에 갱신한다.

| 항목 | Canary 한도 | Full 한도(참고) | 적용 위치 |
| --- | --- | --- | --- |
| 동시 보유 종목 수 | 2종 | 전략 별 capital 한도 내 무제한 | 전략 capital 설정 + `max_pending_orders` |
| 단일 주문 금액 | 1,000,000 KRW | 전략 자본의 1포지션 비중 | `risk_gate.max_order_amount` |
| 전략 1일 손실 한도 | -1.0% | -3.0% | `risk_gate.strategy_loss_limit` |
| 전략 연속 손실 한도 | 3회 | 5회 | 전략 별 cooldown 정책 |
| 미체결 허용 시간 | 5분 | 30분 | `FillReconciliationService` reconcile 윈도우 |
| 계좌 총 노출 한도 | 총자산의 5% | 총자산의 30% | `risk_gate.max_total_exposure` |
| Kill Switch 연속 API 오류 한도 | 3회 | `KillSwitchConfig.max_consecutive_api_errors` 운영 기본값 | `config/config.yaml` `kill_switch.max_consecutive_api_errors` |
| 활성 전략 수 | 검증된 1\~2개 전략만 | 전체 활성 전략 | `StrategyFactory` 등록 |

## 관찰 기간

| 단계 | 최소 기간 | 종료 조건 |
| --- | --- | --- |
| Canary 1주차 | 5 거래일 | 중단 조건 미발생 + 매일 사후 점검 완료 |
| Canary 2\~4주차 | 추가 15 거래일 | 한도 점진 완화(예: 종목 수 2→3, 손실 -1%→-1.5%) 후 안정 |
| 승격 직전 | 추가 5 거래일 | 승격 조건 전부 충족 |

매일 장 마감 후 다음을 기록한다.

- 신호 수 / 진입 수 / 청산 수 / 미체결 수
- 실현 손익 vs paper/shadow 동일 시점 비교
- Kill Switch 트립 여부 및 사유
- 모든 알림(`AlertSource.*`) 발생 빈도
- 사고 발생 시 [docs/operations_runbook.md](operations_runbook.md) 의 *사고 리포트 템플릿* 작성

## 중단 조건

다음 중 **하나라도 발생**하면 즉시 canary 를 중단하고 paper 모드로 복귀한다. 중단 사유는 사고 리포트로 남긴다.

| 분류 | 조건 | 추가 조치 |
| --- | --- | --- |
| 손실 | 1일 실현 손실 > -1.0% (canary 한도) | 당일 자동매매 정지, 익일 paper 복귀 검토 |
| 손실 | 누적 3 거래일 실현 손실 > -2.5% | 즉시 paper 복귀, 전략 재검증 |
| Kill Switch | 트립 발생 (사유 무관) | 1차 발생: 원인 분석 후 재개 가능. 1주 내 2회: paper 복귀 |
| 체결 | 미체결 5분 초과 또는 1일 미체결률 > 20% | 해당 전략 일시 정지, broker 응답 패턴 확인 |
| 데이터 | `AlertSource.DATA_QUALITY` 알림 발생 | 원인 식별 전까지 자동매매 정지 |
| 원장 | broker 잔고 vs journal 불일치 발견 | 즉시 정지, `OpeningPositionReconcileService.reconcile_once()` 결과 확정 후 재개 |
| 시스템 | WebSocket 재연결 15분 내 3회 이상 | 정지, `force_reconnect` trigger 분포 분석 |
| 외부 | broker 측 공지된 장애 또는 API spec 변경 | 정지, 공지 해소 후 재개 |
| 정책 | risk gate fail-open 경로가 실제로 fail-open 으로 동작 (todo `risk_gate.md` *Fail-open Data* 참고) | 정지, fail-open 원인 해소 |

중단 후 재개는 [docs/operations_runbook.md](operations_runbook.md) 의 *재개 조건* 을 적용한다.

## 승격 조건 (Canary → Full-auto)

다음 항목이 **모두 충족**될 때 full-auto 로 승격한다. 승격은 단일 결정 항목이 아니라 별도 PR/checklist 로 기록한다.

- [ ] Canary 누적 운영 ≥ 20 거래일 (관찰 기간 표 기준)
- [ ] 누적 실현 손익이 paper/shadow 대비 의미 있는 음의 괴리 없음 (운영자가 정의한 허용 범위 내)
- [ ] 운영 기간 중 Kill Switch 트립 0회, 또는 모든 트립이 외부 broker 장애로 분류되고 코드 회귀 없음
- [ ] 모든 알림 발생 빈도가 안정 (예: 1일 평균 < 5회) 하고 `OperatorAlertService` dedup 가 정상 동작
- [ ] `FillReconciliationService` 미체결 처리, `OpeningPositionReconcileService` 원장 대사가 사람 개입 없이 자동 회복된다
- [ ] 운영 기간 중 모든 사고 리포트의 *재발 방지* 항목이 코드/문서/모니터링으로 반영 완료
- [ ] 전략 적합성 검증 (todo `356` *전략별 universe 적합성*) 의 1차 결론이 나옴
- [ ] full-auto 단계의 운영 한도(위 표 *Full 한도* 열) 가 명시적으로 합의됨

## 승격 후 첫 1주

승격 직후 1주(5 거래일)는 canary 와 동일한 매일 점검을 유지한다. 이 기간을 안정적으로 통과하면 일일 점검 빈도를 주 2회로 낮춘다.

## 관련 문서

- [docs/operations_runbook.md](operations_runbook.md) — 일상 운영, 장애 대응, Kill Switch, 사고 리포트 템플릿
- [docs/reconcile_failure_policy.md](reconcile_failure_policy.md) — Kill Switch 카운터·트립·해제 규칙
- [docs/opening_position_reconcile_policy.md](opening_position_reconcile_policy.md) — 장 시작 원장 대사
- [docs/risk_gate.md](risk_gate.md) — 주문 차단 Rule, fail-open 정책
- [docs/order_policy.md](order_policy.md) — 주문 결정/거부 규칙
