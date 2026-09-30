# 강제청산 경로 계약

강제청산은 일반 매도와 같은 주문 배관을 사용하지만, 마감 전에 반드시 잔고를 0으로
만들어야 하고 응답 유실 뒤의 재시도가 중복 매도를 만들면 안 된다. #700 이후에도
#899~#901과 #1000에서 수량·대사·집계 경계 조건이 반복 수정되어 아래 불변조건을
경로 단위로 고정한다.

## 계약표

| 계약 | 불변조건 | 실패 시 위험 | 동작 테스트 |
| --- | --- | --- | --- |
| 시간 창 | 마지막 `FORCE_EXIT_TIERS` 누적 비율은 `1.0`이고 `ORDER_CUTOFF_MINUTES_BEFORE_CLOSE`보다 먼저 발화한다. | 잔여 수량이 주문 컷오프 뒤에 남아 오버나이트된다. | `test_strategy_scheduler_time_window.py` |
| 재시도 전 확인 | 서킷브레이커 재시도 전에 기존 미체결 매도와 실제 브로커 잔고를 확인하며, 컷오프를 넘는 재시도는 예약하지 않는다. | 응답 유실 주문을 중복 제출하거나 마감 뒤 주문한다. | `test_strategy_scheduler_force_exit_retry.py` |
| source 전파 | `strategy_force_exit:<strategy>`를 scheduler에서 submitter까지 보존한다. 일반 전략·수동 매도와 같은 source로 축약하지 않는다. | timeout 뒤 잔고 없음 응답을 안전하게 대사할 수 없거나 일반 매도를 잘못 체결 처리한다. | `test_broker_order_submitter.py` |
| 불명확 응답 대사 | 강제청산 매도에서 먼저 network/unknown/timeout 응답이 있었고 뒤이어 잔고 없음이 확인된 경우에만 대사 성공으로 전환한다. 명시적 비즈니스 거부와 일반 매도는 전환하지 않는다. | 실제 거부를 체결로 오인하거나 이미 체결된 주문을 실패로 남긴다. | `test_broker_order_submitter.py` |
| 원장 종결 | 잔고 없음 대사 성공은 주문 FSM과 해당 전략 HOLD 원장을 함께 종결하되, 별도 실현손익 훅으로 이중 집계하지 않는다. | 고아 HOLD 또는 수익률 이중 집계가 생긴다. | `test_order_execution_service.py` |

## 변경 체크리스트

1. tier 시각을 바꾸면 최종 tier와 주문 컷오프의 상대 순서를 함께 검증한다.
2. force-exit source 소비 모듈을 추가하면 `test_force_exit_contract_guard.py` 허용 목록에
   넣기 전에 전략명 분류, RiskGate 매도 우회, 대사 의미를 확인한다.
3. 재시도 또는 잔고 없음 문구를 바꾸면 일반 매도와 명시적 비즈니스 거부의 음성
   테스트를 함께 유지한다.
4. 브로커 성공 응답으로 바꾸는 대사 경로는 FSM, 모의 원장, 알림, 손익 훅에서 한 번만
   종결되는지 확인한다.
