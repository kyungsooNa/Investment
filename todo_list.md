# 📈 Investment Trading App - 통합 To-Do List

## Ⅰ. 🚨 최우선 개선 및 버그 수정 (High Priority)
애플리케이션의 핵심 기능 안정성과 데이터 무결성에 직접적인 영향을 미치는 치명적 버그 및 아키텍처 개선 사항입니다.

### 0. 치명적 불량 수정 (Critical Bugs)
- [ ] **[UI]** page 전환마다 term이 너무김. 원인파악하고 개선.
- [ ] **[설정/환경]** `tr_ids_config.yaml`과 `kis_config.yaml`의 `tr_id`, `url`을 `(실전, 모의)` 튜플(Tuple) 형태로 변경. 모의투자에서 지원하지 않는 API는 빈 값으로 두어 자동 차단되도록 수정.
- [ ] **[전략]** 주문 api 실패헀을경우 기록되지 않게 수정. 
  - 전략에서 매도실패시 재시도를 안하는건지, 매수 실패시 포지션에 포함되는건지?
- [ ] **[전략]** 전략에서 OHLCV 조회시, 이미 확정된 어제부터 60일정도의 data는 caching 되어있으니 O1로 바로 가지고 오고 오늘 data는 이미 가지고 있는 data가 있으니 그걸 붙이면 됨. 불필요하게 ohclv api 호출하는 내용을 개선.
(Oneil_pocket_pivot)

- [ ] **[프로그램매매]** 이전 구독종목 복구 중... 이 너무 오래걸림. 현재 모든 tick 단위가 db에 저장되고 있는데, ui에서는 1분이 최소 단위임으로, 보여지는건 최신의 data를 보여주되 db저장은 1분 단위로 마지막 tick 정보만 저장하도록 수정. 이렇게하면 memory에 다 올려도 부담 없을것으로 예상됨. 종목당 (KRX 09시~15시30분 => 390분(개), NTX 포함해도 08시~20시 => 720분(개))

- [ ] **[주문]** 호가 단위 오류 발생시 단위수정하여 재시도 필요.

### 1. 핵심 아키텍처 및 보안 (Core Architecture)
- [ ] **[아키텍처]** UI 출력 로직 완전 격리: Service 계층 내부에 존재하는 콘솔 출력(`print`) 로직을 제거하고, View 계층에 위임.
- [ ] **[보안]** 단순 쿠키 기반 인증을 JWT(JSON Web Token) 기반으로 고도화 (세션 만료 및 Secure/HttpOnly 적용).
- [ ] **[BackgroundService]** BackgroundService는 단순 task들의 background 수행만 관리. ForegourndService 만들어서 앞에서 도는 서비스(UseCase)와 분리. Background로 전체종목 data의 종목현재가, ohlcv, bb, ma, rsi 등 변하지 않는 data DB에서 가지고 있도록 수정. 
기존에 있는 service들은 backgroundService(Scheduler), forgroundService(Scheduler)에서 돌수있는 task interface를 가지게 하고, UserAction이 있으면 backgroundService는 suspend/resume 할 수 있도록 수정. 
두가지 스케줄러에 모두 등록되는 Service 도 있을수 있음.
(e.g) foreground: 현재가 조회, 계자잔고 조회, 매수/매도(최우선 우선순위), 랭킹, 시가총액, 모의투자 기록, 전략 스케줄러(전략 실행/정지), 프로그램매매 구독 등 User Action에 의한 동작
backgournd: 수행중인 전략 scheduler(전략에서 수행하는 매수/매도는 forground의 매수매도와 동일한 최우선순위) 랭킹정보 udpate, 전체 종목 정보 update, oneil_service의 poolA Update 등 장마감 이후 한번씩 고정된 data를 db에 올리는 작업 등.

- telegram_notifier.py, notification_service.py, naver_finance_scraper.py 도 background task로 전환.


## Ⅱ. ⚡ 성능 최적화 및 인프라 전환 (Performance & Infra)
시스템의 반응 속도를 높이고, 장기적인 운영 안정성을 확보하기 위한 데이터베이스 및 인프라 작업입니다.

