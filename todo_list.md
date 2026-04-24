# Investment Trading App - Refined To-Do List

## 2026-04-24 Summary - Remaining Order Work

### Remaining Order Follow-up
- [ ] Validate real KIS `inquire-daily-ccld` response fields with captured real-account responses.
  - Done already:
    - synthetic fixture/schema prep and parser contract tests
    - sanitized paper captured fixture and regression coverage
    - stuck-order notification/logging
    - `tests/unit_test` timeout investigation and runtime reduction
  - Still required:
    - capture real-account rows for dates/order numbers that actually have executions
    - confirm real response mapping for order number, stock code, side, order qty, cumulative fill qty, remaining qty, average fill price, cancel/reject fields
    - save sanitized real captured examples as regression fixtures

### Recommended Next Order
1. Capture real-account `inquire-daily-ccld` responses that include actual order rows.
2. Add sanitized real fixture(s) and extend regression verification if field shape differs from paper.

최종 업데이트: 2026-04-24

이 문서는 현재 코드베이스와 `CODEX_WORKFLOW.md`, `CODEBASE_SUMMARY.md` 기준으로 다시 정리한 실행용 To-Do다.

정리 원칙:

- 이미 적용된 내용은 신규 도입 과제가 아니라 적용 범위 점검/고도화 과제로 적는다.
- 큰 주제는 바로 착수 가능한 단위로 쪼갠다.
- 우선순위는 실전 운영 위험도, 장애 가능성, 회귀 영향도를 기준으로 둔다.
- 가능하면 `route/service`부터 해결하고, 조립부(`web_app_initializer.py`) 수정은 뒤로 미룬다.

---

## Tier 1. 운영 안정성 / 실전 안전장치

실거래 계좌 보호, 운영 중 장애 방지, 중복 주문/데이터 누락 방지가 최우선이다.

- [ ] 구독 종목 복구 지연 개선
  - 목표: 프로그램매매/실시간 구독 복구 정보를 tick 단위가 아니라 배치 단위로 저장해 DB 쓰기 부담과 복구 지연을 줄인다.
  - 대상:
    - `services/program_trading_stream_service.py`
    - `repositories/program_trading_repo.py`

- [ ] 스트리밍 데이터 수신 누락 원인 정리 및 수정
  - 목표: 현재가 조회 실패 종목, 웹소켓 구독 누락, 재구독 실패를 구분해서 진짜 누락 원인을 고친다.
  - 대상:
    - `services/streaming_service.py`
    - `services/price_stream_service.py`
    - `services/price_subscription_service.py`
    - `brokers/korea_investment/korea_invest_websocket_api.py`
    - `task/background/intraday/websocket_watchdog_task.py`
  - 장중 실검증 체크리스트:
    - 실제 장중에 `python main.py --web` 실행 후 종목 2~3개를 구독한다.
    - 체결가가 정상 수신될 때 `PriceStreamService` 캐시와 관련 스트리밍 로그가 갱신되는지 확인한다.
    - WebSocket 수신 중단을 유도한 뒤 `price_data_gap_*` trigger로 재연결이 발생하는지 확인한다.
    - 재연결 이후 동일 종목의 체결가 수신이 다시 살아나는지 확인한다.
    - PT 데이터는 오는데 체결가가 없을 때 `not_subscribed` 또는 `subscribed_no_tick` 로그가 기대대로 남는지 확인한다.
    - 현재가 REST 조회 실패 상황에서 `rest_failed` 로그가 남는지 확인한다.
    - 장 마감 후 조용한 상태를 장애로 오탐하지 않는지 로그를 함께 점검한다.


- [ ] 계좌 보호용 킬 스위치 추가
  - 목표: 일손실 한도, 연속 손실, 비정상 응답 반복, 체결 이상 시 자동으로 주문/전략 실행을 막는다.
  - 대상:
    - `services/order_execution_service.py`
    - `scheduler/strategy_scheduler.py`
    - `services/notification_service.py`

- [ ] 포지션 사이징 고도화
  - 목표: ATR 또는 변동성 기반으로 진입 수량을 동적으로 제한하고 계좌 노출을 제어한다.
  - 대상:
    - `services/order_execution_service.py`
    - `services/indicator_service.py`
    - 필요 시 신규 유틸 모듈

- [ ] 주문 추적용 Trace ID 파이프라인 구축
  - 목표: 신호 생성부터 주문 전송, 체결, 취소, 알림까지 하나의 ID로 추적 가능하게 만든다.
  - 대상:
    - `core/loggers/app_logger.py`
    - `core/loggers/json_formatter.py`
    - `services/order_execution_service.py`
    - `scheduler/strategy_scheduler.py`

