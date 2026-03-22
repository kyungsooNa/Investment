"""
web_api 테스트는 페이지별로 분리되었습니다:
- test_web_api_auth.py       — 인증, 공통 헬퍼
- test_web_api_stock.py      — 현재가, 차트, 지표, 환경 전환
- test_web_api_balance.py    — 계좌 잔고
- test_web_api_order.py      — 주문
- test_web_api_ranking.py    — 랭킹, 시가총액
- test_web_api_virtual.py    — 가상 매매
- test_web_api_program_trading.py — 프로그램매매, WebSocket
- test_web_api_scheduler.py  — 스케줄러
"""
