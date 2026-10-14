# 유효 캡처 백테스트 파일럿 결과 (2026-10-15)

## 결론

- 품질 게이트를 통과한 19거래일로 활성 전략 7개를 실행했고 모든 subprocess가 정상 완료됐다.
- 7개 전략 모두 BUY 0건, SELL 0건, REJECTED 0건이었다.
- PIT base 후보는 19일 중 18일에 존재했고 후보-일 관측치는 합계 112건이었다. 따라서 이번 0거래를 후보 부재로 설명할 수 없다.
- 현재 확인 가능한 범위에서는 후보 이후 매수 신호가 생성되지 않았다. period journal이 후보별 필터 탈락을 기록하지 않으므로 정확히 어느 조건이 지배적으로 막았는지는 아직 판정할 수 없다.
- walk-forward/Monte Carlo는 유효일 30개가 필요한데 19개만 확보돼 계속 대기한다.

## 실행

```powershell
python -m scripts.run_validation_campaign --output-file data/validation_campaign_20261014.json
```

대상 전략:

- `oneil_pocket_pivot`
- `oneil_squeeze_breakout`
- `high_tight_flag`
- `first_pullback`
- `larry_williams_vbo`
- `rsi2_pullback`
- `larry_williams_channel_breakout`

## 결과 요약

| 항목 | 결과 |
|---|---:|
| 전체 캡처 파일 | 66 |
| 품질 게이트 통과 거래일 | 19 |
| 정상 완료 전략 | 7 / 7 |
| BUY / SELL / REJECTED | 0 / 0 / 0 |
| PIT base 후보가 있는 거래일 | 18 / 19 |
| PIT base 후보-일 관측치 | 112 |
| 일평균 PIT base 후보 | 5.89 |
| walk-forward 준비도 | 19 / 30 (대기) |

최초 실행의 전략별 결과는 모두 `zero_trade_reason=no_signal_or_candidate_observed`였다. 당시 runner가 신호와 journal만으로 후보 관측 여부를 구분할 수 없어 사용한 보수적 분류다. 후속 계측에서 plan에 날짜별 `candidate_observability`를 추가했고, 같은 코퍼스는 18일·112건으로 자동 집계된다. 이제 이 경우의 0거래 사유는 `no_signal_observed`로 분리된다.

## 실행 중 발견하고 수정한 결함

첫 실행에서는 O'Neil 계열 등 5개 전략이 `MarketRegimeService`의 지수 일봉 조회에서 실패했다.

1. replay context가 universe의 중첩 `_regime_svc`에 replay SQS/backtest clock을 주입하지 않았다.
2. 공통 bootstrap이 `StockQueryService`에 broker를 전달하지 않아 지수 일봉 조회가 `None` broker에서 실패했다.

두 경로를 회귀 테스트로 고정한 뒤 캠페인을 다시 실행해 7개 전략 모두 완료되는 것을 확인했다.

## 0거래 재진단

2026-07-03 파일럿 때의 설명 중 현재 premium universe가 비어 후보가 없다는 원인은 이번 캠페인에는 해당하지 않는다. 이번 runner는 날짜별 캡처의 `metadata.candidate_sources.base`를 PIT 후보로 사용했고, 19일 중 18일에서 후보가 확인됐다.

확정 가능한 경계는 다음과 같다.

1. 품질 게이트를 통과한 replay 날짜와 PIT base 후보가 runner에 제공됐다.
2. 전략 실행은 예외 없이 끝났다.
3. BUY 신호, 체결 거부, 포지션은 하나도 journal에 남지 않았다.
4. 따라서 체결 시뮬레이터나 자금/포지션 제한보다 앞선 후보 평가·신호 생성 단계에서 0건이 됐다.

다만 전략별 candidate funnel과 필터 탈락 사유가 period journal에 없어서, 마켓 국면·가격 패턴·거래량·체결강도 등 어느 조건이 주원인인지는 근거 없이 단정할 수 없다. 다음 진단 작업은 주요 필터별 통과/탈락 수를 날짜·전략별로 기록하는 계측이다.

## 검증

- 관련 단위 테스트: 103 passed
- 전체 단위 테스트: 10,117 passed
- 전체 통합 테스트: 318 passed
- 수정 후 캠페인: 7 completed, 0 failed

통합 테스트에서 Windows CRLF 파일을 LF 전용 경계로 파싱하던 frontend DOM harness 결함도 재현되어, 입력 개행을 정규화하도록 함께 수정했다.
