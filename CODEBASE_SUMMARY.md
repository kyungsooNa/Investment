# CODEBASE_SUMMARY

최종 업데이트: 2026-04-22

이 문서는 이후 작업 전에 프로젝트 전체를 다시 훑지 않도록 만든 작업용 요약이다.
작업 시작 순서 권장:

1. `AGENTS.md`
2. `CODEBASE_SUMMARY.md`
3. `CODEX_WORKFLOW.md` (Codex 작업 시)

## 문서 역할 분리

- `AGENTS.md`
  - 프로젝트 공통 규칙, 구조 개요, 테스트 hang 주의사항
- `SKILL.md`
  - Claude 전용 작업 규칙
- `CODEBASE_SUMMARY.md`
  - 모델 공통 프로젝트 요약, 조사 경로, 검증 루틴
- `CODEX_WORKFLOW.md`
  - Codex 전용 작업 순서와 수정 전략

## 프로젝트 정체

- 한국투자증권 Open API 기반의 한국 주식 자동매매/시장분석 플랫폼
- Python 3.10 기반
- 실행 모드:
  - Web: FastAPI + Jinja2
  - CLI: 문서상 지원, 현재 확인된 `main.py`는 웹 실행 중심
- 실시간 조회, 주문 실행, 전략 스캐닝, 스케줄링, 장마감 후 배치, 모의매매 성과 분석까지 포함

## 현재 확인한 핵심 진입점

- `main.py`
  - 크래시 덤프 로깅 활성화
  - 설정 로드 후 웹 서버 실행
  - `view.web.web_main.app`를 `uvicorn`으로 구동
- `view/web/web_main.py`
  - FastAPI 앱 생성
  - `lifespan`에서 `WebAppContext` 초기화
  - `/api/*` 라우터 연결
  - 인증 쿠키 검사, 요청 추적, foreground 우선순위 미들웨어 포함
- `view/web/web_app_initializer.py`
  - 실제 서비스 조립부
  - 브로커, 조회/주문/스트리밍/전략/스케줄러/배치/알림 초기화의 중심

## 아키텍처 한 줄 요약

웹/라우터 -> 서비스 계층 -> BrokerAPIWrapper -> KoreaInvestApiClient -> 한국투자증권 Open API

## 핵심 계층과 역할

### 1. 웹 계층

- `view/web/web_main.py`
  - FastAPI 앱, 템플릿, 페이지 라우팅, 미들웨어
- `view/web/web_api.py`
  - 현재는 `view.web.routes`를 재노출하는 호환 레이어
- 역할
  - 페이지 렌더링
  - `/api/*` 요청 진입점
  - 인증 쿠키 체크
  - 요청 hang 진단 및 우선순위 제어

### 2. 서비스 계층

- `services/stock_query_service.py`
  - 현재가, 체결, 상승/하락 랭킹, 거래량, 시총, 재무비율, 계좌 조회 등
  - 응답을 웹에 맞게 가공
- `services/order_execution_service.py`
  - 매수/매도 주문
  - 장 운영 시간 검사
  - 재시도 로직
  - 주문 후 구독/알림/가상거래 기록 연계
- `services/virtual_trade_service.py`
  - 모의매매 성과 요약
  - 전략별 수익률 변화 계산
  - 로컬 거래 기록과 실제 잔고 reconcile 지원
- `services/market_data_service.py`
  - 브로커 기반 조회 허브
- `services/indicator_service.py`
  - 보조지표 계산
- `services/notification_service.py`
  - 내부/외부 알림 허브

### 3. 브로커/API 계층

- `brokers/broker_api_wrapper.py`
  - 서비스 레이어가 쓰는 통합 브로커 인터페이스
  - 종목명/코드 조회
  - 시세/차트/재무/랭킹/잔고/주문 API 위임
  - Retry Queue + Cache 래핑
  - Circuit Breaker 보유
- `brokers/korea_investment/*`
  - 한국투자증권 API 세부 구현
  - quotations/account/trading/websocket 분리

## 환경 분리

- `KoreaInvestApiEnv`가 모의/실전 전환 핵심
- `is_paper_trading` 플래그로 URL, TR ID, 토큰을 자동 분기
- 웹 초기화 기본값은 모의투자 모드
- 모의 모드에서도 일부 조회는 실전 인증 토큰을 함께 준비
- 일부 기능은 실전 전용

## 전략 시스템

### 대표 전략

- `strategies/momentum_strategy.py`
  - 급등 후 일정 시간 뒤 추가 상승 여부를 보는 모멘텀 전략
- `strategies/volume_breakout_live_strategy.py`
- `strategies/traditional_volume_breakout_strategy.py`
- `strategies/program_buy_follow_strategy.py`
- `strategies/oneil_squeeze_breakout_strategy.py`
- `strategies/oneil_pocket_pivot_strategy.py`
- `strategies/high_tight_flag_strategy.py`
- `strategies/first_pullback_strategy.py`

