# Investment Trading App - 남은 To-Do

최종 업데이트: 2026-05-01 (P8 완료 제거)

이 문서는 현재 남은 실행 항목만 추린 목록입니다. 완료된 구현 상세, 완료 체크 항목, 과거 세션 요약은 제거했습니다.

정리 원칙:

- 실전 계좌 보호와 주문 안정성을 최우선으로 둔다.
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

---

## P5. 전략 검증, 비용 반영, 전략 격리

전략 자체의 품질과 실전 운용 가능성을 검증하는 영역입니다. 백테스트 성과가 실전 성과로 이어지는지 추적해야 합니다.

### 5-1. 전략별 실전 제한값 추가

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

### 5-4. 디버깅 백테스트 확장

- [ ] 과거 시점 시뮬레이션 지원: `--date YYYYMMDD` 인자로 그날 OHLCV 기반 PP 조건을 재현한다.
- [ ] 다른 전략(VolumeBreakout, HighTightFlag 등) 디버깅 어댑터를 추가한다.
- [ ] 디버깅 리포트에 `scan_skipped`(장 미개장/마켓타이밍 불량/워치리스트 없음)와 종목별 탈락 이벤트를 구분해 표시한다.
- [ ] PP/BGU 공통 진입 단계에 `entry_type`을 항상 포함해 체결강도/스마트머니 탈락 원인을 추정이 아닌 확정값으로 표시한다.

주요 파일:

- `strategies/debug/*`
- `scripts/run_strategy_debug.py`
- `strategies/strategy_executor.py`
- `strategies/oneil_pocket_pivot_strategy.py`

### 5-5. 백테스트 엔진 후속 작업

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

## Strategy Log 남은 작업

### Pool B 튜닝 관찰

- [ ] 후보 부족 현상이 재발하면 거래대금 기준을 50억에서 30억으로 추가 완화할지 검토한다.
- [ ] 후보 부족 현상이 재발하면 정배열 조건을 Pool B 전용으로 `current > ma_20d` 중심으로 완화할지 검토한다.

---

## 바로 착수 추천 순서

1. P0 실전 KIS `inquire-daily-ccld` 실제 응답 검증
   - 실전 체결 이력 fixture 확보 및 민감정보 제거
   - paper/real 필드 차이 회귀 테스트 확정
   - 주문번호/체결수량/미체결수량/평균체결가/취소·거부 필드 매핑 확정

2. P5 전략 고도화
   - 백테스트-실거래 괴리 추적
   - 시장 상태 필터 추가
   - 포트폴리오 백테스트 추가
   - 디버깅 백테스트 과거 시점 재현 추가

---

## 완료 기준

표기: `[x]` 완료, `[~]` 부분 완료/진행 필요, `[ ]` 미완료

- [x] 실전 모드에서 주문 접수만으로 보유/체결이 확정되지 않는다.
  - 근거: 실전 모드에서는 `finalize_immediately=True`도 무시하고 `SUBMITTED` 상태를 유지한다.
  - 확인 파일: `services/order_execution_service.py`, `tests/unit_test/services/test_order_execution_service.py`

- [x] 모든 주문은 Risk Gate 통과 전 broker API를 호출하지 않는다.
  - 근거: `_submit_order_with_fsm()`에서 `RiskGateService.validate_order()` 차단 응답 시 broker 주문 호출 전에 반환한다.
  - 확인 파일: `services/order_execution_service.py`, `services/risk_gate_service.py`, `tests/unit_test/services/test_order_execution_service.py`

- [x] 서비스 재시작 후 미체결 주문과 잔고를 복원 또는 reconcile할 수 있다.
  - 근거: Web 시작 시 `restore_state_from_broker()`를 호출하며, 미체결/당일 체결 내역으로 주문 상태를 복원하고 `reconcile_orders_with_broker()`로 미체결/체결/잔고 불일치를 점검한다.
  - 확인 파일: `view/web/web_main.py`, `services/order_execution_service.py`, `tests/unit_test/services/test_order_execution_service.py`

- [x] paper/real URL, TR ID, 토큰, 계좌 분기가 테스트로 검증된다.
  - 근거: 환경 전환 시 URL/계좌/API 키/token provider 교체, TR ID paper/real 선택, 토큰 provider 위임 테스트가 있다.
  - 확인 파일: `brokers/korea_investment/korea_invest_env.py`, `brokers/korea_investment/korea_invest_trid_provider.py`, `tests/unit_test/brokers/korea_investment/`

- [~] 전략 성과는 수수료, 세금, 슬리피지를 반영한 순수익 기준으로 추적된다.
  - 완료된 부분: 거래 비용 계산 유틸과 테스트가 있고, 체결 품질 로그/리포트에서 슬리피지를 추적한다.
  - 진행 필요: 전략별 성과 지표가 수수료+세금+슬리피지를 모두 반영한 순수익(`net PnL/return`)을 기본값으로 저장·집계하도록 표준화해야 한다.
  - 관련 파일: `utils/transaction_cost_utils.py`, `services/order_execution_service.py`, `services/strategy_log_report_service.py`, `tests/unit_test/utils/test_transaction_cost_utils.py`

- [~] 장애, 데이터 지연, websocket 끊김, reconcile 실패 시 신규 주문 차단 또는 경고 상태로 전환된다.
  - 완료된 부분: Kill Switch/Risk Gate 주문 차단, 데이터 품질 오류 주문 차단, websocket watchdog 재연결/경고, reconcile alarm 신규 주문 차단이 구현·테스트되어 있다.
  - 진행 필요: 장애 유형별 정책이 모두 동일한 수준으로 연결되었는지 운영 매트릭스를 확정하고, reconcile task 실패 자체가 주문 차단 또는 명시 경고 상태로 이어지는지 end-to-end 검증을 보강해야 한다.
  - 관련 파일: `services/kill_switch_service.py`, `services/data_quality_service.py`, `services/risk_gate_service.py`, `services/order_execution_service.py`, `task/background/intraday/websocket_watchdog_task.py`, `task/background/after_market/after_market_reconcile_task.py`

## HTF 전략 추가 개선 (차후 검토)

- [ ] **VCP 타이트함 검증**: 깃발 최근 3~5일 일변동폭(고가-저가) 평균이 깃대 구간 변동폭 대비 현저히 축소되었는지 `_detect_pole_and_flag`에 추가
- [ ] **돌파 확인 지연**: pole_high 돌파 후 3~5분간 가격 유지 확인 로직 (현재는 +0.5% 버퍼로 대체)
- [ ] **깃발 기간 20MA 지지**: 횡보 구간 최저점이 20일선을 심각하게 훼손하지 않았는지 확인
- [ ] **이격도 모니터링**: 매수 시그널 발생 시 `(current/pole_high - 1) * 100`을 metrics 로그·대시보드에 노출
