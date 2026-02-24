# To-Do List (개선 계획 - 우선순위)

## Ⅰ. 최우선 개선 (High Priority)
이 항목들은 애플리케이션의 핵심 기능 안정성, 데이터 무결성, 그리고 기본적인 성능 및 개발 효율성에 직접적인 영향을 미칩니다.

### 0. 불량

1. TokenInvalidate 기능 사용시 API 호출하도록 수정.
   (모의 Token이 Invalid면 새로 안받는 버그 있음. 실전도 동일한지 확인 필요.)
2. WebsocketAPi도 TRID_Provider, UrlProvider, HeaderProvdier 적용하기.
3. 1초 넘는 tc 들 점검.
4. api 확인해서 todo_list에 넣기
5. momentum_backtest 정상작동 확인
6. tr_ids_config.yaml과 kis_config.yaml에 있는 tr_id, url을 (실전,모의) tuple로 바꾸고 모의에서 불가능한건 비워놓고 없으면 못쓰는 방식으로 수정하자.
7. token을 무효화하고 바로 요청하면 못받아옴. server로부터 1분 대기시간이 필요한것으로보임.
8. ohlcv 활용한 전략 추가
9. [진행중] IndicatorService(MA20, 52주 고가, 거래대금(=가격×거래량) 등 파생 지표 계산 전용)
10. (옵션) MarketDataRepository / DataStore:
최근에 받은 OHLCV/호가/스냅샷을 메모리/파일 캐시로 보관 (Web API 레벨 캐싱 구현 완료)
“API 다시 부르지 말고 기존 값 쓰자” 요구사항을 충족 (Throttling 및 Fallback 캐시 적용 완료)
StockQueryService: 앱 레벨 오케스트레이션
필요 시 Repository에서 데이터 꺼내거나(없으면 TradingService로 fetch)
IndicatorService로 계산 → 데이터만 반환
12. inquire_time_dailychartprice 의 캐시동작 확인필요
13. VolmueBreakOut에서 수수료도 적용하여 수익률 계산

### 1. 환경 (Environment)

### 2. 성능 (Performance)
* **[개선 필요]** 시장이 닫혔으면 스레드를 통해 전체 종목을 백그라운드로 업데이트하여 RAM에 올려두게 하기.

### 3. 오류 처리 (Error Handling)
* **[강화]** API 응답 검증 강화: `_handle_response` 및 API 응답에서 `output` 데이터의 존재 여부 및 예상 형식에 대한 명시적인 검증 추가.
* **[일관성]** 로그 메시지의 일관성: 모든 중요한 예외 상황에서 `exc_info=True`를 사용하여 스택 트레이스를 일관되게 기록.

### 4. API 상호작용 (API Interaction)
* **[세분화]** 재시도 로직의 세분화: API 응답 코드 또는 오류 유형에 따라 재시도 횟수나 지연 시간을 동적으로 조절하는 백오프(backoff) 전략 구현.

### 5. 테스트 (Tests)
* **[개선 필요]** 실행시간이 오래걸리는 (10초는 너무 김) TC 개선필요 - sleep이 들어가있음.
* **[개선 필요]** 코드 커버리지 100% 달성.

## Ⅱ. 중간 우선순위 (Medium Priority)
이 항목들은 코드의 유지보수성, 개발 효율성, 그리고 장기적인 안정성을 개선하는 데 중요합니다. 최우선 개선 사항들이 해결된 후 진행하는 것이 좋습니다.

### 1. 코드 구조 및 모듈성 (Code Structure & Modularity)
* **[리팩토링]** `trading_app.py`의 초기화 책임 분리: `_complete_api_initialization` 내 과중한 초기화 로직을 별도의 팩토리 함수나 세분화된 단계로 분리.
* **[명확화]** `BrokerAPIWrapper`의 역할 명확화: 증권사 API 추상화에 집중하고, `KoreaInvestApiClient`는 직접적인 API 호출을 담당하도록 역할 분리.
* **[개선]** 콜백 핸들링 개선: `KoreaInvestWebSocketAPI` 내 `on_realtime_message_callback`에서 직접 `print` 문 대신 `CLIView`와 같은 UI 레이어로 메시지 전달 분리.

### 2. 로깅 (Logging)
* **[관리]** 로그 파일 관리: 로그 회전(log rotation) 기능 또는 날짜별/크기별 로그 파일 관리 전략 추가.
* **[세분화]** 로그 상세 수준: `DEBUG` 레벨 로그 세분화 또는 특정 모듈에 대한 상세 로깅 제어 기능 추가.

