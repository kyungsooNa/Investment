# Investment Trading App - 남은 To-Do

최종 업데이트: 2026-05-09 (P0-3 활성 전략 debug dry-run 연결 및 우선순위 재정리)

이 문서는 현재 남은 실행 항목만 추린 목록입니다. 완료된 구현 상세, 완료 체크 항목, 과거 세션 요약은 제거했습니다.

정리 원칙:

- 실전 계좌 보호와 주문 안정성을 최우선으로 둔다.
- 이미 적용된 항목은 새 기능으로 다시 넣지 않고, 검증/고도화 과제로만 남긴다.
- 후보군 관리는 신규 구축 과제가 아니라 기존 `OneilUniverseService` / `SubscriptionPolicy` 구조의 공통 파이프라인 확장 과제로 본다.
- 주문/브로커/스케줄러 변경은 테스트 hang 가이드와 paper/real 분기 검증을 함께 적용한다.
- `VolumeBreakoutStrategy`, `VolumeBreakoutLiveStrategy`, `TraditionalVolumeBreakoutStrategy`, `GapUpPullbackStrategy`, `ProgramBuyFollowStrategy`는 현재 사용하지 않는 전략이므로 신규 연결/개선 우선순위에서 제외한다.

---

## P0. 실전 손실 방지

백테스트에서 이긴 전략이 실거래에서도 같은 방식으로 기록, 체결, 정산, 차단되는지 검증하는 영역입니다. 실전 투입 전 최우선입니다.

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

### 0-2. 백테스트와 실거래 journal schema 표준화

- [~] 백테스트와 실거래/모의거래 성과를 같은 포맷으로 변환한다.
  - 완료된 부분: `common.trade_journal_schema`에 표준 journal normalizer를 추가하고, `VirtualTradeRepository` / `VirtualTradeService`에서 표준 journal 조회를 제공한다.
  - 진행 필요: 모든 백테스트/실거래 저장 경로가 표준 schema를 직접 저장하도록 확장한다.
- [~] 전략별 trade journal 표준 필드를 확정한다: `signal_time`, `decision_reason`, `rejected_reason`, `order_price`, `fill_price`, `cost`, `net_pnl`, `net_return`, `MFE`, `MAE`.
  - 완료된 부분: 표준 필드 version 1을 정의하고 virtual trade / VolumeBreakout backtest 변환 테스트를 추가했다.
  - 진행 필요: 다른 전략과 주문 체결 로그의 누락 필드를 채운다.
- [~] 실거래 로그와 백테스트 로그를 같은 schema로 맞춰 backtest-vs-live 괴리 리포트를 생성한다.
  - 완료된 부분: VolumeBreakout 단일일자 백테스트 결과에 `journal_records`를 추가하고, `common.trade_journal_comparison.compare_trade_journals()`로 전략/종목/거래일 기준 괴리 리포트를 생성한다.
  - 완료된 부분: `StrategyLogReportService`에 optional backtest journal provider를 연결해 after-market 리포트에 괴리 요약을 표시할 수 있게 했다.
  - 완료된 부분: `/api/virtual/journal`에서 현재 모의/실거래 원장을 표준 journal schema로 조회하고, `/api/virtual/backtest-divergence`에서 백테스트 journal payload와 현재 원장의 괴리를 비교한다.
  - 완료된 부분: 모의투자 화면에 backtest-vs-live 괴리 요약/테이블과 백테스트 journal JSON 비교 실행 UI를 연결했다.
  - 완료된 부분: `BacktestJournalRepository`를 추가해 백테스트 journal run을 `data/backtest_journals`에 저장/조회하고, `/api/virtual/backtest-journals` 목록/records API와 모의투자 화면 선택 불러오기를 연결했다.
  - 완료된 부분: Web 초기화 시 `StrategyLogReportService`의 backtest journal provider를 저장소의 날짜별 records 조회로 연결했다.
  - 완료된 부분: `MomentumStrategy` backtest 모드는 성공/실패 후보를 표준 decision journal(`SIGNAL`/`REJECTED`, `decision_reason`/`rejected_reason`)로 저장소에 기록한다.
  - 완료된 부분: `StrategyDebugRunner`가 O'Neil/Minervini 계열 debug 실행의 신호, 탈락 이벤트, watchlist 누락 종목을 표준 decision journal로 만들고 저장소에 기록한다.
  - 완료된 부분: 전략별 debug 이벤트의 세부 필드(`entry_type`, `stage`, `cgld`, `threshold`)를 API 응답 top-level 필드로 보강하고, 운영 UI 괴리 테이블에서 별도 열로 표시한다.
