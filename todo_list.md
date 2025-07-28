# To-Do List (개선 계획 - 우선순위)

## Ⅰ. 최우선 개선 (High Priority)
이 항목들은 애플리케이션의 핵심 기능 안정성, 데이터 무결성, 그리고 기본적인 성능 및 개발 효율성에 직접적인 영향을 미칩니다.

### 0. 불량
* **[개선 필요]** 

2. 상한가 전체종목 조회는 너무 오래걸림 (6분)
3. 2번 balance info 저장하는 ResType 생성
### 실전
3. DEBUG: Headers being sent:
2025-07-25 09:39:33,660 - DEBUG -   Content-Type: b'application/json'
2025-07-25 09:39:33,660 - DEBUG -   User-Agent: b'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36'
2025-07-25 09:39:33,660 - DEBUG -   charset: b'UTF-8'
2025-07-25 09:39:33,660 - DEBUG -   appkey: b'PSvrDBXImiV6W2k3MQLzToa7WYkYlRf4IZrt'
2025-07-25 09:39:33,660 - DEBUG -   appsecret: b'8xF4PL7QyLJYnte1mJdh8N3qq7e/D61oKeY2leXyTy0G0L/Z6djx1zUWMbvVKG7LCDJc/2uvtD7Nq2pewskcsH4qRpZInhj0As1RWg2TOQQT/1LC0WDu+oKPcUW79DeKpEtje+ZNDv9rwlhEYl+twofVh8gTklpHatVp6BDDX9KoKakDkPM='
2025-07-25 09:39:33,660 - DEBUG -   tr_id: b'TTTC0012U'
2025-07-25 09:39:33,660 - DEBUG -   custtype: b'P'
2025-07-25 09:39:33,661 - DEBUG -   gt_uid: b'8b795c882b9cdb7d3c3647bccf72430b'
2025-07-25 09:39:33,661 - DEBUG -   hashkey: b'a30ae3e9a5cc288bdeb436dbf0a3b6094615f276794d51693518a21bbefe8588'
2025-07-25 09:39:48,827 - WARNING - 🔁 토큰 만료 감지 (EGW00123). 재발급 후 1회 재시도
2025-07-25 09:39:51,430 - ERROR - korea_invest_api_base.py:103 - HTTP 오류 발생 (httpx): 403 - {"error_description":"접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)","error_code":"EGW00133"}
2025-07-25 09:39:51,432 - ERROR - korea_invest_api_base.py:80 - 모든 재시도 실패, API 호출 종료
2025-07-25 09:39:51,437 - ERROR - trading_service.py:139 - 매수 주문 실패: 최대 재시도 횟수 초과
2025-07-25 09:39:51,440 - ERROR - order_execution_service.py:32 - 주식 매수 주문 실패: 종목=005930, 결과={'rt_cd': '105', 'msg1': '최대 재시도 횟수 초과'}
2025-07-25 09:39:51,443 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-25 09:39:51 KST+0900)
2025-07-25 09:40:04,080 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-25 09:40:04 KST+0900)
2025-07-25 09:40:04,081 - INFO - Service - 주식 매수 주문 요청 - 종목: 005930, 수량: 1, 가격: 0
2025-07-25 09:40:04,758 - INFO - Hashkey 계산 성공: a30ae3e9a5cc288bdeb436dbf0a3b6094615f276794d51693518a21bbefe8588
2025-07-25 09:40:04,758 - INFO - 주식 buy 주문 시도 - 종목: 005930, 수량: 1, 가격: 0
2025-07-25 09:40:04,758 - DEBUG - API 호출 시도 1/1 - POST https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/trading/order-cash
2025-07-25 09:40:04,759 - DEBUG - 
DEBUG: Headers being sent:
2025-07-25 09:40:04,759 - DEBUG -   Content-Type: b'application/json'
2025-07-25 09:40:04,759 - DEBUG -   User-Agent: b'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36'
2025-07-25 09:40:04,759 - DEBUG -   charset: b'UTF-8'
2025-07-25 09:40:04,759 - DEBUG -   appkey: b'PSvrDBXImiV6W2k3MQLzToa7WYkYlRf4IZrt'
2025-07-25 09:40:04,760 - DEBUG -   appsecret: b'8xF4PL7QyLJYnte1mJdh8N3qq7e/D61oKeY2leXyTy0G0L/Z6djx1zUWMbvVKG7LCDJc/2uvtD7Nq2pewskcsH4qRpZInhj0As1RWg2TOQQT/1LC0WDu+oKPcUW79DeKpEtje+ZNDv9rwlhEYl+twofVh8gTklpHatVp6BDDX9KoKakDkPM='
2025-07-25 09:40:04,760 - DEBUG -   tr_id: b'TTTC0012U'
2025-07-25 09:40:04,760 - DEBUG -   custtype: b'P'
2025-07-25 09:40:04,760 - DEBUG -   gt_uid: b'ae87a0f023d7d6156b5c42f5e87dddd7'
2025-07-25 09:40:04,760 - DEBUG -   hashkey: b'a30ae3e9a5cc288bdeb436dbf0a3b6094615f276794d51693518a21bbefe8588'
2025-07-25 09:40:04,761 - DEBUG -   Authorization: b'Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJ0b2tlbiIsImF1ZCI6ImIwOTZmMGUxLTNmZmYtNDRhYy05MTg2LTUwNjFhOGJmOGFkMSIsInByZHRfY2QiOiIiLCJpc3MiOiJ1bm9ndyIsImV4cCI6MTc1MzQ5MDM2MiwiaWF0IjoxNzUzNDAzOTYyLCJqdGkiOiJQU2p4SllhYkZ0YUlQMjlISllvQ0hlTEtCSVR4eHY3ZzdudmcifQ.vqWfWsLRYHW_w4NAU0eEOrWUFgzRguIYlQuDoUpEPoq_QQ28Wn70P6BgIQYKhtYWyiHTjcxyAG4MEZcTZwG-WQ'
2025-07-25 09:40:21,376 - WARNING - 🔁 토큰 만료 감지 (EGW00123). 재발급 후 1회 재시도
2025-07-25 09:40:44,819 - ERROR - korea_invest_api_base.py:172 - HTTP 오류 발생: 500 - {"rt_cd":"1","msg1":"기간이 만료된 token 입니다.","msg_cd":"EGW00123"}
2025-07-25 09:40:56,230 - ERROR - korea_invest_api_base.py:58 - 복구 불가능한 오류 발생: https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/trading/order-cash, 응답: {"rt_cd":"1","msg1":"기간이 만료된 token 입니다.","msg_cd":"EGW00123"}

8.
2025-07-25 09:42:41,148 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-25 09:42:41 KST+0900)
2025-07-25 09:43:15,667 - INFO - Handler - 005930 시간대별 체결가 조회 요청
2025-07-25 09:43:15,668 - INFO - Service - 005930 종목 시간대별 체결가 조회 요청
2025-07-25 09:43:15,668 - INFO - 005930 종목 체결가 조회 시도...
2025-07-25 09:43:15,668 - DEBUG - API 호출 시도 1/1 - GET https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-time-itemconclude
2025-07-25 09:43:15,668 - DEBUG - 
DEBUG: Headers being sent:
2025-07-25 09:43:15,668 - DEBUG -   Content-Type: b'application/json'
2025-07-25 09:43:15,669 - DEBUG -   User-Agent: b'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36'
2025-07-25 09:43:15,669 - DEBUG -   charset: b'UTF-8'
2025-07-25 09:43:15,669 - DEBUG -   appkey: b'PSvrDBXImiV6W2k3MQLzToa7WYkYlRf4IZrt'
2025-07-25 09:43:15,669 - DEBUG -   appsecret: b'8xF4PL7QyLJYnte1mJdh8N3qq7e/D61oKeY2leXyTy0G0L/Z6djx1zUWMbvVKG7LCDJc/2uvtD7Nq2pewskcsH4qRpZInhj0As1RWg2TOQQT/1LC0WDu+oKPcUW79DeKpEtje+ZNDv9rwlhEYl+twofVh8gTklpHatVp6BDDX9KoKakDkPM='
2025-07-25 09:43:15,669 - DEBUG -   tr_id: b'FHKST01010300'
2025-07-25 09:43:15,669 - DEBUG -   custtype: b'P'
2025-07-25 09:43:15,669 - DEBUG -   Authorization: b'Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJ0b2tlbiIsImF1ZCI6Ijc2ODZjNjZiLWM1NDItNGFjMi05MmRhLWEwZGI5Y2ViZGExNCIsInByZHRfY2QiOiIiLCJpc3MiOiJ1bm9ndyIsImV4cCI6MTc1MzQ1Mzc1NSwiaWF0IjoxNzUzMzY3MzU1LCJqdGkiOiJQU3ZyREJYSW1pVjZXMmszTVFMelRvYTdXWWtZbFJmNElacnQifQ.bciMR_35oyIii2w6Ni0ek-o1vyi669KS9XJkC5kf1yValirnsBhRdQ4UOhuDkaE947-Sjf_cjXbe4kZrUsGbtQ'
2025-07-25 09:43:16,478 - ERROR - korea_invest_api_base.py:115 - JSON 디코딩 오류 발생
2025-07-25 09:43:16,480 - ERROR - korea_invest_api_base.py:80 - 모든 재시도 실패, API 호출 종료
2025-07-25 09:43:16,481 - WARNING - 005930 체결가 정보 조회 실패: 최대 재시도 횟수 초과
2025-07-25 09:43:16,484 - ERROR - stock_query_service.py:455 - 005930 시간대별 체결가 조회 실패: 최대 재시도 횟수 초과
2025-07-25 09:43:16,485 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-25 09:43:16 KST+0900)