### 전략 실행 구조

- `strategies/strategy_executor.py`
  - 종목 리스트를 받아 전략 실행
  - 선택적으로 Minervini Stage Guard 적용
  - 허용 스테이지만 통과시킨 뒤 `strategy.run()` 호출

### 전략 선별/보조 서비스

- `services/minervini_stage_service.py`
- `services/rs_rating_service.py`
- `services/oneil_universe_service.py`
- `services/newhigh_service.py`

## 운영 자동화

`view/web/web_app_initializer.py` 기준으로 아래 자동화가 붙어 있다.

- `StrategyScheduler`
- `BackgroundScheduler`
- `ForegroundScheduler`
- `RankingTask`
- `MinerviniUpdateTask`
- `DailyPriceCollectorTask`
- `OhlcvUpdateTask`
- `PremiumWatchlistGeneratorTask`
- `CacheWarmupTask`
- `LogCleanupTask`
- `NewHighTask`
- `StrategyLogReportTask`
- `NotificationQueueTask`
- `WebSocketWatchdogTask`

즉 이 프로젝트는 단순 전략 연구 코드가 아니라 장중/장마감 운영까지 고려한 시스템이다.

## 실시간/스트리밍 관련

- 실시간 시세 및 체결 스트림 지원
- 프로그램 매매 실시간 데이터 서비스 존재
- 주문 성공 시 가격 구독 추가/해제 로직 존재
- 스트리밍 전용 이벤트 로그 사용

## 웹에서 가능한 기능 범위

현재까지 확인한 범위:

- 종목 조회
- 현재가/체결/차트/지표 확인
- 계좌 잔고 조회
- 매수/매도 주문
- 거래량/상승률/시총 랭킹 조회
- 프로그램 매매 관련 기능
- 가상매매 이력/성과 조회
- 스케줄러 제어
- 로그인 보호 페이지

## 데이터/저장소

- `VirtualTradeRepository`
  - 가상매매 기록 저장
- `StockCodeRepository`
  - 종목코드 <-> 종목명 매핑
- `StockRepository`
  - 종목 데이터/차트성 데이터 저장소 역할
- `RSRatingRepository`
- `FavoriteRepository`

## 관찰된 운영상 특징

- 요청 hang 진단용 `/debug/requests` 서버 별도 실행
- foreground 우선순위 미들웨어로 사용자 요청을 백그라운드 작업보다 우선 처리
- retry queue와 cache를 함께 사용
- 텔레그램 notifier/reporter 연동 가능
- 성능 프로파일링 옵션 존재

## 실제 작업 시 조사 순서

작업 요청을 받았을 때는 아래 순서로 확인하면 재분석 비용이 낮다.

### A. 웹 기능 수정

1. `view/web/web_main.py`
2. `view/web/web_api.py`
3. `view/web/routes/*`
4. 관련 서비스 파일
5. 필요 시 `view/web/web_app_initializer.py`

### B. 조회/주문 로직 수정

1. `services/stock_query_service.py` 또는 `services/order_execution_service.py`
2. `services/market_data_service.py`
3. `brokers/broker_api_wrapper.py`
4. `brokers/korea_investment/korea_invest_client.py`
5. 필요한 도메인 API 구현 (`*_quotations_api.py`, `*_trading_api.py`, `*_account_api.py`)

### C. 전략/스케줄러 수정

1. 대상 전략 파일 `strategies/*.py`
2. `strategies/strategy_executor.py`
3. `scheduler/strategy_scheduler.py`
4. 관련 배치 태스크 `task/background/*`
5. 초기화/주입 경로 `view/web/web_app_initializer.py`

### D. 실시간/구독 이슈

1. `services/price_subscription_service.py`
2. `services/price_stream_service.py`
3. `services/streaming_service.py`
4. `brokers/korea_investment/korea_invest_websocket_api.py`
5. watchdog/task 계층

## 리팩토링 우선순위가 높아 보이는 지점

### 1. `WebAppContext` 비대화

- `view/web/web_app_initializer.py`가 너무 많은 책임을 가진다.
- 환경 로드, 브로커 생성, 서비스 조립, 전략/태스크 등록, 알림 설정, 스케줄러 초기화가 한곳에 몰려 있다.
- 향후 `factory` 또는 `bootstrap modules`로 분리 여지 큼

### 2. 서비스 초기화 순서 의존성

- 여러 서비스가 후주입 방식으로 서로 연결된다.
- 예:
  - `indicator_service.stock_query_service = ...`
  - `favorite_service.stock_query_service = ...`
  - `favorite_service.rs_rating_service = ...`
- 이런 패턴은 테스트와 리팩토링 시 누락 위험이 있다.

### 3. 웹/운영/전략 관심사 결합

