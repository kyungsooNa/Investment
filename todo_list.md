# Investment Trading App - 실전 운영 개선 To-Do

최종 업데이트: 2026-04-29

현재 main 확인: `main...origin/main` 동기화 상태. 최근 반영 커밋 기준으로 주문 안전장치, Web 모드 보호, WebAppContext 분리, 전략 로그 정리, 일부 운영 복구/스트리밍 성능 개선이 반영되어 있다.

이 문서는 `AGENTS.md`, `SKILL.md`, `CODEBASE_SUMMARY.md`, `CODEX_WORKFLOW.md`와 repo review 내용을 기준으로 정리한 실행형 To-Do입니다.

정리 원칙:

- 실전 계좌 보호와 주문 안정성을 최우선으로 둔다.
- 작업 순서는 `주문 직전 안전장치 -> 주문 상태 일관성 -> 실전 모드 보호 -> 전략 검증 -> 운영 복구` 순서로 진행한다.
- 이미 적용된 항목은 새 기능으로 다시 넣지 않고, 검증/고도화 과제로만 남긴다.
- 주문/브로커/스케줄러 변경은 테스트 hang 가이드와 paper/real 분기 검증을 함께 적용한다.

---

## P0. 남은 주문 검증 작업

### 0-1. 실전 KIS `inquire-daily-ccld` 응답 필드 검증

- [blocked] 실제 체결 이력이 있는 실전 계좌 응답을 캡처한다. (현재 실전 계좌 체결 이력 부재)
- [blocked] 민감정보를 제거한 fixture를 추가한다. (실전 응답 확보 후 진행)
- [blocked] paper fixture와 real fixture의 필드 차이를 회귀 테스트에 반영한다. (실전 응답 확보 후 진행)
- [blocked] 주문번호, 종목코드, 매수/매도 구분, 주문수량, 누적체결수량, 미체결수량, 평균체결가, 취소/거부 필드 매핑을 확정한다. (실전 응답 확보 후 진행)

주요 파일:

- `brokers/korea_investment/korea_invest_account_api.py`
- `brokers/korea_investment/korea_invest_trading_api.py`
- `brokers/broker_api_wrapper.py`
- `services/order_execution_service.py`
- `tests/unit_test/`
- `tests/integration_test/`

완료된 기반 작업:

- synthetic fixture/schema 준비
- parser contract test
- sanitized paper captured fixture 회귀 테스트
- stuck-order notification/logging
- `tests/unit_test` timeout 조사 및 실행 시간 단축

---

## P3. 실전/모의 모드 안전장치 강화

실전 계좌로 잘못 주문하는 사고를 막기 위한 보호막입니다. 기본값은 항상 안전한 쪽이어야 합니다.

### 3-1. 실전 모드 이중 안전 체크

- [x] `is_paper_trading` 기본값을 안전(=True/모의)으로 둔다. (`config_loader.py:96`, 키 컨벤션은 `real_trading_enabled`가 아닌 `is_paper_trading`로 통일)
- [x] raw config 기반 `KoreaInvestApiEnv._load_config()`에서도 `is_paper_trading` 누락 시 안전(=True/모의)으로 기본 동작한다. (`korea_invest_env.py`, `test_missing_is_paper_trading_defaults_to_paper_mode`)
- [x] 단건 최대 주문금액(`max_order_amount_won`)을 설정화하고 RiskGate에서 검증한다. (`config_loader.py:58`, `risk_gate_service.py:103-110`)
- [x] 실전 주문 직전 dry-run preview 로그를 남긴다. (`order_execution_service.py` `_log_real_order_preview()` — 종목/수량/가격/계좌 prefix/URL host/source/trace_id 출력, `[REAL ORDER PREVIEW]` 키워드)
- [x] `RiskGateService`에 환경 재검증 가드를 추가한다. (`_check_env_consistency()` — `env.is_paper_trading` vs URL host(`vts` 포함 여부) vs 활성 계좌번호 일치 확인 → 불일치 시 hard block)
- [x] **1일 최대 주문금액(daily cap)** 을 설정화한다. (`RiskGateConfig.max_daily_order_amount_won`, `config.yaml.example`, `risk_gate_service.py` `_check_daily_cap()` + in-memory 누적 트래커, 7일 이상 된 키 자동 정리)
- [x] paper 모드에서 실전 전용 API(랭킹/시총/거래량 랭킹) 호출 시 명시적 `PAPER_NOT_SUPPORTED` 에러를 반환한다. (`korea_invest_quotations_api.py` `@_real_only` 데코레이터 — `get_top_market_cap_stocks_code`, `get_top_rise_fall_stocks`, `get_top_volume_stocks`)