9.
2025-07-25 09:44:07,960 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-25 09:44:07 KST+0900)
2025-07-25 09:44:40,219 - INFO - Handler - 005930 종목 뉴스 조회 요청
2025-07-25 09:44:40,219 - INFO - Service - 005930 종목 뉴스 조회 요청
2025-07-25 09:44:40,220 - INFO - 005930 종목 뉴스 조회 시도...
2025-07-25 09:44:40,220 - DEBUG - API 호출 시도 1/1 - GET https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/news/inquire-item-news
2025-07-25 09:44:40,220 - DEBUG - 
DEBUG: Headers being sent:
2025-07-25 09:44:40,220 - DEBUG -   Content-Type: b'application/json'
2025-07-25 09:44:40,220 - DEBUG -   User-Agent: b'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36'
2025-07-25 09:44:40,221 - DEBUG -   charset: b'UTF-8'
2025-07-25 09:44:40,221 - DEBUG -   appkey: b'PSvrDBXImiV6W2k3MQLzToa7WYkYlRf4IZrt'
2025-07-25 09:44:40,221 - DEBUG -   appsecret: b'8xF4PL7QyLJYnte1mJdh8N3qq7e/D61oKeY2leXyTy0G0L/Z6djx1zUWMbvVKG7LCDJc/2uvtD7Nq2pewskcsH4qRpZInhj0As1RWg2TOQQT/1LC0WDu+oKPcUW79DeKpEtje+ZNDv9rwlhEYl+twofVh8gTklpHatVp6BDDX9KoKakDkPM='
2025-07-25 09:44:40,221 - DEBUG -   tr_id: b'FHPST01040000'
2025-07-25 09:44:40,221 - DEBUG -   custtype: b'P'
2025-07-25 09:44:40,221 - DEBUG -   Authorization: b'Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJ0b2tlbiIsImF1ZCI6Ijc2ODZjNjZiLWM1NDItNGFjMi05MmRhLWEwZGI5Y2ViZGExNCIsInByZHRfY2QiOiIiLCJpc3MiOiJ1bm9ndyIsImV4cCI6MTc1MzQ1Mzc1NSwiaWF0IjoxNzUzMzY3MzU1LCJqdGkiOiJQU3ZyREJYSW1pVjZXMmszTVFMelRvYTdXWWtZbFJmNElacnQifQ.bciMR_35oyIii2w6Ni0ek-o1vyi669KS9XJkC5kf1yValirnsBhRdQ4UOhuDkaE947-Sjf_cjXbe4kZrUsGbtQ'
2025-07-25 09:44:40,254 - ERROR - korea_invest_api_base.py:115 - JSON 디코딩 오류 발생
2025-07-25 09:44:40,255 - ERROR - korea_invest_api_base.py:80 - 모든 재시도 실패, API 호출 종료
2025-07-25 09:44:40,255 - WARNING - 005930 종목 뉴스 조회 실패: 최대 재시도 횟수 초과
2025-07-25 09:44:40,256 - ERROR - stock_query_service.py:551 - 005930 종목 뉴스 조회 실패: 최대 재시도 횟수 초과
2025-07-25 09:44:40,257 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-25 09:44:40 KST+0900)


10.
2025-07-25 09:45:10,417 - INFO - Handler - 133690 ETF 정보 조회 요청
2025-07-25 09:45:10,417 - INFO - Service - 133690 ETF 정보 조회 요청
2025-07-25 09:45:10,417 - INFO - 133690 ETF 정보 조회 시도...
2025-07-25 09:45:10,418 - DEBUG - API 호출 시도 1/1 - GET https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-etf-product-info
2025-07-25 09:45:10,418 - DEBUG - 
DEBUG: Headers being sent:
2025-07-25 09:45:10,418 - DEBUG -   Content-Type: b'application/json'
2025-07-25 09:45:10,418 - DEBUG -   User-Agent: b'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36'
2025-07-25 09:45:10,418 - DEBUG -   charset: b'UTF-8'
2025-07-25 09:45:10,418 - DEBUG -   appkey: b'PSvrDBXImiV6W2k3MQLzToa7WYkYlRf4IZrt'
2025-07-25 09:45:10,419 - DEBUG -   appsecret: b'8xF4PL7QyLJYnte1mJdh8N3qq7e/D61oKeY2leXyTy0G0L/Z6djx1zUWMbvVKG7LCDJc/2uvtD7Nq2pewskcsH4qRpZInhj0As1RWg2TOQQT/1LC0WDu+oKPcUW79DeKpEtje+ZNDv9rwlhEYl+twofVh8gTklpHatVp6BDDX9KoKakDkPM='
2025-07-25 09:45:10,419 - DEBUG -   tr_id: b'FHKST05010100'
2025-07-25 09:45:10,419 - DEBUG -   custtype: b'P'
2025-07-25 09:45:10,419 - DEBUG -   Authorization: b'Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJ0b2tlbiIsImF1ZCI6Ijc2ODZjNjZiLWM1NDItNGFjMi05MmRhLWEwZGI5Y2ViZGExNCIsInByZHRfY2QiOiIiLCJpc3MiOiJ1bm9ndyIsImV4cCI6MTc1MzQ1Mzc1NSwiaWF0IjoxNzUzMzY3MzU1LCJqdGkiOiJQU3ZyREJYSW1pVjZXMmszTVFMelRvYTdXWWtZbFJmNElacnQifQ.bciMR_35oyIii2w6Ni0ek-o1vyi669KS9XJkC5kf1yValirnsBhRdQ4UOhuDkaE947-Sjf_cjXbe4kZrUsGbtQ'
2025-07-25 09:45:10,460 - ERROR - korea_invest_api_base.py:115 - JSON 디코딩 오류 발생
2025-07-25 09:45:10,461 - ERROR - korea_invest_api_base.py:80 - 모든 재시도 실패, API 호출 종료
2025-07-25 09:45:10,462 - WARNING - 133690 ETF 조회 실패: 최대 재시도 횟수 초과
2025-07-25 09:45:10,464 - ERROR - stock_query_service.py:576 - 133690 ETF 정보 조회 실패: 최대 재시도 횟수 초과
2025-07-25 09:45:10,464 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-25 09:45:10 KST+0900)


11. 
2025-07-25 09:45:36,433 - INFO - Handler - '미래' 키워드 종목 검색 요청
2025-07-25 09:45:36,433 - INFO - Service - '미래' 키워드로 종목 검색 요청
2025-07-25 09:45:36,433 - INFO - '미래' 키워드로 종목 검색 시도...
2025-07-25 09:45:36,433 - DEBUG - API 호출 시도 1/1 - GET https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/search/search-stock-info
2025-07-25 09:45:36,434 - DEBUG - 
DEBUG: Headers being sent:
2025-07-25 09:45:36,434 - DEBUG -   Content-Type: b'application/json'
2025-07-25 09:45:36,434 - DEBUG -   User-Agent: b'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36'
2025-07-25 09:45:36,434 - DEBUG -   charset: b'UTF-8'
2025-07-25 09:45:36,434 - DEBUG -   appkey: b'PSvrDBXImiV6W2k3MQLzToa7WYkYlRf4IZrt'
2025-07-25 09:45:36,435 - DEBUG -   appsecret: b'8xF4PL7QyLJYnte1mJdh8N3qq7e/D61oKeY2leXyTy0G0L/Z6djx1zUWMbvVKG7LCDJc/2uvtD7Nq2pewskcsH4qRpZInhj0As1RWg2TOQQT/1LC0WDu+oKPcUW79DeKpEtje+ZNDv9rwlhEYl+twofVh8gTklpHatVp6BDDX9KoKakDkPM='
2025-07-25 09:45:36,435 - DEBUG -   tr_id: b'FHKST01010400'
2025-07-25 09:45:36,435 - DEBUG -   custtype: b'P'
2025-07-25 09:45:36,435 - DEBUG -   Authorization: b'Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJ0b2tlbiIsImF1ZCI6Ijc2ODZjNjZiLWM1NDItNGFjMi05MmRhLWEwZGI5Y2ViZGExNCIsInByZHRfY2QiOiIiLCJpc3MiOiJ1bm9ndyIsImV4cCI6MTc1MzQ1Mzc1NSwiaWF0IjoxNzUzMzY3MzU1LCJqdGkiOiJQU3ZyREJYSW1pVjZXMmszTVFMelRvYTdXWWtZbFJmNElacnQifQ.bciMR_35oyIii2w6Ni0ek-o1vyi669KS9XJkC5kf1yValirnsBhRdQ4UOhuDkaE947-Sjf_cjXbe4kZrUsGbtQ'
2025-07-25 09:45:36,470 - ERROR - korea_invest_api_base.py:115 - JSON 디코딩 오류 발생
2025-07-25 09:45:36,471 - ERROR - korea_invest_api_base.py:80 - 모든 재시도 실패, API 호출 종료
2025-07-25 09:45:36,472 - WARNING - 종목 검색 실패: 최대 재시도 횟수 초과
2025-07-25 09:45:36,473 - ERROR - stock_query_service.py:483 - 종목 검색 실패: 최대 재시도 횟수 초과
2025-07-25 09:45:36,473 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-25 09:45:36 KST+0900)


