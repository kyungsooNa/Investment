# To-Do List (개선 계획 - 우선순위)

## Ⅰ. 최우선 개선 (High Priority)
이 항목들은 애플리케이션의 핵심 기능 안정성, 데이터 무결성, 그리고 기본적인 성능 및 개발 효율성에 직접적인 영향을 미칩니다.

### 0. 불량
* **[개선 필요]** 

0. 실전, 모의 에 대한 토큰 파일 이름 다르게 저장해서 분리하기
1. 테스트 로그가 실제로그 폴더에 남음
2. 상한가 전체종목 조회는 너무 오래걸림 (6분)
### 실전

8.
Traceback (most recent call last):
  File "C:\Users\Kyungsoo\Documents\Code\Investment\main.py", line 14, in main
    await app.run_async() # <--- run_async 메서드 호출 (비동기)
  File "C:\Users\Kyungsoo\Documents\Code\Investment\trading_app.py", line 529, in run_async
    running = await self._execute_action(choice)
  File "C:\Users\Kyungsoo\Documents\Code\Investment\trading_app.py", line 249, in _execute_action
    await self.stock_query_service.handle_get_time_concluded_prices(stock_code)
  File "C:\Users\Kyungsoo\Documents\Code\Investment\app\stock_query_service.py", line 431, in handle_get_time_concluded_prices
    response = await self.trading_service.get_time_concluded_prices(stock_code)
  File "C:\Users\Kyungsoo\Documents\Code\Investment\services\trading_service.py", line 412, in get_time_concluded_prices
    return await self._broker_api_wrapper.get_time_concluded_prices(stock_code)
  File "C:\Users\Kyungsoo\Documents\Code\Investment\brokers\broker_api_wrapper.py", line 104, in get_time_concluded_prices
    return await self._client.get_time_concluded_prices(stock_code)
  File "C:\Users\Kyungsoo\Documents\Code\Investment\brokers\korea_investment\korea_invest_client.py", line 121, in get_time_concluded_prices
    return await self._quotations.get_time_concluded_prices(stock_code)
  File "C:\Users\Kyungsoo\Documents\Code\Investment\brokers\korea_investment\korea_invest_quotations_api.py", line 540, in get_time_concluded_prices
    response: ResCommonResponse = await self.call_api("GET", path, params=params, retry_count=1)
  File "C:\Users\Kyungsoo\Documents\Code\Investment\brokers\korea_investment\korea_invest_api_base.py", line 60, in call_api
    return ResCommonResponse(
TypeError: 'NoneType' object is not callable

call_api 호출이 안됨.

9.
실패: 005930 종목 뉴스 조회. (API 응답 파싱 실패 또는 처리 불가능)
2025-07-22 09:43:01,620 - INFO - Handler - 005930 종목 뉴스 조회 요청
2025-07-22 09:43:01,621 - INFO - Service - 005930 종목 뉴스 조회 요청
2025-07-22 09:43:01,621 - INFO - 005930 종목 뉴스 조회 시도...
2025-07-22 09:43:01,652 - ERROR - HTTP 오류 발생: 404 - 
2025-07-22 09:43:01,653 - ERROR - 복구 불가능한 오류 발생: https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/news/inquire-item-news, 응답: 
2025-07-22 09:43:01,654 - WARNING - 005930 종목 뉴스 조회 실패: API 응답 파싱 실패 또는 처리 불가능
2025-07-22 09:43:01,654 - ERROR - 005930 종목 뉴스 조회 실패: API 응답 파싱 실패 또는 처리 불가능
2025-07-22 09:43:01,655 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 09:43:01 KST+0900)
call_api 호출시 404 뜸.