주요 파일:

- `config/config_loader.py`
- `config/config.yaml.example`
- `brokers/korea_investment/korea_invest_env.py`
- `brokers/korea_investment/korea_invest_tr_id_provider.py`
- `services/order_execution_service.py`
- `services/risk_gate_service.py`
- `view/web/web_app_initializer.py`

### 3-2. Web/CLI에 모드 상태 노출

- [x] 웹 화면 status-bar에 실전/모의 모드 배지를 노출한다. (`view/web/templates/base.html:59` `status-env`)
- [x] `/api/status`에 `is_paper_trading` boolean을 내려 웹 모드 판정이 표시 문구/인코딩에 의존하지 않도록 한다. (`view/web/routes/stock.py`, `view/web/static/js/common.js`)
- [x] 실전 모드에서는 주문 화면에 경고 배너 노출 + 주문 버튼 외곽선 강조 + 2단계 확인(`confirm` → "REAL" 입력) 플로우를 적용한다. (`view/web/templates/order.html`, `view/web/static/js/order.js`)
- [x] 실전 수동 주문은 클라이언트 확인뿐 아니라 서버에서도 `real_order_confirmation == "REAL"` 없이는 broker/order service 호출 전 차단한다. (`OrderRequest.real_order_confirmation`, `view/web/routes/order.py`)
- [x] 웹에서 실전 모드로 전환할 때도 서버에서 `real_mode_confirmation == "REAL"` 없이는 차단한다. 모의 전환은 확인 문자열 없이 허용한다. (`EnvironmentRequest.real_mode_confirmation`, `view/web/routes/stock.py`)
- [~] CLI 주문 경로 — N/A. 현재 main.py는 web 전용으로 동작하며 CLI 모드는 제거된 상태(`view/cli/`, `app/user_action_executor.py` 부재).
- [x] `KoreaInvestEnv.set_trading_mode()` 호출 시 base_url/active_account/token_provider가 모드별로 교체되는지 회귀 테스트로 보장한다. (`tests/unit_test/brokers/korea_investment/test_korea_invest_env.py` — `_swaps_base_url`, `_swaps_active_account`, `_swaps_token_provider_reference`, `_no_change_does_not_swap_provider` 4종)

주요 파일:

- `view/web/routes/order.py`
- `view/web/routes/stock.py`
- `view/web/api_common.py`
- `view/web/templates/*`
- `view/web/static/js/common.js`
- `view/web/static/js/order.js`
- `view/cli/cli_view.py`
- `app/user_action_executor.py`

진행 중.
- P4: 전략별/종목별 체결 품질 리포트, 품질 저하 전략 경고/비활성화 후보 표시
- P5: 백테스트-실거래 괴리 추적, 시장 상태 필터, RSI(2) 전략, 펜볼드/돈천 채널형 전략, 백테스트 고도화
- P6: 데이터 품질 공통 검증, 대시보드 강화, 스케줄러 장애 복구 고도화
- P7: BrokerAPIWrapper 테스트 안정화, 남은 DB/전략 계산 성능 측정
- P8: 주문/리스크/브로커/전략 회귀 테스트 보강

