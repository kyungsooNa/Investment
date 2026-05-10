# 백테스트 진행 현황과 사용법

최종 업데이트: 2026-05-10

이 문서는 현재 프로젝트에서 백테스트와 관련해 이미 진행한 작업, 남은 작업, 실제 실행 방법을 정리한다.

주의: `진행 완료`와 `남은 작업` 섹션은 백테스트 구축이 완전히 끝날 때 제거하고, 최종 문서는 사용법과 운영 기준 중심으로 정리한다.

현재 신규 백테스트 연결 대상은 활성 전략 중심이다. 아래 전략들은 사용하지 않는 전략으로 분류되어 신규 백테스트 연결과 개선 우선순위에서 제외한다.

- `volume_breakout_strategy`
- `volume_breakout_live_strategy`
- `traditional_volume_breakout_strategy`
- `GapUpPullback_strategy`
- `program_buy_follow_strategy`

## 현재 범위

현재 기간 백테스트 CLI는 활성 전략 7개를 지원한다.

지원 전략:

- `oneil_pocket_pivot`
- `oneil_squeeze_breakout`
- `high_tight_flag`
- `first_pullback`
- `larry_williams_vbo`
- `rsi2_pullback`
- `larry_williams_channel_breakout`

지원하는 흐름:

1. 날짜 범위를 순회한다.
2. 전략의 `scan()`으로 매수 신호를 만든다.
3. 전략의 `check_exits()`로 보유 종목 매도 신호를 만든다.
4. 과거 분봉 데이터를 `BacktestBar`로 변환한다.
5. 체결 시뮬레이터가 지정가 도달 여부, 거래량 한도, 수수료, 거래세, 슬리피지, 호가 단위 반올림을 반영한다.
6. 포트폴리오 장부가 현금, 예약 주문, 보유 수량, 평단, 실현 손익을 반영한다.
7. 현금 부족이나 전략별 최대 보유 수 제한은 rejected journal record로 남긴다.

아직 모든 전략의 완성형 성과 리포트 저장까지 끝난 상태는 아니다. 현재는 replay 기반 실행과 포트폴리오/체결 검증 골격이 연결된 상태다.

## 진행 완료

### 표준 journal 기반

- `common.trade_journal_schema`에 표준 journal normalizer를 추가했다.
- `VirtualTradeRepository` / `VirtualTradeService`에서 표준 journal 조회를 제공한다.
- `/api/virtual/journal`로 현재 모의/실거래 원장을 표준 journal schema로 조회할 수 있다.
- `/api/virtual/backtest-divergence`로 백테스트 journal payload와 현재 원장의 괴리를 비교할 수 있다.
- `BacktestJournalRepository`를 추가해 백테스트 journal run을 `data/backtest_journals`에 저장/조회할 수 있다.
- 모의투자 화면에서 backtest-vs-live 괴리 요약과 백테스트 journal JSON 비교 UI를 연결했다.
- 전략별 성과 집계는 비용 포함 순수익 기준을 기본값으로 사용한다.

주요 파일:

- `common/trade_journal_schema.py`
- `common/trade_journal_comparison.py`
- `repositories/backtest_journal_repository.py`
- `services/strategy_log_report_service.py`

### 전략 디버그와 미매수 사유

- `StrategyDebugRunner`가 O'Neil 계열 debug 실행의 신호, 탈락 이벤트, watchlist 누락 종목을 표준 decision journal로 만든다.
- 전략별 debug 이벤트의 `entry_type`, `stage`, `cgld`, `threshold` 같은 세부 필드를 API 응답과 운영 UI에서 볼 수 있게 보강했다.
- `scripts/run_strategy_debug.py`에 `--portfolio-cash`, `--max-positions` 옵션을 추가했다.
- debug 실행에서도 BUY 신호를 포트폴리오 ledger dry-run에 통과시켜 현금 부족과 max positions 거부를 확인할 수 있다.

주요 파일:

