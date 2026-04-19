# 📈 Investment Trading App - 통합 To-Do List

## Ⅰ. 🚨 최우선 개선 및 버그 수정 (High Priority)
애플리케이션의 핵심 기능 안정성과 데이터 무결성에 직접적인 영향을 미치는 치명적 버그 및 아키텍처 개선 사항입니다.

### 0. 치명적 불량 수정 (Critical Bugs)

- [ ] **[프로그램매매]** 이전 구독종목 복구 중... 이 너무 오래걸림. 현재 모든 tick 단위가 db에 저장되고 있는데, ui에서는 1분이 최소 단위임으로, 보여지는건 최신의 data를 보여주되 db저장은 1분 단위로 마지막 tick 정보만 저장하도록 수정. 이렇게하면 memory에 다 올려도 부담 없을것으로 예상됨. 종목당 (KRX 09시~15시30분 => 390분(개), NTX 포함해도 08시~20시 => 720분(개))

- [ ] **[Streaming]** 구독중인데 data를 못받는 종목들이 있음. 이 종목들은 현재가조회가 실패함.
- [ ] **[candle_chart]** 신고가/신저가 표기가 3개월/6개월/1년 버튼을 눌러야 변경됨.

### 1. 핵심 아키텍처 및 보안 (Core Architecture)
- [ ] **[보안]** 단순 쿠키 기반 인증을 JWT(JSON Web Token) 기반으로 고도화 (세션 만료 및 Secure/HttpOnly 적용).


## Ⅱ. ⚡ 성능 최적화 및 인프라 전환 (Performance & Infra)
시스템의 반응 속도를 높이고, 장기적인 운영 안정성을 확보하기 위한 데이터베이스 및 인프라 작업입니다.

### 1. 성능 및 캐싱 (Performance)
- [ ] **[]** 


### 2. 데이터베이스 및 인프라 마이그레이션 (Infrastructure)
- [ ] **[DB 마이그레이션]** 상태 저장 및 관리에 사용 중인 기존 파일 기반 데이터(JSON, CSV)를 **SQLite로 전면 마이그레이션 검토 및 적용**. (데이터 무결성 및 동시성 제어 확보)
- [ ] **[컨테이너화]** `Dockerfile` 및 `docker-compose.yml` 작성. FastAPI 서버, 스케줄러, DB 인프라를 묶어 로컬/클라우드 배포 용이성 확보.

---

## Ⅲ. ✨ 신규 기능 및 확장 (New Features)
사용자 편의성을 높이고, 다양한 투자 기회를 포착하기 위한 신규 UI 및 API 확장 사항입니다.

### 1. 웹 UI 및 알림 기능

### 2. 시장 및 API 확장
- [ ] **[시장 확장]** 종목 검색 및 매매 대상 유니버스에 **NXT 시장** 포함.
- [ ] **[시장 확장]** 해외주식 확장.
- [ ] **[주문/계좌 연동]** 신용 주식 주문, 신용 매수 가능 조회, 주식 예약 주문 및 정정 취소, 실현 손익 등 KIS 신규 API 추가 연동.
- [ ] **[뉴스]** 뉴스 서칭하여 정보 제공하도록 하는 기능 추가 (AI 연동?)
- [ ] **[종목분석]** AI? 활용하여 종목 분석하는 기능 추가.
### 3. Task 확장
- [ ] **[]**



---

## Ⅳ. 🧠 전략 고도화 및 백테스팅 (Strategy & Backtesting)
투자 전략의 승률을 높이고 검증 체계를 고도화하는 작업입니다.

### 1. 공통 전략 시스템 개선


