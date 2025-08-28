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
9. IndicatorService(MA20, 52주 고가, 거래대금(=가격×거래량) 등 파생 지표 계산 전용)
10. (옵션) MarketDataRepository / DataStore:
최근에 받은 OHLCV/호가/스냅샷을 메모리/파일 캐시로 보관
“API 다시 부르지 말고 기존 값 쓰자” 요구사항을 충족
StockQueryService: 앱 레벨 오케스트레이션
필요 시 Repository에서 데이터 꺼내거나(없으면 TradingService로 fetch)
IndicatorService로 계산 → 데이터만 반환
12. inquire_time_dailychartprice 의 캐시동작 확인필요.
## 불량
### 실전

100. 101.
시가총액 상위종목에서 전체로 변경.


70.
2025-08-21 12:45:06,399 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-08-21 12:45:06 KST+0900)
2025-08-21 12:45:22,819 - INFO - StockQueryService - 실시간 스트림 요청: 종목=['005930'], 필드=['price'], 시간=30s
2025-08-21 12:45:22,820 - INFO - 실시간 스트림 시작 - 종목: ['005930'], 필드: ['price'], 시간: 30s
2025-08-21 12:45:22,820 - DEBUG - Bypass - connect_websocket 캐시 건너뜀
2025-08-21 12:45:22,820 - INFO - 웹소켓 접속키 발급 시도...
2025-08-21 12:45:23,501 - INFO - 웹소켓 접속키 발급 성공: e1158d3d-7...
2025-08-21 12:45:23,501 - INFO - 웹소켓 연결 시작: ws://ops.koreainvestment.com:21000
2025-08-21 12:45:23,613 - INFO - 웹소켓 연결 성공.
2025-08-21 12:45:23,613 - DEBUG - Bypass - subscribe_realtime_price 캐시 건너뜀
2025-08-21 12:45:23,614 - INFO - 종목 005930 실시간 체결 데이터 구독 요청 (H0STCNT0)...
2025-08-21 12:45:23,614 - INFO - 실시간 요청 전송: TR_ID=H0STCNT0, TR_KEY=005930, TYPE=1
2025-08-21 12:45:23,615 - INFO - 1.00초 동안 대기합니다 (동기).
2025-08-21 12:45:24,627 - ERROR - trading_service.py:447 - 실시간 스트림 처리 중 오류 발생: object NoneType can't be used in 'await' expression
2025-08-21 12:45:24,627 - DEBUG - Bypass - unsubscribe_realtime_price 캐시 건너뜀
2025-08-21 12:45:24,628 - INFO - 종목 005930 실시간 체결 데이터 구독 해지 요청 (H0STCNT0)...
2025-08-21 12:45:24,629 - INFO - 실시간 요청 전송: TR_ID=H0STCNT0, TR_KEY=005930, TYPE=2
2025-08-21 12:45:24,629 - DEBUG - Bypass - disconnect_websocket 캐시 건너뜀
2025-08-21 12:45:24,630 - INFO - 웹소켓 연결 종료 요청.
2025-08-21 12:45:24,650 - ERROR - korea_invest_websocket_api.py:234 - 실시간 요청 응답 오류: TR_KEY=, RT_CD=9, MSG=JSON PARSING ERROR : header not found
2025-08-21 12:45:24,652 - ERROR - korea_invest_websocket_api.py:234 - 실시간 요청 응답 오류: TR_KEY=, RT_CD=9, MSG=JSON PARSING ERROR : invalid tr_key
2025-08-21 12:45:24,654 - ERROR - korea_invest_websocket_api.py:234 - 실시간 요청 응답 오류: TR_KEY=, RT_CD=9, MSG=JSON PARSING ERROR : header not found
2025-08-21 12:45:24,656 - ERROR - korea_invest_websocket_api.py:122 - 웹소켓 연결이 예외적으로 종료되었습니다: sent 1000 (OK); no close frame received
2025-08-21 12:45:24,656 - INFO - 웹소켓 연결 종료 완료.
2025-08-21 12:45:24,657 - INFO - 실시간 스트림 종료

100. 
Traceback (most recent call last):
  File "C:\Users\Kyungsoo\Documents\Code\Investment\app\user_action_executor.py", line 329, in handle_momentum_strategy
    result = await executor.execute(top_stock_codes)
  File "C:\Users\Kyungsoo\Documents\Code\Investment\strategies\strategy_executor.py", line 11, in execute
    return await self.strategy.run(stock_codes)
  File "C:\Users\Kyungsoo\Documents\Code\Investment\strategies\momentum_strategy.py", line 32, in run
    summary : ResCommonResponse = await self.broker.get_price_summary(code)  # ✅ wrapper 통해 조회[오류] 전략 실행 중 문제 발생: 전략 실행 실패: 'NoneType' object has no attribute 'get_price_summary'
     현재 시각: 2025-08-21 12:45:33