- [~] 수수료, 거래세, 슬리피지 반영 후 순수익을 기본 성과로 사용한다.
  - 완료된 부분: 표준 journal record에 `cost`, `net_pnl`, `net_return`을 계산해 포함한다.
  - 완료된 부분: after-market 포트폴리오 요약은 `net_return`이 있으면 기존 `return_rate`보다 우선 사용한다.
  - 완료된 부분: 웹 API에서 표준 journal과 backtest-vs-live 비교 결과를 순수익 필드 포함 schema로 노출한다.
  - 완료된 부분: 모의투자 화면은 비용 포함 성과 조회를 기본 ON으로 표시한다.
  - 완료된 부분: 전체 전략 성과 집계의 기본값을 비용 포함 net 기준으로 전환하고, gross 기준 조회는 `apply_cost=false` 명시 경로로 유지한다.

주요 파일:

- `strategies/backtest_data_provider.py`
- `services/strategy_log_report_service.py`
- `task/background/after_market/strategy_log_report_task.py`
- `repositories/*`
- `utils/transaction_cost_utils.py`

### 0-3. 체결 시뮬레이터와 포트폴리오 장부

- [~] 체결 시뮬레이터를 분리한다: 지정가/시장가, 당일 고가·저가 도달 여부, 거래량 기반 부분체결, 미체결, 다음 봉 체결 정책을 명시한다.
  - 완료된 부분: `BacktestExecutionSimulator`를 추가해 지정가/시장가, 고가·저가 도달 여부, 거래량 참여율 기반 부분체결/미체결, 시장가 슬리피지, 호가 단위 반올림을 테스트로 고정했다.
  - 진행 필요: "현재 봉/다음 봉 체결" 선택은 호출자가 bar를 주입하는 contract로 열어두었으므로, 각 백테스트 runner에서 정책 이름과 호출 위치를 명시해야 한다.
- [~] 수수료, 거래세, 슬리피지, 호가 단위 반올림을 모든 백테스트 성과 계산에 기본 반영한다.
  - 완료된 부분: 체결 리포트가 `TransactionCostUtils` 기반 매수 수수료/매도 수수료+거래세, 시장가 슬리피지, tick rounding 결과를 포함한다.
  - 진행 필요: 기존 전략별 백테스트 저장 경로가 새 체결 리포트를 사용해 표준 journal을 저장하도록 연결한다.
- [~] 포트폴리오 단위 현금/보유/예약주문 장부를 만든다. 동시 신호 발생 시 자금 부족, 전략별 max positions, 우선순위 정렬을 재현한다.
  - 완료된 부분: `BacktestPortfolioLedger`를 추가해 현금 예약, 체결 반영, 보유 수량/평단, 실현 순손익, 우선순위 정렬, 자금 부족 차단, 전략별 max positions 제한을 테스트로 고정했다.
  - 완료된 부분: 활성 O'Neil 계열 `StrategyDebugRunner`에 선택적 portfolio ledger dry-run을 연결해 BUY 신호가 현금 부족/전략별 max positions에 걸리면 표준 journal의 `REJECTED`/`rejected_reason`으로 남기도록 했다.
  - 완료된 부분: `scripts/run_strategy_debug.py`에 `--portfolio-cash`, `--max-positions` 옵션을 추가해 운영자가 debug 실행에서 포트폴리오 예약 검증을 켤 수 있게 했다.
  - 완료된 부분: `BacktestPeriodRunner`에서 동시 BUY 신호 묶음을 ledger 예약/체결 흐름으로 통과시키고, 자금 부족/전략별 max positions 거부를 표준 journal로 남긴다.