### 3. 코드 가독성 및 유지보수성 (Code Readability & Maintainability)
* **[강화]** 타입 힌트 강화: 모든 함수 인자 및 반환 값에 타입 힌트를 일관되게 적용하고, `Any` 타입을 구체적인 타입으로 변경.
* **[제거]** 매직 넘버/문자열 제거: 반복적으로 사용되는 상수 값들을 별도의 Enum이나 상수로 정의.
* **[전환]** `print` 문의 로거 전환: 사용자에게 정보를 표시하는 `print` 문을 `logger.info` 또는 `cli_view.display_message`와 같은 로깅/뷰 계층 메서드로 전환.

## Ⅲ. 신규 기능 및 장기 계획 (Lower Priority / New Features & Long-term)
이 항목들은 애플리케이션의 가치를 확장하거나 장기적인 비전을 위한 것으로, 위의 우선순위 항목들이 충분히 안정화된 후에 고려하는 것이 좋습니다.

### 1. 기능 (Features)
* **[신규 기능]** 전략 스케줄러 기능의 개선 (by claude)
* **[신규 기능]** 차트에서 Y축의 비율이 있더라도, 0이 중간에 껴있으면 무조건 표기하도록 수정. (모든 data가 양수이거나, 음수일 경우에는 따로 표시할 필요는 없음)
* **[신규 기능]** 현재 계좌잔고/관심종목에 포함되에있는 종목들은 APP 시작시 자동으로 프로그램매매실시간동향 구독하도록 하는 기능 추가.
* **[신규 기능]** 각 tab들을 독립적인 page로 분리.
* **[신규 기능]** 시스템에서 발생하는 event를 알려주는 알림창(expand 될수있는)을 우측상단에 추가
* **[신규 기능]** NXT 시장도 포함하도록 개선.
* **[신규 기능]** (주문/계좌) 
* 주식주문(신용) 
* 주식주문(정정취소)<
* 주식정정취소가능주문조회<
* 주식일별주문체결조회
* 매수가능조회<
* 매도가능수량조회< 
* 신용매수가능조회
* 주식예약주문<
* 주식예약주문정정취소<
* 주식예약주문조회<
* 주식잔고조회_실현손익
* 투자계좌자산현황조회
* 기간별손익일별합산조회
* 기간별매매손익현황조회
* 주식통합증거금 
* 기간별계좌권리현황조회 
* **[신규 기능]** 외국인 순매수 상위 종목 조회 기능 추가.
* **[신규 기능]** Kis Developers API 문서 크롤링해서 API의 tr_id, url, Header, Params, Body를 최신으로 업데이트 할 수 있는 기능 추가 
* **[신규 기능]** Android App으로 거래결과, 서치 결과 알림 기능 추가. 


### 2. 전략 (Strategy)
* **[탐색 필요]** 다른 전략 탐색 (GPT 추천).
* RVOLBreakout1020 전략 / 백테스트 추가
* ConsolidationScanner 기능 추가.

### 3. 테스트 (Tests)
* **[확장 필요]** 통합 테스트의 범위 확장: 실제 API 호출을 포함하는 제한된 통합 테스트 추가 (외부 API 안정성 보장 시).
* **[개선 필요]** Mock 객체의 일관성: 공통 픽스처 활용 또는 Mock 설정 유틸리티를 통해 Mock 객체 설정 중복 제거.

### 4. 인프라 및 아키텍처 (Infrastructure & Architecture)
* **[인프라]** 도커(Docker) 컨테이너화: `Dockerfile` 및 `docker-compose.yml` 작성을 통해 서버 배포 용이성 확보 및 로컬/서버 환경 불일치(OS 의존성 등) 문제 해결.
* **[데이터]** DB(SQLite/SQLAlchemy) 도입: 매매 일지(Trade Journal) 영구 저장 및 봇 비정상 종료 시 재시작 후 상태 복구(State Recovery) 기능 구현.
* **[안정성]** Pydantic 도입: `config.yaml` 로드 및 API 응답 데이터 처리 시 Pydantic 모델을 사용하여 유효성 검사(Validation) 및 타입 안정성 강화 (런타임 에러 방지).
* **[아키텍처]** 이벤트 기반 아키텍처(Event-Driven): '전략(Signal)'과 '주문 실행(Execution Engine)'의 완전한 분리. 이를 통해 백테스팅 신뢰도를 높이고 향후 복합 주문 처리(Netting) 등의 고도화 기반 마련.