- 웹 앱 초기화 시 전략 스케줄러와 after-market task까지 함께 붙는다.
- 웹 서버와 배치 실행 경계를 더 명확히 나누면 유지보수성이 좋아질 가능성 높음

### 4. 브로커 래핑 체인의 복잡성

- `BrokerAPIWrapper -> retry queue wrapper -> cache wrapper -> KoreaInvestApiClient`
- 운영상 유용하지만 테스트에서 mock이 잘못되면 hang/재시도 누적 위험
- AGENTS.md의 hang 패턴과 직결되는 부분

### 5. 일부 문서/실행 진입점 불일치 가능성

- AGENTS/README 설명에는 CLI+Web 지원이 보이나, 현재 확인한 `main.py`는 웹 실행 중심
- 실제 CLI 진입 경로가 남아 있는지 별도 확인 필요

## 주문 안전장치 체크포인트

현재 확인된 안전장치:

- `services/order_execution_service.py`
  - 장 운영 시간 검사
  - 재시도 가능 오류만 제한 횟수 내 재시도
  - 주문 실패 시 알림/실패 로그 기록
- `brokers/korea_investment/korea_invest_trading_api.py`
  - 지정가 주문 시 호가단위 자동 보정
  - `order_price == 0`이면 시장가 처리
  - NXT 거래소 시장가 주문 차단
  - 주문 전 hashkey 생성 실패 시 즉시 실패 반환
  - 실제 주문 API 호출은 `retry_count=10`
- `scheduler/strategy_scheduler.py`
  - 장 마감 전 강제 청산 옵션
  - 당일 첫 장 진입 시 원장 대사 수행
  - 전략 간 실행 시차(STAGGER)로 API 충돌 완화

추가로 작업 시 직접 확인할 체크리스트:

- 주문 수량 상한 검증이 서비스 계층에 충분히 있는지
- 중복 주문 방지 키가 있는지
- 같은 전략/같은 종목 동시 재진입 방지 로직이 충분한지
- 주문 성공 응답 이후 체결 확인/잔고 반영 절차가 분리돼 있는지
- 장애 시 paper/real 모드 오동작 가능성이 없는지

## 스케줄러 핵심 메모

- `scheduler/strategy_scheduler.py`
  - 장중 주기 실행
  - 장마감 전 `FORCE_EXIT_MINUTES_BEFORE` 기준 강제 청산 지원
  - 당일 1회 원장 대사 수행
  - 전략 간 `STAGGER_INTERVAL_SEC`로 호출 간격 보장
  - signal history 저장

즉 전략 스케줄러는 단순 cron이 아니라 포지션/청산/실행간격을 함께 관리하는 운영 컴포넌트다.

## 테스트와 주의 포인트

AGENTS.md에 중요한 테스트 hang 패턴이 정리돼 있다. 특히 아래를 항상 의식한다.

- async 테스트에서 `@patch` 데코레이터 + fixture 혼용 금지
- `BrokerAPIWrapper` 테스트 시 retry/cache 래핑 주의
- mock 반환값은 가능하면 `ResCommonResponse`
- `task.start()`가 백그라운드 Task를 만들면 테스트 종료 전 반드시 `await task.stop()`
- xdist 병렬 실행 시 `StockCodeRepository` 등 외부 I/O 초기화 누락 패치 주의

## 다음 작업 전에 빠르게 볼 파일

작업 유형별 추천:

- 웹 화면/엔드포인트:
  - `view/web/web_main.py`
  - `view/web/web_api.py`
  - `view/web/routes/*`
- 서비스 로직:
  - `services/stock_query_service.py`
  - `services/order_execution_service.py`
  - `services/virtual_trade_service.py`
- 브로커/API:
  - `brokers/broker_api_wrapper.py`
  - `brokers/korea_investment/*`
- 전략/스케줄링:
  - `strategies/*`
  - `scheduler/*`
  - `task/background/*`
- 환경/설정:
  - `config/config_loader.py`
  - `config/*.yaml`

## 수정 난이도 지도

### 비교적 안전하게 수정하기 쉬운 영역

- `view/web/routes/*`
  - 기능별 라우터가 파일로 잘 분리되어 있음
  - 예:
    - `stock.py`
    - `order.py`
    - `balance.py`
    - `ranking.py`
    - `virtual.py`
  - API 입출력 변경, 응답 포맷 조정, 엔드포인트 추가는 여기서 시작하기 좋음
- `services/stock_query_service.py`
  - 조회 응답 가공과 출력용 조합 로직이 많아 국소 수정이 비교적 쉬움
- `services/virtual_trade_service.py`
  - 통계/성과 계산 중심이라 외부 실시간 의존성이 상대적으로 적음

### 중간 난이도 영역