- [ ] 리스크 게이트와 포지션 사이징을 백테스트에서도 동일하게 호출하거나, 동일 contract의 dry-run 구현으로 검증한다.
- [~] 활성 전략군용 기간 백테스트 runner를 만든다.
  - 완료된 부분: `BacktestPeriodRunner`를 추가해 `LiveStrategy.scan()` / `check_exits()` contract를 날짜 루프로 감싸고, BUY 신호 → ledger 예약 → simulator 체결 → ledger 반영, SELL 신호 → simulator 체결 → ledger 반영 흐름을 테스트로 고정했다.
  - 완료된 부분: 기간 runner가 현금 부족, 전략별 max positions 차단을 표준 rejected journal로 남긴다.
  - 완료된 부분: `StockQueryIntradayReplayBarProvider`를 추가해 `StockQueryService.get_day_intraday_minutes_list()` 결과를 `BacktestBar`로 표준화하고, 신호 가격이 닿는 첫 분봉을 체결 후보 봉으로 제공한다.
  - 완료된 부분: `StockQueryBacktestReplayService`를 추가해 전략의 `get_current_price()`/`get_stock_conclusion()` 호출을 과거 분봉 기반 현재가·체결강도 replay로 바꾸고, 일별 프로그램매매 순매수 수량을 `pgtr_ntby_qty`에 보강한다.
  - 완료된 부분: `BacktestPeriodRunner`가 bar provider에도 `set_backtest_date()`를 전달하도록 해 replay context가 날짜 루프와 동기화된다.
  - 완료된 부분: `scripts/run_backtest.py` CLI 진입점을 추가해 `oneil_pocket_pivot` 기간 백테스트를 replay adapter + portfolio ledger + simulator로 실행할 수 있게 했다.
  - 완료된 부분: 기간 백테스트 runner가 `BacktestExecutionReport`를 표준 journal record로 변환하고, `BacktestJournalRepository`에 period run metadata와 함께 저장한다.
  - 진행 필요: 저장된 period backtest run을 운영 UI/괴리 리포트에서 검증하고, 리스크 게이트/포지션 사이징 dry-run contract를 연결한다.

주요 파일:

- `services/backtest_execution_simulator.py`
- `services/backtest_period_runner.py`
- `services/backtest_replay_adapter.py`
- `scripts/run_backtest.py`
- `tests/unit_test/services/test_backtest_execution_simulator.py`
- `tests/unit_test/services/test_backtest_period_runner.py`
- `tests/unit_test/services/test_backtest_replay_adapter.py`
- `tests/unit_test/scripts/test_run_backtest.py`
- `strategies/debug/strategy_debug_runner.py`
- `strategies/debug/rejection_report.py`
- `scripts/run_strategy_debug.py`
- `utils/transaction_cost_utils.py`
- `common/trade_journal_schema.py`

### 0-4. Kill Switch 손익 hook 연결 검증

- [x] `KillSwitchService.record_trade_result(profit_won, code, strategy, account_balance_won)` 가 모든 매도 체결/정산 경로에서 호출되는지 검증한다.
- [x] `KillSwitchService.record_strategy_trade_result(strategy_name, pnl_won)` 를 전략 매도 정산 시점에서 호출한다.
  - 현재 상태: 메서드는 구현되어 있으나 호출 지점이 없어 전략 KS가 실제 손익을 인식하지 못함.
  - 연결 후보: `OrderExecutionService` 체결 확정 콜백 또는 `VirtualTradeService` 매도 정산 시점.
  - 전제 조건: P0 `inquire-daily-ccld` 실전 응답 확보 후 체결 확정 시점이 명확해지면 실전 경로까지 연결.
