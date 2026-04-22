# CODEX_WORKFLOW

최종 업데이트: 2026-04-22

이 문서는 Codex가 이 저장소에서 작업할 때 따를 전용 작업 매뉴얼이다.

주의:

- `SKILL.md`는 Claude 전용 규칙 문서로 유지한다.
- 프로젝트 공용 지식은 `CODEBASE_SUMMARY.md`에 유지한다.
- Codex는 이 문서를 자신의 작업 루틴 문서로 사용한다.

## Git 주의사항

- 이 작업 폴더는 Windows `Encrypted` 속성이 걸려 있어, 샌드박스 상태의 `git`이 가끔 `.git` 폴더를 보고도 `not a git repository`를 반환할 수 있다.
- 이런 경우:
  - 먼저 `Get-ChildItem -Force .git`로 `.git` 폴더 존재를 확인한다.
  - 그 다음 `git rev-parse --show-toplevel` 또는 `git status --short --branch`를 권한 상승으로 다시 실행해 저장소 인식 여부를 확인한다.
  - 저장소 루트가 정상 출력되면 이후 `git checkout -b ...`, `git status`, `git diff` 같은 git 명령도 같은 방식으로 진행한다.
- 즉, 이 저장소에서 git이 갑자기 루트를 못 찾으면 실제 저장소 문제가 아니라 권한/암호화 이슈일 가능성을 먼저 의심한다.

## 시작 순서

새 작업을 시작할 때는 아래 순서를 기본으로 한다.

1. `AGENTS.md`
2. `CODEBASE_SUMMARY.md`
3. 이 문서 `CODEX_WORKFLOW.md`
4. 요청과 가장 가까운 대상 파일

## 문서 역할 분리

- `AGENTS.md`
  - 프로젝트 운영 규칙, 테스트 hang 주의사항, 구조 개요
- `SKILL.md`
  - Claude 전용 규칙
- `CODEBASE_SUMMARY.md`
  - 공용 프로젝트 요약, 아키텍처, 조사 경로, 검증 루틴
- `CODEX_WORKFLOW.md`
  - Codex 전용 작업 순서, 수정 전략, 검증 방식

## 요청 유형별 시작점

### 웹/API 수정

- 먼저 볼 파일:
  - `view/web/routes/*`
  - `view/web/web_main.py`
- 다음 단계:
  - `services/stock_query_service.py`
  - `services/order_execution_service.py`

### 조회 로직 수정

- 먼저 볼 파일:
  - `services/stock_query_service.py`
  - `services/market_data_service.py`
- 다음 단계:
  - `brokers/broker_api_wrapper.py`
  - `brokers/korea_investment/korea_invest_client.py`
  - `brokers/korea_investment/korea_invest_quotations_api.py`

### 주문 로직 수정

- 먼저 볼 파일:
  - `services/order_execution_service.py`
- 다음 단계:
  - `brokers/broker_api_wrapper.py`
  - `brokers/korea_investment/korea_invest_client.py`
  - `brokers/korea_investment/korea_invest_trading_api.py`

### 전략 수정

- 먼저 볼 파일:
  - `strategies/*`
  - `strategies/strategy_executor.py`
- 다음 단계:
  - `scheduler/strategy_scheduler.py`
  - `services/minervini_stage_service.py`
  - `services/rs_rating_service.py`

### 스케줄러/백그라운드 수정

- 먼저 볼 파일:
  - `scheduler/strategy_scheduler.py`
  - `task/background/*`
- 다음 단계:
  - `view/web/web_app_initializer.py`

### 테스트 안정화

- 먼저 볼 파일:
  - `tests/unit_test/*`
  - `tests/integration_test/*`
  - `tests/**/conftest.py`
  - `pytest.ini`

## Codex 작업 전략

### 1. 전체를 다시 읽지 말고 진입점부터 좁게 내려간다

- route/service에서 시작
- 필요한 경우에만 broker/scheduler/task로 내려간다
- 초기화 조립부는 마지막에 본다

### 2. 조립부 수정은 최대한 늦춘다

- `view/web/web_app_initializer.py`는 영향 범위가 크다
- 가능하면 개별 route/service/strategy 수정으로 해결한다
- 정말 필요할 때만 초기화 경로를 수정한다

### 3. 브로커 체인 수정은 테스트 전략과 같이 본다

- `BrokerAPIWrapper`는 retry/cache/circuit breaker가 걸려 있다
- 수정 전후로 mock/retry/hang 가능성을 같이 점검한다

### 4. background task는 생명주기까지 같이 본다

- task 본문만 보지 않는다
- `start()`, `stop()`, `cancel`, `sleep`, restore 흐름을 함께 본다

### 5. 조회 로직은 데이터 소스 우선순위를 같이 본다

- `MarketDataService`는 단순 프록시가 아니다
- 단기 캐시
- DB 스냅샷
- API 폴백
- 이 우선순위를 항상 같이 확인한다