main 반영 확인.
- WebAppContext 비대화 해소 1차 완료: broker/service/scheduler bootstrap 분리, `web_api.set_ctx()` 이동, 초기화 실패 로깅 보강
- 주문 복구/reconcile 1차 완료: 미체결 주문 복원, broker 미체결/체결/잔고 대사, 불일치 시 신규 주문 차단 알람
- Kill Switch 1차 완료: 일손실/API 오류/비정상 체결 기반 trip, 상태 저장/복원, 알림 연동
- 전략별 제한 1차 완료: `StrategySchedulerConfig.max_positions`, 웹 max positions 수정, RiskGate 전략 손실/노출/중복 보유 차단
- 스트리밍 성능 1차 완료: program trading tick write buffer/bulk insert, desired subscription batch flush, SQLite WAL 적용 범위 확대
- 신규 전략 1차 완료: `LarryWilliamsVBOStrategy` 추가. 다만 todo의 펜볼드/돈천 채널 전략과는 규칙이 달라 후속 과제로 유지
---

## P4. 실행 품질과 유동성 관리

실전 수익률은 전략 신호뿐 아니라 체결 품질에 크게 좌우됩니다. “전략이 틀린 것인지, 체결이 나쁜 것인지”를 분리해서 볼 수 있어야 합니다.

### 4-1. Execution Quality 추적

- [x] 주문별 예상 체결가와 실제 체결가를 기록한다. (`OrderContext.expected_fill_price`, `average_fill_price`)
- [x] 슬리피지 금액과 슬리피지 비율을 계산한다. (`slippage_amount_won`, `slippage_pct`)
- [~] 주문 타입, 주문 시각, 체결 지연 시간을 함께 기록한다. (`created_at`, `first_fill_latency_sec`는 `OrderContext`에 기록됨. `order_type`은 `OrderPolicyDecision.context`에 남으며 `OrderContext` 영속 필드는 아님)
- [~] 호가 스프레드는 시장가 주문 정책 판단 컨텍스트에 기록한다. 영속 리포트 반영은 후속 작업으로 남긴다.
- [x] 전략별/종목별 체결 품질 리포트를 추가한다. (`execution_quality` JSON 로그 이벤트 + `StrategyLogReportService` 체결 품질 요약)
- [ ] 체결 품질이 기준 이하인 전략은 경고 또는 자동 비활성화 후보로 표시한다.

주요 파일:

- `services/order_execution_service.py`
- `services/strategy_log_report_service.py`
- `repositories/*`
- `core/loggers/json_formatter.py`

### 4-2. Liquidity Control 추가

- [x] 최소 거래대금 필터를 설정화한다. 예: 100억 이상 (`order_policy.min_trading_value_won`)
- [x] 호가잔량 기준 진입 제한을 추가한다. (`order_policy.max_top_of_book_participation_pct`)
- [ ] 체결 강도/체결 속도 기반 필터를 검토한다.
- [ ] 소형주, 관리종목, 투자경고 종목 제외 규칙을 추가한다.
- [~] 유동성 부족으로 차단된 신호를 주문 정책 응답/로그에 남긴다. 전략 로그 연동은 후속 작업으로 남긴다.

주요 파일:

- `services/risk_gate_service.py`
- `services/stock_query_service.py`
- `services/market_data_service.py`
- `strategies/strategy_executor.py`
- `repositories/*`

---

## P5. 전략 검증, 비용 반영, 전략 격리

전략 자체의 품질과 실전 운용 가능성을 검증하는 영역입니다. 백테스트 성과가 실전 성과로 이어지는지 추적해야 합니다.

### 5-1. 전략별 실전 제한값 추가

