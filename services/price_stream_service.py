# services/price_stream_service.py
"""
실시간 체결가 스트림 소비 서비스.

역할:
  - WebSocket 체결가(H0UNCNT0 / H0STCNT0) 틱 수신 → 최신가 캐시 갱신
  - StockRepository.update_realtime_data() 호출 (TTL 없이 최신가 즉시 반영)

StreamingService와의 역할 구분:
  - StreamingService   : WebSocket 연결·구독·메시지 dispatch (프로토콜 레이어)
  - PriceStreamService : 체결가 데이터 소비·캐시 관리 (데이터 레이어)

사용법:
  streaming_service.set_price_stream_service(price_stream_service)
  → StreamingService가 'realtime_price' 메시지를 받을 때 on_price_tick()을 호출.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List, Optional

from repositories.stock_repository import StockRepository


class PriceStreamService:
    """실시간 체결가 스트림 소비 서비스."""

    def __init__(
        self,
        stock_repo: "StockRepository",
        logger=None,
    ):
        self._stock_repo = stock_repo
        self._logger = logger or logging.getLogger(__name__)
        self._latest_prices: Dict[str, dict] = {}
        self._sse_queues: Dict[str, List[asyncio.Queue]] = {}  # code → SSE 구독 큐 목록

    def on_price_tick(self, realtime_data: dict) -> None:
        """
        StreamingService로부터 'realtime_price' 이벤트를 수신한다.

        1. 내부 최신가 캐시(_latest_prices) 갱신
        2. StockRepository.update_realtime_data() 즉시 반영
        """
        stock_code = realtime_data.get('유가증권단축종목코드')
        current_price = realtime_data.get('주식현재가')

        if not stock_code or not current_price:
            return

        self._latest_prices[stock_code] = {
            "price": current_price,
            "change": realtime_data.get('전일대비', '0'),
            "rate": realtime_data.get('전일대비율', '0.00'),
            "sign": realtime_data.get('전일대비부호', '3'),
            "received_at": time.time(),
        }

        try:
            cum_vol = realtime_data.get('누적거래량', '0')
            vol_int = int(cum_vol) if cum_vol and cum_vol != 'N/A' else 0
            self._stock_repo.update_realtime_data(stock_code, float(current_price), vol_int)
        except Exception as e:
            self._logger.warning(f"StockRepository 실시간 틱 캐시 갱신 실패: {e}")

        if stock_code in self._sse_queues:
            tick = {"code": stock_code, "price": float(current_price), "volume": vol_int}
            for q in self._sse_queues[stock_code]:
                try:
                    q.put_nowait(tick)
                except asyncio.QueueFull:
                    pass

    def get_cached_price(self, code: str) -> Optional[dict]:
        """메모리 캐시에서 최신가 정보를 반환한다."""
        return self._latest_prices.get(code)

    def create_subscriber_queue(self, code: str) -> asyncio.Queue:
        """SSE 클라이언트용 큐를 생성하고 등록한다."""
        queue: asyncio.Queue = asyncio.Queue()
        self._sse_queues.setdefault(code, []).append(queue)
        return queue

    def remove_subscriber_queue(self, code: str, queue: asyncio.Queue) -> None:
        """SSE 클라이언트 큐를 제거한다. 구독자가 없으면 항목 삭제."""
        queues = self._sse_queues.get(code, [])
        if queue in queues:
            queues.remove(queue)
        if not queues:
            self._sse_queues.pop(code, None)