10.
2025-07-22 10:53:14,393 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 10:53:14 KST+0900)
2025-07-22 10:54:27,197 - INFO - Handler - 133690 ETF 정보 조회 요청
2025-07-22 10:54:27,198 - INFO - Service - 133690 ETF 정보 조회 요청
2025-07-22 10:54:27,198 - INFO - 133690 ETF 정보 조회 시도...
2025-07-22 10:54:31,465 - ERROR - HTTP 오류 발생: 404 - 
2025-07-22 10:54:49,437 - ERROR - 복구 불가능한 오류 발생: https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-etf-product-info, 응답: 
2025-07-22 10:54:49,445 - WARNING - 133690 ETF 조회 실패: API 응답 파싱 실패 또는 처리 불가능
2025-07-22 10:54:49,446 - ERROR - 133690 ETF 정보 조회 실패: API 응답 파싱 실패 또는 처리 불가능
2025-07-22 10:54:49,451 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 10:54:49 KST+0900)
call_api 에서 404 뜸.

11. 
2025-07-22 09:45:20,384 - INFO - '미래' 키워드로 종목 검색 시도...
2025-07-22 09:45:20,435 - ERROR - HTTP 오류 발생: 404 - 
2025-07-22 09:45:20,782 - ERROR - 복구 불가능한 오류 발생: https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/search/search-stock-info, 응답: 
2025-07-22 09:45:20,783 - WARNING - 종목 검색 실패: API 응답 파싱 실패 또는 처리 불가능
2025-07-22 09:45:20,783 - ERROR - 종목 검색 실패: API 응답 파싱 실패 또는 처리 불가능
2025-07-22 09:45:20,786 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 09:45:20 KST+0900)
call_api 에서 404 뜸.

12.
2025-07-22 09:45:20,786 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 09:45:20 KST+0900)
2025-07-22 09:47:13,133 - INFO - Handler - 상승률 상위 종목 조회 요청
2025-07-22 09:47:13,133 - INFO - Service - 상승률 상위 종목 조회 요청
2025-07-22 09:47:13,134 - INFO - 상승률 상위 종목 조회 시도...
2025-07-22 09:47:13,164 - ERROR - HTTP 오류 발생: 404 - 
2025-07-22 09:47:13,165 - ERROR - 복구 불가능한 오류 발생: https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/ranking/inquire-rise, 응답: 
2025-07-22 09:47:13,166 - WARNING - 상승률 상위 조회 실패: API 응답 파싱 실패 또는 처리 불가능
2025-07-22 09:47:13,166 - ERROR - 상승률 상위 종목 조회 실패: API 응답 파싱 실패 또는 처리 불가능
2025-07-22 09:47:13,168 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 09:47:13 KST+0900)
call_api 에서 404 뜸.

14. 
2025-07-22 09:48:07,639 - INFO - 시가총액 1~10위 종목 현재가 조회 성공 및 결과 반환.
2025-07-22 09:48:07,639 - ERROR - 시가총액 1~10위 종목 현재가 조회 중 오류 발생: 'ResMarketCapStockItem' object has no attribute 'get'
2025-07-22 09:48:07,640 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 09:48:07 KST+0900)

15.
2025-07-22 09:48:41,573 - INFO - Service - 시가총액 상위 500개 종목 중 상한가 종목 조회 요청
2025-07-22 09:48:41,574 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 09:48:41 KST+0900)
2025-07-22 09:48:41,574 - WARNING - [경고] count 파라미터가 명시되지 않아 기본값 10을 사용합니다. market_code=0000
2025-07-22 09:48:41,574 - INFO - Service - 시가총액 상위 종목 조회 요청 - 시장: 0000, 개수: 10
2025-07-22 09:48:41,574 - INFO - 시가총액 상위 종목 조회 시도 (시장코드: 0000, 요청개수: 10)
2025-07-22 09:48:41,613 - INFO - API로부터 수신한 종목 수: 10
2025-07-22 09:48:41,614 - INFO - Service - 005930 현재가 조회 요청
2025-07-22 09:48:41,614 - INFO - 005930 현재가 조회 시도...
2025-07-22 09:48:41,628 - ERROR - 상한가 종목 조회 중 예기치 않은 오류 발생: 'dict' object has no attribute 'prdy_vrss_sign'
2025-07-22 09:48:41,630 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 09:48:41 KST+0900)

18.