- `services/market_data_service.py`
  - 조회 허브 역할
  - StockRepository 단기 캐시, 장마감 DB 스냅샷, 브로커 API 폴백까지 포함
  - 조회 성능/정확도 관련 수정은 여기서 자주 발생할 가능성 높음
- `services/order_execution_service.py`
  - 장시간 검증, 재시도, 알림, 구독 후처리가 함께 엮여 있음
  - 단일 수정도 주문/알림/가상매매에 영향 가능
- `brokers/korea_investment/korea_invest_client.py`
  - quotation/account/trading/websocket 조립 허브

### 영향 범위가 커서 조심해야 하는 영역

- `view/web/web_app_initializer.py`
  - 서비스/전략/태스크/알림/스케줄러 조립 중심
  - 여기 수정 시 앱 전체 초기화 경로에 영향
- `brokers/broker_api_wrapper.py`
  - 캐시, retry queue, circuit breaker, 종목코드 매핑까지 걸쳐 있음
  - 서비스 대부분이 여기를 통과
- `scheduler/strategy_scheduler.py`
  - 전략 실행, 강제 청산, 원장 대사, signal history, 실행 간격 제어 포함
  - 장중 동작 전체에 영향
- `task/background/*`
  - 백그라운드 루프와 `asyncio.create_task()` 기반 태스크가 많음
  - 생명주기 누락 시 hang 가능성 높음
- `brokers/korea_investment/korea_invest_websocket_api.py`
  - 실시간 스트리밍/구독 안정성에 직접 영향

## 실제 주문 흐름 메모

조회보다 주문 쪽은 영향 반경이 커서 흐름을 기억해 두는 게 좋다.

1. 웹 요청
   - `view/web/routes/order.py` 계열
2. 서비스 진입
   - `services/order_execution_service.py`
3. 사전 검증
   - 장 운영 시간 검사
   - 입력값 파싱
   - 재시도 정책 결정
4. 브로커 호출
   - `brokers/broker_api_wrapper.py`
   - `brokers/korea_investment/korea_invest_client.py`
   - `brokers/korea_investment/korea_invest_trading_api.py`
5. 주문 API 직전 처리
   - 주문 구분(지정가/시장가)
   - 호가단위 보정
   - hashkey 생성
   - NXT 시장가 차단
6. 주문 후처리
   - 실시간 구독 추가/해제
   - 알림 발송
   - 가상매매 실패 로그 또는 성과 반영

즉 주문 관련 수정은 `route -> service -> wrapper -> trading_api`를 한 세트로 보는 것이 안전하다.

## 백그라운드/스케줄러 위험 메모

- `task/background/after_market/*`
  - 장마감 후 데이터 수집/정리/리포트
- `task/background/intraday/*`
  - 장중 감시/스케줄러 어댑터
- `task/background/always_on/*`
  - 항상 실행되는 알림 큐 등

주의점:

- `start()`가 background task를 만들면 테스트 종료 전에 `stop()` 보장 필요
- 장중 API 요청과 웹 foreground 요청이 경쟁할 수 있음
- 스케줄러 수정 시 강제청산/원장대사/실행간격이 함께 영향을 받는지 확인 필요

## 자주 수정할 가능성이 높은 핫스팟

현재 구조와 테스트 구성을 기준으로 보면 아래 영역이 핫스팟일 가능성이 높다.

### 1. 웹 API 라우트

- `view/web/routes/stock.py`
- `view/web/routes/order.py`
- `view/web/routes/balance.py`
- `view/web/routes/ranking.py`
- `view/web/routes/virtual.py`
- `view/web/routes/scheduler.py`

이유:

- 사용자 기능과 직접 맞닿아 있음
- 통합 테스트의 주요 대상일 가능성이 높음
- 응답 포맷이나 기능 추가 요청이 자주 들어올 수 있음

### 2. 조회/주문 서비스

- `services/stock_query_service.py`
- `services/market_data_service.py`
- `services/order_execution_service.py`

이유:

- 라우터와 브로커 사이 핵심 연결부
- 조회 캐시/DB 스냅샷/API 폴백
- 주문 검증/재시도/후처리

### 3. 전략/리포트 관련

- `strategies/*`
- `scheduler/strategy_scheduler.py`
- `task/background/after_market/strategy_log_report_task.py`
- `services/strategy_log_report_service.py`

이유:

- 전략은 계속 튜닝될 가능성이 높음
- 로그/리포트/랭킹/후보군 생성이 운영 개선 포인트가 되기 쉬움

### 4. 종목/지표 선별 계층

- `services/rs_rating_service.py`
- `services/minervini_stage_service.py`
- `services/oneil_universe_service.py`
- `services/newhigh_service.py`

이유:

- 전략 품질 향상 요청이 들어오면 이 영역을 자주 건드리게 될 가능성 높음

## 테스트 체크리스트

### 현재 확인한 pytest 기본 설정