- [ ] 모의투자 기록에 실제 전략의 매매 기록과 sync가 안맞는 버그 존재
  - 4.24에 오닐PP/BGU	한화비전(489790), 오닐스퀴즈돌파	코리아써키트(007810), 하이타이트플래그	코리아써키트(007810), 첫눌림목	SNT에너지(100840), 오닐PP/BGU	SNT에너지(100840)	등 존재하는 기록이 있지만 반영안되어있음.
  - 대상:
    - `repositories/virtual_trade_repository.py`
    - `services/virtual_trade_service.py`
    - `view/web/routes/virtual.py`
---

## Tier 2. 구조 안정화 / 유지보수성

이 프로젝트는 기능보다 조립 구조와 생명주기 리스크가 커서, 운영 안정성을 위해 구조 정리가 필요하다.

- [ ] `WebAppContext` 비대화 해소
  - 목표: 브로커 생성, 서비스 조립, 전략 등록, 태스크 초기화를 단계적으로 분리한다.
  - 방향:
    - broker bootstrap
    - service bootstrap
    - strategy/task bootstrap
  - 대상:
    - `view/web/web_app_initializer.py`

- [ ] 서비스 초기화 순서 의존 제거
  - 목표: 후주입 방식으로 서로 얽힌 서비스 의존성을 줄이고 테스트 누락 위험을 낮춘다.
  - 대상:
    - `view/web/web_app_initializer.py`
    - 관련 서비스 파일 전반

- [ ] background task 공통 lifecycle 점검 및 표준화
  - 목표: `start()`, `stop()`, `cancel`, long sleep, restore 경로를 공통 규약으로 맞추고 hang 리스크를 줄인다.
  - 대상:
    - `task/background/*`
    - `scheduler/strategy_scheduler.py`
    - 관련 테스트

- [ ] `BrokerAPIWrapper` 테스트 안전장치 표준화
  - 목표: retry/cache wrapper 때문에 발생하는 hang 패턴을 fixture/helper로 표준화한다.
  - 대상:
    - `brokers/broker_api_wrapper.py`
    - `tests/unit_test/**`
    - `tests/integration_test/**`
    - `tests/**/conftest.py`


- [ ] 실행 진입점 문서 정합성 정리
  - 목표: 문서상 CLI/Web 설명과 실제 `main.py` 동작의 차이를 정리한다.
  - 대상:
    - `main.py`
    - `AGENTS.md`
    - `CODEBASE_SUMMARY.md`
    - `README` 또는 관련 문서

---

## Tier 3. 성능 / 병목 해소

이미 적용된 최적화는 유지하되, 실제 병목이 남아 있는 구간만 좁혀서 개선한다.

- [ ] DB WAL 적용 범위 점검 및 벌크 쓰기 도입
  - 상태:
    - WAL 자체는 일부 저장소/캐시에 이미 적용되어 있음.
  - 목표:
    - tick/스트림/리포트성 데이터의 잦은 쓰기를 batch insert 또는 queue flush 방식으로 줄인다.
  - 대상:
    - `core/cache/db_cache.py`
    - `repositories/program_trading_repo.py`
    - 관련 DB writer 코드

- [ ] 실시간 지표 계산의 buffer 구조 개선
  - 상태:
    - `orjson`은 이미 여러 핵심 경로에 적용되어 있음.
  - 목표:
    - Pandas append/concat 의존 구간이 있으면 `deque` 또는 circular buffer로 교체한다.
  - 대상:
    - `services/indicator_service.py`
    - `strategies/strategy_executor.py`
    - 필요 시 `scheduler/worker/worker_pool.py`

- [ ] I/O와 CPU 연산 격리 검토
  - 목표: 전략 대량 평가나 지표 재계산이 event loop를 막는지 측정한 뒤, 필요한 구간만 `ProcessPoolExecutor`로 분리한다.
  - 대상:
    - `strategies/strategy_executor.py`
    - `scheduler/worker/worker_pool.py`
    - `scheduler/strategy_scheduler.py`

- [ ] HTTP 세션 재사용 실태 점검
  - 목표: 주문/조회 시 세션 재생성이 반복되는지 먼저 확인하고, 필요할 때만 keep-alive/pool 전략을 강화한다.
  - 대상:
    - `brokers/korea_investment/korea_invest_api_base.py`
    - `brokers/korea_investment/korea_invest_client.py`
    - `view/web/web_app_initializer.py`

---

## Tier 4. 전략 / 백테스트 / 실행 분리

전략 품질 향상도 중요하지만, 실전 실행 엔진과 강하게 얽혀 있으면 확장과 검증이 어려워진다.

