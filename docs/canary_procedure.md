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

## Profitability Gate 수동 Override Runbook (P1 1-1)

`StrategyProfitabilityGateConfig.real_mode_overrides` 는 실전 모드에서만 적용되는 canary 임계 overlay 다. paper/backtest 동작에는 영향이 없고, real 모드 신규 BUY 진입 직전 `StrategyLiveExpansionGateService` 가 base config 위에 overlay 를 덮어 평가한다.

### 기본 (canary) overlay 값

| 필드 | Paper/Backtest 기본 | Real overlay (canary) | 의미 |
| --- | --- | --- | --- |
| `min_trades` | 30 | 100 | 표본 부족한 신규 전략 자동 차단 |
| `min_profit_factor` | 1.2 | 1.3 | 손익비 + 승률 결합 임계 강화 |
| `min_payoff_ratio` | 1.0 | 1.2 | 평균 익절/평균 손절 비율 |
| `min_win_rate` | 0.35 | 0.4 | 승률 하한 |
| `max_mdd_pct` | 20.0 | 12.0 | journal 기준 누적 MDD 상한 |
| `min_regime_trade_count` | 5 | 30 | regime 별 표본 하한 |
| `require_parameter_stability` | False | True | parameter stability 누락 시 block |
| `require_monte_carlo` | False | True | Monte Carlo evidence 누락 시 block |
| `require_regime_balance` | False | True | regime balance 미충족 시 warning → block 승격 |
| `require_multiple_testing_adjustment` | False | True | multiple-testing bias warning 발생 시 best strategy block |
| `multiple_testing_min_adjusted_sharpe` | null | 0.0 | Deflated Sharpe proxy 하한 |
| `multiple_testing_max_pbo_probability` | null | 0.5 | PBO proxy 상한 |
| `ablation_max_variant_outperformance_pct` | null | 10.0 | 필터 제거/완화 variant가 baseline을 과도하게 이기면 block |

### 동작 원칙

- **paper/backtest**: top-level 기본값만 사용. missing evidence 는 warning. journal 표본을 자유롭게 쌓을 수 있다.
- **real**: overlay 적용본을 사용. monte_carlo / parameter_stability / regime_balance 가 누락되면 `monte_carlo_unavailable` / `parameter_stability_unavailable` / `regime_balance_incomplete` 가 `blocking_reasons` 로 승격되어 신규 BUY 가 차단된다. multiple-testing bias 가 감지되면 선택된 best strategy 에 `multiple_testing_bias_warning` 이 추가되어 차단된다. ablation 결과에서 필터 제거/완화 variant가 baseline보다 10% 초과로 우수하면 `ablation_variant_outperforms_baseline` 으로 차단한다.
- 차단 시 scheduler 는 `event=signal_rejected reason=profitability_gate_blocked gate_reason=profitability_gate_fail` 와 함께 `gate_details.blocking_reasons` 에 세부 reason 을 기록한다.

### 수동 Override 절차

신규 전략이 journal 표본 부족으로 영구 차단되는 것을 막기 위해, 운영자는 다음 절차로 overlay 를 한시적으로 완화할 수 있다.

1. **현황 확인**
   - `GET /api/strategies/performance-by-regime?strategy=<name>` 으로 표본/regime 분포 확인.
   - scheduler 로그에서 `signal_rejected reason=profitability_gate_blocked` 를 찾아 `gate_details.blocking_reasons` 의 reason 코드를 식별.

2. **변경 PR 작성** (`config/config.yaml` 직접 수정 금지 — review 필수)
   - 차단 reason 별 권장 완화:
     - `insufficient_trades` → `min_trades` 를 표본 수 × 1.2 정도로 임시 완화.
     - `monte_carlo_unavailable` → 데이터 파이프라인 점검을 우선 검토. 임시 우회 시 `require_monte_carlo: false`.
     - `parameter_stability_unavailable` → 동일. 임시 우회 시 `require_parameter_stability: false`.
     - `regime_balance_incomplete` → 4 regime 전부 표본이 모일 때까지 `require_regime_balance: false`.
     - `multiple_testing_bias_warning` → walk-forward/out-of-sample 근거를 먼저 보강. 임시 우회 시 `require_multiple_testing_adjustment: false`.
     - `ablation_variant_outperforms_baseline` → 필터 기여도가 불명확하므로 ablation variant와 baseline 거래를 재검토. 임시 우회 시 `ablation_max_variant_outperformance_pct: null`.
   - yaml 예시 (필요한 필드만 명시):
     ```yaml
     strategy_profitability_gate:
       real_mode_overrides:
         min_trades: 30           # 표본 누적 전 한시 완화
         require_monte_carlo: false
     ```
   - PR 본문에 **사유 / 적용 기간 / 원복 일자** 를 명시. 원복 일자에 자동 issue/리마인더 등록.

3. **배포 및 검증**
   - `pytest tests/unit_test/config -v -k overrides` 로 yaml 로딩 회귀 확인.
   - 실전 적용 후 첫 1 거래일은 BUY 가 의도대로 흘러가는지 scheduler 로그로 직접 점검.

4. **원복**
   - 표본 누적이 canary 기준을 다시 충족하면 즉시 overlay 항목 삭제 PR 을 올린다.
   - 원복하지 않은 overlay 가 누적되면 P1 1-1 강화 목적이 무력화되므로, **모든 임시 완화 항목은 issue/runbook 로 추적**한다.

### 위험 시 자동 차단 우선

위 절차로도 evidence 가 모이지 않는데 손실이 누적되면 `KillSwitchService` 가 우선 작동한다. overlay 완화는 표본 부족 차단을 풀기 위함이며, 손실 차단 자체를 우회하는 용도가 아니다.

## 관련 문서

- [docs/operations_runbook.md](operations_runbook.md) — 일상 운영, 장애 대응, Kill Switch, 사고 리포트 템플릿
- [docs/reconcile_failure_policy.md](reconcile_failure_policy.md) — Kill Switch 카운터·트립·해제 규칙
- [docs/opening_position_reconcile_policy.md](opening_position_reconcile_policy.md) — 장 시작 원장 대사
- [docs/risk_gate.md](risk_gate.md) — 주문 차단 Rule, fail-open 정책
- [docs/order_policy.md](order_policy.md) — 주문 결정/거부 규칙