- `pytest.ini`
  - `asyncio_mode = auto`
  - 기본 옵션: `-n auto`
  - timeout: 30초
  - 실행시간 긴 테스트는 `slow` marker 사용 가능

즉 기본이 병렬 + async 자동 모드이므로, 테스트는 순차 단독 실행에서만 통과하는 형태가 되지 않게 주의해야 한다.

### 새 테스트 작성/수정 시 체크

- async 테스트에서 `@patch` 데코레이터와 fixture 인자를 함께 쓰지 않는다
- 가능하면 `with patch(...)` 컨텍스트 매니저 사용
- `BrokerAPIWrapper`를 테스트할 때는 retry/cache 래핑을 우회하거나 `_client`를 직접 교체
- mock 반환값은 `ResCommonResponse` 형태를 우선 사용
- background task를 `start()` 했으면 반드시 `finally`에서 `await stop()` 호출
- 외부 I/O 가능 초기화(`StockCodeRepository`, `WebAppContext`)는 테스트에서 확실히 패치
- xdist 병렬 실행 시 worker별 파일/네트워크 초기화가 중복되지 않는지 점검
- `asyncio.sleep` 패치 범위가 실제 호출 모듈까지 덮는지 확인

### 테스트 난이도가 높은 영역

- `view/web/web_app_initializer.py`
  - 초기화 시 많은 객체를 한 번에 생성
- `brokers/broker_api_wrapper.py`
  - retry/cache 체인 때문에 mock 설계가 중요
- `scheduler/strategy_scheduler.py`
  - 장시간/날짜/백그라운드/포지션 상태가 얽힘
- `task/background/*`
  - 생명주기와 sleep/loop 제어가 핵심

### 상대적으로 테스트하기 쉬운 영역

- 순수 계산/가공 성격 서비스
  - `services/virtual_trade_service.py`
  - 일부 `services/stock_query_service.py`의 응답 가공 부분
- 전략 내부의 조건 판별 로직
  - 외부 호출만 잘 mock하면 단위 테스트화 가능

## 현재 테스트 파일 구성에서 읽히는 점

- 단위 테스트는 현재 확인 범위상 `RSRatingService` 쪽 비중이 큼
- 통합 테스트는 웹 API와 retry queue, crash handler, 전략 로깅을 다룸

즉 앞으로 회귀 가능성이 큰 곳도 웹 API, 브로커 retry 체인, 전략 로그/스케줄링 쪽이다.

## 작업 전 점검 루틴

새 작업을 시작할 때 아래 순서를 기본 루틴으로 사용한다.

### 1. 문서 우선 확인

1. `AGENTS.md`
2. `SKILL.md`
3. `CODEBASE_SUMMARY.md`

### 2. 요청 유형 분류

- 웹 UI/API 수정
- 조회 로직 수정
- 주문/실시간/브로커 수정
- 전략/스케줄러 수정
- 테스트 안정화/버그 수정
- 설정/환경/실행 경로 수정

### 3. 진입 파일 좁히기

요청 유형별로 아래에서 시작:

- 웹 UI/API:
  - `view/web/routes/*`
  - `view/web/web_main.py`
- 조회:
  - `services/stock_query_service.py`
  - `services/market_data_service.py`
- 주문:
  - `services/order_execution_service.py`
  - `brokers/broker_api_wrapper.py`
  - `brokers/korea_investment/korea_invest_trading_api.py`
- 전략:
  - `strategies/*`
  - `scheduler/strategy_scheduler.py`
  - `task/background/*`

### 4. 영향 범위 빠른 체크

수정 전에 아래를 짧게 확인:

- 이 로직이 웹 요청 경로인가
- 백그라운드 태스크도 같이 타는가
- 실시간 스트림과 연결되는가
- 모의/실전 분기가 있는가
- cache/retry wrapper가 걸려 있는가
- 테스트에서 patch가 많이 필요한 영역인가

### 5. 수정 후 최소 검증

- 관련 단위 테스트 또는 통합 테스트 우선
- 최소한 변경 경로 1개는 직접 실행 관점으로 검증
- background task를 건드렸으면 hang 가능성 점검
- 주문/전략 쪽이면 모의/실전 분기 영향 재확인

## 앞으로의 기본 작업 전략

이 코드베이스에서는 아래 방식으로 접근하는 것이 안전하다.

### 전략 1. 얕게 넓게 보지 말고, 진입점부터 좁게 내려간다

- 전체 폴더를 다시 훑기보다
- 요청과 가장 가까운 route/service부터 시작해서
- 필요한 경우에만 broker/scheduler/task까지 내려간다

### 전략 2. 초기화 코드는 마지막에 건드린다

- `view/web/web_app_initializer.py`는 영향 범위가 크므로
- 먼저 개별 service/route/strategy 수정이 가능한지 본다
- 정말 필요할 때만 조립부를 수정한다

