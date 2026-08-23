# Overseas Dry-run Would-be Performance

- date: `20260722 ~ 20260821`  signal_source: `['overseas_dryrun', 'overseas_pp_dryrun', 'overseas_bgu_dryrun', 'overseas_cb_dryrun', 'overseas_rsi2_dryrun', 'overseas_osb_dryrun']`  shadow_dir: `logs\strategies\event_shadow`

## 가정/주의

- 왕복 비용 0.500% (`commission_only`): 미국주식 온라인 기본 수수료 0.25%/side를 왕복으로 반영한 값이며, 환전 스프레드·SEC/TAF 등 매도 제비용은 별도입니다.
- 일봉 기반 would-be 진입가: 당일 intraday 체결 경로가 아니라 `open + K * prev_range` 목표가 체결을 가정하므로 실제 장중 체결가보다 낙관적일 수 있습니다.

## 엣지 판정

- overall_decision: `NO_GO`
- trading_days: 22 / 5
- min_strategy_sample: 5, min_avg_return_pct: 0.000%

| strategy | status | signals | sample | avg_return_pct | basis |
|---|---|---:|---:|---:|---|
| VBO | FAIL_NEGATIVE_EDGE | 523 | 188 | -1.624% | multiday_net |
| PP | NO_SIGNALS | 0 | 0 | — | same_day_realized |
| BGU | NO_SIGNALS | 0 | 0 | — | same_day_realized |
| CB | NO_SIGNALS | 0 | 0 | — | same_day_realized |
| RSI2 | NO_SIGNALS | 0 | 0 | — | same_day_realized |
| OSB | NO_SIGNALS | 0 | 0 | — | same_day_realized |

## 전체 집계

| 항목 | 값 |
|---|---:|
| signals | 523 |
| realized_sample | 523 |
| wins / losses | 150 / 373 |
| win_rate | 0.287 |
| avg_realized_pct | -1.054% |
| median_realized_pct | -3.000% |
| sum_realized_pct | -551.108% |

## 전략별

| strategy | signals | realized_sample | wins | sum_realized_pct |
|---|---:|---:|---:|---:|
| VBO | 523 | 523 | 150 | -551.108% |

## 청산 판정 가능성 (bracket)

일봉은 저가 발생 시각을 담지 않아 `저 <= 손절가` 건은 손절 체결 여부를 확정할 수 없습니다(진입 전 저가로도 성립). 비관 평균만 보면 하향 편향되므로 판정 가능 건 집계와 비관·낙관 양끝을 함께 봅니다.

| 항목 | 값 |
|---|---:|
| decided / undecided | 260 / 263 |
| undecided_ratio | 0.503 |
| decided_avg_realized_pct | 0.915% |
| decided_win_rate | 0.577 |
| pessimistic_avg_realized_pct | -1.054% |
| optimistic_avg_realized_pct | 0.326% |

## 청산 사유

| reason | 건수 |
|---|---:|
| eod | 260 |
| stop | 122 |
| undecided | 141 |

## 거래소별

| exchange | signals | wins | sum_realized_pct |
|---|---:|---:|---:|
| NASD | 523 | 150 | -551.108% |

## 거래일별

| date | signals | wins | sum_realized_pct |
|---|---:|---:|---:|
| 20260722 | 29 | 11 | -28.487% |
| 20260723 | 23 | 6 | -27.113% |
| 20260727 | 12 | 7 | 1.352% |
| 20260728 | 21 | 2 | -36.256% |
| 20260729 | 19 | 3 | -25.328% |
| 20260730 | 33 | 5 | -56.521% |
| 20260731 | 18 | 8 | -15.777% |
| 20260803 | 30 | 6 | -55.035% |
| 20260804 | 34 | 14 | -26.663% |
| 20260805 | 14 | 2 | -35.219% |
| 20260806 | 34 | 9 | -51.985% |
| 20260807 | 15 | 5 | -13.420% |
| 20260810 | 22 | 9 | -15.856% |
| 20260811 | 58 | 15 | -89.038% |
| 20260812 | 23 | 3 | -38.983% |
| 20260813 | 37 | 16 | -8.020% |
| 20260814 | 12 | 3 | -14.962% |
| 20260817 | 21 | 3 | -29.292% |
| 20260818 | 18 | 2 | -34.376% |
| 20260819 | 23 | 15 | 81.074% |
| 20260820 | 10 | 1 | -20.866% |
| 20260821 | 17 | 5 | -10.339% |

## 사이징 (would-be USD 노출)

- sized_count: 523
- total_notional_usd: 433259.243
- avg_notional_usd: 828.412
- fx_sized_count: 0
- total_krw_exposure: 0.000
- avg_krw_exposure: —

## 멀티데이 회고 재구성 (would-be 멀티세션 보유)

- reconstructed_count: 188
- unmatched_count: 335
- win_rate: 0.223
- avg_holding_days: 1.011
- avg_net_return_pct: -1.624%
- same_day_avg_realized_pct: -1.222%
- multiday_avg_gross_pct: -1.124%
- **gap_pct (multiday − same_day): 0.099%**

| exit_reason | count |
| --- | ---: |
| stop | 136 |
| terminal | 50 |
| trailing | 2 |

| strategy | count | avg_net_return_pct |
| --- | ---: | ---: |
| VBO | 188 | -1.624% |
