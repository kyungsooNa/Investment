# KIS WebSocket Common-Stock No-Tick Escalation Package

작성일: 2026-07-05 KST

## 목적

한국투자증권 Open API 국내주식 실시간 체결가 WebSocket에서 일부 보통주가 구독 성공 및 ACK 성공 후에도 체결가 프레임을 전혀 수신하지 않는 현상을 KIS에 문의한다.

이 패키지는 ETF/우선주 무틱 이슈를 제외하고, **보통주 단독 구독에서도 0프레임이 재현된 C군 결과**만 에스컬레이션 대상으로 삼는다.

## 핵심 문의

아래 보통주 3종에 대해 `H0UNCNT0` 실시간 체결가 구독 요청이 `SUBSCRIBE SUCCESS`로 처리되고 ACK도 확인되었으나, 장중 180초 관찰 동안 체결가 프레임이 0건이었다.

KIS 서버/계정/종목 단위에서 해당 종목의 국내주식 실시간 체결가 WebSocket 프레임 송신 제한, 미지원, 라우팅 누락, 권한/계정 설정 문제가 있는지 확인 요청한다.

| 종목코드 | 종목명 | 상품군 | 구독 성공 | ACK | 수신 프레임 delta | 품질 reject delta | dispatch delta |
|---|---|---|---|---|---:|---:|---:|
| 080220 | 제주반도체 | 보통주 | True | True | 0 | 0 | 0 |
| 353200 | 대덕전자 | 보통주 | True | True | 0 | 0 | 0 |
| 004710 | 한솔테크닉스 | 보통주 | True | True | 0 | 0 | 0 |

## 재현 조건

- 일시: 2026-06-22 09:12:16~09:15:18 KST
- 모드: 실전 WebSocket 시세 구독
- TR: 국내주식 실시간 체결가 `H0UNCNT0`
- 구독 대상: 위 보통주 3종만 단독 구독
- 관찰 시간: 180초
- 주문/체결 경로 사용 여부: 없음, 시세 구독만 수행

## 배제된 원인

| 가설 | 판단 | 근거 |
|---|---|---|
| 구독 실패 | 배제 | C군 3종 모두 `subscribe_ok=True` |
| ACK 실패 | 배제 | C군 3종 모두 `ack_ok=True` |
| 클라이언트 품질 필터 탈락 | 배제 | `quality_reject_delta=0` |
| 파싱 실패/비정상 payload | 배제 | `malformed_delta=0` |
| ETF/우선주 상품군 문제 | 별도 이슈로 분리 | B군은 정책상 REST 폴백으로 수용, 이번 문의 대상에서 제외 |
| 슬롯/대량 구독 컨텍스트 문제 | 가능성 낮음 | C군에서 보통주 3종만 단독 구독해도 0프레임 |
| 재구독 refresh 복구 가능성 | 배제 | D군에서 refresh 후에도 대상 보통주 5종 0프레임 |

## 비교 관찰

A군 보통주-only 실험에서는 같은 `H0UNCNT0` 경로에서 일부 보통주는 정상 수신되었다. 따라서 WebSocket 연결 전체 또는 TR 전체 장애라기보다, 특정 보통주 또는 계정/종목 조합에서 프레임이 송신되지 않는 현상으로 판단한다.

| 정상 수신 보통주 | 수신 프레임 delta |
|---|---:|
| 028260 삼성물산 | 729 |
| 032830 삼성생명 | 524 |
| 240810 원익IPS | 806 |
| 034730 SK | 247 |
| 298040 효성중공업 | 170 |

## KIS 문의 문안

제목:

국내주식 실시간 체결가 WebSocket(H0UNCNT0) 일부 보통주 ACK 후 프레임 미수신 문의

본문:

안녕하세요. 한국투자증권 Open API 국내주식 실시간 체결가 WebSocket 사용 중 일부 보통주에서 구독 성공 및 ACK 성공 후에도 체결가 프레임이 전혀 수신되지 않는 현상이 반복되어 문의드립니다.

