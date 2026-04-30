# Investment Trading App - 실전 운영 개선 To-Do

최종 업데이트: 2026-04-30 (디버깅 백테스트 Phase 1 + 상태 격리 보강 완료)

## 2026-04-29 Update - O'Neil Market Timing

- [x] `_check_etf_ma_rising()` strict consecutive MA(20) rise filter was relaxed to a net-trend check with small daily MA dip tolerance.
- [x] Market timing debug logs now include `trend_status`, `daily_changes_pct`, `net_change_pct`, `max_daily_drop_pct`, and the active threshold values.
- [x] Unit tests were updated for small KOSDAQ-style MA noise, hard MA declines, and enriched market timing logs.

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
- P5: 백테스트-실거래 괴리 추적, 시장 상태 필터, RSI(2) 전략, 펜볼드/돈천 채널형 전략, 백테스트 고도화
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

- [X] Larry Williams / 브렌트 펜볼드 돈천 채널 돌파 전략을 설계·구현한다. (L218 확장 3과 동일 전략으로 통합)
  - [X] RS Rating ≥ 80, ADX(14) ≥ 25 우상향, 거래대금 필터를 함께 적용한다.
  - [X] 20일 고가 돌파 진입, 10일 저가 trailing stop 청산.
  - [X] Fixed Fractional position sizing (`risk_per_trade_pct=1.5`).
  - [X] 설계 MD: `strategies/larry_williams_channel_breakout_strategy.md`
  - [X] 타입: `strategies/larry_williams_cb_types.py` (`LarryWilliamsCBConfig`, `LarryWilliamsCBPositionState`)
  - [X] 구현: `strategies/larry_williams_channel_breakout_strategy.py`
  - [X] ADX 인프라: `services/indicator_service.py` — `_compute_adx` + `calc_adx_sync` 추가
  - [X] 단위 테스트: `tests/unit_test/strategies/test_larry_williams_channel_breakout_strategy.py` (16건) + ADX 테스트 4건
  - [X] WebAppContext 등록 (`view/web/web_app_initializer.py`, `enabled=False` 수동 활성화 대기)
  - 미적용 리뷰 항목
      ADX 우상황 주석: 본 전략 파일이 아닌 IndicatorService.calc_adx_sync 내부 사항이라 별도 PR 권장.
      _extract_* None 체크 명시화: 사소, 동작 동일하므로 보류.
      check_exits OHLCV 재호출: 문서에 트레이드오프 명시됨. 운영 모니터링 후 결정.
주요 파일:

