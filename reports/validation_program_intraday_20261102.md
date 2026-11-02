# 프로그램매매 point-in-time replay 정정 (2026-11-02)

## 발견

유효 캡처가 31일로 늘어 20/5/5 walk-forward를 시작했지만, 과거 날짜 평가 중
`get_program_trade_by_stock_daily`가 현재 KIS API를 호출했다. 직렬 실행에서도 rate-limit
재시도가 발생했고, 병렬 실행에서는 공유 캐시 DB 잠금까지 겹쳤다. 실행은 결과 저장 전에
중단했다.

기존 캡처의 `program_trades`는 장마감 시점 최종 순매수 단일값이다. 전략 평가 시각은
15:20 등 장중이므로 이 값을 대신 쓰면 미래 정보가 된다. 따라서 외부 호출을 최종값으로
단순 치환하지 않았다.

## 수정

- `pt_history`를 캡처 거래일과 장중 시간 범위로 제한해 `program_trades_intraday` 시계열을 저장한다.
- replay는 평가 시각 이하의 최신 프로그램 순매수만 사용한다.
- 캡처 파일이 존재하는 날짜에는 시계열이 비어 있어도 현재 KIS 프로그램 API로 폴백하지 않는다.
- 품질 게이트는 `program_db` 캡처에 point-in-time 시계열 커버리지 50% 이상을 요구한다.
- 별도 `replay_program_trades_intraday_YYYYMMDD.json` 파일도 함께 기록한다.

## 준비도 영향

수정 전 계획은 31/30일로 ready였지만, 기존 31일에는 point-in-time 프로그램 시계열이 없어
수정 후 0/30일로 대기 상태다. 현재 로컬 `pt_history`가 보존한 날짜는 2026-10-23~30의
6거래일뿐이어서 전체 소급 복원은 불가능하다. 새 캡처 계약으로 30거래일을 다시 축적한 뒤
walk-forward, Monte Carlo, parameter-stability를 실행한다.

## 검증

- 실패 테스트: 날짜 경계 누출, 평가 시각 이후 값 사용, 캡처 날짜의 라이브 API 폴백,
  시계열 없는 캡처의 품질 통과를 각각 재현했다.
- 관련 캡처·replay·품질·캠페인 테스트: 108 passed.
- 전체 단위 테스트: 10,237 passed.
- 전체 통합 테스트: 323 passed.
- `python -m scripts.run_validation_campaign --plan-only`: 유효일 0, walk-forward waiting.

## 부분 소급 복원 (2026-11-02 추가)

위 "전체 소급 복원 불가"는 31일 전체 기준으로는 맞지만, 보존된 6거래일 중 4일은 복원 가능했다.
`pt_history` 는 `RETENTION_DAYS=7` 로 `created_at` 7일 경과 행을 지우므로, 먼저 DB를
`sqlite3` backup API로 `data/program_subscribe/backups/program_trading_20261102.db` 에 고정한 뒤
`scripts/backfill_program_trades_intraday.py` 로 캡처와 동일한 조회 규칙(거래일 epoch 경계 +
`start_hhmmss`~`end_hhmmss`)을 적용했다. 원본 캡처는 `data/backtest_microstructure_backup_20261102/` 에 보존했다.

| 날짜 | 시계열 보유 종목 | program intraday 커버리지 | 게이트 |
|---|---|---|---|
| 20261023 | 0 | 0% (원본 없음) | 미적용 |
| 20261026 | 0 | 0% (1행만 잔존) | 미적용 |
| 20261027 | 23/28 | 82.1% | 통과 |
| 20261028 | 20/26 | 76.9% | 통과 |
| 20261029 | 17/24 | 70.8% | 통과 |
| 20261030 | 22/27 | 81.5% | 통과 |

- `python -m scripts.analyze_backtest_microstructure_quality --date-from 20261027 --date-to 20261102 --fail-on-gate`: 전 날짜 통과.
- `python -m scripts.run_validation_campaign --plan-only`: `valid_dates` 5개(10-27~30, 11-02), walk-forward 5/30 대기.
- 복원 데이터도 장중 시점에 수집된 원본 시계열이라 평가 시각 이하 값만 쓰는 replay 계약이 그대로 적용된다.