12.
2025-07-25 09:45:57,344 - INFO - Handler - 상승률 상위 종목 조회 요청
2025-07-25 09:45:57,344 - INFO - Service - 상승률 상위 종목 조회 요청
2025-07-25 09:45:57,345 - INFO - 상승률 상위 종목 조회 시도...
2025-07-25 09:45:57,345 - DEBUG - API 호출 시도 1/1 - GET https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/ranking/inquire-rise
2025-07-25 09:45:57,346 - DEBUG - 
DEBUG: Headers being sent:
2025-07-25 09:45:57,346 - DEBUG -   Content-Type: b'application/json'
2025-07-25 09:45:57,346 - DEBUG -   User-Agent: b'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36'
2025-07-25 09:45:57,347 - DEBUG -   charset: b'UTF-8'
2025-07-25 09:45:57,347 - DEBUG -   appkey: b'PSvrDBXImiV6W2k3MQLzToa7WYkYlRf4IZrt'
2025-07-25 09:45:57,347 - DEBUG -   appsecret: b'8xF4PL7QyLJYnte1mJdh8N3qq7e/D61oKeY2leXyTy0G0L/Z6djx1zUWMbvVKG7LCDJc/2uvtD7Nq2pewskcsH4qRpZInhj0As1RWg2TOQQT/1LC0WDu+oKPcUW79DeKpEtje+ZNDv9rwlhEYl+twofVh8gTklpHatVp6BDDX9KoKakDkPM='
2025-07-25 09:45:57,347 - DEBUG -   tr_id: b'FHKUP03200000'
2025-07-25 09:45:57,347 - DEBUG -   custtype: b'P'
2025-07-25 09:45:57,347 - DEBUG -   Authorization: b'Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJ0b2tlbiIsImF1ZCI6Ijc2ODZjNjZiLWM1NDItNGFjMi05MmRhLWEwZGI5Y2ViZGExNCIsInByZHRfY2QiOiIiLCJpc3MiOiJ1bm9ndyIsImV4cCI6MTc1MzQ1Mzc1NSwiaWF0IjoxNzUzMzY3MzU1LCJqdGkiOiJQU3ZyREJYSW1pVjZXMmszTVFMelRvYTdXWWtZbFJmNElacnQifQ.bciMR_35oyIii2w6Ni0ek-o1vyi669KS9XJkC5kf1yValirnsBhRdQ4UOhuDkaE947-Sjf_cjXbe4kZrUsGbtQ'
2025-07-25 09:45:57,388 - ERROR - korea_invest_api_base.py:115 - JSON 디코딩 오류 발생
2025-07-25 09:45:57,389 - ERROR - korea_invest_api_base.py:80 - 모든 재시도 실패, API 호출 종료
2025-07-25 09:45:57,390 - WARNING - 상승률 상위 조회 실패: 최대 재시도 횟수 초과
2025-07-25 09:45:57,391 - ERROR - stock_query_service.py:524 - 상승률 상위 종목 조회 실패: 최대 재시도 횟수 초과
2025-07-25 09:45:57,391 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-25 09:45:57 KST+0900)


14. 
가격 포멧 수정