- [x] 전략별 최대 진입 종목 수를 설정화한다. (`StrategySchedulerConfig.max_positions`, 웹 스케줄러에서 수정 가능, 상태 저장/복원 포함)
- [x] 전략별 손실 제한을 추가한다. (`RiskGateStrategyLimitConfig.max_loss_pct`, `RiskGateService._check_strategy_loss_limit()`)
- [ ] 전략별 거래대금/유동성 필터를 추가한다.
- [~] 전략별 자본 할당을 추가한다. (`RiskGateStrategyLimitConfig.max_exposure_pct` 기반 전략 노출 한도는 있음. 논리적 계좌/자본 장부 분리는 남음)
- [~] 한 전략의 손실이 전체 계좌 주문 차단으로 번지지 않도록 격리 정책을 둔다. (전략별 loss/exposure block은 있음. Kill Switch와의 우선순위/운영 정책 정리는 남음)

주요 파일:

- `strategies/*.py`
- `strategies/strategy_executor.py`
- `services/risk_gate_service.py`
- `services/position_sizing_service.py`
- `config/*.yaml`

### 5-2. 백테스트와 실거래 괴리 추적

- [ ] 백테스트와 실거래 성과를 같은 포맷으로 저장한다.
- [ ] 전략별 기대값 대비 실거래 괴리를 계산한다.
- [ ] 수수료, 세금, 슬리피지 반영 후 순수익을 기본 성과로 사용한다.
- [ ] 전략 degradation 감지 기준을 추가한다.
- [ ] 성과 악화 시 알림 또는 전략 비활성화 후보로 표시한다.

주요 파일:

- `strategies/backtest_data_provider.py`
- `services/strategy_log_report_service.py`
- `task/background/after_market/strategy_log_report_task.py`
- `repositories/*`

### 5-3. 시장 상태 필터 추가

- [ ] 시장 상태를 상승/하락/횡보로 분류하는 기준을 정의한다.
- [ ] 코스피/코스닥 지수 기반 전략 ON/OFF 조건을 추가한다.
- [ ] 변동성 기반 진입 제한을 검토한다.
- [ ] 장 초반/후반 타임 필터를 전략 실행 전에 적용한다.
- [ ] 시장 상태별 백테스트 성과를 분리해 리포트한다.

주요 파일:

- `services/market_data_service.py`
- `services/indicator_service.py`
- `strategies/strategy_executor.py`
- `scheduler/strategy_scheduler.py`

### 5-4. 신규 전략 후보

- [X] RSI(2) 눌림목 전략 후보를 별도 전략으로 설계한다.
  - Oneil/Minervini universe 기반 주도주만 대상으로 한다.
  - RSI(2) 과매도 후 반등을 진입 조건으로 둔다.
  - 5MA 회복 또는 손절 조건을 명확히 둔다.
  - [X] 설계 MD: `strategies/rsi2_pullback_strategy.md`
  - [X] 구현: `strategies/rsi2_pullback_strategy.py` + `strategies/rsi2_pullback_types.py`
  - [X] 단위 테스트: `tests/unit_test/strategies/test_rsi2_pullback_strategy.py` (11건)
  - [X] 통합 테스트: `tests/integration_test/strategies/test_it_api_rsi2_pullback_strategy.py` + `test_it_strategy_scan.py::TestRSI2PullbackScan` (5건)
  - [X] WebAppContext 등록 및 StrategyScheduler 연결 (`view/web/web_app_initializer.py`, `enabled=False` 수동 활성화 대기)
- [ ] Larry Williams 채널 돌파 전략 후보를 별도 전략으로 설계한다.
  - RS Rating, ADX, 거래대금 필터를 함께 적용한다.
  - 20일 고가 돌파, 10일 저가 trailing stop을 검토한다.
  - fixed fractional position sizing을 적용한다.

주요 파일:

- `strategies/*`
- `strategies/oneil_common_types.py`
- `services/indicator_service.py`
- `services/oneil_universe_service.py`

   - [ ] 신규 전략 추가 (확장 2: RSI(2) 눌림목 완료, 확장 3: 펜볼드 채널 돌파 미구현)
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
---