2026-06-22 09:12:16~09:15:18 KST 장중에 보통주 3종(080220 제주반도체, 353200 대덕전자, 004710 한솔테크닉스)만 단독으로 `H0UNCNT0` 실시간 체결가를 구독했습니다. 각 종목은 모두 `SUBSCRIBE SUCCESS`와 ACK가 확인되었으나 180초 동안 수신 프레임이 0건이었습니다. 클라이언트 측 quality reject, malformed payload, dispatch 실패도 모두 0건입니다.

같은 세션의 보통주-only 비교 실험에서는 028260, 032830, 240810, 034730, 298040 등 다른 보통주는 정상적으로 수백 건의 체결가 프레임을 수신했습니다. 따라서 WebSocket 연결 또는 TR 전체 장애가 아니라 특정 보통주 또는 계정/종목 조합에서 서버 프레임이 송신되지 않는 현상으로 보입니다.

확인 요청드립니다.

1. 위 3개 보통주가 국내주식 실시간 체결가 WebSocket `H0UNCNT0` 송신 대상에서 제외되거나 제한되는 조건이 있는지
2. 계정/HTS ID/API 권한/실전 시세 권한 설정에 따라 특정 보통주 프레임이 송신되지 않을 수 있는지
3. 서버 로그상 위 시간대에 해당 종목 구독 요청은 정상 등록되었는지, 이후 체결가 프레임 송신 시도가 있었는지
4. 보통주임에도 WebSocket 실시간 체결가가 제공되지 않는 종목 목록 또는 별도 예외 정책이 있는지

첨부 파일에는 실험 결과 JSON/Markdown과 종합 분석 리포트를 포함했습니다. 필요하시면 민감값을 제거한 원본 실행 로그도 추가로 제공하겠습니다.

## 기본 첨부 파일

- `reports/no_tick_operational_experiment_result_C_no_tick_common_solo_20260622_live.json`
- `reports/no_tick_operational_experiment_result_C_no_tick_common_solo_20260622_live.md`
- `reports/no_tick_operational_experiment_analysis_20260622_live.json`
- `reports/no_tick_operational_experiment_analysis_20260622_live.md`
- `reports/no_tick_diagnosis_20260619.json`
- `reports/no_tick_diagnosis_20260619.md`

## 보조 첨부 파일

- `reports/no_tick_operational_experiment_result_A_common_stock_only_20260622_live.json`
- `reports/no_tick_operational_experiment_result_A_common_stock_only_20260622_live.md`
- `reports/no_tick_operational_experiment_result_D_refresh_observation_20260622_live.json`
- `reports/no_tick_operational_experiment_result_D_refresh_observation_20260622_live.md`

원본 로그 `reports/no_tick_live_20260622.log`는 HTS ID와 접속키 일부가 포함될 수 있고 인코딩이 깨져 있으므로 기본 첨부에서 제외한다. KIS가 요청할 때만 HTS ID, approval key, token, app key, 계좌 식별자 등을 제거한 sanitized 로그로 전달한다.

## 완료 판정

이 항목은 다음 중 하나가 확인되면 완료로 본다.

- KIS가 해당 보통주의 WebSocket 미송신/권한/계정/종목 예외를 확인한다.
- KIS가 서버 로그상 정상 송신을 확인하고, 클라이언트가 추가 재현 실험으로 수신 여부를 재검증한다.
- KIS가 공식적으로 해당 종목군 또는 조건에서 WebSocket 체결가 미제공을 안내한다.

완료 전까지 운영 정책은 유지한다.

- ETF/우선주 무틱은 예상된 무틱으로 보고 REST 폴백 처리한다.
- 보통주 무틱은 장중 quarantine + REST 폴백 대상으로 유지한다.
- event-driven 전환은 보통주 WebSocket 수신 신뢰도가 확인될 때까지 shadow/parity 단계로 올리지 않는다.