- [x] 매도 체결 완료 → 실현손익 계산 → KillSwitch 기록 → 전략별 연속 손실/일손실 반영 → 한도 초과 시 전략·주문 차단 흐름을 회귀 테스트로 고정한다.

주요 파일:

- `services/kill_switch_service.py`
- `services/order_execution_service.py`
- `services/virtual_trade_service.py`
- `tests/unit_test/services/test_kill_switch_service.py`
- `tests/unit_test/services/test_order_execution_service.py`

### 0-5. 주문 상태와 잔고 대사 E2E 검증

- [ ] 실전 fixture 기반으로 주문 접수, 부분체결, 전체체결, 미체결, 취소/거부 상태 전이를 검증한다.
- [ ] 주문 접수만으로 보유/체결을 확정하지 않는 기존 FSM 동작을 실전 응답 fixture로 재검증한다.
- [ ] 재시작 후 미체결 주문과 잔고 restore/reconcile 결과가 신규 주문 차단 또는 경고 상태로 이어지는지 end-to-end 검증을 보강한다.
- [ ] reconcile task 실패 자체가 주문 차단 또는 명시 경고 상태로 이어지는 정책을 운영 매트릭스로 확정한다.

---

## P1. 전략 수익성 검증

전략을 더 추가하기보다 현재 전략의 기대값, MDD, 승률, 손익비, 시장 국면별 성과를 먼저 검증합니다.



### 1-3. `TradeSignal` / `PositionSizingService` 수량 contract 표준화

- [x] 전략은 매수 사유, 기준가, 손절 기준, 신뢰도만 생성하고 최종 수량은 `PositionSizingService`가 전담하는 구조로 정리한다.
- [x] 향후 `TradeSignal` 확장 후보를 검토한다: `stop_price`, `risk_pct`, `confidence`.
  - 결정: 본 PR에서는 미도입. `stop_loss_pct`/`atr_multiplier`가 이미 존재해 stop 거리 계산에 충분. 후속 PR에서 `Optional[...]` 추가 시 backward-compat 유지 가능.
- [x] 확장 필드는 즉시 구현하지 않고 sizing 구조 개편 시 backward-compatible migration 계획을 함께 세운다.
  - 계획: 모든 신규 필드는 `Optional[...] = None`으로 추가, 기존 47개 construction site 영향 없음.
- [x] 기존 `qty` 의미를 “전략 상한”으로 유지할지 “요청 수량 없음/무제한”을 표현할지 결정하고 테스트로 고정한다.
  - 결정: `qty: Optional[int] = None`. `None`=sizing 단독 결정, `int`=전략의 자발적 상한. 테스트로 invariant 고정 완료.

주요 파일:

- `common/types.py`
- `services/position_sizing_service.py`
- `scheduler/strategy_scheduler.py`

### 1-4. 시장 국면별 전략 성과 분해

- [ ] 시장 상태를 상승/하락/횡보로 분류하는 기준을 정의한다.
- [ ] 코스피/코스닥 지수 기반 전략 ON/OFF 조건을 추가한다.
- [ ] 변동성 기반 진입 제한을 검토한다.
- [ ] 장 초반/후반 타임 필터를 전략 실행 전에 적용한다.
- [ ] O'Neil/Minervini 계열 전략 성과를 KOSPI 상승장, KOSDAQ 상승장, 지수 횡보장, 지수 하락장, 거래대금 증가 장세로 분리해 리포트한다.

주요 파일:

- `services/market_data_service.py`
- `services/indicator_service.py`
- `strategies/strategy_executor.py`
- `scheduler/strategy_scheduler.py`

### 1-5. 백테스트 검증 확장