### 2. 강력한 백테스팅 환경 구축
- [ ] **[백테스트 엔진]** 기존 `Strategy` 코드를 100% 재사용할 수 있는 가상 브로커(Mock Broker) 및 과거 데이터 주입기(Historical Data Provider) 개발.
- [ ] **[백테스트 엔진]** 기존 `Strategy` 코드를 100% 재사용하여 특정 종목을 넣었을때, 당일(latest) 기준으로 특정 종목들에서 매수신호가 발생했는지, 안했는지, 안했다면 왜안했는지를 판단하는 기능 추가.

- [ ] **[백테스트 시각화]** 웹 UI(`/virtual`)에 백테스트 결과(MDD, 샤프 지수, 승률, 누적 수익률) 시각화 위젯 연동.

### 3. 신규 및 기존 전략 고도화
- [ ] **[오닐 스퀴즈 V3]** - 업종 소분류 주도 스코어링 (+20점) 추가.
  - 코스피/코스닥 마켓 타이밍 지수 직접 조회 연동.
  - 실시간 방아쇠: REST/WS 연동하여 체결강도 120% 이상 & 5,000만 원 이상 고래 탐지 시 즉시 진입(Event-Driven).
- [ ] **[기타 탐색]** GPT 추천 기반 추세 돌파매매(Trend Breakout), ConsolidationScanner 전략 타당성 검토.


### 4. 리뷰 
성능(Performance), 유지보수/안정성(Reliability), 그리고 트레이딩 관점에서의 필수 추가 기능(Missing Features)으로 나누어 엄격하게 짚어드리겠습니다.

1. 성능 관점 (Performance & Bottlenecks)
① 시그널 실행의 순차적 병목 (Sequential Execution Blocking)
scheduler/strategy_scheduler.py의 _run_strategy 내부를 보면 가장 치명적인 병목이 있습니다.

Python
for sig in target_signals:
    if current_count >= cfg.max_positions:
        break
    await self._execute_signal(sig) # ⚠️ 여기서 블로킹 발생
    current_count += 1
여러 종목에 대한 매수 시그널이 동시에 발생했을 때, 첫 번째 종목 주문이 실패하여 OrderExecutionService._retry_order의 재시도 루프(최대 5회, 3초씩 백오프)에 빠지면 어떻게 될까요? 다음 종목의 주문은 최대 15초 이상 지연됩니다. 초단위로 호가가 변하는 장중에는 치명적인 슬리피지(Slippage)를 유발합니다.

개선안: 상태 체크(current_count) 후 주문 실행 자체는 asyncio.gather를 통해 동시 다발적으로(Concurrent) 던지거나, 별도의 Order Queue Worker에 넣어서 비동기로 처리해야 합니다.

② 웹소켓 스트리밍 핸들러의 스레드 풀 고갈 위험
services/streaming_service.py의 dispatch_realtime_message에서 동기(Sync) 핸들러를 처리하는 방식이 위험합니다.

Python
loop.run_in_executor(None, handler, inner)
급등락장에서는 초당 수백~수천 건의 틱(Tick) 데이터가 쏟아집니다. asyncio의 기본 ThreadPoolExecutor 크기는 제한적(일반적으로 min(32, cpu_count() + 4))이므로, 순식간에 스레드 풀이 고갈되어 이벤트 루프 전체가 지연(Lag)되는 현상이 발생할 수 있습니다.

개선안: 틱 데이터 처리에 무거운 동기 함수가 있다면 반드시 ProcessPoolExecutor로 오프로딩하거나, 내부적으로 락/블로킹이 없는 순수 async 함수로 전환해야 합니다.

2. 유지보수 및 안정성 관점 (Reliability & Safety)
① Stage Guard의 'Fail-Open' 정책 (가장 위험한 로직)
strategies/strategy_executor.py에서 타임아웃이나 예외 발생 시의 예외 처리를 보겠습니다.

Python
except Exception:
    return 0  # 오류/타임아웃 → UNKNOWN → 통과
트레이딩 시스템의 제1원칙은 **"상태를 확신할 수 없으면 거래하지 않는다(Fail-Close)"**입니다. API 장애나 네트워크 지연으로 판단을 내릴 수 없는데, 이를 '미계산(0)'으로 치부하고 통과시켜 매수하게 만드는 것은 깡통으로 가는 지름길입니다.

