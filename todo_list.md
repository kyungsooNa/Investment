# 📈 Investment Trading App - 통합 To-Do List (v3.0 - 실전 퀀트 최적화)
# 📈 Investment Trading App - 통합 To-Do List (v3.1 - 실전 퀀트 최적화 및 파일 매핑)

## Ⅰ. 🚨 1순위: 시스템 생존 및 치명적 병목 해소 (Tier 1: Survival & Critical Bottlenecks)
계좌를 보호하는 안전장치를 확보하고, 시스템 다운을 유발하는 치명적 버그와 DB 병목을 최우선으로 해결합니다.

- [ ] **[버그] 구독종목 복구 지연:** DB 저장을 1분 단위로 수정.
    - 📂 `services/program_trading_stream_service.py`, `repositories/program_trading_repo.py`
- [ ] **[버그] Streaming 데이터 수신 누락:** 현재가 조회 실패 종목 수정.
    - 📂 `services/streaming_service.py`, `brokers/korea_investment/korea_invest_websocket_api.py`
- [ ] **[버그] NewhighTask DB 경합:** 신고가 조회 실패 버그 수정.
    - 📂 `task/background/after_market/newhigh_task.py`, `services/newhigh_service.py`
- [ ] **[성능] DB WAL 모드 및 벌크 인서트:** 쓰기 락 방지.
    - 📂 `core/cache/db_cache.py`, `data/stock_db_reader.py`
- [ ] **[안정성] 주문 상태 기계(FSM):** 중복 매수 방지.
    - 📂 `common/types.py`, `services/order_execution_service.py`
- [ ] **[리스크] 킬 스위치 및 포지션 사이징:** 계좌 보호 로직 및 변동성(ATR) 기반 비중 조절.
    - 📂 `core/pnl_utils.py`, `services/order_execution_service.py`, `services/indicator_service.py`
- [ ] **[UI 버그] candle_chart 표기:** 신고가/신저가 기간 버튼 갱신 버그 수정.
    - 📂 `view/web/static/js/candle_chart.js` 

## Ⅱ. ⚡ 2순위: 실시간 체결 성능 및 마이크로 구조 개선 (Tier 2: Execution & Microstructure)
변동성 장세에서 주문 지연과 슬리피지를 최소화하기 위한 아키텍처 및 체결 알고리즘 최적화입니다.

- [ ] **[성능] I/O와 CPU 연산 격리:** `ProcessPoolExecutor` 도입.
    - 📂 `main.py`, `scheduler/worker/worker_pool.py`, `strategies/strategy_executor.py`
- [ ] **[성능] orjson 및 Circular Buffer:** 파싱 및 지표 연산 고속화.
    - 📂 `common/types.py`, `services/indicator_service.py`
- [ ] **[성능] HTTP Keep-Alive 세션 유지:** KIS API 주문 지연 최소화.
    - 📂 `view/web/web_app_initializer.py`, `brokers/korea_investment/korea_invest_client.py`
- [ ] **[알고리즘] 스마트 라우팅 및 호가 분석:** 최적 단가 체결 및 분할 주문(TWAP/VWAP).
    - 📂 `services/order_execution_service.py`, `brokers/korea_investment/korea_invest_websocket_api.py`

## Ⅲ. 🧠 3순위: 백테스트 현실화 및 전략 확장 (Tier 3: Backtesting & Strategy)
수익 모델을 고도화하고, 백테스트와 실전의 괴리를 없애기 위한 환경을 구축합니다.

- [ ] **[백테스트] 가상 브로커 및 Mock 데이터:** 전략 재사용 엔진 및 편향(Survivorship Bias) 제거.
    - 📂 `interfaces/strategy.py`, `strategies/backtest_data_provider.py`, `services/virtual_trade_service.py`
- [ ] **[아키텍처] 전략/실행 결합도 낮추기:** Pub/Sub 시그널 구조.
    - 📂 `scheduler/ticket_queue/message_broker.py`, `strategies/strategy_executor.py`
