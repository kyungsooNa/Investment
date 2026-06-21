# No-Tick 분리 실험 런북 (다음 장중 세션용)

작성일: 2026-06-21 · 대상 블로커: **P2 2-4 WebSocket price 피드 무틱 ≈55%**

> 목적: 2026-06-19 진단(`a1_kis_no_send` = KIS가 ACK 후 프레임 미전송)을 받아,
> **무엇이 무틱을 유발하는지**(상품군 / 구독 컨텍스트(슬롯·churn) / 종목 자체)를
> 장중 1회 세션으로 분리 판정한다. 코드 수정이 아니라 **운영 실험 실행 + 판정**이 목표.

## 0. 현재 상태 (2026-06-21 dry-run 검증 완료)

- 하네스 4종 모두 존재·green: `build_no_tick_operational_plan` → `run_no_tick_operational_experiment`(dry-run/preflight/execute-live) → `analyze_no_tick_operational_experiment`.
- 실험 계획 생성 완료: `reports/no_tick_operational_experiment_plan_20260619.json` (실험 A~D).
- **dry-run 전체 파이프라인 통과 확인**: `--all` dry-run → 4 cohort 결과(JSON+MD) 생성 → analyzer가 `not_executed` verdict로 집계(정상). 단위 테스트 45개 통과.
- 남은 것은 **장중 `--execute-live` 1회 실행 + 판정**뿐. (장 시간 필요 → 본 런북이 그 절차.)

## 1. 실험 설계 요약 (4 cohort)

| ID | 구독 구성 | 분리하려는 변수 | 핵심 가설 |
|----|-----------|-----------------|-----------|
| **A_common_stock_only** | 무틱·정상 공통주 10종만 (ETF/우선주 제외) | **상품군 오염 / 슬롯 churn** | ETF/우선주를 빼면 공통주 무틱이 사라지나? |
| **B_non_common_only** | ETF/우선주 5종만 | **KIS 상품군별 동작** | ETF는 단독 구독에도 0프레임인가? |
| **C_no_tick_common_solo** | 무틱 공통주 3종만 (solo) | **종목 vs 구독 컨텍스트** | 30~40종 컨텍스트를 벗으면 틱이 오나? |
| **D_refresh_observation** | 무틱 5종 + 반복 refresh | **refresh 복구 가능성** | 재구독이 프레임을 복원하나? |

대상 종목(계획서 기준):
- 무틱 공통주: `080220 제주반도체`, `353200 대덕전자`, `004710 한솔테크닉스`, `198440 강동씨앤엘`, `320000 한울반도체`
- 정상 공통주(대조): `028260 삼성물산`, `032830 삼성생명`, `240810 원익IPS` 등
- 무틱 ETF: `069500 KODEX 200`, `396500 TIGER 반도체TOP10`, `379800 KODEX 미국S&P500`, `0162Z0`, `0167A0`

## 2. 실행 절차 (장중, KST 09:00~15:30)

py310 환경 기준. 모든 경로는 repo 루트 기준.

### 2-1. (장 마감 중) preflight·계획 재확인 — 지금도 가능
```powershell
# dry-run으로 계획 로드/검증만 (WebSocket 미연결)
C:\Users\Kyungsoo\anaconda3\envs\py310\python.exe scripts\run_no_tick_operational_experiment.py `
  --plan reports\no_tick_operational_experiment_plan_20260619.json --all `
  --output-dir reports\_dryrun --run-label preflight_check
```
- 4 cohort 결과 파일이 생성되고 모두 `status=dry_run`이면 정상.
- 장중 실행 전 계획서가 최신 무틱 종목을 반영하는지 확인. 종목이 바뀌었으면 §4로 계획 재생성.

### 2-2. (장중) 배치 라이브 실행 + 자동 분석 — 권장 1줄
```powershell
C:\Users\Kyungsoo\anaconda3\envs\py310\python.exe scripts\run_no_tick_operational_experiment.py `
  --plan reports\no_tick_operational_experiment_plan_20260619.json --all `
  --duration-sec 180 --between-sec 30 `
  --output-dir reports --run-label 20260622_live `
  --execute-live --real --analyze-after
```
- `--execute-live` + `--real` → KIS WebSocket 연결, cohort별 180초 구독 후 tick-ingest 델타 기록.
- 장 운영 시간이 아니면 **preflight가 차단**(`Live execution blocked: market is not operating hours`). 의도적 우회 시에만 `--force-live`.
- `--between-sec 30`: cohort 간 슬롯 정리 간격(이전 구독 잔재 배제).
- `--analyze-after`: 4개 결과 JSON을 모아 분석 MD 자동 생성.

> 모의(`--paper`)로도 실행 가능하나, 무틱은 실전 피드 현상이므로 **`--real` 권장**.
> 실전 주문이 아니라 **시세 구독만** 하므로 주문 리스크는 없음(러너는 주문 경로 미사용).

