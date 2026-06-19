# WebSocket No-Tick Operational Diagnosis — 2026-06-19

## 결론

2026-06-19 라이브 로그 기준 WebSocket price no-tick의 1차 판정은 `a1_kis_no_send`이다.

- `subscribed_no_tick`: 16종목
- `a1_kis_no_send`: 15종목
- `unknown_no_snapshot`: 1종목 (`052710`)
- `not_subscribed`: 0종목
- `quality_reject`: 0종목
- 정상 tick 수신: 11종목

즉 현재 증거로는 “구독 ACK 미확정”, “DataQuality 게이트 전량 탈락”, “event router dispatch 실패”보다 **ACK 이후 KIS가 특정 종목 프레임을 보내지 않는 현상**이 우세하다.

## 입력 데이터

- Streaming logs:
  - `logs/streaming/20260619_082018_streaming_1.log.json`
  - `logs/streaming/20260619_131839_streaming_1.log.json`
- Event shadow:
  - `logs/strategies/event_shadow/20260619.jsonl`
- 종목 메타:
  - `data/stock_code_list.csv`
- Base diagnosis:
  - `reports/no_tick_diagnosis_20260619.md`
  - `reports/no_tick_diagnosis_20260619.json`

## 핵심 관찰

### 1. 비보통주 no-tick 집중

| 구분 | 대상 수 | no-tick | 정상 tick |
|------|--------:|--------:|----------:|
| ETF | 8 | 8 | 0 |
| 우선주/비보통 | 1 | 1 | 0 |
| 보통주/기타 | 18 | 7 | 11 |

ETF 8종목과 우선주 1종목은 전부 no-tick이었다. 다만 보통주/기타에서도 7종목이 no-tick이라, ETF만의 문제로 단정하면 안 된다.

### 2. 정상 수신 종목은 재구독 refresh 루프에 걸리지 않음

정상 tick 수신 11종목은 모두 `received > 0`, `dispatched == received`, `missing_reason == 0`이었다.

| 코드 | 종목명 | 시장 | received |
|------|--------|------|---------:|
| 028260 | 삼성물산 | KOSPI | 13,935 |
| 032830 | 삼성생명 | KOSPI | 9,856 |
| 240810 | 원익IPS | KOSDAQ | 9,392 |
| 034730 | SK | KOSPI | 8,902 |
| 298040 | 효성중공업 | KOSPI | 7,833 |
| 222800 | 심텍 | KOSDAQ | 7,348 |
| 329180 | HD현대중공업 | KOSPI | 5,535 |
| 001450 | 현대해상 | KOSPI | 4,716 |
| 443060 | HD현대마린솔루션 | KOSPI | 3,430 |
| 033640 | 네패스 | KOSDAQ | 2,214 |
| 083450 | GST | KOSDAQ | 2,179 |

### 3. no-tick 종목은 refresh 반복에도 회복되지 않음

`subscribed_no_tick_refresh` 흐름으로 `price_unsubscribe`/`price_subscribe`가 반복됐지만, a1 종목의 `received`는 계속 0이었다.

| 코드 | 종목명 | 유형 | no_tick 로그 | subscribe | price_subscribe | price_unsubscribe | received |
|------|--------|------|-------------:|----------:|----------------:|------------------:|---------:|
| 004710 | 한솔테크닉스 | 보통/기타 | 202 | 21 | 72 | 73 | 0 |
| 080220 | 제주반도체 | 보통/기타 | 211 | 16 | 81 | 75 | 0 |
| 353200 | 대덕전자 | 보통/기타 | 209 | 17 | 75 | 75 | 0 |
| 198440 | 강동씨앤엘 | 보통/기타 | 146 | 10 | 50 | 50 | 0 |
| 320000 | 한울반도체 | 보통/기타 | 146 | 9 | 48 | 49 | 0 |
| 0162Z0 | RISE 삼성전자SK하이닉스채권혼합50 | ETF | 138 | 14 | 48 | 48 | 0 |
| 0167A0 | SOL AI반도체TOP2플러스 | ETF | 138 | 14 | 48 | 48 | 0 |
| 069500 | KODEX 200 | ETF | 138 | 13 | 48 | 48 | 0 |
| 396500 | TIGER 반도체TOP10 | ETF | 134 | 14 | 48 | 48 | 0 |
| 379800 | KODEX 미국S&P500 | ETF | 107 | 11 | 37 | 37 | 0 |
| 360750 | TIGER 미국S&P500 | ETF | 76 | 9 | 26 | 26 | 0 |
| 122630 | KODEX 레버리지 | ETF | 72 | 6 | 24 | 24 | 0 |
| 009155 | 삼성전기우 | 우선주 | 66 | 7 | 24 | 24 | 0 |
| 149950 | 아바텍 | 보통/기타 | 20 | 1 | 7 | 7 | 0 |
| 469150 | ACE AI반도체TOP3+ | ETF | 13 | 1 | 5 | 5 | 0 |

이 패턴은 “재구독하면 해결되는 local stale state”보다 “ACK 이후 해당 symbol stream frame이 도착하지 않는 외부/상품군/구독 슬롯 조건” 쪽에 무게를 둔다.

