# AGENTS.md — Investment Project Context

> **Codex 작업 규칙**: Codex는 이 문서만 우선 확인한다. [SKILL.md](SKILL.md)는 Claude 전용 도구 규칙이므로, 사용자가 명시적으로 요청하거나 Claude 규칙 자체를 수정할 때만 읽는다.

## Codex 작업 규칙

### 파일 탐색·읽기
- 파일 탐색은 `rg`, `rg --files`를 우선 사용한다.
- 이 Windows/Codex 환경에서 `rg.exe` 실행이 `Access is denied`로 실패할 수 있다. 이 경우 같은 탐색을 PowerShell 기본 도구(`Get-ChildItem`, `Select-String`)로 즉시 우회한다.
- 전체 파일 검색 시 `__pycache__`, `.pytest_cache`, `htmlcov`, `logs`, `.git`, 빌드/캐시 산출물까지 훑으면 타임아웃과 바이너리 출력이 발생할 수 있다. 우선 `app`, `brokers`, `common`, `config`, `core`, `interfaces`, `managers`, `market_data`, `repositories`, `scheduler`, `services`, `strategies`, `task`, `tests`, `utils`, `view` 등 소스/테스트 범위로 줄이고, 필요하면 제외 조건을 명시한다.
- PowerShell에서 파일을 읽을 때는 기본 인코딩에 맡기지 않고 `Get-Content -Raw -Encoding UTF8 <path>`를 사용한다.
- 한글 출력이 깨질 가능성이 있으면 작업 초기에 아래 설정을 적용한다.
  ```powershell
  chcp 65001
  $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
  ```
- 대형 파일은 전체를 읽기보다 변경 대상 함수·블록 중심으로 필요한 범위만 확인한다.
- `mcp__plugin_context-mode_context-mode__ctx_execute` 도구가 제공되는 환경에서는 탐색·분석 목적의 shell/python 실행에 해당 도구를 우선 사용한다. 해당 도구가 없는 Codex 환경에서는 일반 shell 도구를 사용한다.

### 파일 수정
- 파일 수정은 기본적으로 `apply_patch`를 사용한다.
- PowerShell의 `Set-Content`, `Out-File`은 Windows PowerShell 버전에 따라 BOM/인코딩 차이가 생길 수 있으므로 한글 포함 파일 수정에는 가급적 사용하지 않는다.
- 클래스/모듈을 분리할 때는 기존 파일을 복사한 뒤 불필요한 부분을 제거하는 방식을 우선 고려한다. 새 파일을 통째로 재작성하기보다 기존 코드의 import, 타입, 한글 메시지를 보존하기 위함이다.
- 복잡한 리팩토링이나 다수 파일 수정은 먼저 변경 범위와 순서를 짧게 정리한 뒤 진행한다. 별도 `Changes.md` 작성은 사용자가 요청했거나 작업 규모상 실제로 도움이 될 때만 한다.

### 기능 구현 방식: TDD 우선
- 기능 추가·버그 수정처럼 동작을 바꾸는 작업은 기본적으로 TDD(Test-Driven Development) 방식으로 진행한다.
- 구현 전에 기대 동작을 검증하는 실패 테스트를 먼저 작성하거나 기존 테스트를 먼저 수정한다.
- 새 테스트가 의도한 이유로 실패하는지 확인한 뒤 최소 구현으로 통과시킨다.
- 구현 후 관련 테스트를 실행하고, 필요하면 리팩토링하되 테스트가 계속 통과해야 한다.
- 단순 문서 수정, 설정 변경, 테스트 작성이 불가능하거나 비용 대비 의미가 낮은 작업은 예외로 할 수 있으며, 이 경우 이유를 작업 결과에 짧게 남긴다.

### 작업 행동 원칙 (Karpathy Guidelines 적용)
LLM 코딩 시 자주 발생하는 실수를 줄이기 위한 원칙. 사소한 작업은 판단에 맡긴다.

#### 1. 생각 먼저, 코드 나중 (Think Before Coding)
- 구현 전 **가정을 명시**한다. 불확실하면 묻는다. 혼란을 숨기지 않는다.
- 해석이 여러 가지면 모두 제시하고 사용자가 고르게 한다 — 조용히 선택하지 않는다.
- 더 단순한 접근이 있으면 말한다. 필요하면 사용자 요청에 반박한다.