- `strategies/debug/strategy_debug_runner.py`
- `strategies/debug/rejection_report.py`
- `scripts/run_strategy_debug.py`

### 체결 시뮬레이터

- `BacktestExecutionSimulator`를 추가했다.
- 지정가/시장가 체결을 지원한다.
- 지정가는 고가/저가 도달 여부로 체결을 판단한다.
- 거래량 참여율 기반 부분체결과 미체결을 지원한다.
- 시장가는 슬리피지를 반영할 수 있다.
- 한국 주식 호가 단위 반올림을 적용한다.
- `TransactionCostUtils` 기반 매수 수수료, 매도 수수료, 거래세를 체결 리포트에 포함한다.

주요 파일:

- `services/backtest_execution_simulator.py`
- `tests/unit_test/services/test_backtest_execution_simulator.py`
- `utils/transaction_cost_utils.py`

### 포트폴리오 장부

- `BacktestPortfolioLedger`를 추가했다.
- 초기 현금, 예약 현금, 가용 현금, 보유 포지션, 평단, 실현 순손익을 추적한다.
- 동시 BUY 신호를 우선순위 순서로 예약한다.
- 현금 부족은 `cash_short`로 거부한다.
- 전략별 최대 보유 종목 수 초과는 `max_positions`로 거부한다.
- BUY/SELL 체결 리포트를 장부에 반영한다.

주요 파일:

- `services/backtest_execution_simulator.py`
- `tests/unit_test/services/test_backtest_execution_simulator.py`

### 기간 백테스트 runner

- `BacktestPeriodRunner`를 추가했다.
- 활성 `LiveStrategy` contract인 `scan()` / `check_exits()`를 날짜 루프로 감싼다.
- 날짜마다 전략과 bar provider에 `set_backtest_date()`를 전달한다.
- `BacktestPeriodRunnerConfig.execution_bar_policy`로 체결 후보 봉 선택 정책을 명시한다.
- 현재 지원 정책은 `current_bar`와 `next_bar`다.
- `BacktestPeriodRunner._execute_signal()`에서 bar provider를 호출할 때 정책 이름을 전달한다.
- execution report, 표준 journal metadata, 저장 run metadata, CLI 출력에 체결 봉 정책 이름을 남긴다.
- SELL 신호를 먼저 처리하고, 이후 BUY 신호를 처리한다.
- BUY 신호는 선택적 PositionSizing dry-run, 선택적 RiskGate dry-run, ledger 예약 후 체결 시뮬레이터를 통과한다.
- SELL 신호는 체결 시뮬레이터를 통과한 뒤 장부에 반영한다.
- SELL 체결 journal은 장부 반영 전 보유 원가와 매도 비용을 기준으로 `SOLD`, `net_pnl`, `net_return`을 기록한다.
- 현금 부족, max positions, 미체결은 journal record로 남긴다.
- PositionSizing 결과 수량이 0이면 `sizing_skip:*` rejected journal로 남긴다.
- RiskGate 차단은 simulator/bar 조회 전에 `risk_gate:*` rejected journal로 남긴다.

주요 파일:

- `services/backtest_period_runner.py`
- `tests/unit_test/services/test_backtest_period_runner.py`

### 과거 데이터 replay adapter

- `BacktestMarketClock`을 추가했다.
- runner가 순회 중인 날짜와 `--backtest-time`으로 지정한 장중 시각을 전략/유니버스가 현재 시각으로 보도록 고정한다.
- `apply_backtest_snapshot_context()`는 기존 O'Neil universe의 StockQueryService와 MarketClock 참조를 replay SQS와 backtest clock으로 교체한다.

- `StockQueryIntradayReplayBarProvider`를 추가했다.
- `StockQueryService.get_day_intraday_minutes_list()` 결과를 `BacktestBar`로 변환한다.
- `current_bar` 정책에서는 신호 가격이 닿는 첫 분봉을 체결 후보 봉으로 제공한다.
- `next_bar` 정책에서는 신호 가격이 닿는 신호 봉 다음 분봉을 체결 후보 봉으로 제공한다.
- 신호 가격이 닿지 않으면 마지막 분봉을 반환해 simulator가 미체결을 판단하게 한다.
- 종목, 날짜, 세션 단위로 분봉을 캐시한다.