## P6. 데이터 품질, 모니터링, 운영 복구

실시간 자동매매에서 잘못된 데이터와 장애 미감지는 주문 버그만큼 위험합니다.

### 6-1. Data Integrity 검증

- [ ] 가격 이상치 감지 규칙을 추가한다.
- [ ] 체결 데이터 누락을 감지한다.
- [ ] API 응답 값 sanity check를 공통화한다.
- [ ] 데이터 latency를 측정하고 기준 초과 시 주문을 차단한다.
- [~] REST 조회 실패, websocket 미구독, subscribed-no-tick 상태를 구분해 로깅한다. (`subscribed_no_tick`, stale price, force reconnect 로깅은 있음. REST 실패/공통 데이터 품질 차단까지는 남음)

주요 파일:

- `services/streaming_service.py`
- `services/price_stream_service.py`
- `services/price_subscription_service.py`
- `brokers/korea_investment/korea_invest_websocket_api.py`
- `task/background/intraday/websocket_watchdog_task.py`

### 6-2. 알림과 대시보드 강화

- [x] 주문 실패 알림을 추가한다. (`OrderExecutionService` 매수/매도 실패 시 `NotificationService.emit()` + 실패 거래 기록)
- [x] Kill Switch 발동 알림을 추가한다. (`KillSwitchService._trip()` → notification 연동, 상태 저장/복원)
- [~] 체결 이상/슬리피지 과다 알림을 추가한다. (Kill Switch의 abnormal fill deviation trip은 있음. 전략별 슬리피지 리포트/알림은 남음)
- [~] 시스템 상태 대시보드에 활성 전략 수, 현재 포지션, 미체결 주문, 손익을 표시한다. (스케줄러/전략 상태와 max positions UI는 있음. 미체결/손익 통합 운영 대시보드는 남음)
- [~] Telegram/Slack 등 외부 알림 채널을 설정화한다. (`TelegramNotifier`/`TelegramReporter`는 있음. Slack 및 채널별 config 정리는 남음)

주요 파일:

- `services/notification_service.py`
- `task/background/always_on/notification_queue_task.py`
- `view/web/routes/*`
- `view/web/templates/*`

### 6-3. 스케줄러 장애와 재시작 대응

- [ ] 스케줄러 중복 실행 방지 lock을 추가한다.
- [ ] 장 시작 전 상태 점검 task를 추가한다.
- [~] 장 종료 후 미체결/잔고 검증 task를 추가한다. (`OrderExecutionService.reconcile_orders_with_broker()`는 있음. 장 종료 task wiring은 남음)
- [x] WebSocket 끊김 후 자동 재구독 성공 여부를 검증한다. (`KoreaInvestWebSocketApi` auto reconnect/resubscribe, `WebSocketWatchdogTask.force_reconnect()`, 단위 테스트)
- [x] 장애 발생 시 신규 주문 차단 상태로 전환한다. (reconcile alarm 및 Kill Switch 활성 시 신규 주문 차단)
- [~] background task `start/stop/cancel/sleep` lifecycle 테스트를 보강한다. (일부 scheduler/time dispatcher/watchdog 테스트는 있음. `AfterMarketTask` 계열 전반 표준화는 남음)

주요 파일:

- `scheduler/*`
- `task/background/*`
- `view/web/web_app_initializer.py`
- `services/order_execution_service.py`

---

## P7. 구조 개선과 성능 고도화

운영 안정성에 직접 영향을 주는 구조 개선입니다. 기능 구현보다 후순위이지만, 반복 장애를 줄이는 데 중요합니다.

### 7-1. `WebAppContext` 비대화 해소

현황 (2026-04-28 분석, `web_app_initializer.py` 1069줄):

