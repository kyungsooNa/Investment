# To-Do List (개선 계획 - 우선순위)

## Ⅰ. 최우선 개선 (High Priority)
이 항목들은 애플리케이션의 핵심 기능 안정성, 데이터 무결성, 그리고 기본적인 성능 및 개발 효율성에 직접적인 영향을 미칩니다.

### 0. 불량
2. Test 결과만 저장하면 Test 전용 로거 추가 필요.
4. IntegrationTest의 Mocking을 execute_request로 변경.
### 실전

9. API 잘못됨

11. API 잘못됨

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

### 모의

9. API 잘못됨

11. API 잘못됨
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

### 1. 환경 (Environment)

### 2. 성능 (Performance)
* **[개선 필요]** 시장이 닫혔으면 스레드를 통해 전체 종목을 백그라운드로 업데이트하여 RAM에 올려두게 하기.
* **[최적화]** 반복적인 API 호출 최적화: `StockQueryService.handle_upper_limit_stocks`와 같이 반복적으로 개별 종목의 현재가를 조회하는 로직을 일괄 조회 또는 캐싱 전략으로 개선.

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
* **[신규 기능]** 외국인 순매수 상위 종목 조회 기능 추가.
* **[신규 기능]** 거래대금 상위 종목 조회 기능 추가.
* **[신규 기능]** 웹 뷰어 생성.
* **[신규 기능]** Kis Developers API 문서 크롤링해서 API의 tr_id, url, Header, Params, Body를 최신으로 업데이트 할 수 있는 기능 추가 
* **[신규 기능]** Android App으로 거래결과, 서치 결과 알림 기능 추가. 


### 2. 전략 (Strategy)
* **[탐색 필요]** 다른 전략 탐색 (GPT 추천).

### 3. 테스트 (Tests)
* **[확장 필요]** 통합 테스트의 범위 확장: 실제 API 호출을 포함하는 제한된 통합 테스트 추가 (외부 API 안정성 보장 시).
* **[개선 필요]** Mock 객체의 일관성: 공통 픽스처 활용 또는 Mock 설정 유틸리티를 통해 Mock 객체 설정 중복 제거.