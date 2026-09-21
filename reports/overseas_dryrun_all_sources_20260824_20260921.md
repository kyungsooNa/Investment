# Overseas Dry-run Would-be Performance

- date: `20260824 ~ 20260921`  signal_source: `['overseas_dryrun', 'overseas_pp_dryrun', 'overseas_bgu_dryrun', 'overseas_cb_dryrun', 'overseas_rsi2_dryrun', 'overseas_osb_dryrun']`  shadow_dir: `logs/strategies/event_shadow`

## 가정/주의

- 왕복 비용 0.500% (`commission_only`): 미국주식 온라인 기본 수수료 0.25%/side를 왕복으로 반영한 값이며, 환전 스프레드·SEC/TAF 등 매도 제비용은 별도입니다.
- 일봉 기반 would-be 진입가: 당일 intraday 체결 경로가 아니라 `open + K * prev_range` 목표가 체결을 가정하므로 실제 장중 체결가보다 낙관적일 수 있습니다.

## 엣지 판정

- overall_decision: `CANARY_CANDIDATE`
- trading_days: 18 / 5
- min_strategy_sample: 5, min_avg_return_pct: 0.000%

| strategy | status | signals | sample | avg_return_pct | basis |
|---|---|---:|---:|---:|---|
| VBO | FAIL_NEGATIVE_EDGE | 391 | 391 | -1.873% | multiday_net |
| PP | FAIL_NEGATIVE_EDGE | 24 | 24 | -2.792% | multiday_net |
| BGU | NO_SIGNALS | 0 | 0 | — | same_day_realized |
| CB | NO_SIGNALS | 0 | 0 | — | same_day_realized |
| RSI2 | PASS_CANDIDATE | 59 | 59 | 0.762% | multiday_net |
| OSB | WAIT_SAMPLE | 1 | 1 | -7.400% | multiday_net |

## 전체 집계

| 항목 | 값 |
|---|---:|
| signals | 475 |
| realized_sample | 391 |
| wins / losses | 144 / 247 |
| win_rate | 0.368 |
| avg_realized_pct | -0.664% |
| median_realized_pct | -0.700% |
| sum_realized_pct | -259.534% |

## 전략별

| strategy | signals | realized_sample | wins | sum_realized_pct |
|---|---:|---:|---:|---:|
| OSB | 1 | 0 | 0 | 0.000% |
| PP | 24 | 0 | 0 | 0.000% |
| RSI2 | 59 | 0 | 0 | 0.000% |
| VBO | 391 | 391 | 144 | -259.534% |

## 청산 판정 가능성 (bracket)

일봉은 저가 발생 시각을 담지 않아 `저 <= 손절가` 건은 손절 체결 여부를 확정할 수 없습니다(진입 전 저가로도 성립). 비관 평균만 보면 하향 편향되므로 판정 가능 건 집계와 비관·낙관 양끝을 함께 봅니다.

| 항목 | 값 |
|---|---:|
| decided / undecided | 259 / 132 |
| undecided_ratio | 0.338 |
| decided_avg_realized_pct | 0.527% |
| decided_win_rate | 0.556 |
| pessimistic_avg_realized_pct | -0.664% |
| optimistic_avg_realized_pct | 0.359% |

## 청산 사유

| reason | 건수 |
|---|---:|
| eod | 259 |
| undecided | 132 |
| unknown | 84 |

## 거래소별

| exchange | signals | wins | sum_realized_pct |
|---|---:|---:|---:|
| NASD | 475 | 144 | -259.534% |

## 거래일별

| date | signals | wins | sum_realized_pct |
|---|---:|---:|---:|
| 20260824 | 17 | 7 | -8.731% |
| 20260825 | 17 | 4 | -20.423% |
| 20260826 | 24 | 11 | -3.386% |
| 20260827 | 20 | 10 | -5.476% |
| 20260828 | 18 | 5 | -12.804% |
| 20260831 | 20 | 9 | -3.797% |
| 20260901 | 25 | 6 | -28.874% |
| 20260902 | 23 | 9 | -12.088% |
| 20260903 | 32 | 11 | -28.349% |
| 20260904 | 27 | 6 | -27.692% |
| 20260909 | 16 | 5 | -17.220% |
| 20260910 | 44 | 11 | -31.269% |
| 20260911 | 34 | 10 | -21.918% |
| 20260914 | 48 | 14 | -28.585% |
| 20260915 | 22 | 1 | -14.260% |
| 20260916 | 28 | 5 | -7.700% |
| 20260917 | 17 | 5 | -11.211% |
| 20260918 | 43 | 15 | 24.248% |

## 사이징 (would-be USD 노출)

- sized_count: 475
- total_notional_usd: 389311.926
- avg_notional_usd: 819.604
- fx_sized_count: 0
- total_krw_exposure: 0.000
- avg_krw_exposure: —

## 멀티데이 회고 재구성 (would-be 멀티세션 보유)

- reconstructed_count: 475
- unmatched_count: 0
- win_rate: 0.265
- avg_holding_days: 2.436
- avg_net_return_pct: -1.604%
- same_day_avg_realized_pct: -0.664%
- multiday_avg_gross_pct: -1.104%
- **gap_pct (multiday − same_day): -0.440%**

| exit_reason | count |
| --- | ---: |
| stop | 297 |
| terminal | 135 |
| trailing | 43 |

| strategy | count | avg_net_return_pct |
| --- | ---: | ---: |
| OSB | 1 | -7.400% |
| PP | 24 | -2.792% |
| RSI2 | 59 | 0.762% |
| VBO | 391 | -1.873% |