#### 2. 단순함 우선 (Simplicity First)
- 요청한 것 이상의 기능을 추가하지 않는다.
- 단발성 코드에 추상화를 만들지 않는다.
- 요청되지 않은 "유연성"·"설정성"을 넣지 않는다.
- 발생할 수 없는 시나리오에 에러 핸들링을 추가하지 않는다.
- "시니어 엔지니어가 보면 과하다고 할까?" — Yes 면 단순화한다.

#### 3. 외과수술적 변경 (Surgical Changes)
- **요청에 직접 연결되지 않는 줄은 건드리지 않는다.**
- 인접 코드·주석·포맷팅을 "개선"하지 않는다.
- 깨지지 않은 것을 리팩토링하지 않는다.
- 본인이 다르게 작성했을 코드라도 기존 스타일에 맞춘다.
- 무관한 데드 코드를 발견하면 **언급만 하고 삭제하지 않는다.**
- 본인 변경으로 생긴 고아(import/변수/함수)만 정리한다 — 기존 데드 코드는 요청 없이 건드리지 않는다.

#### 4. 목표 주도 실행 (Goal-Driven Execution)
작업을 검증 가능한 목표로 변환한다:
- "검증 추가" → "잘못된 입력에 대한 테스트 작성 후 통과시키기"
- "버그 수정" → "버그를 재현하는 테스트 작성 후 통과시키기"
- "X 리팩토링" → "리팩토링 전후 모든 테스트 통과 보장"

다단계 작업은 짧은 계획을 먼저 제시한다 — 각 단계마다 검증 기준을 붙인다.

### 작업 후 검증: 통합 테스트 필수 실행
- **코드를 수정한 모든 작업이 끝나면 단위 테스트뿐 아니라 통합 테스트도 반드시 실행한다.**
  ```powershell
  pytest tests/unit_test -v           # 단위 테스트
  pytest tests/integration_test -v    # 통합 테스트 (필수)
  ```
- 통합 테스트가 실패하면 작업을 완료로 보고하지 않고 원인을 분석·수정한다.
- 단위 테스트만 통과해도 통합 테스트에서 회귀가 발견될 수 있으므로 생략 금지.
- **예외**: 순수 문서(`*.md`) 수정, 주석/공백만 수정, 테스트 코드만 추가한 경우는 통합 테스트를 건너뛸 수 있고, 그 이유를 결과 보고에 명시한다.
- 테스트 실행이 hang 되면 본 문서 하단 **테스트 Hang 트러블슈팅 가이드** 참조.

### 테스트 통과 후 게시 흐름
- 하네스/필수 테스트가 모두 통과했고 사용자가 별도 중단을 요청하지 않았으면 게시 흐름까지 이어간다.
- 현재 브랜치가 `main`/`master` 등 기본 브랜치이면 `codex/<작업요약>` 브랜치를 먼저 생성한다. 이미 작업 브랜치가 있으면 새 브랜치를 만들지 않는다.
- 커밋 전 `git status -sb`와 diff를 확인하고, 작업 범위 파일만 명시적으로 stage한다.
- 커밋 후 `git push -u origin <branch>`로 푸시하고 draft PR을 생성한다. 사용자가 ready PR을 명시한 경우에만 draft가 아닌 PR을 만든다.
- 테스트 실패, 혼합 변경, 인증/원격 문제, 사용자의 게시 보류 지시가 있으면 커밋/푸시/PR을 진행하지 않고 상태를 보고한다.

### Claude 전용 규칙 분리
- Claude의 `Read`, `Edit`, `Write` 도구 사용 규칙은 [SKILL.md](SKILL.md)에 둔다.
- Codex는 `Read/Edit/Write` 기반 규칙을 그대로 적용하지 않는다. Codex 도구 체계에 맞게 `rg`, UTF-8 명시 읽기, `apply_patch`를 우선한다.

## 프로젝트 개요
한국투자증권 Open API 기반 **주식 자동매매 시스템** (Python 3.10+, Anaconda `py310` 환경).
CLI(asyncio 터미널) + Web(FastAPI + Jinja2) 두 모드 지원. 모의/실전 투자 환경 전환 가능.

## 실행
```powershell
conda activate py310
python main.py          # 대화형 (1=CLI, 2=Web)
python main.py --cli    # CLI 직접 실행
python main.py --web    # Web 직접 실행 (http://localhost:8000)
```