- `strategies/*`
- `strategies/oneil_common_types.py`
- `services/indicator_service.py`
- `services/oneil_universe_service.py`

   - [X] 신규 전략 추가 (확장 2: RSI(2) 눌림목 완료, 확장 3: 펜볼드/돈천 채널 돌파 → `LarryWilliamsChannelBreakoutStrategy` 로 구현 완료)
   - 🦅 [확장 2] 래리 코너스 RSI(2) 눌림목핵심 철학: "대세 상승장(Stage 2)에서도 주가는 숨을 고른다. RSI(2)가 10 이하라는 것은 고무줄이 팽팽하게 당겨진 상태와 같으므로 반등의 탄성이 가장 크다."
      1. 🚀 페이즈 1: 주도주 및 추세 확인 (Setup)WatchList: OneilUniverseService의 Pool A (우량주) 사용.장기 추세 (미너비니): 주가가 200일 이동평균선 위에 위치하며 200일선이 우상향 중일 것.단기 과매도: 실시간 RSI(기간: 2) 값이 10 이하로 떨어질 것.
      2. 🎯 페이즈 2: 매수 진입 (Trigger)진입 타이밍: RSI(2) < 10 조건이 충족된 상태에서 15:10 이후 종가 베팅 진입.안전장치: 지수 마켓 타이밍(20MA 우상향)이 🔴이더라도, 개별 종목의 장기 추세(200MA)가 강력하다면 비중의 50%만 진입 허용.
      3. ✂️ 페이즈 3: 청산 전략 (Exit)빠른 복귀 익절: 주가가 **5일 이동평균선(5MA)**을 돌파(Touch)하는 순간 전량 익절. (평균 보유 기간 2.5일)하드 스탑: 진입가 대비 -5% 하락하거나, 주가가 200일 이동평균선을 하향 이탈 시 즉시 전량 손절.🦅 
   
   - [확장 3] 브렌트 펜볼드 돈천 채널 돌파핵심 철학: "예측하려 하지 마라. 시장이 신고가를 쓴다는 것은 그 자체가 가장 강력한 매수 신호다. 대신 자금 관리를 통해 파산을 원천 차단하라."
      1. 🚀 페이즈 1: 시장 에너지 검증 (Setup)WatchList: OneilUniverseService의 전체 워치리스트 중 RS Rating 80 이상 종목.추세 강도 필터: ADX(14) 값이 25 이상이며 우상향 중일 것. (횡보장에서의 잦은 손절 방지)
      2. 🎯 페이즈 2: 채널 돌파 (Trigger)진입 기준: 현재가가 최근 20거래일 중 최고가를 돌파하는 순간.거래량 확인: 장중 환산(예상) 거래량이 20일 평균 거래량의 150% 이상.자금 관리 (핵심): 펜볼드식 Fixed Fractional 적용.$$수량(Qty) = \frac{총자산 \times 0.015(리스크 비중)}{진입가 - 손절가}$$단일 종목 손실이 전체 자산의 1.5%를 넘지 않도록 수량을 자동으로 조절함.
      3. ✂️ 페이즈 3: 추세 추종 청산 (Exit)트레일링 채널 스탑: 주가가 최근 10거래일 중 최저가를 하향 이탈할 때까지 홀딩. (수익이 날수록 청산가가 자동으로 올라옴)칼손절: 진입 직후에는 20일 채널 하단 또는 진입가 대비 -7% 중 짧은 것을 손절선으로 설정.
   
   💡 구현을 위한 추가 Tip (나경수님의 시스템 기준)Config 클래스 생성: FirstPullbackConfig처럼 각 전략에 맞는 LarryWilliamsConfig, RSI2Config 등을 oneil_common_types.py에 추가하세요.지표 함수 활용: RSI(2)와 ADX(14)는 기존 IndicatorService에 calc_rsi_sync, calc_adx_sync 형태로 추가하여 scan 로직에서 호출하면 됩니다.PositionState 관리: FPPositionState와 유사하게 래리 코너스 전략은 entry_rsi를, 펜볼드 전략은 channel_low_10d를 저장하여 청산 시 참조하도록 설계하세요.

   - [x] 벡테스트 고도화
   - 1. 전략 코드를 수정하지 않고 "왜 안 샀을까?"(디버깅 백테스트) 구현하기 ✅ **Phase 1 완료 (2026-04-30)**
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

      -  ① 포트폴리오(계좌) 단위 백테스트 (System / Portfolio Backtest) **[우선순위 1 — 가장 중요]**
         - 가장 중요한 백테스트입니다. 종목 하나가 아니라 계좌 전체의 자금 흐름과 제약을 시뮬레이션합니다.
         - 이유: 돌파 매매 특성상 시장이 좋아지는 특정 시점에 여러 주도주에서 동시에 매수 신호가 터져 나옵니다. 종목별 백테스트에서는 모두 수익이 난 것으로 계산되지만, 실제로는 계좌에 현금이 없어 3번째 종목부터는 매수하지 못합니다.
         - 검증 내용: 동시다발적 신호 발생 시 우선순위(예: RS Rating이 더 높은 것 우선) 기준 작동 여부, 자금의 100%가 묶였을 때의 기회비용, 포지션 사이징(예: 종목당 비중 20% 제한) 로직의 성과를 검증합니다.

      - ② 마켓 타이밍(시장 지수) 연동 백테스트 (Market Regime Testing) **[우선순위 2]**
         - 이유: 개별 종목의 조건이 아무리 좋아도 코스피/코스닥 지수가 20일선 아래에서 역배열로 꺾이고 있다면 돌파는 휩소(속임수)로 끝날 확률이 매우 높습니다.
         - 검증 내용: 대세 하락장(Bear Market), 횡보장, 대세 상승장(Bull Market) 등 시장 국면을 나누어 전략의 성과를 분리해 봅니다. 지표(예: 지수 20MA 이탈)에 따라 전략의 진입 비중을 줄이거나 아예 매매를 멈추는 룰(Kill-switch)이 계좌를 얼마나 방어해주는지 확인합니다.

      - ③ 슬리피지 및 체결 지연 스트레스 테스트 (Slippage Sensitivity Analysis) **[우선순위 3]**
         - 이유: 거래량 돌파 전략은 돌파 순간에 시장가 매수세가 몰립니다. 백테스트 상으로는 +10% 트리거에 정확히 체결된 것으로 나오지만, 실전에서는 호가창이 얇거나 체결 속도가 밀려 +11%, +12%에 체결(슬리피지)될 수 있습니다.
         - 검증 내용: 진입가와 청산가에 고의로 페널티(예: 매수 시 +0.5% 비싸게, 매도 시 -0.5% 싸게 체결)를 주었을 때도 전략이 우상향하는지 검증합니다. 이 페널티를 견디지 못한다면 실전에서는 수수료와 세금, 슬리피지에 계좌가 녹아내리게 됩니다.

      - ④ 몬테카를로 시뮬레이션 (Monte Carlo Simulation) **[우선순위 4]**
         - 이유: 과거의 특정 시기에 우연히 "운이 좋아서" 높은 수익률이 나왔을 가능성을 배제하기 위함입니다.
         - 검증 내용: 백테스트에서 발생한 수백 번의 매매 결과(수익/손실 비율)를 무작위로 섞어버립니다. 만약 최악의 운이 작용해서 손절이 5번, 10번 연속으로 먼저 발생했을 때 내 계좌가 파산(Ruin)하지 않고 버틸 수 있는지(최대 낙폭, MDD)를 수학적으로 검증합니다.
   - 3. 후속 개선 항목 (디버깅 백테스트 확장)
      - [x] 특정 종목이 당일 매수되지 않은 이유를 CLI에서 확인하는 기능 추가
         - `python -m scripts.run_strategy_debug --codes 005930,000660`
         - `RejectionCollector`가 전략의 dict 구조화 로그를 수집하고, `StrategyDebugRunner`가 특정 종목만 스캔하도록 universe를 프록시한다.
         - 디버그 실행 중 매수 신호가 발생해도 실전 전략 상태 파일(`data/pp_position_state.json`)을 변경하지 않도록 임시 state file을 주입한다.
         - universe 전체 스캔 시에도 실제 스캔 종목 수가 리포트에 표시되도록 보강한다.
      - [x] `StrategyExecutor` StageGuard 로그를 dict 구조화 로그로 전환 → `RejectionLogHandler` 캡처 대상 확장 (`stage_blocked` 이벤트 추가)
      - [ ] 과거 시점 시뮬레이션 지원 — `--date YYYYMMDD` 인자로 그날 OHLCV 기반 PP 조건 재현
      - [ ] 다른 전략(VolumeBreakout, HighTightFlag 등) 디버깅 어댑터 추가
      - [ ] 디버깅 리포트에 `scan_skipped`(장 미개장/마켓타이밍 불량/워치리스트 없음)와 종목별 탈락 이벤트를 구분해 표시한다.
      - [ ] PP/BGU 공통 진입 단계에 `entry_type`을 항상 포함해 체결강도/스마트머니 탈락 원인을 추정이 아닌 확정값으로 표시한다.

   - 4. 백테스트 엔진 후속 작업
      - [ ] `--date YYYYMMDD` / `--from YYYYMMDD --to YYYYMMDD` 기반 과거 시점 재현용 market clock과 데이터 스냅샷 주입 구조를 만든다.
      - [ ] 실시간 API 응답 대신 과거 OHLCV/체결강도/프로그램매매 데이터를 공급하는 `BacktestStockQueryService` 또는 data replay adapter를 추가한다.
      - [ ] 체결 시뮬레이터를 분리한다: 지정가/시장가, 당일 고가·저가 도달 여부, 거래량 기반 부분체결, 미체결, 다음 봉 체결 정책을 명시한다.
      - [ ] 수수료, 거래세, 슬리피지, 호가 단위 반올림을 모든 백테스트 성과 계산에 기본 반영한다.
      - [ ] 포트폴리오 단위 현금/보유/예약주문 장부를 만든다. 동시 신호 발생 시 자금 부족, 전략별 max positions, 우선순위 정렬을 재현한다.
      - [ ] 리스크 게이트와 포지션 사이징을 백테스트에서도 동일하게 호출하거나, 동일 contract의 dry-run 구현으로 검증한다.
      - [ ] 마켓 타이밍/시장 국면별 성과 분해를 리포트한다. 상승장/하락장/횡보장, KOSPI/KOSDAQ 타이밍 ON/OFF별 기대값을 비교한다.
      - [ ] 전략별 trade journal을 표준 포맷으로 저장한다: signal_time, decision_reason, rejected_reason, order_price, fill_price, cost, pnl, MFE/MAE.
      - [ ] 실거래 로그와 백테스트 로그를 같은 schema로 맞춰 backtest-vs-live 괴리 리포트를 생성한다.
      - [ ] walk-forward 검증을 추가한다. 기간을 train/tune/test로 나누고, 파라미터 튜닝 구간과 검증 구간을 분리한다.
      - [ ] 몬테카를로 시뮬레이션을 추가한다. trade 결과 순서를 섞어 최악 MDD, 연속 손실, ruin probability를 계산한다.
      - [ ] 결과 저장소를 정한다. 초기에는 JSON/CSV, 이후 필요 시 SQLite repository로 승격한다.
      - [ ] CLI를 정리한다: `run_strategy_debug`는 미매수 사유 진단, `run_backtest`는 기간 수익률/포트폴리오 검증으로 역할을 분리한다.
      - [ ] 단위 테스트 fixture를 만든다: 특정 날짜에 PP 통과, PP 탈락, BGU 통과, 체결강도 탈락, 마켓타이밍 탈락 케이스를 고정 데이터로 검증한다.