2025-07-22 10:00:26,586 - INFO - StockQueryService - 실시간 스트림 요청: 종목=['006800'], 필드=['price'], 시간=30s
2025-07-22 10:00:26,587 - INFO - 실시간 스트림 시작 - 종목: ['006800'], 필드: ['price'], 시간: 30s
2025-07-22 10:00:26,587 - INFO - 웹소켓 접속키 발급 시도...
2025-07-22 10:00:27,217 - INFO - 웹소켓 접속키 발급 성공: 71c2fb04-e...
2025-07-22 10:00:27,218 - INFO - 웹소켓 연결 시작: ws://ops.koreainvestment.com:21000
2025-07-22 10:00:27,285 - INFO - 웹소켓 연결 성공.
2025-07-22 10:00:27,285 - INFO - 종목 006800 실시간 체결 데이터 구독 요청 (H0STCNT0)...
2025-07-22 10:00:27,285 - INFO - 실시간 요청 전송: TR_ID=H0STCNT0, TR_KEY=006800, TYPE=1
2025-07-22 10:00:27,289 - ERROR - 실시간 요청 응답 오류: TR_KEY=, RT_CD=9, MSG=JSON PARSING ERROR : header not found
2025-07-22 10:00:27,668 - ERROR - 실시간 요청 응답 오류: TR_KEY=, RT_CD=9, MSG=JSON PARSING ERROR : invalid tr_key
2025-07-22 10:00:37,297 - INFO - PINGPONG 수신됨. PONG 응답.
2025-07-22 10:00:57,312 - INFO - PINGPONG 수신됨. PONG 응답.
2025-07-22 10:00:57,562 - INFO - 종목 006800 실시간 체결 데이터 구독 해지 요청 (H0STCNT0)...
2025-07-22 10:00:57,562 - INFO - 실시간 요청 전송: TR_ID=H0STCNT0, TR_KEY=006800, TYPE=2
2025-07-22 10:00:57,562 - INFO - 웹소켓 연결 종료 요청.
2025-07-22 10:00:57,584 - ERROR - 실시간 요청 응답 오류: TR_KEY=, RT_CD=9, MSG=JSON PARSING ERROR : header not found
2025-07-22 10:00:57,586 - ERROR - 웹소켓 연결이 예외적으로 종료되었습니다: sent 1000 (OK); no close frame received
2025-07-22 10:00:57,606 - INFO - 웹소켓 연결 종료 완료.
2025-07-22 10:00:57,606 - INFO - 실시간 스트림 종료
2025-07-22 10:00:57,607 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 10:00:57 KST+0900)

20.

2025-07-22 10:01:21,427 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 10:01:21 KST+0900)
2025-07-22 10:01:21,428 - WARNING - [경고] count 파라미터가 명시되지 않아 기본값 10을 사용합니다. market_code=0000
2025-07-22 10:01:21,428 - INFO - Service - 시가총액 상위 종목 조회 요청 - 시장: 0000, 개수: 10
2025-07-22 10:01:21,429 - INFO - 시가총액 상위 종목 조회 시도 (시장코드: 0000, 요청개수: 10)
2025-07-22 10:01:21,522 - INFO - API로부터 수신한 종목 수: 10
2025-07-22 10:01:21,523 - ERROR - 모멘텀 전략 실행 중 오류 발생: 'NoneType' object has no attribute 'get_price_summary'
Traceback (most recent call last):
  File "C:\Users\Kyungsoo\Documents\Code\Investment\trading_app.py", line 375, in _execute_action
    result = await executor.execute(top_stock_codes)
  File "C:\Users\Kyungsoo\Documents\Code\Investment\strategies\strategy_executor.py", line 10, in execute
    return await self.strategy.run(stock_codes)
  File "C:\Users\Kyungsoo\Documents\Code\Investment\strategies\momentum_strategy.py", line 31, in run
    summary : ResCommonResponse = await self.broker.get_price_summary(code)  # ✅ wrapper 통해 조회
AttributeError: 'NoneType' object has no attribute 'get_price_summary'
2025-07-22 10:01:21,561 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 10:01:21 KST+0900)

21. 22.
시가총액 상위종목에서 전체로 변경.

98. 토큰 무효화 했지만 정상동작하는걸로 보임.
99. 