15.
2025-07-25 09:47:31,454 - INFO - Service - 시가총액 상위 500개 종목 중 상한가 종목 조회 요청
2025-07-25 09:47:31,455 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-25 09:47:31 KST+0900)
2025-07-25 09:47:31,456 - WARNING - [경고] count 파라미터가 명시되지 않아 기본값 10을 사용합니다. market_code=0000
2025-07-25 09:47:31,456 - INFO - Service - 시가총액 상위 종목 조회 요청 - 시장: 0000, 개수: 10
2025-07-25 09:47:31,456 - INFO - 시가총액 상위 종목 조회 시도 (시장코드: 0000, 요청개수: 10)
2025-07-25 09:47:31,457 - DEBUG - API 호출 시도 1/1 - GET https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/ranking/market-cap
2025-07-25 09:47:31,457 - DEBUG - 
DEBUG: Headers being sent:
2025-07-25 09:47:31,457 - DEBUG -   Content-Type: b'application/json'
2025-07-25 09:47:31,457 - DEBUG -   User-Agent: b'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36'
2025-07-25 09:47:31,457 - DEBUG -   charset: b'UTF-8'
2025-07-25 09:47:31,457 - DEBUG -   appkey: b'PSvrDBXImiV6W2k3MQLzToa7WYkYlRf4IZrt'
2025-07-25 09:47:31,458 - DEBUG -   appsecret: b'8xF4PL7QyLJYnte1mJdh8N3qq7e/D61oKeY2leXyTy0G0L/Z6djx1zUWMbvVKG7LCDJc/2uvtD7Nq2pewskcsH4qRpZInhj0As1RWg2TOQQT/1LC0WDu+oKPcUW79DeKpEtje+ZNDv9rwlhEYl+twofVh8gTklpHatVp6BDDX9KoKakDkPM='
2025-07-25 09:47:31,458 - DEBUG -   tr_id: b'FHPST01740000'
2025-07-25 09:47:31,458 - DEBUG -   custtype: b'P'
2025-07-25 09:47:31,458 - DEBUG -   Authorization: b'Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJ0b2tlbiIsImF1ZCI6Ijc2ODZjNjZiLWM1NDItNGFjMi05MmRhLWEwZGI5Y2ViZGExNCIsInByZHRfY2QiOiIiLCJpc3MiOiJ1bm9ndyIsImV4cCI6MTc1MzQ1Mzc1NSwiaWF0IjoxNzUzMzY3MzU1LCJqdGkiOiJQU3ZyREJYSW1pVjZXMmszTVFMelRvYTdXWWtZbFJmNElacnQifQ.bciMR_35oyIii2w6Ni0ek-o1vyi669KS9XJkC5kf1yValirnsBhRdQ4UOhuDkaE947-Sjf_cjXbe4kZrUsGbtQ'
2025-07-25 09:47:31,537 - DEBUG - API 응답 성공: {"output":[{"mksc_shrn_iscd":"005930","data_rank":"1","hts_kor_isnm":"삼성전자","stck_prpr":"65850","prdy_vrss":"-150","prdy_vrss_sign":"5","prdy_ctrt":"-0.23","acml_vol":"1943202","lstn_stcn":"5919637922","stck_avls":"3898082","mrkt_whol_avls_rlim":"11.89"},{"mksc_shrn_iscd":"000660","data_rank":"2","hts_kor_isnm":"SK하이닉스","stck_prpr":"270000","prdy_vrss":"500","prdy_vrss_sign":"2","prdy_ctrt":"0.19","acml_vol":"672226","lstn_stcn":"728002365","stck_avls":"1965606","mrkt_whol_avls_rlim":"5.99"},{"mksc_shrn_iscd":"373220","data_rank":"3","hts_kor_isnm":"LG에너지솔루션","stck_prpr":"363000","prdy_vrss":"-5000","prdy_vrss_sign":"5","prdy_ctrt":"-1.36","acml_vol":"86985","lstn_stcn":"234000000","stck_avls":"849420","mrkt_whol_avls_rlim":"2.59"},{"mksc_shrn_iscd":"207940","data_rank":"4","hts_kor_isnm":"삼성바이오로직스","stck_prpr":"1078000","prdy_vrss":"-10000","prdy_vrss_sign":"5","prdy_ctrt":"-0.92","acml_vol":"13338","lstn_stcn":"71174000","stck_avls":"767256","mrkt_whol_avls_rlim":"2.34"},{"mksc_shrn_iscd":"012450","data_rank":"5","hts_kor_isnm":"한화에어로스페이스","stck_prpr":"943000","prdy_vrss":"2000","prdy_vrss_sign":"2","prdy_ctrt":"0.21","acml_vol":"22317","lstn_stcn":"51563401","stck_avls":"486243","mrkt_whol_avls_rlim":"1.48"},{"mksc_shrn_iscd":"105560","data_rank":"6","hts_kor_isnm":"KB금융","stck_prpr":"119100","prdy_vrss":"1900","prdy_vrss_sign":"2","prdy_ctrt":"1.62","acml_vol":"1014913","lstn_stcn":"381462103","stck_avls":"454321","mrkt_whol_avls_rlim":"1.39"},{"mksc_shrn_iscd":"005380","data_rank":"7","hts_kor_isnm":"현대차","stck_prpr":"217250","prdy_vrss":"-250","prdy_vrss_sign":"5","prdy_ctrt":"-0.11","acml_vol":"158715","lstn_stcn":"204757766","stck_avls":"444836","mrkt_whol_avls_rlim":"1.36"},{"mksc_shrn_iscd":"005935","data_rank":"8","hts_kor_isnm":"삼성전자우","stck_prpr":"54500","prdy_vrss":"-200","prdy_vrss_sign":"5","prdy_ctrt":"-0.37","acml_vol":"249201","lstn_stcn":"815974664","stck_avls":"444706","mrkt_whol_avls_rlim":"1.36"},{"mksc_shrn_iscd":"000270","data_rank":"9","hts_kor_isnm":"기아","stck_prpr":"105700","prdy_vrss":"700","prdy_vrss_sign":"2","prdy_ctrt":"0.67","acml_vol":"275065","lstn_stcn":"397672632","stck_avls":"420340","mrkt_whol_avls_rlim":"1.28"},{"mksc_shrn_iscd":"034020","data_rank":"10","hts_kor_isnm":"두산에너빌리티","stck_prpr":"65300","prdy_vrss":"-100","prdy_vrss_sign":"5","prdy_ctrt":"-0.15","acml_vol":"1551696","lstn_stcn":"640561146","stck_avls":"418286","mrkt_whol_avls_rlim":"1.28"},{"mksc_shrn_iscd":"068270","data_rank":"11","hts_kor_isnm":"셀트리온","stck_prpr":"178000","prdy_vrss":"-2400","prdy_vrss_sign":"5","prdy_ctrt":"-1.33","acml_vol":"124746","lstn_stcn":"230920342","stck_avls":"411038","mrkt_whol_avls_rlim":"1.25"},{"mksc_shrn_iscd":"329180","data_rank":"12","hts_kor_isnm":"HD현대중공업","stck_prpr":"432500","prdy_vrss":"12500","prdy_vrss_sign":"2","prdy_ctrt":"2.98","acml_vol":"86708","lstn_stcn":"88773116","stck_avls":"383944","mrkt_whol_avls_rlim":"1.17"},{"mksc_shrn_iscd":"035420","data_rank":"13","hts_kor_isnm":"NAVER","stck_prpr":"226500","prdy_vrss":"-500","prdy_vrss_sign":"5","prdy_ctrt":"-0.22","acml_vol":"253697","lstn_stcn":"158437008","stck_avls":"358860","mrkt_whol_avls_rlim":"1.09"},{"mksc_shrn_iscd":"055550","data_rank":"14","hts_kor_isnm":"신한지주","stck_prpr":"69800","prdy_vrss":"500","prdy_vrss_sign":"2","prdy_ctrt":"0.72","acml_vol":"742025","lstn_stcn":"485494934","stck_avls":"338875","mrkt_whol_avls_rlim":"1.03"},{"mksc_shrn_iscd":"028260","data_rank":"15","hts_kor_isnm":"삼성물산","stck_prpr":"168400","prdy_vrss":"-500","prdy_vrss_sign":"5","prdy_ctrt":"-0.30","acml_vol":"35058","lstn_stcn":"169976544","stck_avls":"286241","mrkt_whol_avls_rlim":"0.87"},{"mksc_shrn_iscd":"012330","data_rank":"16","hts_kor_isnm":"현대모비스","stck_prpr":"300000","prdy_vrss":"4000","prdy_vrss_sign":"2","prdy_ctrt":"1.35","acml_vol":"63673","lstn_stcn":"91795094","stck_avls":"275385","mrkt_whol_avls_rlim":"0.84"},{"mksc_shrn_iscd":"042660","data_rank":"17","hts_kor_isnm":"한화오션","stck_prpr":"88000","prdy_vrss":"-800","prdy_vrss_sign":"5","prdy_ctrt":"-0.90","acml_vol":"752759","lstn_stcn":"306413394","stck_avls":"269644","mrkt_whol_avls_rlim":"0.82"},{"mksc_shrn_iscd":"005490","data_rank":"18","hts_kor_isnm":"POSCO홀딩스","stck_prpr":"331500","prdy_vrss":"-2500","prdy_vrss_sign":"5","prdy_ctrt":"-0.75","acml_vol":"106250","lstn_stcn":"80932952","stck_avls":"268293","mrkt_whol_avls_rlim":"0.82"},{"mksc_shrn_iscd":"086790","data_rank":"19","hts_kor_isnm":"하나금융지주","stck_prpr":"92300","prdy_vrss":"1300","prdy_vrss_sign":"2","prdy_ctrt":"1.43","acml_vol":"344308","lstn_stcn":"284723889","stck_avls":"262800","mrkt_whol_avls_rlim":"0.80"},{"mksc_shrn_iscd":"032830","data_rank":"20","hts_kor_isnm":"삼성생명","stck_prpr":"127600","prdy_vrss":"-600","prdy_vrss_sign":"5","prdy_ctrt":"-0.47","acml_vol":"45142","lstn_stcn":"200000000","stck_avls":"255200","mrkt_whol_avls_rlim":"0.78"},{"mksc_shrn_iscd":"011200","data_rank":"21","hts_kor_isnm":"HMM","stck_prpr":"24750","prdy_vrss":"150","prdy_vrss_sign":"2","prdy_ctrt":"0.61","acml_vol":"228974","lstn_stcn":"1025039496","stck_avls":"253697","mrkt_whol_avls_rlim":"0.77"},{"mksc_shrn_iscd":"196170","data_rank":"22","hts_kor_isnm":"알테오젠","stck_prpr":"466000","prdy_vrss":"-11000","prdy_vrss_sign":"5","prdy_ctrt":"-2.31","acml_vol":"118533","lstn_stcn":"53464968","stck_avls":"249147","mrkt_whol_avls_rlim":"0.76"},{"mksc_shrn_iscd":"009540","data_rank":"23","hts_kor_isnm":"HD한국조선해양","stck_prpr":"347000","prdy_vrss":"9500","prdy_vrss_sign":"2","prdy_ctrt":"2.81","acml_vol":"81580","lstn_stcn":"70773116","stck_avls":"245583","mrkt_whol_avls_rlim":"0.75"},{"mksc_shrn_iscd":"015760","data_rank":"24","hts_kor_isnm":"한국전력","stck_prpr":"38000","prdy_vrss":"50","prdy_vrss_sign":"2","prdy_ctrt":"0.13","acml_vol":"589871","lstn_stcn":"641964077","stck_avls":"243946","mrkt_whol_avls_rlim":"0.74"},{"mksc_shrn_iscd":"035720","data_rank":"25","hts_kor_isnm":"카카오","stck_prpr":"54300","prdy_vrss":"200","prdy_vrss_sign":"2","prdy_ctrt":"0.37","acml_vol":"412500","lstn_stcn":"442013722","stck_avls":"240013","mrkt_whol_avls_rlim":"0.73"},{"mksc_shrn_iscd":"051910","data_rank":"26","hts_kor_isnm":"LG화학","stck_prpr":"305000","prdy_vrss":"0","prdy_vrss_sign":"3","prdy_ctrt":"0.00","acml_vol":"131061","lstn_stcn":"70592343","stck_avls":"215307","mrkt_whol_avls_rlim":"0.66"},{"mksc_shrn_iscd":"064350","data_rank":"27","hts_kor_isnm":"현대로템","stck_prpr":"194700","prdy_vrss":"5600","prdy_vrss_sign":"2","prdy_ctrt":"2.96","acml_vol":"336491","lstn_stcn":"109142293","stck_avls":"212500","mrkt_whol_avls_rlim":"0.65"},{"mksc_shrn_iscd":"000810","data_rank":"28","hts_kor_isnm":"삼성화재","stck_prpr":"456500","prdy_vrss":"500","prdy_vrss_sign":"2","prdy_ctrt":"0.11","acml_vol":"15714","lstn_stcn":"46011155","stck_avls":"210041","mrkt_whol_avls_rlim":"0.64"},{"mksc_shrn_iscd":"138040","data_rank":"29","hts_kor_isnm":"메리츠금융지주","stck_prpr":"116000","prdy_vrss":"0","prdy_vrss_sign":"3","prdy_ctrt":"0.00","acml_vol":"44262","lstn_stcn":"180014473","stck_avls":"208817","mrkt_whol_avls_rlim":"0.64"},{"mksc_shrn_iscd":"402340","data_rank":"30","hts_kor_isnm":"SK스퀘어","stck_prpr":"152400","prdy_vrss":"400","prdy_vrss_sign":"2","prdy_ctrt":"0.26","acml_vol":"54918","lstn_stcn":"132540858","stck_avls":"201992","mrkt_whol_avls_rlim":"0.62"}],"rt_cd":"0","msg_cd":"MCA00000","msg1":"정상처리 되었습니다."}
2025-07-25 09:47:31,537 - INFO - API로부터 수신한 종목 수: 10
2025-07-25 09:47:31,538 - INFO - Service - 005930 현재가 조회 요청
2025-07-25 09:47:31,538 - INFO - 005930 현재가 조회 시도...
2025-07-25 09:47:31,538 - DEBUG - API 호출 시도 1/3 - GET https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-price
2025-07-25 09:47:31,538 - DEBUG - 
DEBUG: Headers being sent:
2025-07-25 09:47:31,538 - DEBUG -   Content-Type: b'application/json'
2025-07-25 09:47:31,539 - DEBUG -   User-Agent: b'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36'
2025-07-25 09:47:31,539 - DEBUG -   charset: b'UTF-8'
2025-07-25 09:47:31,539 - DEBUG -   appkey: b'PSvrDBXImiV6W2k3MQLzToa7WYkYlRf4IZrt'
2025-07-25 09:47:31,539 - DEBUG -   appsecret: b'8xF4PL7QyLJYnte1mJdh8N3qq7e/D61oKeY2leXyTy0G0L/Z6djx1zUWMbvVKG7LCDJc/2uvtD7Nq2pewskcsH4qRpZInhj0As1RWg2TOQQT/1LC0WDu+oKPcUW79DeKpEtje+ZNDv9rwlhEYl+twofVh8gTklpHatVp6BDDX9KoKakDkPM='
2025-07-25 09:47:31,539 - DEBUG -   tr_id: b'FHKST01010100'
2025-07-25 09:47:31,539 - DEBUG -   custtype: b'P'
2025-07-25 09:47:31,539 - DEBUG -   Authorization: b'Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJ0b2tlbiIsImF1ZCI6Ijc2ODZjNjZiLWM1NDItNGFjMi05MmRhLWEwZGI5Y2ViZGExNCIsInByZHRfY2QiOiIiLCJpc3MiOiJ1bm9ndyIsImV4cCI6MTc1MzQ1Mzc1NSwiaWF0IjoxNzUzMzY3MzU1LCJqdGkiOiJQU3ZyREJYSW1pVjZXMmszTVFMelRvYTdXWWtZbFJmNElacnQifQ.bciMR_35oyIii2w6Ni0ek-o1vyi669KS9XJkC5kf1yValirnsBhRdQ4UOhuDkaE947-Sjf_cjXbe4kZrUsGbtQ'
2025-07-25 09:47:31,562 - DEBUG - API 응답 성공: {"output":{"iscd_stat_cls_code":"55","marg_rate":"20.00","rprs_mrkt_kor_name":"KOSPI200","bstp_kor_isnm":"전기·전자","temp_stop_yn":"N","oprc_rang_cont_yn":"N","clpr_rang_cont_yn":"N","crdt_able_yn":"Y","grmn_rate_cls_code":"40","elw_pblc_yn":"Y","stck_prpr":"65850","prdy_vrss":"-150","prdy_vrss_sign":"5","prdy_ctrt":"-0.23","acml_tr_pbmn":"127737129950","acml_vol":"1943202","prdy_vrss_vol_rate":"15.35","stck_oprc":"65700","stck_hgpr":"66000","stck_lwpr":"65500","stck_mxpr":"85800","stck_llam":"46200","stck_sdpr":"66000","wghn_avrg_stck_prc":"65735.37","hts_frgn_ehrt":"50.23","frgn_ntby_qty":"0","pgtr_ntby_qty":"-53554","pvt_scnd_dmrs_prc":"67133","pvt_frst_dmrs_prc":"66566","pvt_pont_val":"66233","pvt_frst_dmsp_prc":"65666","pvt_scnd_dmsp_prc":"65333","dmrs_val":"66400","dmsp_val":"65500","cpfn":"7780","rstc_wdth_prc":"19800","stck_fcam":"100","stck_sspr":"50160","aspr_unit":"100","hts_deal_qty_unit_val":"1","lstn_stcn":"5919637922","hts_avls":"3898082","per":"13.30","pbr":"1.14","stac_month":"12","vol_tnrt":"0.03","eps":"4950.00","bps":"57930.00","d250_hgpr":"88000","d250_hgpr_date":"20240716","d250_hgpr_vrss_prpr_rate":"-25.17","d250_lwpr":"49900","d250_lwpr_date":"20241114","d250_lwpr_vrss_prpr_rate":"31.96","stck_dryy_hgpr":"68800","dryy_hgpr_vrss_prpr_rate":"-4.29","dryy_hgpr_date":"20250721","stck_dryy_lwpr":"50800","dryy_lwpr_vrss_prpr_rate":"29.63","dryy_lwpr_date":"20250203","w52_hgpr":"86100","w52_hgpr_vrss_prpr_ctrt":"-23.52","w52_hgpr_date":"20240801","w52_lwpr":"49900","w52_lwpr_vrss_prpr_ctrt":"31.96","w52_lwpr_date":"20241114","whol_loan_rmnd_rate":"0.19","ssts_yn":"Y","stck_shrn_iscd":"005930","fcam_cnnm":"100","cpfn_cnnm":"7,780 억","frgn_hldn_qty":"2973484820","vi_cls_code":"N","ovtm_vi_cls_code":"N","last_ssts_cntg_qty":"875335","invt_caful_yn":"N","mrkt_warn_cls_code":"00","short_over_yn":"N","sltr_yn":"N","mang_issu_cls_code":"N"},"rt_cd":"0","msg_cd":"MCA00000","msg1":"정상처리 되었습니다."}
2025-07-25 09:47:31,565 - ERROR - stock_query_service.py:316 - 상한가 종목 조회 중 예기치 않은 오류 발생: 'ResStockFullInfoApiOutput' object is not subscriptable
2025-07-25 09:47:31,565 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-25 09:47:31 KST+0900)