- `StockQueryBacktestReplayService`를 추가했다.
- 전략 내부의 `get_current_price()` 호출을 과거 분봉 기반 현재가 응답으로 바꾼다.
- 전략 내부의 `get_stock_conclusion()` 호출을 과거 분봉 기반 체결강도 응답으로 바꾼다.
- `get_recent_daily_ohlcv()` 호출은 현재 백테스트 날짜를 `end_date`로 주입한다.
- 일별 프로그램매매 순매수 수량을 `pgtr_ntby_qty`에 보강한다.

주요 파일:

- `services/backtest_replay_context.py`
- `services/backtest_replay_adapter.py`
- `tests/unit_test/services/test_backtest_replay_context.py`
- `tests/unit_test/services/test_backtest_replay_adapter.py`

### CLI 진입점

- `scripts/run_backtest.py`를 추가했다.
- 활성 전략 7개를 `--strategy` 선택지로 지원한다.
- 활성 전략 factory가 replay SQS, `BacktestMarketClock`, universe service, indicator service를 동일 contract로 주입한다.
- 각 runner/phase는 strategy key 기반 임시 state file을 사용해 period/walk-forward phase 간 state를 분리한다.
- replay adapter, portfolio ledger, execution simulator, period runner를 조립해 기간 백테스트를 실행한다.
- console 출력과 JSON 출력을 지원한다.
- 실행 결과를 `BacktestJournalRepository` 표준 저장 경로에 저장한다.
- `--use-risk-sizing` 옵션으로 운영 설정 기반 `PositionSizingService`/`RiskGateService`를 백테스트용 ledger snapshot과 함께 dry-run으로 조립한다.
- `--walk-forward` 옵션으로 기간을 train/tune/test rolling window로 나누고, 각 phase를 독립 period runner/ledger/strategy state로 실행한다.
- `--wf-train-days`, `--wf-tune-days`, `--wf-test-days`, `--wf-step-days`로 walk-forward 창 크기와 이동 폭을 지정할 수 있다.
- `--monte-carlo` 옵션으로 완료 trade의 `net_pnl` 순서를 섞어 최악 MDD, 최장 연속 손실, ruin probability를 계산한다.
- `--mc-runs`, `--mc-seed`, `--mc-ruin-drawdown-pct`로 Monte Carlo 실행 횟수, 재현 seed, ruin 기준 MDD를 지정할 수 있다.
- `--execution-bar-policy current_bar|next_bar` 옵션으로 체결 후보 봉 선택 정책을 지정할 수 있다.
- `--backtest-time HH:MM:SS` 옵션으로 전략 조건 평가에 사용할 과거 장중 시각을 지정할 수 있다.

주요 파일:

- `scripts/run_backtest.py`
- `tests/unit_test/scripts/test_run_backtest.py`

### Walk-forward 검증

- `BacktestWalkForwardRunner`를 추가했다.
- 날짜 목록을 train/tune/test window로 분리한다.
- `step_size`를 지정하지 않으면 test window 크기만큼 다음 구간으로 이동한다.
- 마지막 구간은 train/tune 이후 test 날짜가 1일 이상 남아 있으면 부분 test window로 포함한다.
- 각 phase는 runner factory를 통해 독립 실행되므로 포트폴리오 ledger, 전략 state, journal run을 phase별로 분리할 수 있다.
- 요약 지표는 test phase만 집계한다: 검증 실현손익, 체결 수, rejected record 수.

주요 파일:

- `services/backtest_walk_forward.py`
- `tests/unit_test/services/test_backtest_walk_forward.py`

### Monte Carlo 검증