- [ ] `--date YYYYMMDD` / `--from YYYYMMDD --to YYYYMMDD` 기반 과거 시점 재현용 market clock과 데이터 스냅샷 주입 구조를 만든다.
- [ ] 실시간 API 응답 대신 과거 OHLCV/체결강도/프로그램매매 데이터를 공급하는 `BacktestStockQueryService` 또는 data replay adapter를 추가한다.
- [ ] `run_strategy_debug`는 미매수 사유 진단, `run_backtest`는 기간 수익률/포트폴리오 검증으로 역할을 분리한다.
- [ ] walk-forward 검증을 추가한다. 기간을 train/tune/test로 나누고, 파라미터 튜닝 구간과 검증 구간을 분리한다.
- [ ] 몬테카를로 시뮬레이션을 추가한다. trade 결과 순서를 섞어 최악 MDD, 연속 손실, ruin probability를 계산한다.
- [ ] 단위 테스트 fixture를 만든다: 특정 날짜에 PP 통과, PP 탈락, BGU 통과, 체결강도 탈락, 마켓타이밍 탈락 케이스를 고정 데이터로 검증한다.

주요 파일:

- `strategies/debug/*`
- `scripts/run_strategy_debug.py`
- `strategies/backtest_data_provider.py`
- `strategies/strategy_executor.py`

---

## P2. 시스템 성능

성능 개선의 핵심은 후보군 관리 신규 구축이 아니라, 이미 있는 후보군/구독 정책을 전체 전략과 실시간 snapshot 구조에 연결하는 것입니다.

### 2-1. 후보군/구독 정책 공통 파이프라인 확장

- [x] `OneilUniverseService`의 Pool A, Pool B, Watchlist 병합 구조를 O'Neil 계열뿐 아니라 전체 전략 공통 후보군 파이프라인으로 확장할지 설계한다.
  - 결정: generic `CandidateUniverseService` 및 `RankingCacheService` 미도입. 모든 활성 전략(HTF, FirstPullback, LarryWilliamsVBO, RSI2Pullback, LarryWilliamsCB, OSB, PP)이 이미 `OneilUniverseService.get_watchlist()`를 사용하며 `strategy_oneil` 카테고리로 구독 등록됨. O'Neil Pool A/B 구조는 O'Neil 전용 필드를 동반하므로 일반화 불필요.
  - 행동: `VolumeBreakoutLiveStrategy`, `TraditionalVolumeBreakoutStrategy`, `ProgramBuyFollowStrategy` 3개 전략을 스케줄러 등록에서 제거함 (2026-05-05). 전략 클래스 파일과 테스트는 유지.
- [x] `SubscriptionPolicy`의 보유종목, 전략 감시종목, UI 관심종목 우선순위와 참조 카운팅을 전체 전략 구독 정책에 일관 적용한다.
  - 결과: 스케줄러에 등록된 모든 활성 전략이 `get_watchlist()` → `sync_subscriptions("strategy_oneil", MEDIUM)` 경로로 이미 일관 적용됨. `test_get_watchlist_syncs_subscriptions`로 검증됨.
- [x] 기존 후보 부족 관찰 항목은 Pool B 튜닝 후보로 유지하고, 신규 후보군 구축 과제로 중복 등록하지 않는다.

주요 파일:

- `services/oneil_universe_service.py`
- `services/subscription_policy.py`
- `services/price_subscription_service.py`
- `repositories/streaming_stock_repo.py`

### 2-2. API 호출 최적화

- [x] `StrategyExecutor` Liquidity Filter의 `asyncio.gather()`에 semaphore 기반 동시성 제한을 추가한다.
  - 근거: `strategies/strategy_executor.py:88` `Semaphore(self._max_liquidity_concurrency)` 적용, `:106` gather, `:141` `async with sem:` 으로 REST 호출 보호.
  - snapshot-first chain도 동일 파일 `:127-139`에 구현됨 (PriceStreamService 신선 snapshot 우선 사용 → 없거나 stale 시 REST fallback → REST 응답을 `cache_price_snapshot()`으로 backfill).