16. 상위종목 10개로 제한되어 있음 (300개로 늘리기)

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

2025-07-25 09:53:34,097 - WARNING - [경고] count 파라미터가 명시되지 않아 기본값 10을 사용합니다. market_code=0000
2025-07-25 09:53:34,097 - INFO - Service - 시가총액 상위 종목 조회 요청 - 시장: 0000, 개수: 10
2025-07-25 09:53:34,098 - INFO - 시가총액 상위 종목 조회 시도 (시장코드: 0000, 요청개수: 10)
2025-07-25 09:53:34,098 - DEBUG - API 호출 시도 1/1 - GET https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/ranking/market-cap
2025-07-25 09:53:34,098 - DEBUG - 
DEBUG: Headers being sent:
2025-07-25 09:53:34,098 - DEBUG -   Content-Type: b'application/json'
2025-07-25 09:53:34,099 - DEBUG -   User-Agent: b'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36'
2025-07-25 09:53:34,099 - DEBUG -   charset: b'UTF-8'
2025-07-25 09:53:34,099 - DEBUG -   appkey: b'PSvrDBXImiV6W2k3MQLzToa7WYkYlRf4IZrt'
2025-07-25 09:53:34,099 - DEBUG -   appsecret: b'8xF4PL7QyLJYnte1mJdh8N3qq7e/D61oKeY2leXyTy0G0L/Z6djx1zUWMbvVKG7LCDJc/2uvtD7Nq2pewskcsH4qRpZInhj0As1RWg2TOQQT/1LC0WDu+oKPcUW79DeKpEtje+ZNDv9rwlhEYl+twofVh8gTklpHatVp6BDDX9KoKakDkPM='
2025-07-25 09:53:34,100 - DEBUG -   tr_id: b'FHPST01740000'
2025-07-25 09:53:34,100 - DEBUG -   custtype: b'P'
2025-07-25 09:53:34,100 - DEBUG -   Authorization: b'Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJ0b2tlbiIsImF1ZCI6Ijc2ODZjNjZiLWM1NDItNGFjMi05MmRhLWEwZGI5Y2ViZGExNCIsInByZHRfY2QiOiIiLCJpc3MiOiJ1bm9ndyIsImV4cCI6MTc1MzQ1Mzc1NSwiaWF0IjoxNzUzMzY3MzU1LCJqdGkiOiJQU3ZyREJYSW1pVjZXMmszTVFMelRvYTdXWWtZbFJmNElacnQifQ.bciMR_35oyIii2w6Ni0ek-o1vyi669KS9XJkC5kf1yValirnsBhRdQ4UOhuDkaE947-Sjf_cjXbe4kZrUsGbtQ'
2025-07-25 09:53:34,153 - DEBUG - API 응답 성공: {"output":[{"mksc_shrn_iscd":"005930","data_rank":"1","hts_kor_isnm":"삼성전자","stck_prpr":"65800","prdy_vrss":"-200","prdy_vrss_sign":"5","prdy_ctrt":"-0.30","acml_vol":"2099256","lstn_stcn":"5919637922","stck_avls":"3895122","mrkt_whol_avls_rlim":"11.90"},{"mksc_shrn_iscd":"000660","data_rank":"2","hts_kor_isnm":"SK하이닉스","stck_prpr":"269500","prdy_vrss":"0","prdy_vrss_sign":"3","prdy_ctrt":"0.00","acml_vol":"687596","lstn_stcn":"728002365","stck_avls":"1961966","mrkt_whol_avls_rlim":"5.99"},{"mksc_shrn_iscd":"373220","data_rank":"3","hts_kor_isnm":"LG에너지솔루션","stck_prpr":"361500","prdy_vrss":"-6500","prdy_vrss_sign":"5","prdy_ctrt":"-1.77","acml_vol":"90413","lstn_stcn":"234000000","stck_avls":"845910","mrkt_whol_avls_rlim":"2.58"},{"mksc_shrn_iscd":"207940","data_rank":"4","hts_kor_isnm":"삼성바이오로직스","stck_prpr":"1075000","prdy_vrss":"-13000","prdy_vrss_sign":"5","prdy_ctrt":"-1.19","acml_vol":"14475","lstn_stcn":"71174000","stck_avls":"765121","mrkt_whol_avls_rlim":"2.34"},{"mksc_shrn_iscd":"012450","data_rank":"5","hts_kor_isnm":"한화에어로스페이스","stck_prpr":"941000","prdy_vrss":"0","prdy_vrss_sign":"3","prdy_ctrt":"0.00","acml_vol":"23112","lstn_stcn":"51563401","stck_avls":"485212","mrkt_whol_avls_rlim":"1.48"},{"mksc_shrn_iscd":"105560","data_rank":"6","hts_kor_isnm":"KB금융","stck_prpr":"118900","prdy_vrss":"1700","prdy_vrss_sign":"2","prdy_ctrt":"1.45","acml_vol":"1043474","lstn_stcn":"381462103","stck_avls":"453558","mrkt_whol_avls_rlim":"1.39"},{"mksc_shrn_iscd":"005380","data_rank":"7","hts_kor_isnm":"현대차","stck_prpr":"216500","prdy_vrss":"-1000","prdy_vrss_sign":"5","prdy_ctrt":"-0.46","acml_vol":"175455","lstn_stcn":"204757766","stck_avls":"443301","mrkt_whol_avls_rlim":"1.35"},{"mksc_shrn_iscd":"005935","data_rank":"8","hts_kor_isnm":"삼성전자우","stck_prpr":"54300","prdy_vrss":"-400","prdy_vrss_sign":"5","prdy_ctrt":"-0.73","acml_vol":"269188","lstn_stcn":"815974664","stck_avls":"443074","mrkt_whol_avls_rlim":"1.35"},{"mksc_shrn_iscd":"000270","data_rank":"9","hts_kor_isnm":"기아","stck_prpr":"105300","prdy_vrss":"300","prdy_vrss_sign":"2","prdy_ctrt":"0.29","acml_vol":"300014","lstn_stcn":"397672632","stck_avls":"418749","mrkt_whol_avls_rlim":"1.28"},{"mksc_shrn_iscd":"034020","data_rank":"10","hts_kor_isnm":"두산에너빌리티","stck_prpr":"65200","prdy_vrss":"-200","prdy_vrss_sign":"5","prdy_ctrt":"-0.31","acml_vol":"1661539","lstn_stcn":"640561146","stck_avls":"417646","mrkt_whol_avls_rlim":"1.28"},{"mksc_shrn_iscd":"068270","data_rank":"11","hts_kor_isnm":"셀트리온","stck_prpr":"177600","prdy_vrss":"-2800","prdy_vrss_sign":"5","prdy_ctrt":"-1.55","acml_vol":"129048","lstn_stcn":"230920342","stck_avls":"410115","mrkt_whol_avls_rlim":"1.25"},{"mksc_shrn_iscd":"329180","data_rank":"12","hts_kor_isnm":"HD현대중공업","stck_prpr":"433000","prdy_vrss":"13000","prdy_vrss_sign":"2","prdy_ctrt":"3.10","acml_vol":"89533","lstn_stcn":"88773116","stck_avls":"384388","mrkt_whol_avls_rlim":"1.17"},{"mksc_shrn_iscd":"035420","data_rank":"13","hts_kor_isnm":"NAVER","stck_prpr":"227000","prdy_vrss":"0","prdy_vrss_sign":"3","prdy_ctrt":"0.00","acml_vol":"256929","lstn_stcn":"158437008","stck_avls":"359652","mrkt_whol_avls_rlim":"1.10"},{"mksc_shrn_iscd":"055550","data_rank":"14","hts_kor_isnm":"신한지주","stck_prpr":"69800","prdy_vrss":"500","prdy_vrss_sign":"2","prdy_ctrt":"0.72","acml_vol":"757429","lstn_stcn":"485494934","stck_avls":"338875","mrkt_whol_avls_rlim":"1.03"},{"mksc_shrn_iscd":"028260","data_rank":"15","hts_kor_isnm":"삼성물산","stck_prpr":"168100","prdy_vrss":"-800","prdy_vrss_sign":"5","prdy_ctrt":"-0.47","acml_vol":"39952","lstn_stcn":"169976544","stck_avls":"285731","mrkt_whol_avls_rlim":"0.87"},{"mksc_shrn_iscd":"012330","data_rank":"16","hts_kor_isnm":"현대모비스","stck_prpr":"300000","prdy_vrss":"4000","prdy_vrss_sign":"2","prdy_ctrt":"1.35","acml_vol":"67588","lstn_stcn":"91795094","stck_avls":"275385","mrkt_whol_avls_rlim":"0.84"},{"mksc_shrn_iscd":"042660","data_rank":"17","hts_kor_isnm":"한화오션","stck_prpr":"87900","prdy_vrss":"-900","prdy_vrss_sign":"5","prdy_ctrt":"-1.01","acml_vol":"783487","lstn_stcn":"306413394","stck_avls":"269337","mrkt_whol_avls_rlim":"0.82"},{"mksc_shrn_iscd":"005490","data_rank":"18","hts_kor_isnm":"POSCO홀딩스","stck_prpr":"331000","prdy_vrss":"-3000","prdy_vrss_sign":"5","prdy_ctrt":"-0.90","acml_vol":"113701","lstn_stcn":"80932952","stck_avls":"267888","mrkt_whol_avls_rlim":"0.82"},{"mksc_shrn_iscd":"086790","data_rank":"19","hts_kor_isnm":"하나금융지주","stck_prpr":"91900","prdy_vrss":"900","prdy_vrss_sign":"2","prdy_ctrt":"0.99","acml_vol":"356394","lstn_stcn":"284723889","stck_avls":"261661","mrkt_whol_avls_rlim":"0.80"},{"mksc_shrn_iscd":"032830","data_rank":"20","hts_kor_isnm":"삼성생명","stck_prpr":"127200","prdy_vrss":"-1000","prdy_vrss_sign":"5","prdy_ctrt":"-0.78","acml_vol":"49562","lstn_stcn":"200000000","stck_avls":"254400","mrkt_whol_avls_rlim":"0.78"},{"mksc_shrn_iscd":"011200","data_rank":"21","hts_kor_isnm":"HMM","stck_prpr":"24750","prdy_vrss":"150","prdy_vrss_sign":"2","prdy_ctrt":"0.61","acml_vol":"244013","lstn_stcn":"1025039496","stck_avls":"253697","mrkt_whol_avls_rlim":"0.77"},{"mksc_shrn_iscd":"196170","data_rank":"22","hts_kor_isnm":"알테오젠","stck_prpr":"464000","prdy_vrss":"-13000","prdy_vrss_sign":"5","prdy_ctrt":"-2.73","acml_vol":"123279","lstn_stcn":"53464968","stck_avls":"248077","mrkt_whol_avls_rlim":"0.76"},{"mksc_shrn_iscd":"009540","data_rank":"23","hts_kor_isnm":"HD한국조선해양","stck_prpr":"347000","prdy_vrss":"9500","prdy_vrss_sign":"2","prdy_ctrt":"2.81","acml_vol":"84743","lstn_stcn":"70773116","stck_avls":"245583","mrkt_whol_avls_rlim":"0.75"},{"mksc_shrn_iscd":"015760","data_rank":"24","hts_kor_isnm":"한국전력","stck_prpr":"37950","prdy_vrss":"0","prdy_vrss_sign":"3","prdy_ctrt":"0.00","acml_vol":"620129","lstn_stcn":"641964077","stck_avls":"243625","mrkt_whol_avls_rlim":"0.74"},{"mksc_shrn_iscd":"035720","data_rank":"25","hts_kor_isnm":"카카오","stck_prpr":"54200","prdy_vrss":"100","prdy_vrss_sign":"2","prdy_ctrt":"0.18","acml_vol":"428689","lstn_stcn":"442013722","stck_avls":"239571","mrkt_whol_avls_rlim":"0.73"},{"mksc_shrn_iscd":"051910","data_rank":"26","hts_kor_isnm":"LG화학","stck_prpr":"302000","prdy_vrss":"-3000","prdy_vrss_sign":"5","prdy_ctrt":"-0.98","acml_vol":"139879","lstn_stcn":"70592343","stck_avls":"213189","mrkt_whol_avls_rlim":"0.65"},{"mksc_shrn_iscd":"064350","data_rank":"27","hts_kor_isnm":"현대로템","stck_prpr":"194200","prdy_vrss":"5100","prdy_vrss_sign":"2","prdy_ctrt":"2.70","acml_vol":"356123","lstn_stcn":"109142293","stck_avls":"211954","mrkt_whol_avls_rlim":"0.65"},{"mksc_shrn_iscd":"000810","data_rank":"28","hts_kor_isnm":"삼성화재","stck_prpr":"455000","prdy_vrss":"-1000","prdy_vrss_sign":"5","prdy_ctrt":"-0.22","acml_vol":"16565","lstn_stcn":"46011155","stck_avls":"209351","mrkt_whol_avls_rlim":"0.64"},{"mksc_shrn_iscd":"138040","data_rank":"29","hts_kor_isnm":"메리츠금융지주","stck_prpr":"115900","prdy_vrss":"-100","prdy_vrss_sign":"5","prdy_ctrt":"-0.09","acml_vol":"48138","lstn_stcn":"180014473","stck_avls":"208637","mrkt_whol_avls_rlim":"0.64"},{"mksc_shrn_iscd":"402340","data_rank":"30","hts_kor_isnm":"SK스퀘어","stck_prpr":"152100","prdy_vrss":"100","prdy_vrss_sign":"2","prdy_ctrt":"0.07","acml_vol":"58013","lstn_stcn":"132540858","stck_avls":"201595","mrkt_whol_avls_rlim":"0.62"}],"rt_cd":"0","msg_cd":"MCA00000","msg1":"정상처리 되었습니다."}
2025-07-25 09:53:34,153 - INFO - API로부터 수신한 종목 수: 10
2025-07-25 09:53:34,163 - ERROR - trading_app.py:373 - 모멘텀 전략 실행 중 오류 발생: 'NoneType' object has no attribute 'get_price_summary'
Traceback (most recent call last):
  File "C:\Users\Kyungsoo\Documents\Code\Investment\trading_app.py", line 366, in _execute_action
    result = await executor.execute(top_stock_codes)
  File "C:\Users\Kyungsoo\Documents\Code\Investment\strategies\strategy_executor.py", line 10, in execute
    return await self.strategy.run(stock_codes)
  File "C:\Users\Kyungsoo\Documents\Code\Investment\strategies\momentum_strategy.py", line 31, in run
    summary : ResCommonResponse = await self.broker.get_price_summary(code)  # ✅ wrapper 통해 조회
