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