## 테스트
```powershell
pytest tests/unit_test -v           # 단위 테스트
pytest tests/integration_test -v    # 통합 테스트
.\1.test_with_coverage.bat          # 단위 테스트 + 커버리지
.\2.integration_test_with_coverage.bat  # 통합 테스트 + 커버리지
```
- pytest 설정: `asyncio_mode=auto`, `pytest-xdist -n auto` 병렬 실행

## Pytest 실행 방법 예시
cd /c/Users/Kyungsoo/Documents/Code/Investment && /c/Users/Kyungsoo/anaconda3/envs/py310/python.exe -m pytest tests/unit_test/test_korea_invest_websocket_api.py::test_disconnect_with_receive_task_exception -v 2>&1 | tail -50

## 디렉토리 구조
```
Investment/
├── main.py                 # 진입점 (CLI/Web 모드 선택)
├── app/                    # TradingApp, UserActionExecutor (메뉴 커맨드 디스패치)
├── brokers/korea_investment/  # 한국투자증권 API 클라이언트 계층
│   ├── korea_invest_env.py          # 환경(실전/모의), API키, 토큰 관리
│   ├── korea_invest_client.py       # 최상위 클라이언트 (Quotations+Account+Trading 통합)
│   ├── korea_invest_api_base.py     # HTTP 엔진 (httpx, 재시도 로직)
│   ├── korea_invest_quotations_api.py  # 시세 조회 API
│   ├── korea_invest_account_api.py     # 계좌 조회 API
│   ├── korea_invest_trading_api.py     # 매수/매도 주문 API
│   ├── korea_invest_websocket_api.py   # 실시간 WebSocket (체결가, 호가, 프로그램매매)
│   ├── korea_invest_*_provider.py      # Header, URL, TrID, Params 프로바이더
│   └── korea_invest_token_provider.py   # 토큰 발급/갱신/저장
├── common/types.py         # 공통 데이터 모델 (ResCommonResponse, ErrorCode, TradeSignal 등)
├── config/                 # YAML 설정 파일들
│   ├── config.yaml(.example)   # API키, 계좌번호, URL (gitignore 대상)
│   ├── tr_ids_config.yaml      # TR ID 매핑
│   ├── kis_config.yaml         # 엔드포인트 경로 + 쿼리 파라미터
│   ├── cache_config.yaml       # 캐시 설정 (TTL, 활성 메서드 목록)
│   └── DynamicConfig.py        # 코드 내 상수 (OHLCV 범위 등)
├── core/                   # 인프라 (Logger, MarketClock, Cache 서브시스템)
├── data/                   # stock_code_list.csv (KOSPI+KOSDAQ 종목코드)
├── interfaces/strategy.py  # Strategy 추상 인터페이스
├── managers/               # VirtualTradeManager (CSV 기반 모의매매 저널)
├── market_data/            # StockCodeRepository (종목코드 ↔ 이름 매핑)
├── services/               # 비즈니스 로직
│   ├── trading_service.py          # 핵심 도메인 서비스
│   ├── stock_query_service.py      # 시세 조회 서비스 (분봉 페이지네이션 포함)
│   └── order_execution_service.py  # 주문 실행 서비스
├── strategies/             # 매매 전략 + 백테스트
│   ├── momentum_strategy.py        # 모멘텀 전략 (변동률+후속상승)
│   ├── GapUpPullback_strategy.py   # 갭상승 눌림목 전략
│   ├── volume_breakout_strategy.py # 거래량 돌파 백테스트 (독립형)
│   ├── strategy_executor.py        # Strategy 래퍼
│   └── backtest_data_provider.py   # 백테스트 데이터 제공
├── tests/
│   ├── unit_test/          # 34개 단위 테스트
│   └── integration_test/   # 통합 테스트 (HTTP 모킹)
├── utils/                  # 종목코드 CSV 업데이터 (pykrx)
└── view/
    ├── cli/cli_view.py     # CLI 뷰 (터미널 I/O)
    └── web/                # FastAPI 웹 뷰
        ├── web_main.py         # FastAPI 앱, 라우트, lifespan
        ├── web_api.py          # /api/* 엔드포인트
        ├── web_app_initializer.py  # WebAppContext (서비스 초기화)
        └── templates/          # Jinja2 HTML
```