AttributeError: 'NoneType' object has no attribute 'get_price_summary'
2025-07-25 09:53:34,165 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-25 09:53:34 KST+0900)

21. 22.
시가총액 상위종목에서 전체로 변경.

98. 토큰 무효화 했지만 정상동작하는걸로 보임.
99. 

### 모의
1.
2025-07-25 09:56:12,389 - INFO - 005930 현재가 조회 시도...
2025-07-25 09:56:12,389 - DEBUG - API 호출 시도 1/3 - GET https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-price
2025-07-25 09:56:12,389 - DEBUG - 
DEBUG: Headers being sent:
2025-07-25 09:56:12,390 - DEBUG -   Content-Type: b'application/json'
2025-07-25 09:56:12,390 - DEBUG -   User-Agent: b'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36'
2025-07-25 09:56:12,390 - DEBUG -   charset: b'UTF-8'
2025-07-25 09:56:12,390 - DEBUG -   appkey: b'PSvrDBXImiV6W2k3MQLzToa7WYkYlRf4IZrt'
2025-07-25 09:56:12,390 - DEBUG -   appsecret: b'8xF4PL7QyLJYnte1mJdh8N3qq7e/D61oKeY2leXyTy0G0L/Z6djx1zUWMbvVKG7LCDJc/2uvtD7Nq2pewskcsH4qRpZInhj0As1RWg2TOQQT/1LC0WDu+oKPcUW79DeKpEtje+ZNDv9rwlhEYl+twofVh8gTklpHatVp6BDDX9KoKakDkPM='
2025-07-25 09:56:12,391 - DEBUG -   tr_id: b'FHKST01010100'
2025-07-25 09:56:12,391 - DEBUG -   custtype: b'P'
2025-07-25 09:56:12,445 - WARNING - 🔁 토큰 만료 감지 (EGW00123). 재발급 후 1회 재시도
2025-07-25 09:56:13,925 - ERROR - korea_invest_api_base.py:172 - HTTP 오류 발생: 500 - {"rt_cd":"1","msg1":"기간이 만료된 token 입니다.","msg_cd":"EGW00123"}
2025-07-25 09:56:13,927 - ERROR - korea_invest_api_base.py:58 - 복구 불가능한 오류 발생: https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-price, 응답: {"rt_cd":"1","msg1":"기간이 만료된 token 입니다.","msg_cd":"EGW00123"}
2025-07-25 09:56:13,927 - WARNING - 현재가 조회 실패
2025-07-25 09:56:13,932 - ERROR - stock_query_service.py:35 - 005930 현재가 조회 실패: None
2025-07-25 09:56:13,932 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-25 09:56:13 KST+0900)

