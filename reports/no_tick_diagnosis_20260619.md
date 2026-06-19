# WebSocket 무틱(no-tick) 진단 리포트 (P2 2-4)

- 무틱(subscribed_no_tick) 종목 수: **16**
- ACK 미확정(not_subscribed only) 종목 수: 0
- 우세 판정: **a1_kis_no_send** — ACK 후 KIS 프레임 미전송 (received==0)

## 분류 집계

| 분류 | 종목 수 | 설명 |
|------|---------|------|
| a1_kis_no_send | 15 | ACK 후 KIS 프레임 미전송 (received==0) |
| malformed_payload | 0 | 필수 필드 누락/파싱 이상 (malformed>0) |
| a2_quality_reject | 0 | DataQuality 게이트 전량 탈락 (received>0, dispatched==0) |
| a3_inconsistent | 0 | 디스패치됐는데 no-tick 표시 (측정/타이밍 갭, 무틱 아님) |
| received_not_dispatched | 0 | 프레임 진입했으나 reject/dispatch 아님 |
| unknown_no_snapshot | 1 | tick-ingest 스냅샷에 종목 없음 |

## 종목별 상세

| 종목 | 분류 | received | malformed | quality_reject | dispatched | no_tick 로그수 |
|------|------|----------|-----------|----------------|------------|----------------|
| 004710 | a1_kis_no_send | 0 | 0 | 0 | 0 | 202 |
| 009155 | a1_kis_no_send | 0 | 0 | 0 | 0 | 66 |
| 0162Z0 | a1_kis_no_send | 0 | 0 | 0 | 0 | 138 |
| 0167A0 | a1_kis_no_send | 0 | 0 | 0 | 0 | 138 |
| 052710 | unknown_no_snapshot | - | - | - | - | 4 |
| 069500 | a1_kis_no_send | 0 | 0 | 0 | 0 | 138 |
| 080220 | a1_kis_no_send | 0 | 0 | 0 | 0 | 210 |
| 122630 | a1_kis_no_send | 0 | 0 | 0 | 0 | 72 |
| 149950 | a1_kis_no_send | 0 | 0 | 0 | 0 | 20 |
| 198440 | a1_kis_no_send | 0 | 0 | 0 | 0 | 146 |
| 320000 | a1_kis_no_send | 0 | 0 | 0 | 0 | 146 |
| 353200 | a1_kis_no_send | 0 | 0 | 0 | 0 | 209 |
| 360750 | a1_kis_no_send | 0 | 0 | 0 | 0 | 76 |
| 379800 | a1_kis_no_send | 0 | 0 | 0 | 0 | 107 |
| 396500 | a1_kis_no_send | 0 | 0 | 0 | 0 | 134 |
| 469150 | a1_kis_no_send | 0 | 0 | 0 | 0 | 13 |