### 모의
2.
FATAL ERROR: 애플리케이션 실행 중 치명적인 오류 발생: CLIView.display_account_balance_failure() takes 1 positional argument but 2 were given
Traceback (most recent call last):
  File "C:\Users\Kyungsoo\Documents\Code\Investment\main.py", line 14, in main
    await app.run_async() # <--- run_async 메서드 호출 (비동기)
  File "C:\Users\Kyungsoo\Documents\Code\Investment\trading_app.py", line 529, in run_async
    running = await self._execute_action(choice)
  File "C:\Users\Kyungsoo\Documents\Code\Investment\trading_app.py", line 222, in _execute_action
    self.cli_view.display_account_balance_failure(balance_response.msg1)
TypeError: CLIView.display_account_balance_failure() takes 1 positional argument but 2 were given

3.
2025-07-22 10:08:13,174 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 10:08:13 KST+0900)
2025-07-22 10:08:13,174 - INFO - Service - 주식 매수 주문 요청 - 종목: 005930, 수량: 1, 가격: 0
2025-07-22 10:08:13,799 - INFO - Hashkey 계산 성공: 1a313f9e70dce6b7c63164578818187bc98ff94de248d146d23f6903c938cd18
2025-07-22 10:08:13,799 - INFO - 주식 buy 주문 시도 - 종목: 005930, 수량: 1, 가격: 0
2025-07-22 10:08:13,872 - ERROR - HTTP 오류 발생: 500 - {"rt_cd":"1","msg_cd":"IGW00007","msg1":"MCA 전문바디 구성 중 오류가 발생하였습니다."}
2025-07-22 10:08:14,243 - ERROR - 복구 불가능한 오류 발생: https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/trading/order-cash, 응답: {"rt_cd":"1","msg_cd":"IGW00007","msg1":"MCA 전문바디 구성 중 오류가 발생하였습니다."}
2025-07-22 10:08:14,244 - ERROR - 매수 주문 실패: API 응답 파싱 실패 또는 처리 불가능
2025-07-22 10:08:14,247 - ERROR - 주식 매수 주문 실패: 종목=005930, 결과={'rt_cd': '101', 'msg1': 'API 응답 파싱 실패 또는 처리 불가능'}
2025-07-22 10:08:14,264 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 10:08:14 KST+0900)

4.
2025-07-22 10:09:05,406 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 10:09:05 KST+0900)
2025-07-22 10:09:05,406 - INFO - Service - 주식 매도 주문 요청 - 종목: 005930, 수량: 1, 가격: 0
2025-07-22 10:09:06,035 - INFO - Hashkey 계산 성공: 8a1720bd4a24571414da39123373689f59d57dc5a2982f71bd4dc2987972d817
2025-07-22 10:09:06,035 - INFO - 주식 sell 주문 시도 - 종목: 005930, 수량: 1, 가격: 0
2025-07-22 10:09:06,105 - ERROR - HTTP 오류 발생: 500 - {"rt_cd":"1","msg_cd":"IGW00007","msg1":"MCA 전문바디 구성 중 오류가 발생하였습니다."}
2025-07-22 10:09:06,106 - ERROR - 복구 불가능한 오류 발생: https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/trading/order-cash, 응답: {"rt_cd":"1","msg_cd":"IGW00007","msg1":"MCA 전문바디 구성 중 오류가 발생하였습니다."}
2025-07-22 10:09:06,108 - ERROR - 매도 주문 실패: API 응답 파싱 실패 또는 처리 불가능
2025-07-22 10:09:06,109 - ERROR - 주식 매도 주문 실패: 종목=005930, 결과={'rt_cd': '101', 'msg1': 'API 응답 파싱 실패 또는 처리 불가능'}
2025-07-22 10:09:06,110 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 10:09:06 KST+0900)

7.
2025-07-22 10:09:33,379 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 10:09:33 KST+0900)
2025-07-22 10:09:45,253 - INFO - Handler - 005930 호가 정보 조회 요청
2025-07-22 10:09:45,254 - INFO - Service - 005930 종목 호가 정보 조회 요청
2025-07-22 10:09:45,254 - INFO - 005930 종목 호가잔량 조회 시도...
2025-07-22 10:09:45,305 - ERROR - 예상치 못한 예외 발생: 'output'
2025-07-22 10:09:45,307 - ERROR - 모든 재시도 실패, API 호출 종료
2025-07-22 10:09:45,308 - WARNING - 005930 호가 정보 조회 실패: 최대 재시도 횟수 초과
2025-07-22 10:09:45,308 - ERROR - 005930 호가 정보 조회 실패: 최대 재시도 횟수 초과