---

### 디버깅 백테스트 Phase 1 구현 요약 (2026-04-30)

**구현된 파일**

| 파일 | 역할 |
|------|------|
| `strategies/debug/rejection_collector.py` | `RejectionLogHandler` — dict 구조화 로그를 이벤트로 수집. `CAPTURED_EVENTS` 화이트리스트 기반 |
| `strategies/debug/strategy_debug_runner.py` | `StrategyDebugRunner` — 전략을 1회 실행하며 탈락 이벤트 수집. `_UniverseFilterProxy`로 특정 종목만 스캔 |
| `strategies/debug/rejection_report.py` | `format_console()` / `format_json()` — 탈락 사유를 수치 포함 리포트로 렌더링 |
| `scripts/run_strategy_debug.py` | CLI 진입점 — `--codes` / `--all` / `--json` 옵션 |

**Phase 1에서 해결한 한계**

| 항목 | 변경 내용 |
|------|---------|
| `entry_rejected` 로그에 `entry_type` 누락 | `oneil_pocket_pivot_strategy.py` 로그에 `"entry_type": entry_type` 추가 |
| StageGuard 탈락 미캡처 | `strategy_executor.py`에 per-code `{"event": "stage_blocked", ...}` dict 로그 추가, `RejectionCollector` 캡처 확장, `StrategyDebugRunner`에 `stage_service` 선택 주입 지원 |
| 탈락 사유에 수치 없음 | `rejection_report._event_label()`에 reason별 수치 포맷 구현 (`pos`, `closest_ma_pct`, `proj_vol`, `pg_tv_pct` 등) |