## 아키텍처 계층
```
View (CLIView / FastAPI)
  → UserActionExecutor / web_api.py
    → StockQueryService / OrderExecutionService
      → TradingService (도메인 로직)
        → BrokerAPIWrapper (브로커 추상화 + 캐시 프록시)
          → KoreaInvestApiClient
            → Quotations / Account / Trading / WebSocket API
              → KoreaInvestApiBase (httpx HTTP 엔진)
```

## 핵심 설계 패턴
- **DI**: 모든 주요 클래스가 생성자를 통해 의존성 주입 (logger, market_clock, env 등)
- **Command 패턴**: `UserActionExecutor.COMMANDS` dict → 메뉴 번호 → 핸들러 메서드 디스패치
- **Strategy 패턴**: `Strategy` 추상 클래스 → `MomentumStrategy`, `GapUpPullbackStrategy` 구현
- **Proxy/Decorator**: `ClientWithCache`가 API 클라이언트를 래핑 (장 마감 후 캐시 활성)
- **실전/모의 투명 전환**: URL, TR ID가 `is_paper_trading` 플래그에 따라 자동 전환

## 주요 데이터 모델 (common/types.py)
- `ResCommonResponse[T]` — 모든 API 응답 래퍼 (`rt_cd="0"` 성공)
- `ErrorCode` — SUCCESS, API_ERROR, NETWORK_ERROR, MARKET_CLOSED 등
- `TradeSignal` — 매매 신호 (code, action, price, qty, reason, strategy_name)
- `ResStockFullInfoApiOutput` — 현재가 전체 정보 (50+ 필드)
- `with_from_dict` 데코레이터 — dataclass에 `from_dict()` 자동 추가

## 캐시 동작
- 장중: 캐시 우회 (실시간 데이터 필요)
- 장 마감 후 ~ 다음 장 시작 전: 캐시 유효
- 성공 응답(`rt_cd=="0"`)만 캐시
- `cache_config.yaml`에서 메서드별 활성화/비활성화 제어

## 수동매매 성과요약
- **VirtualTradeManager** (`managers/virtual_trade_manager.py`): CSV(`data/trade_journal.csv`) 기반 거래 기록/통계
  - `log_buy(strategy, code, price)` / `log_sell(code, price)` — 매수/매도 기록 (중복 매수 방지)
  - `get_summary()` → `{total_trades, win_rate, avg_return}` (SOLD 건만 집계)
  - `get_all_trades()` / `get_holds()` / `get_holds_by_strategy()` — 조회
- **웹 API**: `GET /api/virtual/summary`, `GET /api/virtual/history`
- **웹 주문 연동**: `POST /api/order` 성공 시 `"수동매매"` 전략명으로 자동 기록 (`web_api.py`)
- **웹 UI**: 전략별 탭 필터링 + 성과 요약 박스 (`app.js` — `loadVirtualHistory()`, `filterVirtualStrategy()`)
- **CLI**: 성과요약 전용 메뉴 없음 (웹 UI에서만 조회 가능)

## 실전 전용 기능 (모의투자에서 사용 불가)
시가총액 랭킹, 상승/하락률 랭킹, 거래량 랭킹, 일별 분봉, 상한가 조회

## 주요 의존성
httpx, websockets, pycryptodome, fastapi, uvicorn, jinja2, pandas, PyYAML, pytz, pykrx

## 컨벤션
- 비동기 우선: 모든 API 호출과 서비스 메서드는 `async def`
- 한글 메뉴/메시지, 영문 코드/변수명
- 테스트 파일명: `test_*.py` (단위), `test_it_*.py` (통합)
- config.yaml, token_*.json은 git에 포함하지 않음

---

## 테스트 Hang 트러블슈팅 가이드

### 증상
`pytest tests/` 전체 실행 시 일부 TC가 무한 대기 (hang) → xdist worker가 멈춰 전체 스위트가 freeze.

### 원인 패턴 1: `@patch` 데코레이터 + `async def` + pytest fixture 혼용 (pytest-asyncio 1.0.0)

**문제**: `asyncio_mode=auto` 환경에서 `@patch` 데코레이터를 `async def` 테스트 함수에 사용하면서 동시에 pytest fixture를 인자로 받으면 데드락 발생.

```python
# ❌ 데드락 발생 패턴
@patch("module.SomeClass")
async def test_foo(mock_class, my_fixture):  # fixture + @patch 혼용 → hang
    ...
```