8.
2025-07-22 10:09:45,313 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 10:09:45 KST+0900)
2025-07-22 10:10:21,597 - INFO - Handler - 005930 시간대별 체결가 조회 요청
2025-07-22 10:10:21,598 - INFO - Service - 005930 종목 시간대별 체결가 조회 요청
2025-07-22 10:10:21,598 - INFO - 005930 종목 체결가 조회 시도...
2025-07-22 10:10:21,627 - ERROR - HTTP 오류 발생: 404 - 
2025-07-22 10:10:21,628 - ERROR - 복구 불가능한 오류 발생: https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/quotations/inquire-time-itemconclude, 응답: 
2025-07-22 10:10:21,629 - WARNING - 005930 체결가 정보 조회 실패: API 응답 파싱 실패 또는 처리 불가능
2025-07-22 10:10:21,629 - ERROR - 005930 시간대별 체결가 조회 실패: API 응답 파싱 실패 또는 처리 불가능

9.
2025-07-22 10:10:46,327 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 10:10:46 KST+0900)
2025-07-22 10:11:01,422 - INFO - Handler - 005930 종목 뉴스 조회 요청
2025-07-22 10:11:01,423 - INFO - Service - 005930 종목 뉴스 조회 요청
2025-07-22 10:11:01,423 - INFO - 005930 종목 뉴스 조회 시도...
2025-07-22 10:11:01,462 - ERROR - HTTP 오류 발생: 404 - 
2025-07-22 10:11:01,464 - ERROR - 복구 불가능한 오류 발생: https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/news/inquire-item-news, 응답: 
2025-07-22 10:11:01,465 - WARNING - 005930 종목 뉴스 조회 실패: API 응답 파싱 실패 또는 처리 불가능
2025-07-22 10:11:01,465 - ERROR - 005930 종목 뉴스 조회 실패: API 응답 파싱 실패 또는 처리 불가능

10.
2025-07-22 10:11:01,466 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 10:11:01 KST+0900)
2025-07-22 10:11:47,974 - INFO - Handler - 133690 ETF 정보 조회 요청
2025-07-22 10:11:47,975 - INFO - Service - 133690 ETF 정보 조회 요청
2025-07-22 10:11:47,975 - INFO - 133690 ETF 정보 조회 시도...
2025-07-22 10:11:48,005 - ERROR - HTTP 오류 발생: 404 - 
2025-07-22 10:11:48,006 - ERROR - 복구 불가능한 오류 발생: https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/quotations/inquire-etf-product-info, 응답: 
2025-07-22 10:11:48,007 - WARNING - 133690 ETF 조회 실패: API 응답 파싱 실패 또는 처리 불가능
2025-07-22 10:11:48,007 - ERROR - 133690 ETF 정보 조회 실패: API 응답 파싱 실패 또는 처리 불가능

11.
2025-07-22 10:11:48,008 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 10:11:48 KST+0900)
2025-07-22 10:12:23,770 - INFO - Handler - '미래' 키워드 종목 검색 요청
2025-07-22 10:12:23,771 - INFO - Service - '미래' 키워드로 종목 검색 요청
2025-07-22 10:12:23,771 - INFO - '미래' 키워드로 종목 검색 시도...
2025-07-22 10:12:23,804 - ERROR - HTTP 오류 발생: 404 - 
2025-07-22 10:12:23,805 - ERROR - 복구 불가능한 오류 발생: https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/search/search-stock-info, 응답: 
2025-07-22 10:12:23,806 - WARNING - 종목 검색 실패: API 응답 파싱 실패 또는 처리 불가능
2025-07-22 10:12:23,806 - ERROR - 종목 검색 실패: API 응답 파싱 실패 또는 처리 불가능