개선안: 예외나 타임아웃 발생 시 무조건 해당 종목은 필터링(제외) 처리해야 합니다. 알 수 없는 리스크는 지는 것이 아닙니다.

② Graceful Shutdown의 부재
main.py의 종료 처리가 아쉽습니다.

Python
except Exception as e:
    logger.exception("애플리케이션 예기치 못한 종료")
Docker나 클라우드 환경, 혹은 터미널 강제 종료 시 OS는 SIGTERM이나 SIGINT(Ctrl+C)를 보냅니다. 이는 일반적인 Exception을 상속하지 않으므로 위 블록에 잡히지 않을 수 있습니다.

개선안: signal 모듈을 사용해 SIGINT, SIGTERM을 명시적으로 캐치하고, 현재 진행 중인 API 주문이 완료될 때까지 기다린 후 스트리밍을 닫고 안전하게 종료(Drain)하는 로직이 필수입니다.

③ 강제 청산(Force Exit) 로직의 '시장가' 의존성
스케줄러에서 장 마감 전 강제 청산을 위해 price=0(시장가)으로 던집니다. 유동성이 풍부한 대형주라면 괜찮지만, 호가창이 얇은 중소형주를 장 마감 직전에 시장가로 던지면 하한가 근처에서 체결되는 '슬리피지 폭탄'을 맞을 수 있습니다.

개선안: 현재 호가(최우선 매수호가)를 조회한 뒤, 일정 틱(Tick) 아래로 지정가 하한선을 두어 주문하는 '조건부 지정가(Limit Order)' 방식이나 분할 매도 로직이 필요합니다.

3. 필수 추가 필요 기능 (Missing Features for Production)
① 한국투자증권 API 서킷 브레이커 (Circuit Breaker)
증권사 API는 간헐적으로 500 에러를 뱉거나 응답이 멈추는 경우가 허다합니다. 현재 ApiRequestQueue에 재시도 로직이 있지만, 증권사 서버 자체가 죽었을 때 계속해서 API를 호출하면 IP 차단을 당할 수 있습니다.

권장: 연속 N회 통신 실패 시 M분 동안 시스템 전체의 주문/조회를 일시 정지하고 텔레그램 알림만 쏘는 '서킷 브레이커' 패턴을 BrokerAPIWrapper 레벨에 구현해야 합니다.

② 포지션 원장 대사 (Reconciliation) 프로세스
현재 VirtualTradeService와 스케줄러의 DB(SCHEDULER_DB_FILE)에 로컬 상태를 저장합니다. 만약 주문 API는 성공하여 실제 주식은 매수되었으나, 로컬 DB 업데이트 직전 앱이 크래시가 난다면 어떻게 될까요? 시스템은 주식이 없는 줄 알고 다음 날 또 매수하게 됩니다.

권장: 장 시작 전(08:50)과 장 마감 후(15:40)에 반드시 실제 증권사 계좌의 잔고 API를 호출하여, 로컬 시스템이 인지하는 보유 잔고와 **"강제 동기화(Sync)"**하는 원장 대사 배치가 있어야 합니다.

③ 미체결 주문 관리 (Open Order Management)
코드 상 주문을 넣고 "성공"으로 간주하지만, 지정가 주문이거나 장 상황에 따라 **'미체결'**로 남을 수 있습니다. 시스템은 미체결된 것을 모른 채 매수/매도되었다고 판단하고 다음 로직을 진행할 위험이 있습니다.

권장: signing_notice (체결 통보 웹소켓)를 받아 실제 잔고를 업데이트하거나, N분 이상 미체결 상태인 주문을 자동 취소(Cancel) 또는 정정(Modify)하는 워치독(Watchdog) 태스크가 필요합니다.