- [ ] **[전략] 오닐 스퀴즈 V3 및 마켓 타이밍 지표:** 업종 스코어링 및 시장 폭 지표 구현.
    - 📂 `services/oneil_universe_service.py`, `services/market_data_service.py`
- [ ] **[유니버스] 시장 확장 및 AI 연동:** NXT/해외주식 편입 및 LLM 분석.
    - 📂 `services/stock_sync_service.py` 

## Ⅳ. 🔍 4순위: 관측성 및 유지보수성 개선 (Tier 4: Observability & Maintenance)
블랙박스 현상을 방지하고, 운영 및 유지보수 생산성을 극대화합니다.

- [ ] **[관측성] Trace ID 기반 로그 추적:** 주문 블랙박스 구현.
    - 📂 `core/loggers/app_logger.py`, `core/loggers/json_formatter.py`
- [ ] **[유지보수] 전략 노후화 감지:** 실거래 성과 모니터링 및 자동 관망 전환.
    - 📂 `services/strategy_log_report_service.py`, `task/background/after_market/strategy_log_report_task.py`
- [ ] **[유지보수] 카오스 엔지니어링:** 결함 주입 테스트 추가.
    - 📂 `tests/integration_test/test_it_crash_handler.py`, `core/retry_queue/client_with_retry_queue.py`
- [ ] **[보안 및 인프라]** JWT 인증, 컨테이너화(Docker), UI 위젯 추가.
    - 📂 `view/web/routes/auth.py`, `Dockerfile`, `view/web/templates/virtual.html`


### 성능 리뷰
1. 🐍 Python GIL 우회 및 아키텍처 분리 (I/O vs CPU)
현재 FastAPI와 비동기(asyncio) 기반으로 시스템을 구성하셨다면 I/O(웹소켓 수신, API 호출) 처리에는 매우 탁월합니다. 하지만 파이썬의 GIL(Global Interpreter Lock) 특성상, 하나의 프로세스 내에서 무거운 연산을 수행하면 비동기 루프 전체가 멈추는 블로킹(Blocking) 현상이 발생합니다.

문제점: streaming_service.py에서 수백 종목의 틱을 비동기로 수신하는 도중, strategy_executor.py나 indicator_service.py에서 무거운 연산(예: 복잡한 지표 계산이나 N개 종목 동시 스캔)이 실행되면 틱 수신 지연이 발생합니다.

개선 방안 (ProcessPool 분리): CPU 연산이 많이 필요한 전략 판단(Strategy Evaluation)이나 지표의 대규모 재계산 작업은 concurrent.futures.ProcessPoolExecutor를 사용하여 별도의 프로세스로 위임(Off-loading)해야 합니다. I/O 처리를 하는 메인 Async 이벤트 루프가 절대 멈추지 않도록 보호하는 것이 1원칙입니다.

2. 🗄️ SQLite 및 파일 DB 병목 해소 (Write-Lock Contention)
todo_list.md에 "현재 모든 tick 단위가 db에 저장되고 있는데... 1분 단위로 수정"한다는 내용이 있었습니다. 파일 기반에서 SQLite로 넘어가더라도, SQLite는 동시 쓰기(Concurrency Write)에 매우 취약합니다.

문제점: 수십 개 종목의 틱 데이터를 SQLite에 매번 INSERT 하면 'Database is locked' 에러가 발생하거나 심각한 I/O 대기(Wait)가 걸립니다.

개선 방안 (Batch Insert & WAL Mode): 1.  WAL 모드 활성화: SQLite 연결 시 반드시 PRAGMA journal_mode=WAL; (Write-Ahead Logging)과 PRAGMA synchronous=NORMAL;을 적용하여 읽기/쓰기 락 경합을 최소화해야 합니다.
2.  메모리 버퍼링 및 벌크 인서트: 틱 데이터를 즉시 DB에 쓰지 말고, Redis 리스트나 파이썬 내장 asyncio.Queue에 버퍼링해 둡니다. 그리고 백그라운드 태스크(예: daily_price_collector_task.py)에서 1초에 한 번씩 수천 건을 한 번에 묶어서 쿼리 하나로 executemany() 벌크 인서트를 하도록 변경해야 합니다.