[2025-08-21 12:45:33]


### 모의
70.
2025-08-21 12:49:15,659 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-08-21 12:49:15 KST+0900)
2025-08-21 12:49:23,537 - INFO - StockQueryService - 실시간 스트림 요청: 종목=['005930'], 필드=['price'], 시간=30s
2025-08-21 12:49:23,537 - INFO - 실시간 스트림 시작 - 종목: ['005930'], 필드: ['price'], 시간: 30s
2025-08-21 12:49:23,538 - DEBUG - Bypass - connect_websocket 캐시 건너뜀
2025-08-21 12:49:23,538 - INFO - 웹소켓 접속키 발급 시도...
2025-08-21 12:49:24,244 - INFO - 웹소켓 접속키 발급 성공: f7652b54-7...
2025-08-21 12:49:24,244 - INFO - 웹소켓 연결 시작: ws://ops.koreainvestment.com:31000
2025-08-21 12:49:24,335 - INFO - 웹소켓 연결 성공.
2025-08-21 12:49:24,335 - DEBUG - Bypass - subscribe_realtime_price 캐시 건너뜀
2025-08-21 12:49:24,335 - INFO - 종목 005930 실시간 체결 데이터 구독 요청 (H0STCNT0)...
2025-08-21 12:49:24,336 - INFO - 실시간 요청 전송: TR_ID=H0STCNT0, TR_KEY=005930, TYPE=1
2025-08-21 12:49:24,337 - INFO - 1.00초 동안 대기합니다 (동기).
2025-08-21 12:49:25,998 - ERROR - trading_service.py:447 - 실시간 스트림 처리 중 오류 발생: object NoneType can't be used in 'await' expression
2025-08-21 12:49:25,998 - DEBUG - Bypass - unsubscribe_realtime_price 캐시 건너뜀
2025-08-21 12:49:25,998 - INFO - 종목 005930 실시간 체결 데이터 구독 해지 요청 (H0STCNT0)...
2025-08-21 12:49:25,998 - INFO - 실시간 요청 전송: TR_ID=H0STCNT0, TR_KEY=005930, TYPE=2
2025-08-21 12:49:25,999 - DEBUG - Bypass - disconnect_websocket 캐시 건너뜀
2025-08-21 12:49:25,999 - INFO - 웹소켓 연결 종료 요청.
2025-08-21 12:49:26,005 - ERROR - korea_invest_websocket_api.py:234 - 실시간 요청 응답 오류: TR_KEY=, RT_CD=9, MSG=JSON PARSING ERROR : header not found
2025-08-21 12:49:26,007 - ERROR - korea_invest_websocket_api.py:234 - 실시간 요청 응답 오류: TR_KEY=, RT_CD=9, MSG=JSON PARSING ERROR : invalid tr_key
2025-08-21 12:49:26,009 - ERROR - korea_invest_websocket_api.py:234 - 실시간 요청 응답 오류: TR_KEY=, RT_CD=9, MSG=JSON PARSING ERROR : header not found
2025-08-21 12:49:26,011 - ERROR - korea_invest_websocket_api.py:122 - 웹소켓 연결이 예외적으로 종료되었습니다: sent 1000 (OK); no close frame received
2025-08-21 12:49:26,012 - INFO - 웹소켓 연결 종료 완료.
2025-08-21 12:49:26,012 - INFO - 실시간 스트림 종료
2025-08-21 12:49:26,012 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-08-21 12:49:26 KST+0900)


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
* **[신규 기능]** 거래대금 상위 종목 조회 기능 추가.
* **[신규 기능]** 웹 뷰어 생성.
* **[신규 기능]** Kis Developers API 문서 크롤링해서 API의 tr_id, url, Header, Params, Body를 최신으로 업데이트 할 수 있는 기능 추가 
* **[신규 기능]** Android App으로 거래결과, 서치 결과 알림 기능 추가. 


### 2. 전략 (Strategy)
* **[탐색 필요]** 다른 전략 탐색 (GPT 추천).
* RVOLBreakout1020 전략 / 백테스트 추가
* ConsolidationScanner 기능 추가.

### 3. 테스트 (Tests)
* **[확장 필요]** 통합 테스트의 범위 확장: 실제 API 호출을 포함하는 제한된 통합 테스트 추가 (외부 API 안정성 보장 시).
* **[개선 필요]** Mock 객체의 일관성: 공통 픽스처 활용 또는 Mock 설정 유틸리티를 통해 Mock 객체 설정 중복 제거.