| 메서드 | 현재 역할 | 문제 |
|---|---|---|
| `__init__` | 필드 30+ 선언 + `web_api.set_ctx(self)` | 순환 임포트 (`web_api` ↔ `web_app_initializer`) |
| `load_config_and_env()` | config + env + MarketClock + 알림 서비스 | broker 전처리까지 포함 |
| `initialize_services()` | 토큰 발급 + Broker 생성 + 서비스 전체 | broker/service bootstrap 미분리 |
| `initialize_scheduler()` | 전략 + 태스크 + Scheduler 전체 | strategy/task bootstrap 미분리 |

구체적 이슈:
- `line 71`: `from view.web import web_api` + `line 140`: `web_api.set_ctx(self)` — 계층 역전 (initializer → web_api 임포트)
- `initialize_services()` 내부에서 토큰 발급(broker auth) + `BrokerAPIWrapper` 생성 + 모든 서비스 생성이 순차 실행
- 개별 서비스 생성 실패 시 try/except 없이 전파되어 어느 컴포넌트가 실패했는지 알 수 없음

개선 방향:
- `initialize_services()` → `_bootstrap_broker()` + `_bootstrap_services()` + `_bootstrap_tasks()`로 분해
- `web_api.set_ctx()` 호출을 `web_main.py`의 lifespan으로 이동해 순환 의존 해소
- 각 bootstrap 단계를 `try/except` + 컴포넌트 이름 로깅으로 감쌈

- [X] broker bootstrap, service bootstrap, strategy/task bootstrap을 분리한다.
- [X] 서비스 간 순환 의존을 줄인다. (`web_api.set_ctx` → `web_main.py` lifespan으로 이동)
- [X] 초기화 실패 시 어떤 컴포넌트가 실패했는지 명확히 로깅한다.

주요 파일:

- `view/web/web_app_initializer.py`
- `view/web/web_main.py`

### 7-2. `BrokerAPIWrapper` 테스트 안정화

- [ ] retry/cache wrapper를 우회하는 테스트 helper를 표준화한다.
- [~] wrapper 테스트 mock 반환값은 가능하면 `ResCommonResponse`로 통일한다. (일부 테스트는 아직 plain dict 반환 사용)
- [ ] `@patch` decorator와 async fixture 혼용을 제거한다.
- [ ] `asyncio.sleep` patch 범위를 retry queue 내부까지 확인한다.

주요 파일:

- `brokers/broker_api_wrapper.py`
- `tests/**/conftest.py`
- `tests/unit_test/**`
- `tests/integration_test/**`

### 7-3. 스트리밍/DB 성능 개선

- [x] tick 단위 DB write를 batch insert 또는 queue flush로 전환한다. (`ProgramTradingRepo` write buffer/bulk insert/flush loop)
- [~] 이미 적용된 SQLite WAL 범위를 점검한다. (`cache`, `favorite`, `program_trading`, `rs_rating`, `stock_ohlcv`, `virtual_trade`, `strategy_scheduler_store` 등에 WAL 적용 확인. 전체 DB 경로 점검은 남음)
- [ ] pandas append/concat 병목이 있으면 `deque` 또는 circular buffer로 교체한다.
- [ ] 전략 계산이 event loop를 막는 구간을 측정하고 필요한 경우 worker/process pool로 분리한다.

주요 파일:

- `core/cache/db_cache.py`
- `repositories/program_trading_repo.py`
- `services/indicator_service.py`
- `strategies/strategy_executor.py`
- `scheduler/worker/worker_pool.py`

---

## P8. 테스트 보강 우선순위

### 8-1. 주문/리스크 테스트

- [x] `tests/unit_test/services/test_order_execution_service.py`를 보강한다.
- [x] `tests/unit_test/services/test_risk_gate_service.py`를 신규 생성한다.
- [x] 실전 `finalize_immediately=False` 강제 테스트를 추가한다.
- [x] 주문 접수/부분체결/미체결/취소/거부 상태 테스트를 추가한다.
- [x] 재시작 후 restore/reconcile 테스트를 추가한다. (`restore_active_orders_from_broker`, `reconcile_orders_with_broker`, reconcile alarm 차단)