### 1. 성능 및 캐싱 (Performance)
- [ ] **[차트 렌더링]** 현재가 차트(Candle chart) 조회 속도 1초 미만으로 최적화. (변하지 않는 과거 데이터는 로컬에서 가져오고, 당일 데이터 및 MA/BB 지표만 새로 호출하여 병합).
- [ ] **[StockRepo]** 캐시 성능 고도화. (전략에 watchlist, 보유종목, 관심종목 위주로 cache 구성 하는 background task 추가)

### 2. 데이터베이스 및 인프라 마이그레이션 (Infrastructure)
- [ ] **[DB 마이그레이션]** 상태 저장 및 관리에 사용 중인 기존 파일 기반 데이터(JSON, CSV)를 **SQLite로 전면 마이그레이션 검토 및 적용**. (데이터 무결성 및 동시성 제어 확보)
- [ ] **[컨테이너화]** `Dockerfile` 및 `docker-compose.yml` 작성. FastAPI 서버, 스케줄러, DB 인프라를 묶어 로컬/클라우드 배포 용이성 확보.

---

## Ⅲ. ✨ 신규 기능 및 확장 (New Features)
사용자 편의성을 높이고, 다양한 투자 기회를 포착하기 위한 신규 UI 및 API 확장 사항입니다.

### 1. 웹 UI 및 알림 기능
- [ ] **[관심종목Page]** 관심그룹 and 관심종목 Page를 만들어서 즐겨찾기를 할 수 있는 기능 추가.
- [ ] **[현재가차트]** 거래량에 전일대비 거래량 상승/하락에 대해 빨강/파랑 색상 표시

### 2. 시장 및 API 확장
- [ ] **[데이터 확장]** 체결량 및 체결대금 확인 API (`H0STCNT0`) 웹소켓 추가 및 UI 반영.
- [ ] **[시장 확장]** 종목 검색 및 매매 대상 유니버스에 **NXT 시장** 포함.
- [ ] **[주문/계좌 연동]** 신용 주식 주문, 신용 매수 가능 조회, 주식 예약 주문 및 정정 취소, 실현 손익 등 KIS 신규 API 추가 연동.
- [ ] **[뉴스]** 뉴스 서칭하여 정보 제공하도록 하는 기능 추가 (AI 연동?)

---

## Ⅳ. 🧠 전략 고도화 및 백테스팅 (Strategy & Backtesting)
투자 전략의 승률을 높이고 검증 체계를 고도화하는 작업입니다.

### 1. 공통 전략 시스템 개선
- [ ] **[State 복구 검증]** 장 마감 시간 프로그램 종료 후 재개 시, SQLite/File 기반 State Store/Load가 완벽하게 복구되는지 시나리오 검증 및 정책 수립.

### 2. 강력한 백테스팅 환경 구축
- [ ] **[백테스트 엔진]** 기존 `Strategy` 코드를 100% 재사용할 수 있는 가상 브로커(Mock Broker) 및 과거 데이터 주입기(Historical Data Provider) 개발.
- [ ] **[백테스트 시각화]** 웹 UI(`/virtual`)에 백테스트 결과(MDD, 샤프 지수, 승률, 누적 수익률) 시각화 위젯 연동.

### 3. 신규 및 기존 전략 고도화
- [ ] **[오닐 스퀴즈 V3]** - 업종 소분류 주도 스코어링 (+20점) 추가.
  - 외국인/기관 3일 누적 순매수 대금 기반 스코어링 (+15점) 추가.
  - 코스피/코스닥 마켓 타이밍 지수 직접 조회 연동.
  - 실시간 방아쇠: REST/WS 연동하여 체결강도 120% 이상 & 5,000만 원 이상 고래 탐지 시 즉시 진입(Event-Driven).
- [ ] **[기타 탐색]** GPT 추천 기반 추세 돌파매매(Trend Breakout), ConsolidationScanner 전략 타당성 검토.