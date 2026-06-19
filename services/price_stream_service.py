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

from common.market_snapshot import ConclusionSnapshot, MarketSnapshot
from repositories.stock_repository import StockRepository
from services.notification_service import NotificationCategory, NotificationLevel


class PriceStreamService:
    """실시간 체결가 스트림 소비 서비스."""

    def __init__(
        self,
        stock_repo: "StockRepository",
        logger=None,
        data_quality_service=None,
        notification_service=None,
        event_router=None,
    ):
        self._stock_repo = stock_repo
        self._logger = logger or logging.getLogger(__name__)
        self._data_quality_service = data_quality_service
        self._notification_service = notification_service
        self._event_router = event_router
        self._latest_prices: Dict[str, dict] = {}
        self._latest_conclusions: Dict[str, dict] = {}  # code → conclusion snapshot dict
        self._sse_queues: Dict[str, List[asyncio.Queue]] = {}  # code → SSE 구독 큐 목록
        self._last_tick_ts: Dict[str, float] = {}
        self._last_any_tick_ts: float = 0.0
        self._subscription_requested_ts: Dict[str, float] = {}
        # P2 2-4 shadow no-tick 진단: 종목별 누적 tick 처리 카운터.
        self._tick_ingest_received: Dict[str, int] = {}
        self._tick_ingest_quality_reject: Dict[str, int] = {}
        self._tick_ingest_dispatched: Dict[str, int] = {}
        self._tick_ingest_malformed: Dict[str, int] = {}

    def set_event_router(self, event_router) -> None:
        """Late injection 용. WebAppContext 조립 순서 문제로 생성자에 주입 못한 경우 사용."""
        self._event_router = event_router

    def tick_ingest_stats_snapshot(self, codes=None) -> Dict[str, Dict[str, int]]:
        """종목별 누적 tick 처리 카운터 스냅샷 (P2 2-4 shadow no-tick 진단).

        - received: 유효 payload 로 on_price_tick 에 진입한 frame 수
        - malformed: 필수 필드(종목코드/현재가) 누락으로 버린 frame 수
        - quality_reject: DataQuality 게이트에서 탈락한 frame 수
        - dispatched: event_router 로 전달된 frame 수
        codes 가 주어지면 해당 종목만(미수신은 0으로) 반환한다.
        """
        if codes is None:
            keys = (
                set(self._tick_ingest_received)
                | set(self._tick_ingest_quality_reject)
                | set(self._tick_ingest_dispatched)
                | set(self._tick_ingest_malformed)
            )
        else:
            keys = set(codes)
        return {
            c: {
                "received": self._tick_ingest_received.get(c, 0),
                "quality_reject": self._tick_ingest_quality_reject.get(c, 0),
                "dispatched": self._tick_ingest_dispatched.get(c, 0),
                "malformed": self._tick_ingest_malformed.get(c, 0),
            }
            for c in sorted(keys)
        }

    def on_price_tick(self, realtime_data: dict) -> None:
        """
        StreamingService로부터 'realtime_price' 이벤트를 수신한다.

        1. 내부 최신가 캐시(_latest_prices) 갱신
        2. StockRepository.update_realtime_data() 즉시 반영
        """
        stock_code = realtime_data.get('유가증권단축종목코드')
        current_price = realtime_data.get('주식현재가')

        if not stock_code or not current_price:
            key = str(stock_code).strip() if stock_code else "__unknown__"
            self._tick_ingest_malformed[key] = self._tick_ingest_malformed.get(key, 0) + 1
            return

        self._tick_ingest_received[stock_code] = self._tick_ingest_received.get(stock_code, 0) + 1

        now_ts = time.time()
        quality_status = "ok"
        quality_reason = "ok"
        latency_sec = 0.0
        if self._data_quality_service is not None:
            result = self._data_quality_service.validate_price_tick(realtime_data, received_at=now_ts)
            quality_status = "ok" if result.ok else result.severity
            quality_reason = result.reason
            latency_sec = result.latency_sec or 0.0
            if not result.ok:
                self._logger.warning(
                    f"실시간 체결가 품질 검증 실패: code={result.code or stock_code}, reason={result.reason}, "
                    f"metadata={result.metadata}"
                )
                if self._notification_service is not None:
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(self._notification_service.emit(
                            NotificationCategory.SYSTEM,
                            NotificationLevel.ERROR,
                            "실시간 체결가 품질 검증 실패",
                            f"{result.code or stock_code}: {result.reason}",
                            metadata=result.to_dict(),
                        ))
                    except RuntimeError:
                        pass
                self._tick_ingest_quality_reject[stock_code] = (
                    self._tick_ingest_quality_reject.get(stock_code, 0) + 1
                )
                return

        self._last_tick_ts[stock_code] = now_ts
        self._last_any_tick_ts = now_ts

        cum_vol = realtime_data.get('누적거래량', '0')
        try:
            vol_int = int(cum_vol) if cum_vol and cum_vol != 'N/A' else 0
        except (ValueError, TypeError):
            vol_int = 0

        cum_tr_pbmn = realtime_data.get('누적거래대금', '0')
        try:
            tr_pbmn_int = int(cum_tr_pbmn) if cum_tr_pbmn and cum_tr_pbmn != 'N/A' else 0
        except (ValueError, TypeError):
            tr_pbmn_int = 0

        def _sf(val: str, default: float = 0.0) -> Optional[float]:
            try:
                return float(val) if val and val != 'N/A' else None
            except (ValueError, TypeError):
                return None

        self._latest_prices[stock_code] = {
            "price": current_price,
            "change": realtime_data.get('전일대비', '0'),
            "rate": realtime_data.get('전일대비율', '0.00'),
            "sign": realtime_data.get('전일대비부호', '3'),
            "acml_vol": vol_int,
            "acml_tr_pbmn": tr_pbmn_int,
            "high": _sf(realtime_data.get('주식최고가', '')),
            "low": _sf(realtime_data.get('주식최저가', '')),
            "open": _sf(realtime_data.get('주식시가', '')),
            "received_at": now_ts,
            "latency_sec": latency_sec,
            "quality_status": quality_status,
            "quality_reason": "websocket",
        }

        try:
            self._stock_repo.update_realtime_data(stock_code, float(current_price), vol_int)
        except Exception as e:
            self._logger.warning(f"StockRepository 실시간 틱 캐시 갱신 실패: {e}")

        if stock_code in self._sse_queues:
            tick = {
                "code": stock_code,
                "price": float(current_price),
                "volume": vol_int,
                "change": realtime_data.get('전일대비', '0'),
                "rate": realtime_data.get('전일대비율', '0.00'),
                "sign": realtime_data.get('전일대비부호', '3'),
                "open": _sf(realtime_data.get('주식시가', '')) or 0.0,
                "high": _sf(realtime_data.get('주식최고가', '')) or 0.0,
                "low": _sf(realtime_data.get('주식최저가', '')) or 0.0,
            }
            for q in self._sse_queues[stock_code]:
                try:
                    q.put_nowait(tick)
                except asyncio.QueueFull:
                    pass

        if self._event_router is not None:
            try:
                loop = asyncio.get_running_loop()
                snapshot = dict(self._latest_prices[stock_code])
                snapshot["code"] = stock_code
                snapshot["snapshot_ts"] = now_ts
                loop.create_task(
                    self._event_router.on_price_tick(
                        stock_code, snapshot, snapshot_ts=now_ts
                    )
                )
                self._tick_ingest_dispatched[stock_code] = (
                    self._tick_ingest_dispatched.get(stock_code, 0) + 1
                )
            except RuntimeError:
                pass
            except Exception as e:
                self._logger.warning(f"StrategyEventRouter dispatch 실패: {e}")

    def get_market_snapshot(self, code: str) -> Optional[MarketSnapshot]:
        """메모리 캐시에서 MarketSnapshot 을 반환한다.

        snapshot 이 없으면 None. 타입 정보가 필요 없는 기존 경로는 get_cached_price() 사용.
        """
        cached = self._latest_prices.get(code)
        if cached is None:
            return None
        source = "websocket" if cached.get("quality_reason") == "websocket" else "rest"
        return MarketSnapshot.from_legacy_dict(code, cached, source=source)

    def cache_conclusion_snapshot(self, code: str, execution_strength_pct: float) -> None:
        """체결강도 REST 응답을 캐시에 저장한다."""
        if not code:
            return
        self._latest_conclusions[code] = {
            "execution_strength_pct": execution_strength_pct,
            "received_at": time.time(),
        }

    def get_conclusion_snapshot(self, code: str) -> Optional[ConclusionSnapshot]:
        """캐시에서 ConclusionSnapshot 을 반환한다. 없으면 None."""
        cached = self._latest_conclusions.get(code)
        if cached is None:
            return None
        return ConclusionSnapshot(
            code=code,
            execution_strength_pct=cached["execution_strength_pct"],
            received_at=cached["received_at"],
            source="rest",
        )

    def get_cached_price(self, code: str) -> Optional[dict]:
        """메모리 캐시에서 최신가 정보를 반환한다."""
        return self._latest_prices.get(code)

    def cache_price_snapshot(
        self,
        code: str,
        price: str,
        change: str = '0',
        rate: str = '0.00',
        sign: str = '3',
        volume: str = '0',
        acml_tr_pbmn: Optional[str] = None,
        high: Optional[str] = None,
        low: Optional[str] = None,
        open_price: Optional[str] = None,
    ) -> None:
        """REST 스냅샷 현재가를 최신가 캐시에 반영한다."""
        if not code or not price:
            return

        try:
            vol_int = int(volume) if volume and volume != 'N/A' else 0
        except (ValueError, TypeError):
            vol_int = 0

        try:
            tr_pbmn_int = (
                int(acml_tr_pbmn) if acml_tr_pbmn and acml_tr_pbmn != 'N/A' else 0
            )
        except (ValueError, TypeError):
            tr_pbmn_int = 0

        def _parse_opt(v: Optional[str]) -> Optional[float]:
            if not v or v == 'N/A':
                return None
            try:
                return float(v)
            except (ValueError, TypeError):
                return None

        now_ts = time.time()
        self._latest_prices[code] = {
            "price": price,
            "change": change,
            "rate": rate,
            "sign": sign,
            "acml_vol": vol_int,
            "acml_tr_pbmn": tr_pbmn_int,
            "high": _parse_opt(high),
            "low": _parse_opt(low),
            "open": _parse_opt(open_price),
            "received_at": now_ts,
            "latency_sec": 0.0,
            "quality_status": "ok",
            "quality_reason": "rest_snapshot",
        }

        try:
            self._stock_repo.update_realtime_data(code, float(price), vol_int)
        except Exception as e:
            self._logger.warning(f"StockRepository 현재가 스냅샷 캐시 갱신 실패: {e}")

    def get_liquidity_snapshot(self, code: str) -> Optional[dict]:
        """체결틱 스냅샷에서 거래량/거래대금/수신시각을 반환한다.

        반환 dict: {'acml_vol': int, 'acml_tr_pbmn': int, 'received_at': float}
        snapshot 이 없거나 acml 필드가 미저장(예: 옛 캐시) 인 경우 None.
        """
        cached = self._latest_prices.get(code)
        if not cached:
            return None
        if 'acml_vol' not in cached or 'acml_tr_pbmn' not in cached:
            return None
        return {
            'acml_vol': cached['acml_vol'],
            'acml_tr_pbmn': cached['acml_tr_pbmn'],
            'received_at': cached.get('received_at', 0.0),
        }

    def mark_subscription_requested(self, code: str) -> None:
        """체결가 구독 요청 시각을 기록한다."""
        if code:
            self._subscription_requested_ts[code] = time.time()

    def clear_subscription_state(self, code: str) -> None:
        """구독 해제 시 감시용 상태를 정리한다."""
        self._subscription_requested_ts.pop(code, None)
        self._last_tick_ts.pop(code, None)
        self._latest_prices.pop(code, None)
        self._latest_conclusions.pop(code, None)

    def get_last_tick_ts(self, code: str) -> float:
        """종목별 마지막 틱 수신 시각(epoch)을 반환한다."""
        return self._last_tick_ts.get(code, 0.0)

    def get_last_any_tick_ts(self) -> float:
        """전체 체결가 스트림의 마지막 틱 수신 시각(epoch)을 반환한다."""
        return self._last_any_tick_ts

    def get_subscription_age(self, code: str) -> float:
        """종목 구독 요청 이후 경과 시간(초)을 반환한다."""
        requested_ts = self._subscription_requested_ts.get(code, 0.0)
        if requested_ts <= 0:
            return 0.0
        return max(0.0, time.time() - requested_ts)

    def get_stale_codes(self, threshold_sec: float, codes: Optional[List[str]] = None) -> List[str]:
        """임계값 이상 틱이 없던 종목 목록을 반환한다."""
        now_ts = time.time()
        target_codes = codes or list(self._last_tick_ts.keys())
        stale_codes: List[str] = []

        for code in target_codes:
            last_tick_ts = self._last_tick_ts.get(code, 0.0)
            if last_tick_ts > 0:
                if (now_ts - last_tick_ts) > threshold_sec:
                    stale_codes.append(code)
                continue

            requested_ts = self._subscription_requested_ts.get(code, 0.0)
            if requested_ts > 0 and (now_ts - requested_ts) > threshold_sec:
                stale_codes.append(code)

        return sorted(stale_codes)

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