2.
Backend tkagg is interactive backend. Turning interactive mode on.
FATAL ERROR: 애플리케이션 실행 중 치명적인 오류 발생: CLIView.display_account_balance_failure() takes 1 positional argument but 2 were given
Traceback (most recent call last):
  File "C:\Users\Kyungsoo\Documents\Code\Investment\main.py", line 14, in main
    await app.run_async() # <--- run_async 메서드 호출 (비동기)
  File "C:\Users\Kyungsoo\Documents\Code\Investment\trading_app.py", line 521, in run_async
    running = await self._execute_action(choice)
  File "C:\Users\Kyungsoo\Documents\Code\Investment\trading_app.py", line 213, in _execute_action
    self.cli_view.display_account_balance_failure(balance_response.msg1)
TypeError: CLIView.display_account_balance_failure() takes 1 positional argument but 2 were given


3.
2025-07-25 09:57:44,673 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-25 09:57:44 KST+0900)
2025-07-25 09:57:44,674 - INFO - Service - 주식 매수 주문 요청 - 종목: 005930, 수량: 1, 가격: 0
2025-07-25 09:57:45,342 - INFO - Hashkey 계산 성공: a30ae3e9a5cc288bdeb436dbf0a3b6094615f276794d51693518a21bbefe8588
2025-07-25 09:57:45,342 - INFO - 주식 buy 주문 시도 - 종목: 005930, 수량: 1, 가격: 0
2025-07-25 09:57:45,343 - DEBUG - API 호출 시도 1/1 - POST https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/trading/order-cash
2025-07-25 09:57:45,343 - DEBUG - 
DEBUG: Headers being sent:
2025-07-25 09:57:45,343 - DEBUG -   Content-Type: b'application/json'
2025-07-25 09:57:45,343 - DEBUG -   User-Agent: b'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36'
2025-07-25 09:57:45,343 - DEBUG -   charset: b'UTF-8'
2025-07-25 09:57:45,344 - DEBUG -   appkey: b'PSvrDBXImiV6W2k3MQLzToa7WYkYlRf4IZrt'
2025-07-25 09:57:45,344 - DEBUG -   appsecret: b'8xF4PL7QyLJYnte1mJdh8N3qq7e/D61oKeY2leXyTy0G0L/Z6djx1zUWMbvVKG7LCDJc/2uvtD7Nq2pewskcsH4qRpZInhj0As1RWg2TOQQT/1LC0WDu+oKPcUW79DeKpEtje+ZNDv9rwlhEYl+twofVh8gTklpHatVp6BDDX9KoKakDkPM='
2025-07-25 09:57:45,344 - DEBUG -   tr_id: b'TTTC0012U'
2025-07-25 09:57:45,344 - DEBUG -   custtype: b'P'
2025-07-25 09:57:45,344 - DEBUG -   gt_uid: b'478862b4ed326d753b7d462484506ff2'
2025-07-25 09:57:45,344 - DEBUG -   hashkey: b'a30ae3e9a5cc288bdeb436dbf0a3b6094615f276794d51693518a21bbefe8588'
2025-07-25 09:57:45,403 - WARNING - 🔁 토큰 만료 감지 (EGW00123). 재발급 후 1회 재시도
2025-07-25 09:57:46,690 - ERROR - korea_invest_api_base.py:103 - HTTP 오류 발생 (httpx): 403 - {"error_description":"접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)","error_code":"EGW00133"}
2025-07-25 09:57:46,692 - ERROR - korea_invest_api_base.py:80 - 모든 재시도 실패, API 호출 종료
2025-07-25 09:57:46,696 - ERROR - trading_service.py:139 - 매수 주문 실패: 최대 재시도 횟수 초과
2025-07-25 09:57:46,700 - ERROR - order_execution_service.py:32 - 주식 매수 주문 실패: 종목=005930, 결과={'rt_cd': '105', 'msg1': '최대 재시도 횟수 초과'}
2025-07-25 09:57:46,701 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-25 09:57:46 KST+0900)

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