- `BacktestMonteCarloSimulator`를 추가했다.
- 표준 journal record 중 완료 trade(`SOLD`, `ROUND_TRIP`, `CLOSED`)의 `net_pnl`을 입력으로 사용한다.
- trade 결과 순서를 무작위로 섞어 equity path를 반복 계산한다.
- 최악 MDD, 최악 MDD 비율, 최장 연속 손실, ruin probability를 계산한다.
- 최종 equity의 평균, 5/50/95 분위수를 계산한다.
- `--mc-seed`를 지정하면 같은 결과를 재현할 수 있다.
- walk-forward 실행에서는 train/tune이 아니라 test phase journal만 Monte Carlo 입력으로 사용한다.

주요 파일:

- `services/backtest_monte_carlo.py`
- `tests/unit_test/services/test_backtest_monte_carlo.py`

### O'Neil PP/BGU fixture 검증

- 특정 날짜 기반 O'Neil PP/BGU 진입 fixture를 추가했다.
- `20260504` 기준 PP 통과, PP 거래량 탈락, BGU 통과, 체결강도 탈락, 마켓타이밍 탈락 케이스를 고정했다.
- `20260505` 기준 PP 수급 부족 탈락, MA 이격 탈락 케이스를 추가했다.
- `20260506` 기준 BGU 09:10 이전 휩소 guard 탈락, 시가 지지 실패 탈락 케이스를 추가했다.
- fixture 테스트는 `OneilPocketPivotStrategy.scan()` 실제 흐름을 호출해 신호 생성 여부, `entry_type`, reason, 마켓타이밍 OFF 시 가격 조회 생략을 검증한다.
- 같은 fixture를 period runner와 strategy debug runner에도 통과시켜 결과 parity를 검증한다.
- period runner는 최종 BUY 체결 여부를 fixture 기대값과 비교한다.
- strategy debug runner는 최종 `SIGNAL` / `REJECTED` decision journal이 fixture 기대값과 같은 방향인지 비교한다.
- debug runner decision journal은 최종 BUY 신호가 있는 종목의 중간 로그(`buy_signal_generated`, PP/BGU 분기 중간 탈락)를 별도 `REJECTED`로 저장하지 않는다.

주요 파일:

- `tests/fixtures/backtest/oneil_pp_bgu_entry_cases.json`
- `tests/unit_test/strategies/test_oneil_pocket_pivot_fixture_cases.py`
- `tests/unit_test/strategies/test_oneil_pp_bgu_fixture_runner_parity.py`
- `strategies/debug/strategy_debug_runner.py`

## 남은 작업

### 1. 표준 journal 저장 경로 후속 정리

기간 백테스트 결과는 이제 `BacktestJournalRepository`에 저장되고 운영 UI에서 체결 상세를 볼 수 있다. 남은 작업은 리포트 활용도를 더 높이는 것이다.

해야 할 일:

- 부분체결/미체결 record가 운영자가 보기 쉬운 상태명과 reason으로 표시되는지 확인한다.
- backtest-vs-live 비교 리포트에서 period run metadata를 함께 보여준다.

### 2. replay context 후속 정리

현재 활성 전략 7개는 replay SQS와 backtest clock을 같은 factory contract로 주입받는다. O'Neil PP/BGU fixture는 여러 거래일과 경계 조건까지 period/debug runner parity를 검증한다.

해야 할 일:

- O'Neil PP/BGU 외 활성 전략에도 전략별 fixture를 추가할지 결정

### 3. 체결 정책 후속 정리

현재 runner는 `current_bar`와 `next_bar` 정책 이름을 명시하고, replay provider가 정책별 체결 후보 봉을 선택한다. 남은 작업은 장 마감 직전과 데이터 공백 같은 경계 조건을 운영 정책으로 확정하는 것이다.

정해야 할 정책:

- 장 마감 직전 신호를 다음 거래일로 넘길지
- `next_bar` 정책에서 다음 분봉이 없는 경우 reject할지, 마지막 봉으로 검증할지
- 분봉 데이터가 없는 종목을 reject할지 skip할지

### 4. 성과 리포트 확장