### 6. 주문 로직은 후처리까지 같이 본다

- 주문 API 호출만 보지 않는다
- 구독 추가/해제
- 알림
- 가상매매 기록
- 스케줄러/포지션 영향까지 함께 본다

## 수정 전 체크리스트

- 요청이 웹/API, 조회, 주문, 전략, 스케줄러, 테스트 중 무엇인가?
- route/service 선에서 끝나는가?
- broker까지 영향이 번지는가?
- background task가 같이 영향을 받는가?
- paper/real 분기가 있는가?
- cache/retry wrapper 영향이 있는가?
- 테스트에서 patch가 많이 필요할 것 같은가?

## 수정 후 체크리스트

- 가장 가까운 테스트부터 실행했는가?
- async/hang 의심 시 `-n0`로 먼저 확인했는가?
- integration 영향이 있으면 관련 통합 테스트를 봤는가?
- background task 수정이면 cleanup/hang 여부를 봤는가?
- 주문/전략 수정이면 paper/real 분기를 다시 확인했는가?

## 실행 명령

기본 환경:

```powershell
cd C:\Users\Kyungsoo\Documents\Code\Investment
conda activate py310
```

앱 실행:

```powershell
python main.py
```

단위 테스트:

```powershell
pytest tests/unit_test -v
```

통합 테스트:

```powershell
pytest tests/integration_test -v
```

전체 테스트:

```powershell
pytest tests -v
```

특정 테스트 단독 실행:

```powershell
pytest tests/unit_test/test_xxx.py::test_yyy -v -n0
```

커버리지:

```powershell
.\1.test_with_coverage.bat
.\2.integration_test_with_coverage.bat
```

## 테스트 주의사항

- `pytest.ini`
  - `asyncio_mode = auto`
  - 기본 `-n auto`
  - timeout 30초

Codex는 아래를 기본 원칙으로 삼는다.

- async 테스트에서 `@patch` 데코레이터와 fixture 혼용을 피한다
- `BrokerAPIWrapper` 테스트에서는 retry/cache 래핑 영향을 먼저 의심한다
- mock 반환값은 가능하면 `ResCommonResponse`로 맞춘다
- background task를 시작했으면 종료 cleanup까지 테스트에 포함한다
- xdist 병렬 실행에서만 깨지면 외부 I/O 초기화 누락을 먼저 의심한다

### 테스트 실행 트러블슈팅 메모

- 이 저장소에서는 `pytest`를 PATH에 잡힌 기본 Python으로 바로 실행하지 않는다.
  - 실제로 기본 `pytest`가 Python 3.14 환경으로 실행되면서 프로젝트 의존성(`orjson`)이 없어 수집 단계에서 실패한 적이 있다.
  - 이 경우 `pytest.ini` 해석이나 plugin 조합도 프로젝트 의도와 달라질 수 있다.

- 기본 원칙:
  - 항상 프로젝트 Anaconda `py310` 인터프리터로 실행한다.
  - 권장 명령:

```powershell
C:\Users\Kyungsoo\anaconda3\envs\py310\python.exe -m pytest tests\unit_test\view\web\test_web_app_initializer.py -v -o addopts=
```

- `-n0` 또는 기본 `addopts` 때문에 인자 파싱이 꼬이거나 xdist 관련 오류가 보이면:
  - 먼저 `python -m pytest ... -o addopts=`로 `pytest.ini`의 기본 `addopts`를 비우고 순차 실행으로 확인한다.
  - 즉, "가장 작은 범위 + py310 + `-o addopts=`"를 1차 진단 루틴으로 사용한다.

- 권장 진단 순서:
  1. `C:\Users\Kyungsoo\anaconda3\envs\py310\python.exe -m pytest <대상파일> -v -o addopts=`
  2. 통과하면 필요한 경우에만 병렬 옵션이나 기본 설정으로 다시 확인
  3. 실패가 import 단계라면 의존성/인터프리터 문제를 먼저 의심
  4. 실패가 실행 단계라면 hang, mock, background cleanup 문제로 내려간다

## 위험 구역

아래 파일/영역은 수정 시 영향 범위가 크다.

- `view/web/web_app_initializer.py`
- `brokers/broker_api_wrapper.py`
- `scheduler/strategy_scheduler.py`
- `task/background/*`
- `brokers/korea_investment/korea_invest_websocket_api.py`

## 비교적 안전한 구역

- `view/web/routes/*`
- `services/stock_query_service.py`의 응답 가공 부분
- `services/virtual_trade_service.py`
- 전략 내부의 순수 조건 판별 로직

## Codex 메모

- 새 기능/버그 수정 전에는 `CODEBASE_SUMMARY.md`에서 비슷한 작업 예시 경로를 먼저 찾는다.
- 공용 지식은 `CODEBASE_SUMMARY.md`에 누적한다.
- Codex 전용 습관/전략 변화는 `CODEX_WORKFLOW.md`에 갱신한다.
