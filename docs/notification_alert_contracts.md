# Notification Alert Contracts

최종 업데이트: 2026-08-18

알림 기능을 추가하거나 수정할 때 반복 확인할 공통 계약이다. 새 알림은 아래 네 가지를 명시해야 한다.

## 공통 규칙

- 입력 키는 알림 소스에 들어오는 즉시 정규화한다.
  - 국내 종목코드: 숫자 6자리 zero-pad.
  - 미국장 심볼: 대문자.
- 외부 전파 대상 알림은 `metadata.dedup_key`를 안정적으로 채운다.
- 텔레그램 report bot으로 보내야 하는 알림은 `metadata.force_external=true`, `metadata.telegram_channel="report"`를 채운다.
- 재시작 뒤 반복되면 안 되는 알림은 당일 상태 파일 또는 `OperatorAlertService` active state로 복원 가능해야 한다.
- 중복 억제 키와 복원 키는 같은 정규화 함수를 거친 값을 사용한다.

## 현재 알림 계약

| 알림 | 발신 조건 | 중복 억제 | 재시작 복원 | 테스트 기준 |
| --- | --- | --- | --- | --- |
| 관심종목 국내 등락률 | 관심종목이 ±5% 단위 bucket을 새로 돌파 | 종목별 최고 상승 bucket, 최저 하락 bucket | `favorite_alert_state.json` 당일 bucket | `test_favorite_price_alert_service.py` |
| 관심종목 국내 상한가 | 관심종목이 상한가 sign 또는 상한가 임계에 도달 | 종목별 당일 상한가 set | `favorite_alert_state.json` upper-limit set | `test_favorite_price_alert_service.py` |
| 관심종목 미국장 등락률 | `overseas_us` 관심 심볼이 ±5% 단위 bucket을 새로 돌파 | 심볼별 최고 상승 bucket, 최저 하락 bucket | `favorite_alert_state.json` 당일 bucket | `test_favorite_price_alert_service.py`, `test_overseas_favorite_price_alert_task.py` |
| 시장 안전장치/사이드카 | 거래정지·VI·futures sidecar 조건 감지 | `market_status:*` / `market_futures:*` dedup key | active key set 기반 resolve 경로 | `test_market_status_alert_service.py` |
| 지수 임계 알림 | 설정된 지수 threshold crossing | threshold key 및 hysteresis/cooldown | 태스크 상태 파일 | `test_market_index_threshold_alert_task.py` |
| 운영자 알림 | source/dedup_key 단위 NEW/ESCALATED/RESOLVED 전이 | `OperatorAlertService` active map | operator alert state file | `test_operator_alert_service.py` |

## 추가 전 체크리스트

- [ ] 저장소 값, API 입력 값, 실시간/REST 틱 값이 서로 다른 표기여도 같은 키로 매칭되는가?
- [ ] 같은 경계값 주변에서 chatter가 나도 같은 알림을 반복하지 않는가?
- [ ] 프로세스 재시작 뒤 당일 동일 알림을 재발행하지 않는가?
- [ ] 날짜가 바뀌면 당일 알림 상태가 의도대로 초기화되는가?
- [ ] 외부 전파 대상이면 dedup key, `force_external`, `telegram_channel` 테스트가 있는가?