현재 console 출력은 요약 중심이다. 운영 판단에는 더 많은 지표가 필요하다.

추가 후보:

- 총수익률, 순수익률
- 승률
- 평균 손익비
- MDD
- 연속 손실
- MFE/MAE
- 전략별/일자별 rejected reason 분포
- 시장 국면별 성과

### 5. 백테스트 fixture 보강

단일 기간 결과만으로 전략을 판단하지 않기 위해 검증용 고정 데이터를 더 늘려야 한다.

해야 할 일:

- O'Neil PP/BGU 외 활성 전략에도 fixture 기반 결과를 period runner와 strategy debug runner 양쪽에서 비교할지 결정
- 장 후반 종가 베팅 전략용 fixture를 추가해 RSI2/Channel Breakout 계열 경계 조건을 보강

### 6. 실전 체결 fixture 확보

백테스트 결과가 실거래와 같은 의미를 갖기 위해 실전 `inquire-daily-ccld` 응답 필드 검증이 필요하다.

현재 상태:

- 실제 체결 이력이 있는 실전 계좌 응답이 아직 없어 blocked 상태다.
- fixture 확보 후 민감정보를 제거해야 한다.
- 주문번호, 매수/매도, 체결/미체결/취소/거부 필드 매핑을 확정해야 한다.

## 백테스트 실행 방법

### 사전 조건

Python 환경:

```powershell
conda activate py310
```

프로젝트 루트에서 실행한다.

```powershell
cd C:\Users\Kyungsoo\Documents\Code\Investment
```

`config/config.yaml`에는 한국투자증권 API 설정이 필요하다.

주의:

- 과거 분봉 조회와 일별 프로그램매매 조회는 실전 전용 API다.
- 따라서 `scripts/run_backtest.py`는 기본적으로 실전 데이터 모드로 초기화한다.
- 실제 주문은 발생하지 않는다. 이 CLI는 과거 데이터 조회와 백테스트 계산만 수행한다.
- `--paper` 옵션은 서비스 그래프를 모의투자 모드로 초기화하지만, 과거 분봉/프로그램매매 API가 모의투자에서 지원되지 않을 수 있다.

### 지원 전략 확인

`--strategy`는 아래 값 중 하나를 사용한다.

```text
oneil_pocket_pivot
oneil_squeeze_breakout
high_tight_flag
first_pullback
larry_williams_vbo
rsi2_pullback
larry_williams_channel_breakout
```

### 날짜 목록으로 실행

```powershell
python -m scripts.run_backtest --strategy oneil_pocket_pivot --dates 20260501,20260504,20260505
```

### 날짜 범위로 실행

```powershell
python -m scripts.run_backtest --strategy oneil_pocket_pivot --start-date 20260501 --end-date 20260510
```

### 과거 장중 시각 지정

기본값은 `12:00:00`이다. 이 시각은 전략의 마켓 시간 필터, 거래량 환산, 유니버스 refresh 판단에 사용된다.

```powershell
python -m scripts.run_backtest --strategy oneil_pocket_pivot --start-date 20260501 --end-date 20260510 --backtest-time 09:30:00
```

### 초기 현금 지정

```powershell
python -m scripts.run_backtest --strategy oneil_pocket_pivot --start-date 20260501 --end-date 20260510 --initial-cash 10000000
```

### 전략별 최대 보유 종목 수 제한

```powershell
python -m scripts.run_backtest --strategy oneil_pocket_pivot --start-date 20260501 --end-date 20260510 --initial-cash 10000000 --max-positions 3
```

### 체결 봉 정책 지정

기본값은 `current_bar`다. 가격에 닿은 첫 분봉을 체결 후보 봉으로 사용한다.

```powershell
python -m scripts.run_backtest --strategy oneil_pocket_pivot --start-date 20260501 --end-date 20260510 --execution-bar-policy current_bar
```

`next_bar`는 가격에 닿은 신호 봉 다음 분봉을 체결 후보 봉으로 사용한다.