- [ ] 전략 신호와 실행 책임 분리
  - 목표: 전략은 시그널만 발행하고, 실행 서비스가 실제 주문/가상주문/기록 여부를 결정하도록 분리한다.
  - 대상:
    - `scheduler/ticket_queue/message_broker.py`
    - `strategies/strategy_executor.py`
    - `services/order_execution_service.py`

- [ ] 백테스트용 가상 브로커/Mock 실행 엔진 도입
  - 목표: 실전 주문 흐름과 유사한 인터페이스로 전략 재사용성과 현실성을 높인다.
  - 대상:
    - `interfaces/strategy.py`
    - `strategies/backtest_data_provider.py`
    - `services/virtual_trade_service.py`

- [ ] 전략 후보군/필터 계층 고도화
  - 목표: 오닐, Minervini, RS, 신고가 기반 후보군 선별 로직을 전략과 분리해서 재사용 가능하게 정리한다.
  - 대상:
    - `services/oneil_universe_service.py`
    - `services/minervini_stage_service.py`
    - `services/rs_rating_service.py`
    - `services/newhigh_service.py`

- [ ] 전략 노후화 감지 및 자동 관망 전환
  - 목표: 전략별 최근 실거래 성과를 기준으로 경고 또는 자동 비활성화 기준을 둔다.
  - 대상:
    - `services/strategy_log_report_service.py`
    - `task/background/after_market/strategy_log_report_task.py`
    - 필요 시 `scheduler/strategy_scheduler.py`

- [ ] 신규 전략 추가
  - 🦅 [확장 2] 래리 코너스 RSI(2) 눌림목핵심 철학: "대세 상승장(Stage 2)에서도 주가는 숨을 고른다. RSI(2)가 10 이하라는 것은 고무줄이 팽팽하게 당겨진 상태와 같으므로 반등의 탄성이 가장 크다."
    1. 🚀 페이즈 1: 주도주 및 추세 확인 (Setup)WatchList: OneilUniverseService의 Pool A (우량주) 사용.장기 추세 (미너비니): 주가가 200일 이동평균선 위에 위치하며 200일선이 우상향 중일 것.단기 과매도: 실시간 RSI(기간: 2) 값이 10 이하로 떨어질 것.
    2. 🎯 페이즈 2: 매수 진입 (Trigger)진입 타이밍: RSI(2) < 10 조건이 충족된 상태에서 15:10 이후 종가 베팅 진입.안전장치: 지수 마켓 타이밍(20MA 우상향)이 🔴이더라도, 개별 종목의 장기 추세(200MA)가 강력하다면 비중의 50%만 진입 허용.
    3. ✂️ 페이즈 3: 청산 전략 (Exit)빠른 복귀 익절: 주가가 **5일 이동평균선(5MA)**을 돌파(Touch)하는 순간 전량 익절. (평균 보유 기간 2.5일)하드 스탑: 진입가 대비 -5% 하락하거나, 주가가 200일 이동평균선을 하향 이탈 시 즉시 전량 손절.🦅 
  
  - [확장 3] 브렌트 펜볼드 돈천 채널 돌파핵심 철학: "예측하려 하지 마라. 시장이 신고가를 쓴다는 것은 그 자체가 가장 강력한 매수 신호다. 대신 자금 관리를 통해 파산을 원천 차단하라."
    1. 🚀 페이즈 1: 시장 에너지 검증 (Setup)WatchList: OneilUniverseService의 전체 워치리스트 중 RS Rating 80 이상 종목.추세 강도 필터: ADX(14) 값이 25 이상이며 우상향 중일 것. (횡보장에서의 잦은 손절 방지)
    2. 🎯 페이즈 2: 채널 돌파 (Trigger)진입 기준: 현재가가 최근 20거래일 중 최고가를 돌파하는 순간.거래량 확인: 장중 환산(예상) 거래량이 20일 평균 거래량의 150% 이상.자금 관리 (핵심): 펜볼드식 Fixed Fractional 적용.$$수량(Qty) = \frac{총자산 \times 0.015(리스크 비중)}{진입가 - 손절가}$$단일 종목 손실이 전체 자산의 1.5%를 넘지 않도록 수량을 자동으로 조절함.
    3. ✂️ 페이즈 3: 추세 추종 청산 (Exit)트레일링 채널 스탑: 주가가 최근 10거래일 중 최저가를 하향 이탈할 때까지 홀딩. (수익이 날수록 청산가가 자동으로 올라옴)칼손절: 진입 직후에는 20일 채널 하단 또는 진입가 대비 -7% 중 짧은 것을 손절선으로 설정.
  
  💡 구현을 위한 추가 Tip (나경수님의 시스템 기준)Config 클래스 생성: FirstPullbackConfig처럼 각 전략에 맞는 LarryWilliamsConfig, RSI2Config 등을 oneil_common_types.py에 추가하세요.지표 함수 활용: RSI(2)와 ADX(14)는 기존 IndicatorService에 calc_rsi_sync, calc_adx_sync 형태로 추가하여 scan 로직에서 호출하면 됩니다.PositionState 관리: FPPositionState와 유사하게 래리 코너스 전략은 entry_rsi를, 펜볼드 전략은 channel_low_10d를 저장하여 청산 시 참조하도록 설계하세요.