5.
2025-07-25 09:58:18,672 - INFO - Service - 005930 현재가 조회 요청
2025-07-25 09:58:18,673 - INFO - 005930 현재가 조회 시도...
2025-07-25 09:58:18,673 - DEBUG - API 호출 시도 1/3 - GET https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-price
2025-07-25 09:58:18,673 - DEBUG - 
DEBUG: Headers being sent:
2025-07-25 09:58:18,673 - DEBUG -   Content-Type: b'application/json'
2025-07-25 09:58:18,673 - DEBUG -   User-Agent: b'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36'
2025-07-25 09:58:18,673 - DEBUG -   charset: b'UTF-8'
2025-07-25 09:58:18,674 - DEBUG -   appkey: b'PSvrDBXImiV6W2k3MQLzToa7WYkYlRf4IZrt'
2025-07-25 09:58:18,674 - DEBUG -   appsecret: b'8xF4PL7QyLJYnte1mJdh8N3qq7e/D61oKeY2leXyTy0G0L/Z6djx1zUWMbvVKG7LCDJc/2uvtD7Nq2pewskcsH4qRpZInhj0As1RWg2TOQQT/1LC0WDu+oKPcUW79DeKpEtje+ZNDv9rwlhEYl+twofVh8gTklpHatVp6BDDX9KoKakDkPM='
2025-07-25 09:58:18,674 - DEBUG -   tr_id: b'FHKST01010100'
2025-07-25 09:58:18,674 - DEBUG -   custtype: b'P'
2025-07-25 09:58:18,753 - WARNING - 🔁 토큰 만료 감지 (EGW00123). 재발급 후 1회 재시도
2025-07-25 09:58:19,422 - ERROR - korea_invest_api_base.py:103 - HTTP 오류 발생 (httpx): 403 - {"error_description":"접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)","error_code":"EGW00133"}
2025-07-25 09:58:19,423 - INFO - 예외 발생, 재시도: 1/3, 지연 1초
2025-07-25 09:58:20,438 - DEBUG - API 호출 시도 2/3 - GET https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-price
2025-07-25 09:58:20,438 - DEBUG - 
DEBUG: Headers being sent:
2025-07-25 09:58:20,438 - DEBUG -   Content-Type: b'application/json'
2025-07-25 09:58:20,438 - DEBUG -   User-Agent: b'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36'
2025-07-25 09:58:20,438 - DEBUG -   charset: b'UTF-8'
2025-07-25 09:58:20,439 - DEBUG -   appkey: b'PSvrDBXImiV6W2k3MQLzToa7WYkYlRf4IZrt'
2025-07-25 09:58:20,439 - DEBUG -   appsecret: b'8xF4PL7QyLJYnte1mJdh8N3qq7e/D61oKeY2leXyTy0G0L/Z6djx1zUWMbvVKG7LCDJc/2uvtD7Nq2pewskcsH4qRpZInhj0As1RWg2TOQQT/1LC0WDu+oKPcUW79DeKpEtje+ZNDv9rwlhEYl+twofVh8gTklpHatVp6BDDX9KoKakDkPM='
2025-07-25 09:58:20,439 - DEBUG -   tr_id: b'FHKST01010100'
2025-07-25 09:58:20,439 - DEBUG -   custtype: b'P'
2025-07-25 09:58:20,439 - DEBUG -   Authorization: b'Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJ0b2tlbiIsImF1ZCI6ImIwOTZmMGUxLTNmZmYtNDRhYy05MTg2LTUwNjFhOGJmOGFkMSIsInByZHRfY2QiOiIiLCJpc3MiOiJ1bm9ndyIsImV4cCI6MTc1MzQ5MDM2MiwiaWF0IjoxNzUzNDAzOTYyLCJqdGkiOiJQU2p4SllhYkZ0YUlQMjlISllvQ0hlTEtCSVR4eHY3ZzdudmcifQ.vqWfWsLRYHW_w4NAU0eEOrWUFgzRguIYlQuDoUpEPoq_QQ28Wn70P6BgIQYKhtYWyiHTjcxyAG4MEZcTZwG-WQ'
2025-07-25 09:58:20,490 - WARNING - 🔁 토큰 만료 감지 (EGW00123). 재발급 후 1회 재시도
2025-07-25 09:58:21,227 - ERROR - korea_invest_api_base.py:172 - HTTP 오류 발생: 500 - {"rt_cd":"1","msg1":"기간이 만료된 token 입니다.","msg_cd":"EGW00123"}
2025-07-25 09:58:21,228 - ERROR - korea_invest_api_base.py:58 - 복구 불가능한 오류 발생: https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-price, 응답: {"rt_cd":"1","msg1":"기간이 만료된 token 입니다.","msg_cd":"EGW00123"}
2025-07-25 09:58:21,228 - WARNING - 현재가 조회 실패
2025-07-25 09:58:21,233 - ERROR - stock_query_service.py:162 - 005930 전일대비 등락률 조회 실패: ResCommonResponse(rt_cd='101', msg1='API 응답 파싱 실패 또는 처리 불가능 - {"rt_cd":"1","msg1":"기간이 만료된 token 입니다.","msg_cd":"EGW00123"}', data=None)
2025-07-25 09:58:21,234 - INFO - 시장 상태 - 시장이 열려 있습니다. (현재: 2025-07-25 09:58:21 KST+0900)

6.
2025-07-25 09:58:49,190 - INFO - Service - 005930 현재가 조회 요청
2025-07-25 09:58:49,190 - INFO - 005930 현재가 조회 시도...
2025-07-25 09:58:49,191 - DEBUG - API 호출 시도 1/3 - GET https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-price
2025-07-25 09:58:49,191 - DEBUG - 
DEBUG: Headers being sent:
2025-07-25 09:58:49,191 - DEBUG -   Content-Type: b'application/json'
2025-07-25 09:58:49,191 - DEBUG -   User-Agent: b'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36'
2025-07-25 09:58:49,191 - DEBUG -   charset: b'UTF-8'
2025-07-25 09:58:49,192 - DEBUG -   appkey: b'PSvrDBXImiV6W2k3MQLzToa7WYkYlRf4IZrt'
2025-07-25 09:58:49,192 - DEBUG -   appsecret: b'8xF4PL7QyLJYnte1mJdh8N3qq7e/D61oKeY2leXyTy0G0L/Z6djx1zUWMbvVKG7LCDJc/2uvtD7Nq2pewskcsH4qRpZInhj0As1RWg2TOQQT/1LC0WDu+oKPcUW79DeKpEtje+ZNDv9rwlhEYl+twofVh8gTklpHatVp6BDDX9KoKakDkPM='
2025-07-25 09:58:49,192 - DEBUG -   tr_id: b'FHKST01010100'
2025-07-25 09:58:49,193 - DEBUG -   custtype: b'P'
2025-07-25 09:58:49,193 - DEBUG -   Authorization: b'Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJ0b2tlbiIsImF1ZCI6ImIwOTZmMGUxLTNmZmYtNDRhYy05MTg2LTUwNjFhOGJmOGFkMSIsInByZHRfY2QiOiIiLCJpc3MiOiJ1bm9ndyIsImV4cCI6MTc1MzQ5MDM2MiwiaWF0IjoxNzUzNDAzOTYyLCJqdGkiOiJQU2p4SllhYkZ0YUlQMjlISllvQ0hlTEtCSVR4eHY3ZzdudmcifQ.vqWfWsLRYHW_w4NAU0eEOrWUFgzRguIYlQuDoUpEPoq_QQ28Wn70P6BgIQYKhtYWyiHTjcxyAG4MEZcTZwG-WQ'
2025-07-25 09:58:49,224 - WARNING - 🔁 토큰 만료 감지 (EGW00123). 재발급 후 1회 재시도
2025-07-25 09:58:49,845 - ERROR - korea_invest_api_base.py:103 - HTTP 오류 발생 (httpx): 403 - {"error_description":"접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)","error_code":"EGW00133"}
2025-07-25 09:58:49,846 - INFO - 예외 발생, 재시도: 1/3, 지연 1초
2025-07-25 09:58:50,855 - DEBUG - API 호출 시도 2/3 - GET https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-price
2025-07-25 09:58:50,855 - DEBUG - 
DEBUG: Headers being sent:
2025-07-25 09:58:50,855 - DEBUG -   Content-Type: b'application/json'
2025-07-25 09:58:50,855 - DEBUG -   User-Agent: b'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_9_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/35.0.1916.47 Safari/537.36'
2025-07-25 09:58:50,856 - DEBUG -   charset: b'UTF-8'
2025-07-25 09:58:50,856 - DEBUG -   appkey: b'PSvrDBXImiV6W2k3MQLzToa7WYkYlRf4IZrt'
2025-07-25 09:58:50,856 - DEBUG -   appsecret: b'8xF4PL7QyLJYnte1mJdh8N3qq7e/D61oKeY2leXyTy0G0L/Z6djx1zUWMbvVKG7LCDJc/2uvtD7Nq2pewskcsH4qRpZInhj0As1RWg2TOQQT/1LC0WDu+oKPcUW79DeKpEtje+ZNDv9rwlhEYl+twofVh8gTklpHatVp6BDDX9KoKakDkPM='
2025-07-25 09:58:50,856 - DEBUG -   tr_id: b'FHKST01010100'
2025-07-25 09:58:50,856 - DEBUG -   custtype: b'P'
2025-07-25 09:58:50,856 - DEBUG -   Authorization: b'Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJzdWIiOiJ0b2tlbiIsImF1ZCI6ImIwOTZmMGUxLTNmZmYtNDRhYy05MTg2LTUwNjFhOGJmOGFkMSIsInByZHRfY2QiOiIiLCJpc3MiOiJ1bm9ndyIsImV4cCI6MTc1MzQ5MDM2MiwiaWF0IjoxNzUzNDAzOTYyLCJqdGkiOiJQU2p4SllhYkZ0YUlQMjlISllvQ0hlTEtCSVR4eHY3ZzdudmcifQ.vqWfWsLRYHW_w4NAU0eEOrWUFgzRguIYlQuDoUpEPoq_QQ28Wn70P6BgIQYKhtYWyiHTjcxyAG4MEZcTZwG-WQ'
2025-07-25 09:58:50,887 - WARNING - 🔁 토큰 만료 감지 (EGW00123). 재발급 후 1회 재시도
2025-07-25 09:58:51,486 - ERROR - korea_invest_api_base.py:103 - HTTP 오류 발생 (httpx): 403 - {"error_description":"접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)","error_code":"EGW00133"}
2025-07-25 09:58:51,487 - INFO - 예외 발생, 재시도: 2/3, 지연 1초


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