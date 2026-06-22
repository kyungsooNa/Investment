# No-Tick Operational Experiment Analysis

## Summary

- Experiments: 4

### Verdicts

| Verdict | Count |
|---------|------:|
| common_no_tick_persists | 1 |
| non_common_product_class_no_tick_likely | 1 |
| refresh_ineffective | 1 |
| symbol_or_account_level_issue_likely | 1 |

### Code Classes

| Class | Count |
|-------|------:|
| subscribe_failure | 0 |
| ack_failure | 0 |
| quality_reject | 0 |
| received | 5 |
| no_tick | 18 |
| not_executed | 0 |

## Experiments

### A_common_stock_only

- Verdict: `common_no_tick_persists`
- Evidence: Some common stocks still received zero ticks while peers received ticks.
- Next action: Run C_no_tick_common_solo or inspect symbol/slot ordering.

| Code | Name | Type | Class | Received Δ | Dispatched Δ | Reject Δ |
|------|------|------|-------|-----------:|-------------:|---------:|
| 028260 | 삼성물산 | common_or_other | received | 729 | 0 | 0 |
| 032830 | 삼성생명 | common_or_other | received | 524 | 0 | 0 |
| 240810 | 원익IPS | common_or_other | received | 806 | 0 | 0 |
| 034730 | SK | common_or_other | received | 247 | 0 | 0 |
| 298040 | 효성중공업 | common_or_other | received | 170 | 0 | 0 |
| 080220 | 제주반도체 | common_or_other | no_tick | 0 | 0 | 0 |
| 353200 | 대덕전자 | common_or_other | no_tick | 0 | 0 | 0 |
| 004710 | 한솔테크닉스 | common_or_other | no_tick | 0 | 0 | 0 |
| 198440 | 강동씨앤엘 | common_or_other | no_tick | 0 | 0 | 0 |
| 320000 | 한울반도체 | common_or_other | no_tick | 0 | 0 | 0 |

### B_non_common_only

- Verdict: `non_common_product_class_no_tick_likely`
- Evidence: KIS sent no frames for the ETF/preferred-only cohort after ACK.
- Next action: Ask KIS to confirm ETF/preferred WebSocket support or separate product TR behavior.

| Code | Name | Type | Class | Received Δ | Dispatched Δ | Reject Δ |
|------|------|------|-------|-----------:|-------------:|---------:|
| 0162Z0 | RISE 삼성전자SK하이닉스채권혼합50 | ETF | no_tick | 0 | 0 | 0 |
| 0167A0 | SOL AI반도체TOP2플러스 | ETF | no_tick | 0 | 0 | 0 |
| 069500 | KODEX 200 | ETF | no_tick | 0 | 0 | 0 |
| 396500 | TIGER 반도체TOP10 | ETF | no_tick | 0 | 0 | 0 |
| 379800 | KODEX 미국S&P500 | ETF | no_tick | 0 | 0 | 0 |

### C_no_tick_common_solo

- Verdict: `symbol_or_account_level_issue_likely`
- Evidence: Isolated common-stock symbols still received zero ticks.
- Next action: Escalate selected common symbols to KIS with runner output attached.

| Code | Name | Type | Class | Received Δ | Dispatched Δ | Reject Δ |
|------|------|------|-------|-----------:|-------------:|---------:|
| 080220 | 제주반도체 | common_or_other | no_tick | 0 | 0 | 0 |
| 353200 | 대덕전자 | common_or_other | no_tick | 0 | 0 | 0 |
| 004710 | 한솔테크닉스 | common_or_other | no_tick | 0 | 0 | 0 |

### D_refresh_observation

- Verdict: `refresh_ineffective`
- Evidence: No refreshed symbols recovered tick flow.
- Next action: Reduce refresh churn and quarantine no-tick symbols intraday.

| Code | Name | Type | Class | Received Δ | Dispatched Δ | Reject Δ |
|------|------|------|-------|-----------:|-------------:|---------:|
| 080220 | 제주반도체 | common_or_other | no_tick | 0 | 0 | 0 |
| 353200 | 대덕전자 | common_or_other | no_tick | 0 | 0 | 0 |
| 004710 | 한솔테크닉스 | common_or_other | no_tick | 0 | 0 | 0 |
| 198440 | 강동씨앤엘 | common_or_other | no_tick | 0 | 0 | 0 |
| 320000 | 한울반도체 | common_or_other | no_tick | 0 | 0 | 0 |