**해결**: `@patch` 데코레이터 대신 `with patch()` 컨텍스트 매니저 사용.

```python
# ✅ 올바른 패턴
async def test_foo(my_fixture):
    with patch("module.SomeClass") as mock_class:
        ...
```

### 원인 패턴 2: `ClientWithRetryQueue`를 통한 mock 호출 시 무한 재시도

**문제**: `BrokerAPIWrapper` 는 내부적으로 `ClientWithCache → ClientWithRetryQueue → 실제 클라이언트` 체인으로 구성됨.
테스트에서 mock이 `ResCommonResponse` 가 아닌 **plain dict / None** 을 반환하면 `classify()` 가 `RETRY` 판정 → `MAX_RETRIES(5)` 회 재시도 → `asyncio.sleep` 지연 누적 → hang.

```python
# ❌ plain dict 반환 → classify() → RETRY → 5회 재시도 → hang
mock_client.some_method.return_value = {"key": "value"}
wrapper = BrokerAPIWrapper(...)  # ClientWithRetryQueue 래핑됨
await wrapper.some_method(...)   # hang!
```

**해결 방법 A (권장)**: `BrokerAPIWrapper` 의 래핑 레이어를 bypass 하여 mock client를 직접 주입.

```python
# ✅ cache_wrap_client, retry_queue_wrap_client 를 identity 함수로 패치
async def test_delegation(mock_env, mock_logger):
    with patch(f"{wrapper_module.__name__}.KoreaInvestApiClient") as mock_client_class, \
         patch(f"{wrapper_module.__name__}.cache_wrap_client", side_effect=lambda c, *a, **kw: c), \
         patch(f"{wrapper_module.__name__}.retry_queue_wrap_client", side_effect=lambda c, *a, **kw: c):
        wrapper = BrokerAPIWrapper("korea_investment", env=mock_env, logger=mock_logger)
        # wrapper._client 가 mock_client_class.return_value 로 직접 할당됨
```

**해결 방법 B**: 테스트 픽스처가 이미 `BrokerAPIWrapper` 를 생성한 경우 `_client` 를 직접 교체.

```python
# ✅ wrapper 생성 후 _client 를 mock 으로 직접 대체
wrapper = BrokerAPIWrapper(...)
wrapper._client = mock_client_instance  # 래핑 레이어 우회
```

**해결 방법 C**: mock이 `ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, ...)` 를 반환하도록 변경 → `classify()` → `DONE` → 즉시 완료.

### 원인 패턴 3: conftest `fast_sleep` 가 `ApiRequestQueue` 의 sleep 을 커버 못 할 경우

`conftest.py` 의 `fast_sleep` fixture 는 `asyncio.sleep` 을 전역 patch 하지만,
**integration test** conftest 에서는 `core.retry_queue.api_request_queue.asyncio.sleep` 을 별도 patch 해야 할 수 있음.

```python
# integration test conftest 또는 개별 TC 내
@pytest.fixture
def mock_sleep():
    with patch("core.retry_queue.api_request_queue.asyncio.sleep", new_callable=AsyncMock) as m:
        yield m
```

### 원인 패턴 4: 픽스처에서 외부 네트워크 호출 누락 패치 → xdist 병렬 실행 시 429

**증상**: `pytest tests/unit_test -n auto` 병렬 실행 시에만 `ERROR ... urllib.error.HTTPError: HTTP Error 429: Too Many Requests` 발생. 단독(`-n0`) 또는 파일 단위 실행에서는 통과.

**원인**: `WebAppContext.__init__` 에서 `StockCodeRepository` 를 직접 인스턴스화하는데, 픽스처가 이를 패치하지 않으면 DB 파일 부재 시 실제 네트워크 요청 발생.

```
WebAppContext.__init__
 └─ StockCodeRepository.__init__
     └─ (DB 파일 없으면) save_stock_code_list(force_update=True)  ← stock_sync_service.py
         ├─ FinanceDataReader (내부적으로 urllib.request 사용)
         └─ pykrx (requests 사용)
```

xdist 가 worker 별 임시 환경을 만들 때 DB 파일이 없는 경우 여러 worker 가 동시에 외부 서버 호출 → 레이트 리밋(429) 발생.

**해결**: `WebAppContext` 를 생성하는 픽스처에서 `StockCodeRepository` 를 반드시 패치.