- [ ] 벡테스트 고도화
  - 1. 전략 코드를 수정하지 않고 "왜 안 샀을까?"(디버깅 백테스트) 구현하기
    - 기존 전략 클래스(예: VolumeBreakoutStrategy)의 내부 로직을 직접 뜯어고치지 않고도 디버깅 백테스트를 수행할 수 있습니다. 주로 다음과 같은 디자인 패턴과 우회 기법을 사용합니다.

    - A. 설명자(Explainer) / 검사기(Inspector) 모듈 분리 (추천)
      - 전략 코드 자체는 매수/매도 신호만 발생시키는 순수 함수 역할을 유지합니다. 대신, 특정 종목이 왜 탈락했는지 분석하는 StrategyExplainer 클래스를 별도로 만듭니다.
      - 이 클래스는 전략이 사용하는 조건식(장기 추세, 거래량 배수, 이격도 등)을 단계별로 검사하여 리포트를 생성합니다. 로직이 일부 중복될 수 있다는 단점이 있지만, 실거래용 실행 엔진을 완벽하게 무결한 상태로 유지할 수 있습니다.

    - B. 프록시(Proxy) 객체를 통한 데이터 가로채기
      - 전략 코드는 데이터를 stock_query_service나 지표 계산 서비스에서 가져옵니다. 백테스트 시 이 서비스들을 감싸는(Wrap) 프록시(Proxy) 객체를 주입합니다. 프록시는 전략이 특정 종목의 '200일 이동평균선'이나 '어제 거래량'을 요청할 때마다, 그 값을 백그라운드에서 기록(Trace)해 둡니다. 전략이 최종적으로 매수 신호를 내지 않고 종료되면, 프록시에 남은 기록을 분석하여 어느 지표에서 막혔는지 역추적할 수 있습니다.

    - C. 옵저버(Observer) 패턴 도입 (약간의 인터페이스 수정 필요)
      - 전략 코드 자체의 '로직'은 수정하지 않되, 진행 상태를 외부로 방송(Emit)하는 한 줄짜리 이벤트 알림 코드만 추가하는 방식입니다. 실거래 시에는 이 방송을 아무도 듣지 않지만, 디버깅 백테스트 시에는 TraceObserver가 방송을 듣고 실패 사유를 수집합니다.

  - 2. 단일 종목 / 수익률 검증 외에 반드시 필요한 백테스트 유형
    - VCP 패턴이나 포켓 피봇과 같은 추세 추종 및 돌파 매매는 단순히 종목 하나의 과거 수익률을 보는 것만으로는 실전 성과를 담보하기 어렵습니다. 시스템을 고도화하기 위해 다음의 4가지 백테스트가 추가로 필요합니다.

    -  ① 포트폴리오(계좌) 단위 백테스트 (System / Portfolio Backtest)
      - 가장 중요한 백테스트입니다. 종목 하나가 아니라 계좌 전체의 자금 흐름과 제약을 시뮬레이션합니다.
      - 이유: 돌파 매매 특성상 시장이 좋아지는 특정 시점에 여러 주도주에서 동시에 매수 신호가 터져 나옵니다. 종목별 백테스트에서는 모두 수익이 난 것으로 계산되지만, 실제로는 계좌에 현금이 없어 3번째 종목부터는 매수하지 못합니다.
      - 검증 내용: 동시다발적 신호 발생 시 우선순위(예: RS Rating이 더 높은 것 우선) 기준 작동 여부, 자금의 100%가 묶였을 때의 기회비용, 포지션 사이징(예: 종목당 비중 20% 제한) 로직의 성과를 검증합니다.

    - ② 마켓 타이밍(시장 지수) 연동 백테스트 (Market Regime Testing)
      - 이유: 개별 종목의 조건이 아무리 좋아도 코스피/코스닥 지수가 20일선 아래에서 역배열로 꺾이고 있다면 돌파는 휩소(속임수)로 끝날 확률이 매우 높습니다.
      - 검증 내용: 대세 하락장(Bear Market), 횡보장, 대세 상승장(Bull Market) 등 시장 국면을 나누어 전략의 성과를 분리해 봅니다. 지표(예: 지수 20MA 이탈)에 따라 전략의 진입 비중을 줄이거나 아예 매매를 멈추는 룰(Kill-switch)이 계좌를 얼마나 방어해주는지 확인합니다.

    - ③ 슬리피지 및 체결 지연 스트레스 테스트 (Slippage Sensitivity Analysis)
      - 이유: 거래량 돌파 전략은 돌파 순간에 시장가 매수세가 몰립니다. 백테스트 상으로는 +10% 트리거에 정확히 체결된 것으로 나오지만, 실전에서는 호가창이 얇거나 체결 속도가 밀려 +11%, +12%에 체결(슬리피지)될 수 있습니다.
      - 검증 내용: 진입가와 청산가에 고의로 페널티(예: 매수 시 +0.5% 비싸게, 매도 시 -0.5% 싸게 체결)를 주었을 때도 전략이 우상향하는지 검증합니다. 이 페널티를 견디지 못한다면 실전에서는 수수료와 세금, 슬리피지에 계좌가 녹아내리게 됩니다.

    - ④ 몬테카를로 시뮬레이션 (Monte Carlo Simulation)
      - 이유: 과거의 특정 시기에 우연히 "운이 좋아서" 높은 수익률이 나왔을 가능성을 배제하기 위함입니다.
      - 검증 내용: 백테스트에서 발생한 수백 번의 매매 결과(수익/손실 비율)를 무작위로 섞어버립니다. 만약 최악의 운이 작용해서 손절이 5번, 10번 연속으로 먼저 발생했을 때 내 계좌가 파산(Ruin)하지 않고 버틸 수 있는지(최대 낙폭, MDD)를 수학적으로 검증합니다.