### 2-3. (개별 실행이 필요하면) cohort 단위
```powershell
C:\Users\Kyungsoo\anaconda3\envs\py310\python.exe scripts\run_no_tick_operational_experiment.py `
  --plan reports\no_tick_operational_experiment_plan_20260619.json `
  --experiment-id A_common_stock_only --duration-sec 180 --execute-live --real
```
실행 후 분석:
```powershell
C:\Users\Kyungsoo\anaconda3\envs\py310\python.exe scripts\analyze_no_tick_operational_experiment.py `
  --result "reports\no_tick_operational_experiment_result_*_20260622_live.json" `
  --output-markdown reports\no_tick_operational_experiment_analysis_20260622.md
```

## 3. 판정 기준 (결과 → 결론 → 다음 행동)

각 cohort 결과의 per-code `Received Δ`(>0 = 프레임 수신)로 판정한다.
verdict는 analyzer가 `received` / `no_tick` / `quality_reject` 등으로 분류.

| Cohort | 결과 | 결론 | 다음 행동 |
|--------|------|------|-----------|
| **A** | 무틱 공통주가 **틱 수신** | 상품군 오염/슬롯 churn이 원인 | ETF/우선주를 별도 연결로 격리 → 공통주 피드 보호 |
| **A** | 여전히 **0프레임** | 상품군은 원인 아님 | C/B 결과로 종목 vs KIS 판정 |
| **B** | ETF가 solo에서도 **0프레임** | **KIS의 ETF 상품군 미전송** 구조적 확정 | ETF는 실시간 구독 대상에서 제외 또는 REST 폴백 |
| **B** | ETF가 **틱 수신** | ETF 자체는 정상, 혼합 컨텍스트 문제 | A와 교차 → 슬롯/혼합 원인 |
| **C** | solo 공통주가 **틱 수신** | **구독 컨텍스트(30~40종 슬롯/churn)** 가 원인 | 활성 구독 수 축소 / 우선순위 슬롯 / churn 감소 |
| **C** | solo에서도 **0프레임** | **종목 단위 KIS 미서빙** | 해당 종목 quarantine + REST 폴백, KIS 문의 |
| **D** | refresh로도 **복구 안 됨** | 재구독은 무효 | 장중 무틱 종목 **quarantine**(재구독 중단) + REST 폴백 |
| **D** | refresh로 **복구됨** | 일시적 슬롯 누락 | watchdog refresh 정책 유지·강화 |

### 종합 판정 매트릭스
- **A 회복 + C 회복** → 컨텍스트(슬롯/churn) 원인 → *구독 수 축소·격리*가 운영 해법 (코드 가능).
- **A 미회복 + C 미회복 + B 0프레임** → **KIS 상품군/종목 미전송** → 코드로 해결 불가, *quarantine + REST 폴백* + KIS 문의가 유일.
- **B만 0프레임** → ETF 한정 문제 → ETF만 REST 폴백.

→ 결론은 `reports/no_tick_operational_experiment_analysis_*.md`에 verdict로 남고,
   운영 결정은 `docs/operations_runbook.md`에 반영.

## 4. (선택) 계획 재생성 — 무틱 종목이 바뀐 경우
최신 streaming 로그로 진단을 갱신한 뒤 계획을 다시 빌드:
```powershell
C:\Users\Kyungsoo\anaconda3\envs\py310\python.exe scripts\analyze_no_tick_diagnosis.py --output-json reports\no_tick_diagnosis_<date>.json --output-markdown reports\no_tick_diagnosis_<date>.md
C:\Users\Kyungsoo\anaconda3\envs\py310\python.exe scripts\build_no_tick_operational_plan.py --diagnosis reports\no_tick_diagnosis_<date>.json --output-json reports\no_tick_operational_experiment_plan_<date>.json --output-markdown reports\no_tick_operational_experiment_plan_<date>.md
```
(정확한 인자는 각 스크립트 `--help` 참조.)

## 5. 안전·주의
- 러너는 **시세 구독만** 한다 — 주문/체결 경로 없음. 실전 토큰을 쓰지만 거래 리스크 없음.
- 장중 라이브 실행은 운영 트래픽과 WebSocket 슬롯을 공유하므로 가능하면 **운영 스케줄러 정지 후** 단독 실행(슬롯 cap 40 경쟁 배제).
- 결과는 1회 세션으로 충분(분리 실험). 무틱 비율이 날마다 흔들리면 2~3 세션 반복.

## 6. 결정 후 코드 후속 (판정 결과별)
- 컨텍스트 원인 → 구독 우선순위/슬롯 수 축소 (`services/subscription_policy.py`, `price_subscription_service.py`).
- 종목/상품군 미서빙 → 무틱 quarantine + REST 폴백 경로 (`websocket_watchdog_task.py`, `market_data_service.py`).
- 어느 쪽이든 **P2 2-4의 PR-3(shadow parity) 진입 가능 여부**가 여기서 갈린다.