### 8-2. 브로커 계층 테스트

- [x] 미체결 주문 조회 wrapper/API 테스트를 추가한다. (`BrokerAPIWrapper.inquire_unfilled_orders`, `KoreaInvestAccountApi.inquire_unfilled_orders`)
- [x] 체결 내역 조회 wrapper/API 테스트를 추가한다. (`inquire_daily_ccld` delegation/params/fixture contract)
- [~] 주문 타입별 API 파라미터 검증 테스트를 추가한다. (일부 주문/계좌 조회 params 테스트는 있음. 주문 타입별 정책 매트릭스는 남음)
- [x] paper/real TR ID 분기 테스트를 추가한다. (`test_korea_invest_trid_provider.py`, env mode swap tests)

### 8-3. 전략/스케줄러 테스트

- [x] 전략별 최대 진입 종목 수 테스트를 추가한다. (`test_run_strategy_scan_respects_max_positions`, update/status/restore tests)
- [x] 전략별 손실/노출 제한 테스트를 추가한다. (`test_strategy_loss_limit_blocks`, `test_strategy_exposure_limit_blocks`)
- [ ] 시장 상태 필터 ON/OFF 테스트를 추가한다.
- [~] background task 시작 테스트에는 반드시 `try/finally await stop()`을 적용한다. (일부 반영. `AfterMarketTask` 계열 전체 점검은 남음)
- [ ] xdist 병렬 실행 시 외부 네트워크 호출이 발생하지 않도록 fixture를 점검한다.

권장 실행:

```powershell
C:\Users\Kyungsoo\anaconda3\envs\py310\python.exe -m pytest tests\unit_test\services\test_order_execution_service.py -v -o addopts=
C:\Users\Kyungsoo\anaconda3\envs\py310\python.exe -m pytest tests\unit_test\services\test_risk_gate_service.py -v -o addopts=
C:\Users\Kyungsoo\anaconda3\envs\py310\python.exe -m pytest tests\integration_test -v -o addopts=
```

---

## 바로 착수 추천 순서
1. P0 실전 KIS `inquire-daily-ccld` 실제 응답 검증
   - 실전 체결 이력 fixture 확보 및 민감정보 제거
   - paper/real 필드 차이 회귀 테스트 확정
   - 주문번호/체결수량/미체결수량/평균체결가/취소·거부 필드 매핑 확정

2. P4 체결 품질 리포트
   - 전략별/종목별 slippage/latency 집계
   - 기준 이하 전략을 경고/비활성화 후보로 표시

3. P5 전략 고도화
   - RSI(2) 눌림목 전략 설계/구현
   - 기존 `LarryWilliamsVBO`와 별도로 펜볼드/돈천 채널 전략 구현 여부 결정
   - 백테스트-실거래 괴리, 시장 상태 필터, 포트폴리오 백테스트 추가

4. P6/P7 운영 안정화
   - 데이터 latency/sanity 공통 검증
   - 장 시작 전/장 종료 후 상태 점검 task wiring
   - BrokerAPIWrapper 테스트 helper 표준화 및 xdist 외부 I/O 점검

---

## 완료 기준

- 실전 모드에서 주문 접수만으로 보유/체결이 확정되지 않는다.
- 모든 주문은 Risk Gate 통과 전 broker API를 호출하지 않는다.
- 서비스 재시작 후 미체결 주문과 잔고를 복원 또는 reconcile할 수 있다.
- paper/real URL, TR ID, 토큰, 계좌 분기가 테스트로 검증된다.
- 전략 성과는 수수료, 세금, 슬리피지를 반영한 순수익 기준으로 추적된다.
- 장애, 데이터 지연, websocket 끊김, reconcile 실패 시 신규 주문 차단 또는 경고 상태로 전환된다.



