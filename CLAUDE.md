# CLAUDE.md — Investment Project Context

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
│   └── korea_invest_token_manager.py   # 토큰 발급/갱신/저장
├── common/types.py         # 공통 데이터 모델 (ResCommonResponse, ErrorCode, TradeSignal 등)
├── config/                 # YAML 설정 파일들
│   ├── config.yaml(.example)   # API키, 계좌번호, URL (gitignore 대상)
│   ├── tr_ids_config.yaml      # TR ID 매핑
│   ├── kis_config.yaml         # 엔드포인트 경로 + 쿼리 파라미터
│   ├── cache_config.yaml       # 캐시 설정 (TTL, 활성 메서드 목록)
│   └── DynamicConfig.py        # 코드 내 상수 (OHLCV 범위 등)
├── core/                   # 인프라 (Logger, TimeManager, Cache 서브시스템)
├── data/                   # stock_code_list.csv (KOSPI+KOSDAQ 종목코드)
├── interfaces/strategy.py  # Strategy 추상 인터페이스
├── managers/               # VirtualTradeManager (CSV 기반 모의매매 저널)
├── market_data/            # StockCodeMapper (종목코드 ↔ 이름 매핑)
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
- **DI**: 모든 주요 클래스가 생성자를 통해 의존성 주입 (logger, time_manager, env 등)
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
