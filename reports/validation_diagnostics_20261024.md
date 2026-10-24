# 기간 백테스트 후보 진단 (2026-10-24)

## 계측 범위

7개 지원 전략의 실제 후보 평가 진입, 필터별 관측 사유, BUY 신호 수,
스캔 전체 생략 사유를 날짜별 `scan_diagnostics`로 저장한다.
종목·사유 중복은 제거하지만 여러 패턴을 검사한 종목은 여러 사유에 포함될 수 있다.
아무 사유도 남지 않은 평가 종목은 `unexplained_codes`에 보존한다.
진단은 거래 journal과 분리되어 주문 거부 수와 성과 표본을 바꾸지 않는다.

## 최신 코퍼스 준비도

`python -m scripts.run_validation_campaign --plan-only --output-file data/validation_plan_20261024.json`

- 품질 통과 거래일: 26일 (2026-07-22~2026-10-23 중 유효일)
- PIT base 후보가 있는 거래일: 25일
- 후보-일 관측: 153건
- 20/5/5 walk-forward: 26/30일, 4일 부족
- 10월 15일 보고서의 19일·112건보다 유효일 7일·후보-일 41건 증가했다.

## 확인된 실행 시각 문제

다음 명령으로 채널 돌파 전략을 별도 재현했다.

```powershell
python -m scripts.run_backtest --strategy larry_williams_channel_breakout --dates 20261014 --microstructure-dir data/backtest_microstructure --captured-candidate-dir data/backtest_microstructure --output json --output-file data/channel_breakout_diagnostic_20261024.json
```

결과는 `evaluated_candidates=0`, `buy_signals=0`,
`scan_skip_reasons={"Before entry cutoff (15:10)": 1}`이었다.
해당 날짜의 PIT base 후보는 5건이지만 watchlist 조회 전에 스캔을 생략했다.
CLI는 RSI2에만 15:20을 적용하고 채널 돌파에는 12:00 기본값을 적용한다.
채널 돌파의 0거래에는 이 실행 시각 불일치가 먼저 작용하므로, 성과·필터 품질로 해석할 수 없다.
이번 계측 변경에서는 시각 정책을 바꾸지 않았다.

## 전체 캠페인 실행 제한

`python -m scripts.run_validation_campaign --output-file data/validation_campaign_20261024.json`
으로 26거래일·7전략 재실행을 시작했으나, 첫 PP 전략의 과거 조회가 오래 지속됐다.
최종 코드의 PP 하루 확인 실행에서도 KIS `Rate limit exceeded`와 RetryQueue 재시도가
확인되어 조회 부하를 줄이기 위해 이번 작업에서 시작한 두 실행을 중단했다.
전체 캠페인 JSON은 생성되지 않았으며, 7전략 실행 완료나 전체 필터별 원인 확정으로 보고하지 않는다.
관련 로컬 로그는 `data/pocket_pivot_diagnostic_20261024.log`다.

코드 대조상 `--microstructure-dir`는 체결용 bar provider에 연결되지만,
`StockQueryBacktestReplayService._get_intraday_rows`는 원본 SQS의 지정일 분봉 조회를 호출한다.
따라서 캡처 파일이 있어도 신호 평가가 완전한 오프라인 실행은 아니다.
전체 재진단의 다음 선행 작업은 캡처 분봉을 신호 평가에도 연결할지 검토하고,
장중 시각 cutoff·체결강도·프로그램매매의 과거 시점 일관성을 테스트로 고정하는 것이다.
현재 증거로 PP 0거래 전체를 특정 필터나 레이트 제한 탓이라고 단정할 수 없다.

## 로컬 검증

- 관련 테스트 278개 통과, 이후 공통 계층 이동·로그 레벨 수정 관련 26개 통과.
- 최종 전체 단위 테스트: 10,183 passed, 1 failed.
  실패는 `test_krx_after_market_scope_guard.py::test_only_manual_order_path_enables_krx_after_market`
  실행 중 xdist worker 종료였다. 동일 가드는 단독 실행에서 통과했다.
- 최종 전체 통합 테스트: 323 passed.
- 재현: `python -m pytest tests/unit_test -v` / 단독 확인:
  `python -m pytest tests/unit_test/services/test_krx_after_market_scope_guard.py -v -n0`.
- 전체 단위 스위트의 로컬 워커 종료는 PR CI `build-and-test`에서 최종 확인한다.