### 전략 3. 브로커 래퍼 수정은 테스트 전략과 같이 생각한다

- `BrokerAPIWrapper`를 수정하면 테스트 hang 위험이 같이 올라간다
- 변경 전에 mock/retry/cache 영향부터 떠올린다

### 전략 4. background task는 생명주기까지 한 세트로 본다

- task 로직만 보지 말고
- start/stop/cancel/sleep/restore 흐름을 같이 본다
- 테스트도 반드시 cleanup 경로를 포함한다

### 전략 5. 조회는 캐시/DB/API 우선순위를 같이 본다

- `MarketDataService`는 단순 API 프록시가 아니다
- StockRepository 단기 캐시
- 장마감 DB 스냅샷
- API 폴백 순서가 있으므로
- 조회값 이상은 데이터 소스 우선순위까지 확인한다

### 전략 6. 주문은 후처리까지 확인한다

- 주문 성공/실패 자체만 보지 않는다
- 구독 추가/해제
- 알림 발송
- 가상매매 기록
- 스케줄러 포지션 상태 영향까지 함께 본다

### 전략 7. 전략 수정은 선별 서비스와 함께 본다

- 전략 파일만 수정하고 끝나는 경우가 적다
- `rs_rating`, `minervini_stage`, `oneil_universe`, `newhigh` 같은
- 후보군/필터 계층도 함께 영향 받는지 확인한다

## 빠른 판단용 작업 템플릿

작업 착수 전 머릿속 체크:

- 요청 종류는 무엇인가?
- 첫 진입 파일은 어디인가?
- 영향이 route/service 수준에서 끝나는가?
- broker/scheduler/task까지 번지는가?
- mock/retry/cache/background 때문에 테스트가 까다로워지는가?
- 모의/실전 분기 확인이 필요한가?

이 질문들에 답하고 나서 수정에 들어가면 불필요한 재탐색이 많이 줄어든다.

## 작업 유형별 치트시트

아주 짧게 빠르게 보고 시작할 때는 아래만 본다.

### 웹 API 수정

- 시작:
  - `view/web/routes/<target>.py`
- 보조 확인:
  - `services/stock_query_service.py`
  - `services/order_execution_service.py`
  - `view/web/web_main.py`
- 주의:
  - 응답 포맷 변경이 프런트와 통합 테스트에 영향

### 조회 로직 수정

- 시작:
  - `services/stock_query_service.py`
  - `services/market_data_service.py`
- 아래로 내려갈 곳:
  - `brokers/broker_api_wrapper.py`
  - `brokers/korea_investment/korea_invest_client.py`
  - `*_quotations_api.py`
- 주의:
  - 캐시/DB/API 폴백 순서 확인
  - paper/real 분기 확인

### 주문 로직 수정

- 시작:
  - `services/order_execution_service.py`
- 아래로 내려갈 곳:
  - `brokers/broker_api_wrapper.py`
  - `brokers/korea_investment/korea_invest_client.py`
  - `brokers/korea_investment/korea_invest_trading_api.py`
- 후처리 확인:
  - 알림
  - 구독 추가/해제
  - 가상매매 기록
- 주의:
  - 장시간 검사
  - 재시도
  - 호가 보정
  - NXT 시장가 차단

### 전략 수정

- 시작:
  - `strategies/<target>_strategy.py`
- 함께 볼 것:
  - `strategies/strategy_executor.py`
  - `scheduler/strategy_scheduler.py`
  - `services/minervini_stage_service.py`
  - `services/rs_rating_service.py`
- 주의:
  - 단순 전략 수정이 후보군/필터/청산 흐름에 영향 줄 수 있음

### 스케줄러/백그라운드 수정

- 시작:
  - `scheduler/strategy_scheduler.py`
  - `task/background/*`
- 함께 볼 것:
  - `view/web/web_app_initializer.py`
- 주의:
  - start/stop/cancel/sleep
  - 강제청산
  - 원장 대사
  - hang 가능성

### 테스트 안정화 작업

- 시작:
  - `tests/unit_test/*`
  - `tests/integration_test/*`
  - `tests/**/conftest.py`
- 함께 볼 것:
  - `pytest.ini`
  - `AGENTS.md`의 hang 가이드
- 주의:
  - async + patch + fixture 조합
  - wrapper retry/cache
  - background cleanup

## 자주 쓰는 실행/검증 명령

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

특정 테스트 1개:

```powershell
pytest tests/unit_test/test_xxx.py::test_yyy -v -n0
```

커버리지 배치:

```powershell
.\1.test_with_coverage.bat
.\2.integration_test_with_coverage.bat
```

pytest 기본 설정 메모:

- `asyncio_mode = auto`
- 기본 addopts에 `-n auto` 포함
- timeout 30초

## 수정 후 검증 루틴