---

## Tier 5. 테스트 / 관측성 / 운영성

실전 시스템은 기능 구현보다 장애를 빨리 찾고 안전하게 복구하는 능력이 중요하다.

- [ ] 네트워크 장애/결함 주입 테스트 추가
  - 목표: KIS API 500, rate limit, websocket 끊김, 지연 수신을 모의하는 테스트를 추가한다.
  - 대상:
    - `tests/integration_test/test_it_crash_handler.py`
    - `core/retry_queue/client_with_retry_queue.py`
    - 스트리밍 관련 테스트

- [ ] queue/backpressure 부하 테스트 추가
  - 목표: 순간적인 tick 폭주 시 OOM 없이 drop/backpressure가 동작하는지 검증한다.
  - 대상:
    - `scheduler/ticket_queue/message_broker.py`
    - `services/streaming_service.py`
    - 관련 테스트

- [ ] 운영 로그 블랙박스화
  - 목표: 주문 실패, 슬리피지, 재시도 폭주, 복구 이벤트를 짧은 시간 안에 추적 가능하게 로그 구조를 표준화한다.
  - 대상:
    - `core/loggers/app_logger.py`
    - `core/loggers/json_formatter.py`
    - `core/loggers/streaming_event_logger.py`

- [ ] 인증/인프라/UI 항목 분리 정리
  - 목표: 한 항목에 묶인 서로 다른 작업을 분리한다.
  - 세부:
    - 인증 강화: `view/web/routes/auth.py`
    - 컨테이너화: `Dockerfile` 신규 여부 검토
    - 가상매매 UI 개선: `view/web/templates/virtual.html`

---

## 이미 적용되어 있어 재정의한 항목

아래 항목은 "신규 도입"이 아니라 "적용 범위 점검/고도화"로 보는 것이 맞다.

- `orjson`
  - 이미 적용된 경로 예:
    - `brokers/korea_investment/korea_invest_api_base.py`
    - `brokers/korea_investment/korea_invest_websocket_api.py`
    - `core/loggers/json_formatter.py`
    - `scheduler/strategy_scheduler.py`

- SQLite WAL
  - 이미 적용된 경로 예:
    - `core/cache/db_cache.py`
    - 일부 repository 구현

- 인증/UI 일부 기반
  - 이미 존재:
    - `view/web/routes/auth.py`
    - `view/web/templates/virtual.html`
  - 아직 없음:
    - `Dockerfile`

---

## 지금 바로 착수 추천 순서

1. real 계좌 `inquire-daily-ccld` 실응답 캡처 및 fixture화
2. 계좌 보호용 킬 스위치 추가
3. 구독 종목 복구 지연 개선
4. 스트리밍 데이터 수신 누락 원인 정리 및 수정
5. `WebAppContext` 비대화 해소