- [x] 종목별 current price REST 호출을 최소화하고 WebSocket/stream snapshot을 우선 사용한다.
  - 완료: `StockQueryService.get_current_price()`에 snapshot-first 로직 추가. `price_stream_service` 주입 시 `get_cached_price()` 우선 참조 → stale/없음 시 REST fallback. `web_app_initializer.py`에서 `price_stream_service` 주입.
  - 구독 중이나 tick 미수신(snapshot=None) 케이스: `no_tick_fallback`으로 분류해 REST fallback. `_price_lookup_stats`에 `snapshot_hit/no_tick_fallback/stale_fallback/rest_fallback` 카운터 노출.
  - REST 성공 시 `cache_price_snapshot()`으로 backfill → 다음 호출 snapshot hit 유도.
  - 전략들은 호출 코드 변경 없이 자동으로 WebSocket 캐시 활용.
- [x] REST API는 snapshot 누락, 검증, 보정용으로 제한한다.
  - 완료: snapshot 없음/stale → REST fallback. `force_fresh=True` 시 snapshot 무시(주문/리스크/수동 조회 경로에서 사용).
  - `_price_lookup_stats` dict를 `price_lookup_stats` 이벤트 로그로 노출 가능.
- [x] 전략별 중복 조회를 제거하고 동일 종목 데이터는 공통 snapshot에서 읽도록 정리한다.
  - 완료: 전략들이 `StockQueryService.get_current_price()` 한 통로를 거치므로 snapshot-first 기본화로 자연 해결. REST backfill로 첫 호출 이후 동일 종목 재조회 시 캐시 hit.

주요 파일:

- `strategies/strategy_executor.py`
- `services/stock_query_service.py`
- `services/price_stream_service.py`
- `view/web/web_app_initializer.py` (price_stream_service 주입)
- `services/order_execution_service.py`, `services/risk_gate_service.py` (`force_fresh=True` 명시)

### 2-3. Market snapshot 표준화

- [ ] 기존 `PriceStreamService`의 `get_cached_price()` / `cache_price_snapshot()` 구조를 중복 구현하지 않고, 전략 공통 snapshot contract로 승격할 방법을 설계한다.
- [ ] WebSocket / REST / DB snapshot 입력을 한곳에서 표준화해 현재가, 거래량, 거래대금, 고가, 저가, 체결강도, 데이터 시각을 같은 형태로 제공한다.
- [ ] 전략은 REST 직접 조회보다 snapshot provider를 우선 참조하고, stale/missing reason을 rejected reason으로 남긴다.

주요 파일:

- `services/price_stream_service.py`
- `services/execution_flow_service.py`
- `services/data_quality_service.py`
- `view/web/web_app_initializer.py`

### 2-4. Polling에서 event-driven으로 점진 전환

- [ ] 체결 이벤트 수신 → snapshot 업데이트 → 후보군 상태 갱신 → 전략 조건 평가 → RiskGate → 주문 → 체결/잔고/손익 대사 흐름을 목표 아키텍처로 문서화한다.
- [ ] 우선순위 높은 전략부터 polling loop를 event-triggered 평가로 옮길 수 있는지 검토한다.

---

## P3. 유지보수성

기능이 늘어난 만큼 초기화 조립부와 주문 실행부의 책임을 줄여 테스트와 리팩토링 누락 위험을 낮춥니다.

### 3-1. `WebAppContext` / `web_app_initializer` 분리

- [ ] 환경 로드, 브로커 생성, 서비스 조립, 전략/태스크 등록, 알림 설정, 스케줄러 초기화를 단계별 bootstrap으로 분리한다.
- [ ] `BrokerFactory`, `ServiceContainer`, `StrategyFactory`, `SchedulerBootstrap`, `WebBootstrap`, `ConfigBootstrap` 분리 후보를 검토한다.
- [ ] 후주입 방식 서비스 연결을 줄이고, 누락 시 테스트에서 빨리 드러나도록 생성 contract를 명확히 한다.

주요 파일:

- `view/web/web_app_initializer.py`
- `view/web/web_main.py`
- `tests/unit_test/view/web/test_web_app_initializer.py`

### 3-2. 웹 / 운영 / 전략 runtime 경계 분리

