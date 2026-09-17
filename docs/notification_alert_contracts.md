# Notification Alert Contracts

최종 업데이트: 2026-09-17

알림 기능을 추가하거나 수정할 때 반복 확인할 공통 계약이다. 새 알림은 아래 네 가지를 명시해야 한다.

## 공통 규칙

- 입력 키는 알림 소스에 들어오는 즉시 정규화한다.
  - 국내 종목코드: 숫자 6자리 zero-pad.
  - 미국장 심볼: 대문자.
- 외부 전파 대상 알림은 `metadata.dedup_key`를 안정적으로 채운다.
- 텔레그램 report bot으로 보내야 하는 알림은 `metadata.force_external=true`, `metadata.telegram_channel="report"`를 채운다.
- 재시작 뒤 반복되면 안 되는 알림은 당일 상태 파일 또는 `OperatorAlertService` active state로 복원 가능해야 한다.
- 중복 억제 키와 복원 키는 같은 정규화 함수를 거친 값을 사용한다.
- **발송에 성공한 뒤에만 '보냈다' 를 영속화한다.** 감지와 발송 사이에 프로세스가 죽었을 때 '보냄' 이 먼저 적혀 있으면 그 사건은 영영 건너뛴다 — 실제로 거래일 20260908 해외 dry-run 티켓이 그렇게 유실됐다(#969). 인메모리 표시는 즉시 해도 되지만(같은 루프의 중복 예약 방지), 디스크 기록은 발송 성공 뒤다.
- **점수·임계로 거르는 알림은 즉시 발송과 일일 요약에 같은 하한을 적용한다.** 한쪽만 걸면 즉시 알림에서 걸러낸 건이 요약에서 되살아난다(#966).
- 새 발신 경로는 **이벤트성 알림**(같은 사건이 두 번 나가면 안 됨 — 계약표에 행 필요)과 **정기 리포트**(스케줄 1회 발송 — dedup 키 불필요) 중 하나로 분류한다. 분류는 `tests/unit_test/services/test_notification_alert_contract_guard.py` 가 강제한다.

## 현재 알림 계약

| 알림 | 발신 조건 | 중복 억제 | 재시작 복원 | 테스트 기준 |
| --- | --- | --- | --- | --- |
| 관심종목 국내 등락률 | 관심종목이 ±5% 단위 bucket을 새로 돌파 | 종목별 최고 상승 bucket, 최저 하락 bucket | `favorite_alert_state.json` 당일 bucket | `test_favorite_price_alert_service.py` |
| 관심종목 국내 상한가 | 관심종목이 상한가 sign 또는 상한가 임계에 도달 | 종목별 당일 상한가 set | `favorite_alert_state.json` upper-limit set | `test_favorite_price_alert_service.py` |
| 관심종목 미국장 등락률 | `overseas_us` 관심 심볼이 ±5% 단위 bucket을 새로 돌파 | 심볼별 최고 상승 bucket, 최저 하락 bucket | `favorite_alert_state.json` 당일 bucket | `test_favorite_price_alert_service.py`, `test_overseas_favorite_price_alert_task.py` |
| 시장 안전장치/사이드카 | 거래정지·VI·futures sidecar 조건 감지 | `market_status:*` / `market_futures:*` dedup key | active key set 기반 resolve 경로 | `test_market_status_alert_service.py` |
| 지수 임계 알림 | 설정된 지수 threshold crossing | threshold key 및 hysteresis/cooldown | 태스크 상태 파일 | `test_market_index_threshold_alert_task.py` |
| 마켓타이밍 일간 갱신 | 한국장 장전 window에서 KOSPI/KOSDAQ 레짐을 일 1회 갱신 | 태스크의 `last_checked_date` | 없음(일중 프로세스 기준 1회, 재시작 시 장전 window 내 재발행 가능) | `test_market_timing_daily_update_task.py`, `test_oneil_universe_service.py` |
| 운영자 알림 | source/dedup_key 단위 NEW/ESCALATED/RESOLVED 전이 | `OperatorAlertService` active map | operator alert state file | `test_operator_alert_service.py` |
| 무역 트렌드 릴리스 (`send_national_trade_trend_report`) | 관세청/산업부 국가 수출입 릴리스를 새로 감지 | `release.dedup_key`(`national_trade:{phase}:{기간}:{url}`) — URL 은 `canonicalize_national_trade_url` 로 정규화 | `data/trade_trend_state.json` 의 `sent_keys`(로드 시 재정규화) | `test_trade_trend_repository.py`, `test_trade_trend_service.py` |
| 제주 반도체 무역 (`send_jeju_semiconductor_trade_report`) | 해당 월 제주 반도체 수출 데이터가 확보됨 | 리포트 `dedup_key`(월 단위) | 같은 `sent_keys` 상태 파일 | `test_trade_trend_monitor_task.py` |
| 제주 무역 pending (`send_jeju_trade_pending_report`) | 해당 월 데이터가 아직 업스트림에 없음(부재를 장애로 오인하지 않게 알림) | `jeju_trade_pending:{기간}:{품목코드}` | 같은 `sent_keys` 상태 파일 | `test_trade_trend_monitor_task.py`, `test_telegram_notifier_report_paths.py` |
| 장중 거래량 급증 (`send_intraday_volume_surge_alert`) | 랭킹 후보의 예상 일거래량이 평소 대비 tier 배수 이상(거래대금 하한·ETF 제외·09:05 이후) | 종목별 **최고 tier** — 같은 종목은 더 높은 tier 로만 재발신 | **없음**(태스크 메모리 `_sent_tiers`, 거래일 바뀌면 초기화) — 재시작 시 당일 재발신 가능. 관찰용 알림이라 수용한다 | `test_intraday_volume_surge_alert_task.py` |
| 공시 즉시 알림 (`send_disclosure_alert`) | 공시 중요도 점수가 `immediate_alert_score`(기본 70) 이상. `minimum_alert_score`(기본 31) 미만은 저장·알림·요약에서 모두 제외 | `rcept_no` 고유 + `immediate_sent_at` | SQLite `disclosures` 테이블(프로세스 밖 영속) | `test_dart_disclosure_monitor_task.py`, `test_dart_disclosure_repository.py` |
| 공시 일일 요약 (`send_disclosure_digest`) | 당일 수집분 중 `minimum_alert_score` 이상 ~ 즉시 알림 임계 미만 | `digest_sent_at` | 같은 SQLite 테이블 | `test_dart_disclosure_monitor_task.py`, `test_dart_disclosure_repository.py` |
| YouTube 다이제스트 | 장전 window 에 채널 신규 영상 자막을 수집·요약. 차단(IpBlocked)은 빈 리포트로 위장하지 않고 ERROR 로 알린다 | 태스크의 `_last_run_date`(일 1회) + `TimeDispatcher` 일일 티켓 | dispatcher 의 발행 날짜(발행 성공 뒤 저장 — 위 공통 규칙) | `test_youtube_digest_task.py`, `test_time_dispatcher.py` |

## 추가 전 체크리스트

- [ ] 저장소 값, API 입력 값, 실시간/REST 틱 값이 서로 다른 표기여도 같은 키로 매칭되는가?
- [ ] 같은 경계값 주변에서 chatter가 나도 같은 알림을 반복하지 않는가?
- [ ] 프로세스 재시작 뒤 당일 동일 알림을 재발행하지 않는가?
- [ ] 날짜가 바뀌면 당일 알림 상태가 의도대로 초기화되는가?
- [ ] 외부 전파 대상이면 dedup key, `force_external`, `telegram_channel` 테스트가 있는가?
- [ ] 발송 성공 전에 '보냄' 을 디스크에 적지 않는가? (감지와 발송 사이에 죽어도 사건이 유실되지 않는가?)
- [ ] 점수·임계 필터가 있다면 즉시 알림과 일일 요약에 같은 하한이 걸리는가?
- [ ] 새 발신 메서드를 알림/리포트로 분류하고, 알림이면 위 계약표에 행을 추가했는가?