# Strategy Log 남은작업

## 완료 (2026-04-28 세션)

- (a) ✅ 1번 A+B 묶음 커밋 — 반영 완료
- (b) ✅ 전략 로그 파일 누적 (7,414개) 해소
  - 변경: `core/logger.py:get_strategy_logger` 멱등화 + 날짜-only 파일명 + `append_to_latest=True`
  - 파일: `core/loggers/log_config.py`, `core/loggers/size_time_rotating_file_handler.py`, `core/logger.py`, `tests/unit_test/core/test_logger.py`
  - 효과: 같은 프로세스 내 반복 호출 시 핸들러 재사용, 같은 날엔 같은 파일에 append, 10MB 단위 자연 롤오버, 30일 cleanup 유지
- (c) ✅ `get_summary()` 강제종결 제외
  - 변경: `reason=="reconciled_force_close"` 매도는 win_rate/avg_return 계산에서 제외, `force_closed_count` 별도 노출
  - 파일: `repositories/virtual_trade_repository.py`, `tests/unit_test/repositories/test_virtual_trade_repository.py`
- (e) ✅ "삼성전자 50일째 보유중" 오인 표시 — 원인 추적 + 데이터 cleanup + 방어 로그
  - 원인: `data/{pp,osb,fp}_position_state.json` 의 가짜 시드 entry → `sync_live_strategy_positions()` 가 production DB 에 INSERT → DB id 201/202/203 가짜 HOLD 행
  - cleanup: state 파일 가짜 entry 제거, DB 가짜 행 3건 삭제 (백업: `virtual_trade.db.bak_before_fake_cleanup`)
  - 방어: `sync_live_strategy_positions()` 에 INSERT 행 단위 추적 로그 + 자정 buy_date 의심 패턴 warning 추가
  - 가장 유력한 root cause: 테스트/스크립트가 production STATE_FILE 경로(`data/*_position_state.json`)에 직접 시드 데이터 기록

## 미진행 — 2026-04-28 장마감 이후 재확인 예정

- (d) BB 스퀴즈 제거 효과 검증
  - 거래일(2026-04-28) `build_watchlist_finished` 로그로 후보 수 확인
  - 부족 시 옵션 2(시총 별도 범위) / 옵션 3(거래대금 완화) 진행
- (e') 테스트가 production STATE_FILE 경로 못 쓰게 격리 강제
  - 현재 strategy 클래스의 `STATE_FILE` 이 class-level hardcode (`data/pp_position_state.json` 등) → 테스트가 override 안 하면 production 파일 덮어씀
  - 후보 수정: `STATE_FILE` 을 인스턴스 인자로 변경 (또는 환경변수 prefix), 테스트 fixture 자동 tmp_path override
  - 영향 범위 큼 → 별도 작업 권장

## 미진행 — 2026-04-29 장중/장마감 이후 재확인 예정

- (d-2) Pool B 전용 완화 효과 검증
  - 적용 내용: `daily_surge_min_avg_trading_value_5d=50억`, `daily_surge_cap_min=1000억`, `daily_surge_cap_max=100조`
  - 거래일(2026-04-29) 전략 로그에서 `daily_surge_candidates_collected` 확인
    - `raw_count`, `skip_count`, `candidate_count`
  - `daily_surge_pool_sorted` 확인
    - `candidate_count`, `selected_count`, `items`
  - `build_watchlist_finished` 확인
    - `daily_surge_stocks`가 0에서 증가했는지 확인
  - 여전히 0이면 drop 로그 분포 확인
    - `daily_surge_low_trading_value`
    - `daily_surge_market_cap_out_of_range`
    - `not_uptrend`
    - `far_from_52w_high`
  - 부족 시 다음 후보
    - 거래대금 50억 → 30억 추가 완화
    - 정배열 조건을 Pool B 전용으로 `current > ma_20d` 중심으로 완화 검토