**사용법**

```powershell
# 단일 종목 디버깅
cd /c/Users/Kyungsoo/Documents/Code/Investment
conda activate py310
python -m scripts.run_strategy_debug --codes 120110

# 여러 종목
python -m scripts.run_strategy_debug --codes 005930,000660,035720

# JSON 출력
python -m scripts.run_strategy_debug --codes 120110 --json

# universe 전체 스캔 (신호 없는 경우도 확인)
python -m scripts.run_strategy_debug --all
```

**StageGuard 활성화 (선택)**

`scripts/run_strategy_debug.py`에서 `StrategyDebugRunner` 생성 시 `stage_service` 주입:

```python
runner = StrategyDebugRunner(strategy, debug_logger, stage_service=minervini_svc)
```

**탈락 사유별 출력 예시**

```
120110  ✗ 탈락  PP 조건 탈락 — poor_candle_quality (pos=0.23 < 0.4)
005930  ✗ 탈락  PP 조건 탈락 — no_ma_proximity (최근접MA=+3.52%)
000660  ✗ 탈락  PP 조건 탈락 — insufficient_volume (예상=1,234,567 < 2,345,678)
035720  ✗ 탈락  체결강도/공통 조건 탈락 [PP] — cgld=95.3 < 120
012345  ✗ 탈락  StageGuard 탈락 — stage=3
```
   ---
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