```python
# ❌ StockCodeRepository 미패치 → xdist 병렬 시 429
with patch('view.web.web_app_initializer.StockRepository') as MockSR, \
     patch('view.web.web_app_initializer.Logger') as MockLogger:
    ctx = WebAppContext(app_context)

# ✅ StockCodeRepository 추가 패치
with patch('view.web.web_app_initializer.StockRepository') as MockSR, \
     patch('view.web.web_app_initializer.StockCodeRepository') as MockSCR, \
     patch('view.web.web_app_initializer.Logger') as MockLogger:
    ctx = WebAppContext(app_context)
```

**진단 포인트**: `ERROR` (FAILED 아님) 이면서 `-n0` 단독 실행에서 통과 → 픽스처 setup 중 실제 네트워크/파일 I/O 호출 누락 패치 의심.

---

### 원인 패턴 5: `start()`가 생성한 백그라운드 asyncio.Task 미취소 → `asyncio.sleep(long)` hang

**문제**: `task.start()` 내부에서 `asyncio.create_task(start_after_market_scheduler())`를 생성한다.
이 Task는 `run_after_market_loop()`의 `while True:` 루프를 실행한다.
테스트에서 `mcs=None`, `market_clock=None`으로 생성하면 루프 마지막의 `_smart_sleep()` → `asyncio.sleep(12 * 3600)` (12시간 대기)에 진입한다.
테스트 함수가 정상 종료해도 이 백그라운드 Task가 살아 있어 pytest-asyncio가 이벤트 루프를 닫지 못하고 hang.

```python
# ❌ 백그라운드 Task 미취소 → 12시간 sleep으로 hang
async def test_lifecycle():
    task = MinerviniUpdateTask(minervini_service=DummyMinerviniSvc({}))
    await task.start()           # asyncio.create_task(무한루프) 생성
    await task.suspend()
    await task.resume()
    # 테스트 종료 — 백그라운드 Task는 여전히 sleep(43200) 중 → hang
```

**해결**: 테스트 종료 전 반드시 `await task.stop()`으로 백그라운드 Task를 취소한다.
`try/finally`를 사용해 assertion 실패 시에도 정리되도록 보장.

```python
# ✅ finally 블록에서 stop() 호출
async def test_lifecycle():
    task = MinerviniUpdateTask(minervini_service=DummyMinerviniSvc({}))
    try:
        await task.start()
        assert task.state == TaskState.RUNNING
        await task.suspend()
        assert task.state == TaskState.SUSPENDED
        await task.resume()
        assert task.state == TaskState.RUNNING
    finally:
        await task.stop()  # 백그라운드 스케줄러 Task 취소
```

**적용 범위**: `start()`가 `asyncio.create_task()`로 백그라운드 루프를 생성하는 모든 태스크 클래스
(`AfterMarketTask` 서브클래스 전체 — `MinerviniUpdateTask`, `DailyPriceCollectorTask`, `RankingTask` 등).

---

### 진단 체크리스트

TC가 hang 할 때 아래 순서로 확인:

1. **`-n0` 으로 단독 실행** → 여전히 hang 하면 xdist 문제 아님, TC 자체 문제
   ```bash
   pytest tests/unit_test/test_foo.py::test_bar -v -n0
   ```
2. **`@patch` 데코레이터 + `async def` + fixture 혼용** 여부 확인 → `with patch()` 로 교체
3. **mock 반환값이 `ResCommonResponse` 인지** 확인 → plain dict/None 이면 RETRY 루프 진입 가능
4. **`BrokerAPIWrapper` 를 직접 생성하는 TC** 인지 확인 → `cache_wrap_client` / `retry_queue_wrap_client` bypass 패치 적용
5. **`asyncio.sleep` 이 제대로 mock** 되는지 확인 → `fast_sleep` autouse fixture 가 동작 범위 내인지 점검
6. **ERROR(FAILED 아님) + `-n0` 단독 통과** → 픽스처 setup 중 외부 I/O 누락 패치 의심 → `WebAppContext` 생성 픽스처에서 `StockCodeRepository` 등 네트워크 호출 가능 클래스 패치 확인
7. **`task.start()`를 호출하는 TC** → `asyncio.create_task(무한루프)` 생성 여부 확인 → 테스트 종료 전 `await task.stop()` 호출 (`try/finally` 블록 권장)
