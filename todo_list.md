# 📈 Investment Trading App - 통합 To-Do List (v2.0)

## Ⅰ. 🚨 최우선 개선 및 버그 수정 (High Priority)
애플리케이션의 핵심 기능 안정성과 데이터 무결성에 직접적인 영향을 미치는 사항입니다.

### 0. 치명적 불량 수정 (Critical Bugs)
- [ ] **[프로그램매매]** 이전 구독종목 복구 지연 개선: DB 저장을 1분 단위 마지막 틱 정보만 저장하도록 수정하여 메모리 부하 절감.
- [ ] **[Streaming]** 데이터 수신 실패 종목(현재가 조회 실패) 원인 파악 및 수정.
- [ ] **[candle_chart]** 신고가/신저가 표기 UI 갱신 버그 수정.
- [ ] **[newhighTask]** DB 덮어쓰기 문제로 인한 신고가 종목 조회 실패 버그 수정.

### 1. 보안 및 안정성
- [ ] **[보안]** JWT(JSON Web Token) 기반 인증 고도화 및 Secure/HttpOnly 쿠키 적용.
- [ ] **[모니터링]** 시스템 관측성(Observability) 확보: Prometheus/Grafana 연동하여 API 레이턴시 및 에러율 실시간 모니터링 (신규 제안).

---

## Ⅱ. ⚡ 인프라 및 데이터 아키텍처 (Infrastructure & Data)
시스템 성능 최적화와 장기적 운영 안정성을 위한 과제입니다.

### 1. 데이터베이스 마이그레이션
- [ ] **[DB 마이그레이션]** 파일 기반 데이터(JSON, CSV)를 SQLite로 전면 마이그레이션.
- [ ] **[시계열 DB 검토]** 대량의 틱/분봉 데이터 처리를 위한 전용 시계열 DB(TimescaleDB 등) 도입 검토 (신규 제안).

### 2. 배포 및 환경 구축
- [ ] **[컨테이너화]** Dockerfile 및 docker-compose.yml 작성을 통한 배포 용이성 확보.

---

## Ⅲ. 🛡️ 리스크 및 포트폴리오 관리 (Risk & Portfolio Management)
실전 매매에서의 생존율을 높이기 위한 핵심 제어 시스템입니다. (전체 신규 제안)

### 1. 자금 관리 (Position Sizing)
- [ ] **[동적 비중 조절]** 변동성(ATR) 기반 포지션 사이징 엔진 개발.
- [ ] **[상관관계 필터]** 포트폴리오 내 종목 간 상관관계(Correlation) 분석을 통한 특정 섹터 쏠림 방지 로직.

### 2. 계좌 보호 (Global Safety)
- [ ] **[킬 스위치]** 일일 최대 손실폭(Daily Stop-loss) 또는 MDD 임계치 도달 시 자동 전량 매도 및 매매 중단 기능.
- [ ] **[동적 손절]** MAE(최대 평가손실)/MFE(최대 평가수익) 데이터 기반의 전략별 최적 트레일링 스탑 구현.

---

## Ⅳ. 💹 주문 집행 및 시장 구조 (Execution & Microstructure)
체결 효율을 높이고 슬리피지를 최소화하기 위한 기능입니다. (전체 신규 제안)

### 1. 알고리즘 매매 (Execution Algos)
- [ ] **[분할 주문]** 대량 주문 시 충격을 줄이기 위한 TWAP(시간 가중 평균 가격) 또는 VWAP(거래량 가중 평균 가격) 알고리즘 추가.
- [ ] **[스마트 라우팅]** 호가창 스프레드 상태에 따른 지정가/시장가 동적 주문 라우팅 로직.

### 2. 시장 분석 고도화
- [ ] **[호가창 분석]** 호가 불균형(Orderbook Imbalance) 및 체결 강도 틱 단위 분석 지표 추가.
- [ ] **[시장 폭]** 상승/하락 종목 수(AD Line), 신고가/신저가 비율 등 마켓 타이밍 지표 서비스 구축.

---

## Ⅴ. 🧠 전략 고도화 및 백테스팅 (Strategy & Backtesting)
전략의 승률을 높이고 검증 체계를 정밀화하는 작업입니다.

### 1. 백테스트 엔진의 현실화
- [ ] **[가상 브로커]** 기존 전략 코드를 재사용하는 Mock Broker 개발.
- [ ] **[슬리피지 모델링]** 실제 호가 상황을 반영한 슬리피지 및 수수료 정밀 계산 기능 (신규 제안).
- [ ] **[편향 제거]** 상장폐지 종목 데이터를 포함한 생존자 편향(Survivorship Bias) 방지 로직 (신규 제안).

### 2. 전략 관리 및 AI 확장
- [ ] **[오닐 스퀴즈 V3]** 업종 소분류 스코어링 및 실시간 고래 탐지(5,000만 원 이상) 진입 로직.
- [ ] **[성과 모니터링]** 전략 노후화(Strategy Decay) 감지: 실거래 성과가 백테스트 통계치를 이탈할 경우 자동 관망 모드 전환 (신규 제안).
- [ ] **[AI 연동]** 뉴스/종목 분석 기능을 위한 AI(LLM) 연동 인터페이스 추가.

---

## Ⅵ. ✨ 웹 UI 및 사용자 경험 (Web UI & UX)
- [ ] **[현재가차트]** 종목 검색 History View 추가.
- [ ] **[백테스트 시각화]** 웹 UI(/virtual)에 MDD, 샤프 지수, 수익률 곡선 시각화 위젯 연동.
- [ ] **[알림]** 텔레그램/웹 푸시를 통한 전략 진입/청산 및 시스템 에러 실시간 알림.


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