## 가설

### H1. ETF/우선주 price stream 제약 또는 TR/상품군 차이

근거:
- ETF 8/8 no-tick
- 우선주 1/1 no-tick
- 정상 수신 11종목은 모두 보통주/기타

반증/주의:
- 보통주/기타도 7종목 no-tick이므로 ETF/우선주만 제거해도 전체 문제가 완전히 사라진다고 단정할 수 없다.

### H2. 구독 슬롯/순서/리밸런스 상태에 따른 KIS frame 누락

근거:
- no-tick 보통주가 존재한다.
- no-tick 종목은 refresh가 반복되어도 frame count가 0이다.
- 정상 종목과 no-tick 종목이 같은 전략(`래리윌리엄스VBO`) 후보군 안에 섞여 있다.

반증/주의:
- 구독 ACK 자체는 active mark 조건을 통과했으므로 단순 ACK 미확정 문제는 아니다.

### H3. 특정 종목의 실제 장중 체결 없음

현재 근거로는 낮은 가능성이다.

이유:
- `080220`, `353200`, `004710` 등은 일반적으로 체결이 없는 종목으로 보기 어렵다.
- `missing_reason`이 장중 장시간 반복되고, `received=0`이 유지됐다.

## 다음 운영 실험

### 실험 A: 보통주만 구독

목표: ETF/우선주 제거 시 no-tick 비율이 급감하는지 확인.

대상 예시:
- 정상 수신 보통주 5개: `028260`, `032830`, `240810`, `034730`, `222800`
- no-tick 보통주 5개: `004710`, `080220`, `353200`, `198440`, `320000`

판정:
- no-tick 보통주가 계속 `received=0`이면 상품군 문제가 아니라 symbol/slot/order 문제 가능성 증가.
- no-tick 보통주가 정상화되면 ETF/우선주 혼합 구독 또는 슬롯 churn 영향 가능성 증가.

### 실험 B: ETF/우선주만 소수 구독

목표: ETF/우선주가 KIS price stream에서 구조적으로 frame 미전송인지 확인.

대상 예시:
- ETF: `069500`, `122630`, `360750`, `396500`
- 우선주: `009155`

판정:
- 단독/소수 구독에서도 `received=0`이면 KIS 상품군 제약 가능성 매우 높음.
- 단독 구독에서는 수신되면 혼합 구독/슬롯 순서 문제 가능성 증가.

### 실험 C: no-tick 보통주 단독 구독

목표: 개별 보통주 symbol이 KIS stream에서 정상 frame을 받을 수 있는지 확인.

대상 예시:
- `080220`, `353200`, `004710`

판정:
- 단독에서도 `received=0`이면 KIS symbol-level 문제 또는 환경/계정 제한 가능성.
- 단독에서는 수신되지만 30~40개 후보군에서만 no-tick이면 슬롯/순서/동시 구독 조건 문제.

### 실험 D: refresh 정책 관찰

목표: no-tick refresh가 회복에 기여하는지 확인.

현재 로그에서는 refresh 반복이 회복으로 이어지지 않았다. 다음 실험에서도 동일하면, 운영 중 과도한 unsubscribe/subscribe churn을 줄이는 정책을 검토한다.

## KIS 문의용 요약

문의 포인트:

1. 국내주식 실시간 체결가 WebSocket에서 ETF/우선주가 일반 주식과 동일 TR/구독 방식으로 tick frame을 제공하는지 확인 필요.
2. 구독 요청은 ACK/active 조건을 통과했으나, 특정 종목은 장중 `received=0`으로 frame 자체가 오지 않음.
3. 같은 세션에서 일부 보통주는 정상 수신됨. 예: `028260` 13,935 ticks, `032830` 9,856 ticks.
4. 문제 종목은 재구독 refresh를 수십 회 반복해도 회복되지 않음. 예: `080220` price_subscribe 81회, received 0.
5. `not_subscribed`는 0건이고, DataQuality reject도 0건이라 client-side parsing/quality reject 문제로 보기 어려움.

첨부 근거:

- `reports/no_tick_diagnosis_20260619.md`
- `reports/no_tick_diagnosis_20260619.json`
- `reports/no_tick_operational_diagnosis_20260619.md`
- 원본 로그:
  - `logs/streaming/20260619_082018_streaming_1.log.json`
  - `logs/streaming/20260619_131839_streaming_1.log.json`
  - `logs/strategies/event_shadow/20260619.jsonl`

## 운영 우선순위

1. 보통주-only 실험으로 ETF/우선주 혼입 영향을 제거한다.
2. ETF/우선주-only 소수 실험으로 상품군 제약 여부를 KIS에 확인 가능한 형태로 만든다.
3. no-tick 보통주 단독 실험으로 symbol-level/slot-level 문제를 분리한다.
4. refresh 반복이 계속 무효이면 refresh 빈도를 줄이거나, no-tick symbol을 당일 격리하는 정책을 검토한다.
5. 원인 해소 전까지 event shadow parity 판정은 보류한다. polling 경로와 profitability journal 축적은 계속 진행한다.
