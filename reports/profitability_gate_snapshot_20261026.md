# 수익성 게이트 journal 스냅샷 (2026-10-26)

## 범위

`data/VirtualTradeRepository/virtual_trade.db`의 표준 journal을 읽기 전용으로 재집계했다.
실전 확장 기준은 기본 real overlay를 적용했다.

- 최소 SOLD 표본 100건
- profit factor 1.3 이상
- payoff ratio 1.2 이상
- 승률 40% 이상
- 최대 낙폭 12% 이하
- regime별 최소 표본 30건
- parameter stability, Monte Carlo, regime balance 필수

## 축적 현황

- 전체 458건
- SOLD 218건
- 진행 중 240건
- 게이트 지표에 포함된 SOLD 197건
- 제외된 SOLD 21건: 잔고 대사 강제종결 21건(VBO 8, CB 2, broker reconciled 11)
- `data_quality_flag` 제외: 0건
- journal source: `virtual_trade` 458건

마지막 항목 때문에 현재 원장만으로는 shadow, paper, 소액 canary 표본을 구분할 수 없다.
아래 결과는 가상매매 원장 전체의 전략별 결과이며 canary 실체결 근거가 아니다.

## 전략별 판정

| 전략 | 인정 SOLD | 상태 | 승률 | Profit factor | Payoff | 평균 순수익률 | 총 순손익 |
|---|---:|---|---:|---:|---:|---:|---:|
| Larry Williams VBO | 155 | fail | 47.1% | 0.709 | 1.054 | -0.065% | -797,056원 |
| RSI2 Pullback | 16 | insufficient sample | 25.0% | 0.270 | 0.710 | -3.488% | -841,040원 |
| First Pullback | 11 | insufficient sample | 18.2% | 0.315 | 1.865 | -3.362% | -842,306원 |
| Larry Williams CB | 4 | insufficient sample | 25.0% | 1.049 | 3.096 | 0.169% | 20,451원 |
| Inverse ETF Regime | 4 | insufficient sample | 0.0% | N/A | N/A | -5.576% | -29,701원 |
| O'Neil PP/BGU | 2 | insufficient sample | 50.0% | 4.415 | 4.435 | 9.621% | 382,595원 |
| O'Neil Squeeze Breakout | 2 | insufficient sample | 50.0% | 1.048 | 1.058 | 0.210% | 6,839원 |
| O'Neil Pocket Pivot | 2 | insufficient sample | 100.0% | N/A | N/A | 14.301% | 281,822원 |
| 수동매매 | 1 | insufficient sample | 0.0% | N/A | N/A | -0.078% | -3원 |

## 결론

VBO는 최소 표본 100건을 넘었지만 profit factor, payoff ratio, 평균 순수익과 총 순손익이
모두 실전 확장 기준에 미달한다. Monte Carlo와 parameter stability 증거도 없고,
KOSPI/KOSDAQ 상승 regime 순손익이 음수이며 SIDEWAYS 표본이 없어 regime balance도
충족하지 못한다. 현재 규칙으로 자금 확대나 실전 전환 근거는 없다.

다른 전략은 모두 최소 표본 100건에 미달한다. 특히 표본 1~4건의 양수 결과는 수익성
근거로 해석하지 않는다. 다음 재집계 전에는 journal source를 shadow/paper/canary로
구분해 저장할 수 있어야 각 단계의 증거를 따로 판정할 수 있다.
