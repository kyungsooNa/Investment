# CLAUDE.md — Investment Project Context

> **작업 전 필수**: 모든 작업 시작 전 아래 파일들을 반드시 `Read` 도구로 읽고 규칙을 준수한다.
>
> 1. `Read("SKILL.md")` — Claude 작업 패턴 규칙 (Edit/Read/Write 방식)
> 2. `Read("CODEBASE_SUMMARY.md")` — 프로젝트 운영 지식, 수정 난이도 지도, 작업 경로
> 3. 존재할 경우: `Read("CLAUDE.local.md")`, `Read("SKILL.local.md")` — 개인 환경/도구 설정 (gitignore 대상)

## 작업 행동 원칙 (Karpathy Guidelines 적용)
LLM 코딩 시 자주 발생하는 실수를 줄이기 위한 원칙. 사소한 작업은 판단에 맡긴다.

### 1. 생각 먼저, 코드 나중 (Think Before Coding)
- 구현 전 **가정을 명시**한다. 불확실하면 묻는다. 혼란을 숨기지 않는다.
- 해석이 여러 가지면 모두 제시하고 사용자가 고르게 한다 — 조용히 선택하지 않는다.
- 더 단순한 접근이 있으면 말한다. 필요하면 사용자 요청에 반박한다.

### 2. 단순함 우선 (Simplicity First)
- 요청한 것 이상의 기능을 추가하지 않는다.
- 단발성 코드에 추상화를 만들지 않는다.
- 요청되지 않은 "유연성"·"설정성"을 넣지 않는다.
- 발생할 수 없는 시나리오에 에러 핸들링을 추가하지 않는다.
- "시니어 엔지니어가 보면 과하다고 할까?" — Yes 면 단순화한다.

### 3. 외과수술적 변경 (Surgical Changes)
- **요청에 직접 연결되지 않는 줄은 건드리지 않는다.**
- 인접 코드·주석·포맷팅을 "개선"하지 않는다.
- 깨지지 않은 것을 리팩토링하지 않는다.
- 본인이 다르게 작성했을 코드라도 기존 스타일에 맞춘다.
- 무관한 데드 코드를 발견하면 **언급만 하고 삭제하지 않는다.**
- 본인 변경으로 생긴 고아(import/변수/함수)만 정리한다 — 기존 데드 코드는 요청 없이 건드리지 않는다.

### 4. 목표 주도 실행 (Goal-Driven Execution)
작업을 검증 가능한 목표로 변환한다:
- "검증 추가" → "잘못된 입력에 대한 테스트 작성 후 통과시키기"
- "버그 수정" → "버그를 재현하는 테스트 작성 후 통과시키기"
- "X 리팩토링" → "리팩토링 전후 모든 테스트 통과 보장"

다단계 작업은 짧은 계획을 먼저 제시한다 — 각 단계마다 검증 기준을 붙인다.

## 기능 구현 방식: TDD 우선
- 기능 추가·버그 수정처럼 동작을 바꾸는 작업은 기본적으로 TDD(Test-Driven Development) 방식으로 진행한다.
- 구현 전에 기대 동작을 검증하는 실패 테스트를 먼저 작성하거나 기존 테스트를 먼저 수정한다.
- 새 테스트가 의도한 이유로 실패하는지 확인한 뒤 최소 구현으로 통과시킨다.
- 구현 후 관련 테스트를 실행하고, 필요하면 리팩토링하되 테스트가 계속 통과해야 한다.
- 단순 문서 수정, 설정 변경, 테스트 작성이 불가능하거나 비용 대비 의미가 낮은 작업은 예외로 할 수 있으며, 이 경우 이유를 작업 결과에 짧게 남긴다.

## 작업 후 검증: 통합 테스트 필수 실행
- **코드를 수정한 모든 작업이 끝나면 단위 테스트뿐 아니라 통합 테스트도 반드시 실행한다.**
  ```powershell
  pytest tests/unit_test -v           # 단위 테스트
  pytest tests/integration_test -v    # 통합 테스트 (필수)
  ```
- 통합 테스트가 실패하면 작업을 완료로 보고하지 않고 원인을 분석·수정한다.
- 단위 테스트만 통과해도 통합 테스트에서 회귀가 발견될 수 있으므로 생략 금지.
- **예외**: 순수 문서(`*.md`) 수정, 주석/공백만 수정, 테스트 코드만 추가한 경우는 통합 테스트를 건너뛸 수 있고, 그 이유를 결과 보고에 명시한다.
- 테스트 실행이 hang 되면 `TEST_HANG_TROUBLESHOOTING.md` 참조.

## 테스트 통과 후 게시 흐름
AGENTS.md의 Codex 게시 흐름과 동일한 규칙을 Claude에도 적용한다 (브랜치 접두사만 다름).

- 하네스/필수 테스트가 모두 통과했고 사용자가 별도 중단을 요청하지 않았으면 게시 흐름까지 이어간다.
- 현재 브랜치가 `main`/`master` 등 기본 브랜치이면 `claude/<작업요약>` 브랜치를 먼저 생성한다. 이미 작업 브랜치가 있으면 새 브랜치를 만들지 않는다.
- 커밋 전 `git status -sb`와 diff를 확인하고, 작업 범위 파일만 명시적으로 stage한다.
- 커밋 후 `git push -u origin <branch>`로 푸시하고 draft PR을 생성한다. 사용자가 ready PR을 명시한 경우에만 draft가 아닌 PR을 만든다.
- 테스트 실패, 혼합 변경, 인증/원격 문제, 사용자의 게시 보류 지시가 있으면 커밋/푸시/PR을 진행하지 않고 상태를 보고한다.

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

## 트러블슈팅

### 테스트 Hang 발생 시
`pytest tests/` 실행 중 TC 가 무한 대기 (hang) 하거나 xdist worker 가 freeze 되는 경우 → **`Read("TEST_HANG_TROUBLESHOOTING.md")`** 로 진단 체크리스트 및 5가지 원인 패턴 참고.

주요 트리거 키워드 (이 중 하나라도 해당하면 위 가이드 참고):
- `pytest` 실행 후 응답 없음 / Ctrl+C 로만 중단됨
- `@patch` + `async def` + fixture 혼용 의심
- `BrokerAPIWrapper` mock 무한 재시도 의심
- `asyncio.sleep` long-wait (12시간 등) 으로 인한 백그라운드 Task 미정리
- xdist 병렬 실행 시에만 `HTTP 429` ERROR 발생