- [ ] 웹 화면/API, 장중 전략/주문 실행, 장마감 데이터 수집/리포트, 수동 운영 점검 runtime 경계를 명확히 한다.
- [ ] 웹 서버 초기화가 모든 after-market task와 장중 scheduler를 직접 끌어안지 않도록 분리한다.

분리 후보:

- `web_app.py` — 화면/API
- `trading_runtime.py` — 장중 전략/주문 실행
- `batch_runtime.py` — 장마감/데이터 수집/리포트
- `admin_runtime.py` — 수동 조작/점검

### 3-3. 주문 서비스 역할 분리

- [ ] `OrderExecutionService`에서 validator, risk, sizing, submit, state machine, fill reconciliation, execution quality reporting 책임을 단계적으로 분리한다.
- [ ] 주문 수량 상한, 중복 주문 방지 키, 같은 전략/같은 종목 동시 재진입 방지, 체결 확인/잔고 반영 분리 기준을 명확히 한다.
- [ ] 분리 전후로 기존 주문 안전장치 테스트가 그대로 통과하도록 단위 테스트를 먼저 보강한다.

분리 후보:

- `OrderValidator`
- `RiskGateAdapter`
- `PositionSizingAdapter`
- `OrderSubmitter`
- `OrderStateMachine`
- `FillReconciliationService`
- `ExecutionQualityReporter`

---

## P4. 운영 품질

실전에서는 “왜 샀는지”뿐 아니라 “왜 안 샀는지”와 “언제 전략을 쉬게 해야 하는지”가 중요합니다.

### 4-1. 전략별 성과 저하 감지

- [ ] 전략별 최근 20거래 손익, 최근 20거래 승률, 평균 손익비, MDD, 연속 손실, MFE/MAE를 집계한다.
- [ ] 백테스트 기대값 대비 실거래 괴리를 전략별로 계산한다.
- [ ] 성과 악화 시 신규 진입 중단, 수량 축소, paper mode 전환, 관리자 알림 후보로 표시한다.
- [ ] 자동 차단 기준은 KillSwitch/RiskGate와 충돌하지 않도록 정책 우선순위를 정의한다.

주요 파일:

- `services/strategy_log_report_service.py`
- `task/background/after_market/strategy_log_report_task.py`
- `services/kill_switch_service.py`
- `services/risk_gate_service.py`

### 4-2. Rejected reason 리포트

- [ ] 거래량 부족, 거래대금 부족, Stage Guard 탈락, RS Rating 부족, 시장 타이밍 OFF, RiskGate 차단, 현금 부족, 동일 종목 재진입 차단, 호가/체결강도 불량을 rejected reason으로 표준화한다.
- [ ] 전략별/일자별 rejected reason 분포를 리포트한다.
- [ ] `run_strategy_debug`와 운영 대시보드에서 rejected reason을 같은 필드명으로 표시한다.

### 4-3. 운영 대시보드와 알림

- [ ] 전략별 성과 저하, reconcile alarm, websocket watchdog 경고, 데이터 품질 차단, 주문 정책 차단을 운영자 대시보드에서 한눈에 볼 수 있게 정리한다.
- [ ] 알림은 신규 차단, 위험도 상승, 자동 해제/복구를 구분해 중복 발송을 줄인다.

---

## Strategy Log 남은 작업

### Pool B 튜닝 관찰

- [ ] 후보 부족 현상이 재발하면 거래대금 기준을 50억에서 30억으로 추가 완화할지 검토한다.
- [ ] 후보 부족 현상이 재발하면 정배열 조건을 Pool B 전용으로 `current > ma_20d` 중심으로 완화할지 검토한다.

---

## 바로 착수 추천 순서

1. P0 실전 손실 방지
   - 실전 체결 이력 fixture 확보 및 민감정보 제거
   - 실전 fixture 기반 주문번호, 종목코드, 매수/매도, 체결/미체결/취소/거부 필드 매핑 확정
   - 주문 접수/부분체결/전체체결/미체결/취소/거부 상태 전이와 잔고 대사 E2E 검증
   - reconcile task 실패가 주문 차단 또는 명시 경고로 이어지는 정책 매트릭스 확정