2. P4-3 체결 품질 기반 전략 경고
   - 완료: 비활성화 후보 리포트 요약, 전략 경고 알림, 웹 알림 센터 강조
   - 완료: 4-2 적용 전/후 리포트 라벨 분리

3. P5 전략 고도화
   - RSI(2) 눌림목 전략 설계/구현
   - 기존 `LarryWilliamsVBO`와 별도로 펜볼드/돈천 채널 전략 구현 여부 결정
   - 백테스트-실거래 괴리, 시장 상태 필터, 포트폴리오 백테스트 추가

4. P7 운영 안정화
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
- (e') ✅ 테스트가 production STATE_FILE 경로 못 쓰게 격리 강제
  - 변경: live strategy 6종 생성자에 optional `state_file` 인자를 추가하고 `_load_state()` 전 인스턴스 `STATE_FILE`에 반영.
  - 격리: `tests/conftest.py` autouse fixture가 PP/OSB/HTF/FP/RSI2/TVB 기본 state file을 매 테스트 `tmp_path/strategy_state/`로 강제 override.
  - 회귀 방지: `tests/unit_test/strategies/test_strategy_state_file_isolation.py` 추가.
  - 검증: strategy 관련 단위 테스트 302개 + 신규 격리 테스트 1개 통과.

## 남은 작업

## 튜닝 메모

- (d-2) Pool B 전용 완화 결과 — 2026-04-29 확인
  - 적용 내용: `daily_surge_min_avg_trading_value_5d=50억`, `daily_surge_cap_min=1000억`, `daily_surge_cap_max=100조`
  - 결과: `build_watchlist_finished.daily_surge_stocks`가 0에서 증가함
  - 관측값: 08:25=1, 09:10=25, 09:30=8, 10:00=22, 10:30=6, 12:00=20, 14:01=5
  - 판단: 추가 완화는 보류. 후보 부족 현상이 재발할 때만 아래 항목 재검토
  - 다음 조정 후보: 거래대금 50억 → 30억 추가 완화
  - 다음 조정 후보: 정배열 조건을 Pool B 전용으로 `current > ma_20d` 중심으로 완화 검토