12.
2025-07-22 10:12:23,807 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 10:12:23 KST+0900)
2025-07-22 10:12:43,750 - INFO - Handler - 상승률 상위 종목 조회 요청
2025-07-22 10:12:43,751 - INFO - Service - 상승률 상위 종목 조회 요청
2025-07-22 10:12:43,751 - INFO - 상승률 상위 종목 조회 시도...
2025-07-22 10:12:43,781 - ERROR - HTTP 오류 발생: 404 - 
2025-07-22 10:12:43,782 - ERROR - 복구 불가능한 오류 발생: https://openapivts.koreainvestment.com:29443/uapi/domestic-stock/v1/ranking/inquire-rise, 응답: 
2025-07-22 10:12:43,783 - WARNING - 상승률 상위 조회 실패: API 응답 파싱 실패 또는 처리 불가능
2025-07-22 10:12:43,783 - ERROR - 상승률 상위 종목 조회 실패: API 응답 파싱 실패 또는 처리 불가능

13.
FATAL ERROR: 애플리케이션 실행 중 치명적인 오류 발생: 'CLIView' object has no attribute 'display_warning_paper_trading_not_supported'
Traceback (most recent call last):
  File "C:\Users\Kyungsoo\Documents\Code\Investment\main.py", line 14, in main
    await app.run_async() # <--- run_async 메서드 호출 (비동기)
  File "C:\Users\Kyungsoo\Documents\Code\Investment\trading_app.py", line 529, in run_async
    running = await self._execute_action(choice)
  File "C:\Users\Kyungsoo\Documents\Code\Investment\trading_app.py", line 266, in _execute_action
    self.cli_view.display_warning_paper_trading_not_supported("시가총액 상위 종목 조회")
AttributeError: 'CLIView' object has no attribute 'display_warning_paper_trading_not_supported'

14.
FATAL ERROR: 애플리케이션 실행 중 치명적인 오류 발생: 'CLIView' object has no attribute 'display_warning_paper_trading_not_supported'
Traceback (most recent call last):
  File "C:\Users\Kyungsoo\Documents\Code\Investment\main.py", line 14, in main
    await app.run_async() # <--- run_async 메서드 호출 (비동기)
  File "C:\Users\Kyungsoo\Documents\Code\Investment\trading_app.py", line 529, in run_async
    running = await self._execute_action(choice)
  File "C:\Users\Kyungsoo\Documents\Code\Investment\trading_app.py", line 271, in _execute_action
    self.cli_view.display_warning_paper_trading_not_supported("시가총액 1~10위 종목 조회")
AttributeError: 'CLIView' object has no attribute 'display_warning_paper_trading_not_supported'

18.
2025-07-22 10:16:51,206 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 10:16:51 KST+0900)
2025-07-22 10:17:02,265 - INFO - StockQueryService - 실시간 스트림 요청: 종목=['005930'], 필드=['price'], 시간=30s
2025-07-22 10:17:02,265 - INFO - 실시간 스트림 시작 - 종목: ['005930'], 필드: ['price'], 시간: 30s
2025-07-22 10:17:02,266 - INFO - 웹소켓 접속키 발급 시도...
2025-07-22 10:17:02,883 - INFO - 웹소켓 접속키 발급 성공: 09a6ee34-4...
2025-07-22 10:17:02,884 - INFO - 웹소켓 연결 시작: ws://ops.koreainvestment.com:31000
2025-07-22 10:17:02,957 - INFO - 웹소켓 연결 성공.
2025-07-22 10:17:02,957 - INFO - 종목 005930 실시간 체결 데이터 구독 요청 (H0STCNT0)...
2025-07-22 10:17:02,957 - INFO - 실시간 요청 전송: TR_ID=H0STCNT0, TR_KEY=005930, TYPE=1
2025-07-22 10:17:02,973 - ERROR - 실시간 요청 응답 오류: TR_KEY=, RT_CD=9, MSG=JSON PARSING ERROR : header not found
2025-07-22 10:17:03,345 - ERROR - 실시간 요청 응답 오류: TR_KEY=, RT_CD=9, MSG=JSON PARSING ERROR : invalid tr_key
2025-07-22 10:17:12,986 - INFO - PINGPONG 수신됨. PONG 응답.
2025-07-22 10:17:32,973 - INFO - PINGPONG 수신됨. PONG 응답.
2025-07-22 10:17:33,550 - INFO - 종목 005930 실시간 체결 데이터 구독 해지 요청 (H0STCNT0)...
2025-07-22 10:17:33,550 - INFO - 실시간 요청 전송: TR_ID=H0STCNT0, TR_KEY=005930, TYPE=2
2025-07-22 10:17:33,551 - INFO - 웹소켓 연결 종료 요청.
2025-07-22 10:17:33,557 - ERROR - 실시간 요청 응답 오류: TR_KEY=, RT_CD=9, MSG=JSON PARSING ERROR : header not found
2025-07-22 10:17:33,563 - ERROR - 실시간 요청 응답 오류: TR_KEY=, RT_CD=9, MSG=JSON PARSING ERROR : invalid tr_key
2025-07-22 10:17:33,564 - ERROR - 웹소켓 연결이 예외적으로 종료되었습니다: sent 1000 (OK); no close frame received
2025-07-22 10:17:33,566 - INFO - 웹소켓 연결 종료 완료.
2025-07-22 10:17:33,566 - INFO - 실시간 스트림 종료
2025-07-22 10:17:33,566 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 10:17:33 KST+0900)