2. P0/P1 백테스트 신뢰도
   - 저장된 period backtest journal의 운영 UI/괴리 리포트 표시 검증
   - 리스크 게이트와 포지션 사이징 dry-run contract 추가
   - walk-forward, Monte Carlo 순서로 확장

3. P1 전략 수익성
   - 시장 국면별 성과 분리
   - 코스피/코스닥 지수 기반 전략 ON/OFF 조건 검증
   - 변동성·장 초반/후반 필터를 성과 리포트로 먼저 검증
   - 전략별 stop/target/trailing 기준 표준화
   - 전략별 risk budget 분리

4. P2 시스템 성능
   - 기존 stream snapshot 기반 공통 snapshot contract 정리
   - WebSocket / REST / DB snapshot 입력 표준화
   - stale/missing reason을 rejected reason으로 기록
   - event-driven 전략 평가 목표 아키텍처 문서화

5. P3/P4 유지보수와 운영 품질
   - rejected reason 표준화 및 전략별/일자별 분포 리포트
   - 전략별 성과 저하 감지 지표 집계
   - `WebAppContext` 분리
   - ServiceContainer / Factory 도입
   - `OrderExecutionService` 역할 분리

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
  - 완료된 부분: 전략별 성과 지표 조회/집계 기본값이 비용 포함 순수익(`net PnL/return`) 기준으로 전환되었고, gross 기준은 `apply_cost=false` 명시 경로로 유지한다.
  - 완료된 부분: 백테스트용 `BacktestExecutionSimulator`가 명시적 시장가 슬리피지, 호가 단위 반올림, 수수료/거래세 포함 체결 리포트를 생성한다.
  - 진행 필요: 모든 전략의 백테스트 저장 단계가 체결 리포트와 포트폴리오 장부를 사용하도록 연결해야 한다.
  - 관련 파일: `utils/transaction_cost_utils.py`, `services/backtest_execution_simulator.py`, `services/order_execution_service.py`, `services/strategy_log_report_service.py`, `tests/unit_test/utils/test_transaction_cost_utils.py`, `tests/unit_test/services/test_backtest_execution_simulator.py`

- [~] 장애, 데이터 지연, websocket 끊김, reconcile 실패 시 신규 주문 차단 또는 경고 상태로 전환된다.
  - 완료된 부분: Kill Switch/Risk Gate 주문 차단, 데이터 품질 오류 주문 차단, websocket watchdog 재연결/경고, reconcile alarm 신규 주문 차단이 구현·테스트되어 있다.
  - 진행 필요: 장애 유형별 정책이 모두 동일한 수준으로 연결되었는지 운영 매트릭스를 확정하고, reconcile task 실패 자체가 주문 차단 또는 명시 경고 상태로 이어지는지 end-to-end 검증을 보강해야 한다.
  - 관련 파일: `services/kill_switch_service.py`, `services/data_quality_service.py`, `services/risk_gate_service.py`, `services/order_execution_service.py`, `task/background/intraday/websocket_watchdog_task.py`, `task/background/after_market/after_market_reconcile_task.py`

## HTF 전략 추가 개선 (차후 검토)

- [ ] **VCP 타이트함 검증**: 깃발 최근 3~5일 일변동폭(고가-저가) 평균이 깃대 구간 변동폭 대비 현저히 축소되었는지 `_detect_pole_and_flag`에 추가
- [ ] **돌파 확인 지연**: pole_high 돌파 후 3~5분간 가격 유지 확인 로직 (현재는 +0.5% 버퍼로 대체)
- [ ] **깃발 기간 20MA 지지**: 횡보 구간 최저점이 20일선을 심각하게 훼손하지 않았는지 확인
- [ ] **이격도 모니터링**: 매수 시그널 발생 시 `(current/pole_high - 1) * 100`을 metrics 로그·대시보드에 노출