### 1. 가장 작은 범위부터 검증

- 수정한 파일과 가장 가까운 테스트 먼저
- 가능하면 파일 단위 또는 케이스 단위로 시작

예:

```powershell
pytest tests/unit_test/test_xxx.py -v -n0
```

### 2. async/hang 의심 시 단독 실행

- 병렬 이슈인지 코드 이슈인지 분리하려면 먼저 `-n0`

```powershell
pytest tests/unit_test/test_xxx.py::test_yyy -v -n0
```

### 3. 관련 통합 테스트 확인

- 웹/API/브로커 영향이 있으면 integration_test까지 확인

```powershell
pytest tests/integration_test/test_it_web_api_deep.py -v -n0
```

### 4. 백그라운드/스케줄러 변경 시 추가 확인

- 테스트 종료 후 hang 없는지
- cleanup 누락 없는지
- sleep patch 범위가 충분한지

### 5. 주문/전략 변경 시 추가 확인

- 모의/실전 분기 영향
- 주문 후처리 영향
- 강제청산/스케줄링 영향

## 추천 검증 전략

요청 유형별 추천:

- 웹 응답/라우트 변경:
  - 관련 unit 또는 integration test 1개
  - 필요 시 웹 API deep/paper 테스트
- 조회 로직 변경:
  - 서비스 단위 테스트
  - 캐시/DB/API 폴백 경로 확인
- 주문 로직 변경:
  - service 레벨 검증
  - broker/trading API 영향 테스트
- 전략/스케줄러 변경:
  - 관련 전략 테스트
  - scheduler/integration/logging 테스트
- hang/비동기 버그 수정:
  - 먼저 `-n0`
  - 그다음 기본 병렬 옵션으로 재확인

## 작업 예시별 조사 경로

실제 요청이 들어왔을 때 아래 시나리오 중 가장 가까운 것을 골라 그대로 따라간다.

### 예시 1. 웹에서 종목 조회 응답 필드를 바꾸고 싶다

예:

- 현재가 응답에 필드 추가
- 종목 상세 응답 포맷 변경
- 프런트에서 쓰는 키 이름 변경

조사 경로:

1. `view/web/routes/stock.py`
2. `services/stock_query_service.py`
3. `services/market_data_service.py`
4. `brokers/broker_api_wrapper.py`
5. 필요 시 `brokers/korea_investment/korea_invest_quotations_api.py`

확인 포인트:

- 응답이 route에서 직접 조립되는지
- service에서 view model 형태로 가공되는지
- 원본 API 응답 구조가 이미 dataclass인지 dict인지
- 프런트 템플릿/JS가 해당 필드를 기대하는지

추천 검증:

- 관련 API 테스트
- 관련 웹 통합 테스트

### 예시 2. 주문 버튼 동작이나 주문 조건을 바꾸고 싶다

예:

- 매수/매도 전 검증 조건 추가
- 시장가/지정가 처리 방식 수정
- 주문 실패 메시지 개선

조사 경로:

1. `view/web/routes/order.py`
2. `services/order_execution_service.py`
3. `brokers/broker_api_wrapper.py`
4. `brokers/korea_investment/korea_invest_client.py`
5. `brokers/korea_investment/korea_invest_trading_api.py`

함께 볼 것:

- `services/virtual_trade_service.py`
- 구독/알림 관련 서비스

확인 포인트:

- 장 운영 시간 검사 영향
- 재시도 정책 영향
- 주문 후 구독 추가/해제 영향
- 가상매매 기록 및 실패 로그 영향
- 모의/실전 분기 영향

추천 검증:

- 주문 서비스 테스트
- 관련 웹 API 통합 테스트

### 예시 3. 특정 전략의 매수 조건을 수정하고 싶다

예:

- 모멘텀 임계값 변경
- 포켓 피벗 조건 보정
- 눌림목 진입 조건 수정

조사 경로:

1. 대상 전략 파일 `strategies/<target>_strategy.py`
2. `strategies/strategy_executor.py`
3. `scheduler/strategy_scheduler.py`
4. 필요 시 아래 보조 서비스
   - `services/minervini_stage_service.py`
   - `services/rs_rating_service.py`
   - `services/oneil_universe_service.py`
   - `services/newhigh_service.py`

확인 포인트:

- 전략 자체 조건만 바뀌는지
- 후보군 필터도 같이 바뀌어야 하는지
- 스케줄러 포지션/청산 흐름과 충돌 없는지
- 로그/리포트에 표시되는 값도 수정이 필요한지

추천 검증:

- 전략 단위 테스트
- 전략 로그/통합 테스트

### 예시 4. 스케줄러가 특정 시간에 이상하게 동작한다

예:

- 전략이 너무 자주 실행됨
- 장 마감 전 강제 청산이 안 됨
- 장 시작 직후 원장 대사가 이상함

