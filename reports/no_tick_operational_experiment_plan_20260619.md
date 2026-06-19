# No-Tick Operational Experiment Plan

## Summary

- Verdict: `a1_kis_no_send`
- No-tick codes: 16
- Received common-stock codes: 11

### By Instrument Type

| Type | Total | No-tick | Received |
|------|------:|--------:|---------:|
| ETF | 8 | 8 | 0 |
| common_or_other | 18 | 7 | 11 |
| preferred | 1 | 1 | 0 |

## Experiments

### A_common_stock_only

- Goal: Remove ETF/preferred contamination and test whether common-stock no-tick persists.
- Codes: `028260, 032830, 240810, 034730, 298040, 080220, 353200, 004710, 198440, 320000`
- Expected signal: If no-tick common names recover, mixed product class or slot churn is implicated.

| Code | Name | Market | Type | Class | Received | No-tick logs |
|------|------|--------|------|-------|---------:|-------------:|
| 028260 | 삼성물산 | KOSPI | common_or_other | received | 13935 | 0 |
| 032830 | 삼성생명 | KOSPI | common_or_other | received | 9856 | 0 |
| 240810 | 원익IPS | KOSDAQ | common_or_other | received | 9392 | 0 |
| 034730 | SK | KOSPI | common_or_other | received | 8902 | 0 |
| 298040 | 효성중공업 | KOSPI | common_or_other | received | 7833 | 0 |
| 080220 | 제주반도체 | KOSDAQ | common_or_other | a1_kis_no_send | 0 | 210 |
| 353200 | 대덕전자 | KOSPI | common_or_other | a1_kis_no_send | 0 | 209 |
| 004710 | 한솔테크닉스 | KOSPI | common_or_other | a1_kis_no_send | 0 | 202 |
| 198440 | 강동씨앤엘 | KOSDAQ | common_or_other | a1_kis_no_send | 0 | 146 |
| 320000 | 한울반도체 | KOSDAQ | common_or_other | a1_kis_no_send | 0 | 146 |

### B_non_common_only

- Goal: Check whether ETF/preferred symbols structurally receive no price frames.
- Codes: `0162Z0, 0167A0, 069500, 396500, 379800`
- Expected signal: If these still receive zero frames solo, KIS product-class behavior is likely.

| Code | Name | Market | Type | Class | Received | No-tick logs |
|------|------|--------|------|-------|---------:|-------------:|
| 0162Z0 | RISE 삼성전자SK하이닉스채권혼합50 | ETF | ETF | a1_kis_no_send | 0 | 138 |
| 0167A0 | SOL AI반도체TOP2플러스 | ETF | ETF | a1_kis_no_send | 0 | 138 |
| 069500 | KODEX 200 | ETF | ETF | a1_kis_no_send | 0 | 138 |
| 396500 | TIGER 반도체TOP10 | ETF | ETF | a1_kis_no_send | 0 | 134 |
| 379800 | KODEX 미국S&P500 | ETF | ETF | a1_kis_no_send | 0 | 107 |

### C_no_tick_common_solo

- Goal: Isolate symbol-level vs slot/order effects for common-stock no-tick names.
- Codes: `080220, 353200, 004710`
- Expected signal: If solo common names receive ticks, the 30-40 name subscription context is implicated.

| Code | Name | Market | Type | Class | Received | No-tick logs |
|------|------|--------|------|-------|---------:|-------------:|
| 080220 | 제주반도체 | KOSDAQ | common_or_other | a1_kis_no_send | 0 | 210 |
| 353200 | 대덕전자 | KOSPI | common_or_other | a1_kis_no_send | 0 | 209 |
| 004710 | 한솔테크닉스 | KOSPI | common_or_other | a1_kis_no_send | 0 | 202 |

### D_refresh_observation

- Goal: Observe whether repeated subscribed_no_tick refresh ever restores frames.
- Codes: `080220, 353200, 004710, 198440, 320000`
- Expected signal: If refresh still does not restore frames, reduce churn or quarantine no-tick symbols intraday.

| Code | Name | Market | Type | Class | Received | No-tick logs |
|------|------|--------|------|-------|---------:|-------------:|
| 080220 | 제주반도체 | KOSDAQ | common_or_other | a1_kis_no_send | 0 | 210 |
| 353200 | 대덕전자 | KOSPI | common_or_other | a1_kis_no_send | 0 | 209 |
| 004710 | 한솔테크닉스 | KOSPI | common_or_other | a1_kis_no_send | 0 | 202 |
| 198440 | 강동씨앤엘 | KOSDAQ | common_or_other | a1_kis_no_send | 0 | 146 |
| 320000 | 한울반도체 | KOSDAQ | common_or_other | a1_kis_no_send | 0 | 146 |

## KIS 문의 요약

1. Confirm whether domestic equity price WebSocket frames are supported identically for ETF/preferred symbols.
2. Subscriptions reached ACK/active state, but selected symbols remained received=0 for the full session.
3. Some common stocks received thousands of frames in the same session, so the feed was not globally dead.
4. Repeated unsubscribe/subscribe refresh did not restore frames for a1_kis_no_send symbols.
5. not_subscribed and DataQuality reject were not the dominant failure modes.

### Attachments

- `reports/no_tick_diagnosis_20260619.md`
- `reports/no_tick_diagnosis_20260619.json`
- `reports/no_tick_operational_diagnosis_20260619.md`