3. 🚀 데이터 역직렬화(Deserialization) 오버헤드 최소화
증권사 웹소켓(한국투자증권 등)에서 날아오는 실시간 체결 데이터는 보통 JSON 포맷이거나 특정 구분자(| 또는 ^)로 연결된 긴 문자열입니다.

개선 방안 (orjson 도입): 파이썬 내장 json 모듈은 매우 느립니다. 이를 Rust로 작성된 orjson이나 C 기반의 ujson으로 교체하기만 해도 JSON 파싱 속도가 3~5배 이상 빨라집니다. 틱 데이터가 초당 수천 번 들어오는 환경에서는 이 파싱 시간 자체가 레이턴시의 주범이 됩니다.

객체 생성 오버헤드 감소: 틱이 들어올 때마다 매번 무거운 Pydantic 모델이나 큰 딕셔너리(dict) 객체를 새로 생성하면 Python의 **가비지 컬렉터(GC Pause)**가 빈번하게 작동하여 시스템이 순간적으로 멈춥니다(스파이크 발생). 실시간 틱 데이터 클래스는 가급적 __slots__를 사용하여 메모리 할당 속도를 높이고 메모리 풋프린트를 줄여야 합니다.

4. 🧮 시계열 연산 고속화 (Pandas의 함정 피하기)
퀀트 시스템을 만들 때 가장 흔히 하는 실수 중 하나가 실시간 처리 루프 내에서 Pandas DataFrame을 사용하는 것입니다.

문제점: 백테스트에서는 Pandas의 벡터화(Vectorization) 연산이 최고지만, 라이브 환경에서 틱이나 1분봉 데이터 1줄을 DataFrame에 append() 하거나 concat() 하는 작업은 메모리를 통째로 재할당하기 때문에 최악의 성능을 냅니다.

개선 방안 (Circular Buffer 활용): 실시간 지표 계산(이동평균, RSI 등) 시에는 Pandas 대신 파이썬 내장 collections.deque(maxlen=N)를 사용하거나, **미리 크기를 할당해 둔 NumPy 배열(Circular Buffer 방식)**을 사용해야 합니다. 최신 틱이 들어오면 가장 오래된 데이터를 밀어내고 덮어쓰는(O(1) 연산) 구조로 가야 CPU 부하 없이 즉각적인 타점 계산이 가능합니다.

5. 📡 네트워크 레이턴시 및 세션 재사용
현재 core/retry_queue/ 쪽에 API 재시도 로직이 잘 구현되어 있는 것으로 보입니다.

개선 방안 (Keep-Alive 및 Connection Pool): KIS API 등으로 주문을 넣거나 데이터를 조회할 때, 매번 requests.get/post나 새로운 aiohttp.ClientSession을 생성하면 TCP/TLS Handshake 과정에서 수백 밀리초(ms)가 낭비됩니다. 앱 기동 시(web_app_initializer.py) 하나의 aiohttp.ClientSession 풀을 생성하여 전역적으로 유지하고, Keep-Alive가 적용된 상태에서 주문 패킷만 쏘도록 재사용해야 주문 체결 반응 속도를 극대화할 수 있습니다.


### '운영 수준(Production-level)의 퀀트 시스템 유지보수성 및 확장성' 관점에서 추가할 만한 개선 포인트들을 제안해 드립니다.

1. AI 친화적 아키텍처 (AI-Native DX)
최근 개발 워크플로우에서는 Claude Code, Cursor AI, GitHub Copilot 같은 AI 코딩 어시스턴트의 효율을 극대화하는 코딩 컨벤션이 유지보수성의 새로운 표준으로 자리 잡고 있습니다.

엄격한 타입 힌팅과 인터페이스 분리: Python 3.10의 타입 힌팅을 극대화하여 mypy 통과 수준으로 엄격하게 관리하는 것을 추천합니다. AI 어시스턴트들은 함수의 입출력 타입과 Pydantic 모델이 명확할 때, 전략 추가나 리팩토링 시 문맥(Context)을 놓치지 않고 훨씬 더 정확한 코드를 제안합니다.