조사 경로:

1. `scheduler/strategy_scheduler.py`
2. `services/market_calendar_service.py`
3. `core/market_clock.py`
4. 필요 시
   - `task/background/intraday/strategy_scheduler_task_adapter.py`
   - `view/web/routes/scheduler.py`
   - `view/web/web_app_initializer.py`

확인 포인트:

- 현재 시각 기준 계산
- 휴장일/장운영 시간 계산
- `_last_run`, 강제청산 플래그, reconciliation 플래그 상태
- background task start/stop 경로

추천 검증:

- scheduler 관련 테스트
- 단독 실행 `-n0`

### 예시 5. 테스트가 hang 나거나 병렬에서만 깨진다

예:

- `pytest tests/` 전체 실행 시 멈춤
- 단독 실행은 통과하는데 `-n auto`에서 실패

조사 경로:

1. 실패 테스트 파일
2. `tests/**/conftest.py`
3. `AGENTS.md` hang 가이드
4. 관련 production 코드
   - `brokers/broker_api_wrapper.py`
   - `scheduler/strategy_scheduler.py`
   - `task/background/*`
   - `view/web/web_app_initializer.py`

확인 포인트:

- `@patch` + async fixture 혼용 여부
- mock 반환값이 `ResCommonResponse`인지
- background task cleanup 누락 여부
- `asyncio.sleep` patch 범위
- 외부 I/O 초기화 누락 패치

추천 검증:

- 먼저 `-n0`
- 그 다음 병렬 옵션으로 재확인

### 예시 6. 웹소켓/실시간 가격이 갱신되지 않는다

예:

- 실시간 체결가가 안 들어옴
- 구독은 했는데 화면 반영이 안 됨

조사 경로:

1. `view/web/routes/streaming.py`
2. `services/streaming_service.py`
3. `services/price_stream_service.py`
4. `services/price_subscription_service.py`
5. `brokers/korea_investment/korea_invest_websocket_api.py`
6. 필요 시 `task/background/intraday/websocket_watchdog_task.py`

확인 포인트:

- 실제 구독 등록이 되었는지
- 스트림 수신 후 저장소/큐 반영이 되는지
- 프런트로 브로드캐스트 경로가 있는지
- watchdog이 재연결을 처리하는지

추천 검증:

- streaming 관련 테스트
- 로그/스트리밍 로그 확인

### 예시 7. 장마감 후 랭킹/리포트/후처리 결과가 이상하다

예:

- 랭킹 결과가 비어 있음
- 전략 로그 리포트 누락
- OHLCV 업데이트 실패

조사 경로:

1. 대상 after-market task 파일
   - `task/background/after_market/ranking_task.py`
   - `task/background/after_market/strategy_log_report_task.py`
   - `task/background/after_market/ohlcv_update_task.py`
2. 관련 서비스 파일
3. `view/web/web_app_initializer.py`
4. 필요 시 저장소/리포트 출력 파일 경로

확인 포인트:

- task가 실제 등록/시작되는지
- 장마감 이후 실행 조건이 맞는지
- 필요한 선행 데이터가 준비돼 있는지
- worker pool / dispatcher 흐름 문제 없는지

추천 검증:

- 관련 태스크 테스트
- 로그 파일 및 출력 산출물 확인

### 예시 8. 모의투자/실전투자 전환이 이상하다

예:

- paper인데 실전 API를 타는 것 같음
- 실전 전용 API가 paper에서 호출됨
- 토큰/URL/TR ID가 예상과 다름

조사 경로:

1. `brokers/korea_investment/korea_invest_env.py`
2. `config/config_loader.py`
3. `brokers/korea_investment/korea_invest_client.py`
4. `brokers/korea_investment/*_provider.py`
5. `view/web/web_app_initializer.py`

확인 포인트:

- `is_paper_trading` 설정 시점
- quotation/account/trading 각각 어떤 URL/provider를 쓰는지
- 실전 인증 강제 사용 경로가 있는지
- 실전 전용 API 가드가 있는지

추천 검증:

- paper/real 통합 테스트
- 관련 설정값 확인

## 지금까지의 결론

- 이 저장소는 한국 주식 대상 자동매매/보조매매 플랫폼이다.
- 웹 UI는 단순 조회 화면이 아니라 주문과 전략 운영 콘솔이다.
- 서비스 조립의 중심은 `WebAppContext`다.
- 브로커 추상화의 중심은 `BrokerAPIWrapper`다.
- 전략은 개별 파일로 끝나지 않고 스케줄러/배치/리포트 체계 안에서 운영된다.

## 유지보수 메모

- 이후 큰 기능을 분석했으면 이 문서에 먼저 반영해 두는 것이 좋다.
- 새 작업 전에는 이 문서를 기준으로 필요한 파일만 국소적으로 다시 읽는다.