```powershell
python -m scripts.run_backtest --strategy oneil_pocket_pivot --start-date 20260501 --end-date 20260510 --execution-bar-policy next_bar
```

### 운영 RiskGate/PositionSizing 설정 적용

```powershell
python -m scripts.run_backtest --strategy oneil_pocket_pivot --start-date 20260501 --end-date 20260510 --initial-cash 10000000 --use-risk-sizing
```

이 옵션은 실제 주문을 내지 않는다. `config.yaml` / `risk_gate_config.yaml`의 PositionSizing/RiskGate 설정을 읽고, 백테스트 포트폴리오 ledger를 계좌 snapshot처럼 사용해 수량 조정과 주문 차단을 dry-run으로 재현한다.

### Walk-forward 검증 실행

```powershell
python -m scripts.run_backtest --strategy oneil_pocket_pivot --start-date 20260501 --end-date 20260630 --walk-forward --wf-train-days 20 --wf-tune-days 5 --wf-test-days 5
```

이 실행은 날짜 목록을 train 20일, tune 5일, test 5일 구간으로 나눈다. `--wf-step-days`를 생략하면 test window 크기만큼 다음 구간으로 이동한다.

이 구간들은 서로 독립 실행된다. 즉, train에서 생긴 보유 종목이나 현금 상태가 tune/test로 이어지지 않고, 각 phase의 journal run도 따로 저장된다.

이 옵션은 아직 자동 파라미터 탐색을 수행하지 않는다. 현재 구현은 튜닝 구간과 검증 구간을 분리해 실행하고, 검증(test) 구간 성과만 요약 집계하는 기반이다.

운영 RiskGate/PositionSizing dry-run과 함께 사용할 수 있다.

```powershell
python -m scripts.run_backtest --strategy oneil_pocket_pivot --start-date 20260501 --end-date 20260630 --walk-forward --wf-train-days 20 --wf-tune-days 5 --wf-test-days 5 --use-risk-sizing
```

### Monte Carlo 검증 실행

```powershell
python -m scripts.run_backtest --strategy oneil_pocket_pivot --start-date 20260501 --end-date 20260630 --monte-carlo --mc-runs 1000 --mc-seed 42 --mc-ruin-drawdown-pct 30
```

이 실행은 백테스트 결과 journal에서 완료 trade의 `net_pnl`을 모아 순서를 섞고, 최악 MDD, 최장 연속 손실, ruin probability를 계산한다.

walk-forward와 함께 쓰면 test phase 결과만 Monte Carlo 입력으로 사용한다.

```powershell
python -m scripts.run_backtest --strategy oneil_pocket_pivot --start-date 20260501 --end-date 20260630 --walk-forward --wf-train-days 20 --wf-tune-days 5 --wf-test-days 5 --monte-carlo --mc-runs 1000 --mc-seed 42
```

### JSON으로 출력

```powershell
python -m scripts.run_backtest --strategy oneil_pocket_pivot --start-date 20260501 --end-date 20260510 --output json
```

### 파일로 저장

```powershell
python -m scripts.run_backtest --strategy oneil_pocket_pivot --start-date 20260501 --end-date 20260510 --output json --output-file data/backtest_result.json
```

현재 `--output-file`은 CLI 결과 파일을 별도로 저장하는 옵션이다. 이 옵션을 쓰지 않아도 백테스트 run은 `data/backtest_journals`에 표준 journal로 저장된다.

## 출력 해석

console 출력 예:

```text
[BACKTEST RESULT]
전략: 오닐PP/BGU
기간: 20260501 ~ 20260510 (10일)
BUY 체결: 2
SELL 체결: 1
거부 기록: 3
보유 종목: 1
현금: 9,500,000
가용현금: 9,500,000
실현손익(순): 120,000
체결 봉 정책: current_bar
journal run: period_오닐PP/BGU_20260501_20260510
```

의미:

- `BUY 체결`: simulator에서 수량이 1주 이상 체결된 매수 report 수
- `SELL 체결`: simulator에서 수량이 1주 이상 체결된 매도 report 수
- `거부 기록`: 현금 부족, max positions, 미체결 등으로 journal에 남은 record 수
- `보유 종목`: 기간 종료 시점에 장부에 남아 있는 포지션 수
- `현금`: 체결과 비용 반영 후 현금
- `가용현금`: 예약 현금을 제외한 현금
- `실현손익(순)`: 매도 체결 후 수수료/세금 반영 기준 실현 손익
- `체결 봉 정책`: runner가 bar provider에 전달한 체결 후보 봉 선택 정책
- `journal run`: `data/backtest_journals`에 저장된 표준 journal run id

JSON 출력에는 `execution_reports`, `journal_records`, `portfolio`가 포함된다.

## 전략 디버그와 기간 백테스트 차이

`run_strategy_debug`:

- 목적: 왜 샀는지, 왜 안 샀는지 진단
- 단일 실행 시점의 후보와 탈락 사유 확인에 적합
- portfolio dry-run 옵션으로 현금 부족과 max positions를 확인할 수 있음

예:

```powershell
python -m scripts.run_strategy_debug --codes 005930,000660 --portfolio-cash 10000000 --max-positions 3
```

`run_backtest`:

- 목적: 날짜 범위 동안 전략 신호, 체결, 포트폴리오 장부 변화를 재현
- 기간 수익률과 보유/현금 변화를 검증하는 방향
- 활성 전략 7개를 같은 replay clock/context contract로 실행

예:

```powershell
python -m scripts.run_backtest --strategy oneil_pocket_pivot --start-date 20260501 --end-date 20260510
```

## 현재 한계

- 지원 전략은 활성 전략 7개로 확장됐지만, fixture 기반 parity 검증은 아직 O'Neil PP/BGU 중심이다.
- `--use-risk-sizing`은 백테스트 ledger 기반 snapshot을 사용하므로 실제 계좌 잔고나 미체결 주문 상태를 조회하지 않는다.
- `--walk-forward`는 train/tune/test 분리 실행과 test phase 요약 집계까지 지원한다. 자동 파라미터 최적화는 아직 수행하지 않는다.
- `--monte-carlo`는 표준 journal에 `net_pnl`이 있는 완료 trade만 입력으로 사용한다. period runner의 SELL 체결 journal은 현재 `SOLD` 상태와 `net_pnl`을 기록한다.
- 성과 리포트는 기본 요약 중심이다.
- 과거 체결강도는 분봉 row에 `tday_rltv` 또는 `execution_strength` 유사 필드가 있어야 replay된다.
- 과거 분봉 또는 프로그램매매 API가 비어 있으면 신호가 없거나 미체결/empty 결과가 나올 수 있다.
- 실전 체결 fixture가 없어 실거래 체결 대사와 백테스트 체결 리포트의 완전한 end-to-end 검증은 아직 blocked 상태다.

## 관련 테스트

관련 단위 테스트:

```powershell
pytest tests/unit_test/services/test_backtest_execution_simulator.py -v
pytest tests/unit_test/services/test_backtest_period_runner.py -v
pytest tests/unit_test/services/test_backtest_replay_adapter.py -v
pytest tests/unit_test/services/test_backtest_replay_context.py -v
pytest tests/unit_test/services/test_backtest_walk_forward.py -v
pytest tests/unit_test/services/test_backtest_monte_carlo.py -v
pytest tests/unit_test/scripts/test_run_backtest.py -v
pytest tests/unit_test/strategies/test_oneil_pocket_pivot_fixture_cases.py -v
pytest tests/unit_test/strategies/test_oneil_pp_bgu_fixture_runner_parity.py -v
pytest tests/unit_test/strategies/debug/test_strategy_debug_runner.py -v
```

전체 검증:

```powershell
pytest tests/unit_test -v
pytest tests/integration_test -v
```

최근 확인 결과:

- 관련 테스트: `111 passed`
- 전체 단위 테스트: `4202 passed`
- 전체 통합 테스트: `208 passed`