순수 함수(Pure Function)의 고립: indicator_service.py 내부의 계산 로직처럼 부수 효과(Side-effect)가 없는 순수 함수들을 별도의 코어 모듈로 완전히 분리해 두면, AI를 통한 유닛 테스트 자동 생성이나 비동기/멀티프로세싱 전환이 매우 매끄러워집니다.

2. 카오스 엔지니어링 및 결함 주입 테스트 (Fault Injection)
현재 단위 테스트와 통합 테스트가 잘 구축되어 있지만, 실전 트레이딩 시스템은 로직의 오류보다는 '외부 요인'에 의해 무너지는 경우가 많습니다.

극한 상황 시뮬레이션: KIS API의 간헐적 500 에러, Rate Limit 초과, 한국투자증권 웹소켓의 갑작스러운 끊김이나 지연 수신 상황을 인위적으로 발생시키는 '결함 주입(Mocking Network Failures)' 테스트 케이스를 추가해야 합니다.

비동기 큐 정체 테스트: Redis나 메시지 큐에 순간적으로 수만 개의 틱 데이터나 매매 시그널이 쌓였을 때, 애플리케이션이 OOM(Out of Memory)으로 죽지 않고 오래된 데이터를 버리거나(Drop) 백프레셔(Backpressure)를 정상적으로 작동시키는지 검증하는 부하 테스트가 필요합니다.

3. 주문 생애주기 추적성 (Traceability & Blackbox)
프로젝트 내에 json_formatter.py 등을 활용하여 로그 시스템을 체계적으로 잡아가고 계신 점이 눈에 띕니다. 여기에 돈이 걸린 문제의 원인을 1분 안에 파악하기 위한 '블랙박스' 기능이 추가되어야 합니다.

Trace ID 파이프라인 구축: 매수 조건이 포착된 순간부터 고유한 Signal_ID 또는 Trace_ID를 발급하여, 지표 계산 -> 전략 판단 -> 주문 생성 -> API 전송 -> 체결 수신까지의 모든 비동기 흐름에 이 ID를 꼬리표처럼 달고 다니도록(Context Variables 활용) 로깅을 개선해야 합니다. 이를 통해 특정 주문이 왜 슬리피지가 발생했는지, 혹은 왜 취소되었는지 단일 ID 검색만으로 전체 타임라인을 복기할 수 있습니다.

4. 상태 기계(State Machine) 기반의 주문 관리
비동기 환경에서 주문을 넣고 체결을 기다리는 과정은 생각보다 복잡한 상태 관리가 필요합니다.

주문 상태의 명시적 분리: 단순히 '주문 완료'로 끝나는 것이 아니라, PENDING_SUBMIT -> SUBMITTED -> PARTIAL_FILLED -> FILLED or CANCELED의 상태를 전이(Transition)하는 유한 상태 기계(FSM) 패턴을 도입하는 것이 좋습니다. 이를 통해 네트워크 지연으로 인해 주문이 중복으로 들어가거나, 미체결 상태에서 엉뚱한 후속 로직이 실행되는 동시성 버그(Race Condition)를 원천적으로 차단할 수 있습니다.

5. 전략(Strategy)과 실행(Execution)의 결합도 낮추기
현재 To-Do 리스트에 가상 브로커(Mock Broker)를 통한 백테스팅 엔진 도입을 계획 중이십니다.

이벤트 드리븐(Event-Driven) 백엔드: 이 계획이 성공적으로 유지보수성을 가지려면, 전략 객체(Strategy)는 "A 종목을 100주 매수해라"라는 순수한 '의도(Signal Event)'만 발행(Publish)하고 끝나야 합니다. 이 시그널을 구독(Subscribe)하고 있는 실행 엔진(Execution Service)이 실제 KIS API로 쏠지, 아니면 가상 계좌 DB에 기록할지를 결정하도록 책임을 완전히 분리(Decoupling)하면 시스템의 확장성과 테스트 용이성이 극대화됩니다.