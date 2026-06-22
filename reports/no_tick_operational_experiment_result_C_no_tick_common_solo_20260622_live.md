# No-Tick Experiment Result: C_no_tick_common_solo

## Summary

- Status: `completed`
- Duration: `180s`
- Total codes: 3
- Received codes: 0
- No-tick codes: 3
- Subscribe failures: none
- ACK failures: none

## Cohort

- Goal: Isolate symbol-level vs slot/order effects for common-stock no-tick names.
- Expected signal: If solo common names receive ticks, the 30-40 name subscription context is implicated.

| Code | Name | Type | Subscribed | ACK | Received Delta | Dispatch Delta | Reject Delta |
|------|------|------|------------|-----|---------------:|---------------:|-------------:|
| 080220 | 제주반도체 | common_or_other | True | True | 0 | 0 | 0 |
| 353200 | 대덕전자 | common_or_other | True | True | 0 | 0 | 0 |
| 004710 | 한솔테크닉스 | common_or_other | True | True | 0 | 0 | 0 |