22.
2025-07-22 10:18:51,586 - WARNING - Service - 시가총액 상위 종목 조회는 모의투자를 지원하지 않습니다.
2025-07-22 10:18:51,586 - ERROR - [GapUpPullback] 전략 실행 오류: 'str' object has no attribute '응답 형식 오류'
2025-07-22 10:18:51,591 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-22 10:18:51 KST+0900)



### 1. 환경 (Environment)
* **[개선 필요]** CacheManager 추가

### 2. 성능 (Performance)
* **[개선 필요]** 전체 종목 정보를 읽었으면 RAM에 유지하는 기능 추가 (시장이 닫혔을 경우).
* **[개선 필요]** 시장이 닫혔으면 스레드를 통해 전체 종목을 백그라운드로 업데이트하여 RAM에 올려두게 하기.
* **[최적화]** 반복적인 API 호출 최적화: `StockQueryService.handle_upper_limit_stocks`와 같이 반복적으로 개별 종목의 현재가를 조회하는 로직을 일괄 조회 또는 캐싱 전략으로 개선.

### 3. 오류 처리 (Error Handling)
* **[강화]** API 응답 검증 강화: `_handle_response` 및 API 응답에서 `output` 데이터의 존재 여부 및 예상 형식에 대한 명시적인 검증 추가.
* **[일관성]** 로그 메시지의 일관성: 모든 중요한 예외 상황에서 `exc_info=True`를 사용하여 스택 트레이스를 일관되게 기록.

### 4. API 상호작용 (API Interaction)
* **[일관성]** 동기/비동기 API 호출의 일관성: 모든 API 호출을 `httpx` 기반의 비동기 방식으로 통일.
* **[세분화]** 재시도 로직의 세분화: API 응답 코드 또는 오류 유형에 따라 재시도 횟수나 지연 시간을 동적으로 조절하는 백오프(backoff) 전략 구현.

### 5. 테스트 (Tests)
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
* **[신규 기능]** 거래량 상위 종목 조회 기능 추가.
* **[신규 기능]** 외국인 순매수 상위 종목 조회 기능 추가.
* **[신규 기능]** 전체 종목 중 거래대금 상위 10등 조회 기능 추가.
* **[신규 기능]** 웹 뷰어 생성.

### 2. 전략 (Strategy)
* **[탐색 필요]** 다른 전략 탐색 (GPT 추천).

### 3. 테스트 (Tests)
* **[확장 필요]** 통합 테스트의 범위 확장: 실제 API 호출을 포함하는 제한된 통합 테스트 추가 (외부 API 안정성 보장 시).
* **[개선 필요]** Mock 객체의 일관성: 공통 픽스처 활용 또는 Mock 설정 유틸리티를 통해 Mock 객체 설정 중복 제거.