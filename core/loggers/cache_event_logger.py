import logging

class CacheEventLogger:
    """
    현재가·OHLCV 캐시 동작을 JSON으로 기록하는 전용 로거.

    logs/cache/{timestamp}_cache.log.json 에 기록한다.

    --- 로그 종류 ---

    [현재가 캐시 — StockPriceRepository (LRU)]
      price_set         : API 응답으로 현재가 캐시 등록/갱신 (before/after price, is_new)
      price_update_tick : WebSocket 틱으로 현재가 갱신 (before/after price, volume)
      price_hit         : 캐시 히트 (caller, age_sec, is_streaming)
      price_miss        : 캐시 미스 (caller, reason: "not_found" | "ttl_expired")
      price_evicted     : LRU capacity 초과로 캐시 제거

    [스트리밍 상태 — StockPriceRepository]
      streaming_mark    : 실시간 스트리밍 등록 (streaming_count)
      streaming_unmark  : 실시간 스트리밍 해제 (streaming_count)

    [OHLCV 캐시 — StockOhlcvRepository (LFU)]
      ohlcv_loaded      : DB에서 OHLCV 로드 후 캐시 등록 (caller, ohlcv_count, latest_date)
      ohlcv_hit         : 캐시 히트 (caller, ohlcv_count, has_today_candle)
      ohlcv_miss        : 캐시 미스 (caller)
      ohlcv_evicted     : LFU capacity 초과로 캐시 제거 (freq, ohlcv_count)
      ohlcv_invalidated : upsert 후 캐시 무효화
      ohlcv_upsert      : OHLCV upsert 배치 완료 (record_count, code_count, invalidated_codes)
      today_candle      : 당일 캔들 갱신 (before/after price, high, low, is_new_candle)

    [통합 통계]
      cache_stats       : 현재가+OHLCV 캐시 hit/miss 통계 스냅샷
    """

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    # ── 현재가 캐시 이벤트 ───────────────────────────────────────────

    def log_price_set(
        self,
        code: str,
        caller: str,
        before_price: str,
        after_price: str,
        is_new: bool,
    ) -> None:
        """API 응답으로 현재가 캐시 등록 또는 갱신.

        Args:
            code: 종목코드
            caller: 호출 출처 (e.g., "market_data_service", "streaming")
            before_price: 갱신 전 stck_prpr (캐시 미존재 시 None)
            after_price: 갱신 후 stck_prpr
            is_new: True이면 캐시에 처음 등록 (신규 종목)
        """
        if not self._logger.isEnabledFor(logging.INFO):
            return
        self._logger.info({
            "action": "price_set",
            "code": code,
            "caller": caller,
            "before_price": before_price,
            "after_price": after_price,
            "is_new": is_new,
        })

    def log_price_update_tick(
        self,
        code: str,
        before_price: str,
        after_price: str,
        volume: int,
    ) -> None:
        """WebSocket 틱 데이터로 현재가 갱신. 가격 변동 시에만 기록.

        Args:
            code: 종목코드
            before_price: 갱신 전 stck_prpr
            after_price: 갱신 후 stck_prpr
            volume: 누적 거래량
        """
        if not self._logger.isEnabledFor(logging.DEBUG):
            return
        self._logger.debug({
            "action": "price_update_tick",
            "code": code,
            "before_price": before_price,
            "after_price": after_price,
            "volume": volume,
        })

    def log_price_hit(
        self,
        code: str,
        caller: str,
        age_sec: float,
        is_streaming: bool,
    ) -> None:
        """현재가 캐시 히트.

        Args:
            code: 종목코드
            caller: 호출 출처
            age_sec: 캐시 데이터 경과 시간 (초)
            is_streaming: 실시간 스트리밍 중 여부 (TTL 무제한)
        """
        if not self._logger.isEnabledFor(logging.DEBUG):
            return
        self._logger.debug({
            "action": "price_hit",
            "code": code,
            "caller": caller,
            "age_sec": round(age_sec, 2),
            "is_streaming": is_streaming,
        })

    def log_price_miss(self, code: str, caller: str, reason: str) -> None:
        """현재가 캐시 미스.

        Args:
            code: 종목코드
            caller: 호출 출처
            reason: "not_found" | "ttl_expired"
        """
        if not self._logger.isEnabledFor(logging.DEBUG):
            return
        self._logger.debug({
            "action": "price_miss",
            "code": code,
            "caller": caller,
            "reason": reason,
        })

    def log_price_evicted(self, code: str, capacity: int) -> None:
        """LRU 용량 초과로 현재가 캐시에서 제거.

        Args:
            code: 제거된 종목코드
            capacity: 캐시 최대 용량
        """
        if not self._logger.isEnabledFor(logging.WARNING):
            return
        self._logger.warning({
            "action": "price_evicted",
            "code": code,
            "capacity": capacity,
        })

    # ── 스트리밍 상태 이벤트 ─────────────────────────────────────────

    def log_streaming_mark(self, code: str, streaming_count: int) -> None:
        """실시간 스트리밍 등록 (TTL 무제한 전환).

        Args:
            code: 종목코드
            streaming_count: 등록 후 총 스트리밍 종목 수
        """
        if not self._logger.isEnabledFor(logging.INFO):
            return
        self._logger.info({
            "action": "streaming_mark",
            "code": code,
            "streaming_count": streaming_count,
        })

    def log_streaming_unmark(self, code: str, streaming_count: int) -> None:
        """실시간 스트리밍 해제 (TTL 정상 적용 복귀).

        Args:
            code: 종목코드
            streaming_count: 해제 후 총 스트리밍 종목 수
        """
        if not self._logger.isEnabledFor(logging.INFO):
            return
        self._logger.info({
            "action": "streaming_unmark",
            "code": code,
            "streaming_count": streaming_count,
        })

    # ── OHLCV 캐시 이벤트 ───────────────────────────────────────────

    def log_ohlcv_loaded(
        self,
        code: str,
        caller: str,
        ohlcv_count: int,
        latest_date: str,
    ) -> None:
        """DB에서 OHLCV 데이터를 읽어 캐시에 등록.

        Args:
            code: 종목코드
            caller: 호출 출처
            ohlcv_count: 적재된 OHLCV 일수
            latest_date: 가장 최근 OHLCV 날짜 (데이터 신선도 확인)
        """
        if not self._logger.isEnabledFor(logging.INFO):
            return
        self._logger.info({
            "action": "ohlcv_loaded",
            "code": code,
            "caller": caller,
            "ohlcv_count": ohlcv_count,
            "latest_date": latest_date,
        })

    def log_ohlcv_hit(
        self,
        code: str,
        caller: str,
        ohlcv_count: int,
        has_today_candle: bool,
    ) -> None:
        """OHLCV 캐시 히트.

        Args:
            code: 종목코드
            caller: 호출 출처
            ohlcv_count: 캐시에 있는 총 OHLCV 일수 (historical + today 포함)
            has_today_candle: 당일 캔들 존재 여부
        """
        if not self._logger.isEnabledFor(logging.DEBUG):
            return
        self._logger.debug({
            "action": "ohlcv_hit",
            "code": code,
            "caller": caller,
            "ohlcv_count": ohlcv_count,
            "has_today_candle": has_today_candle,
        })

    def log_ohlcv_miss(self, code: str, caller: str) -> None:
        """OHLCV 캐시 미스 (DB 조회 필요).

        Args:
            code: 종목코드
            caller: 호출 출처
        """
        if not self._logger.isEnabledFor(logging.DEBUG):
            return
        self._logger.debug({
            "action": "ohlcv_miss",
            "code": code,
            "caller": caller,
        })

    def log_ohlcv_evicted(self, code: str, freq: int, ohlcv_count: int, capacity: int) -> None:
        """LFU 용량 초과로 OHLCV 캐시에서 제거.

        Args:
            code: 제거된 종목코드
            freq: 제거 시점까지의 누적 접근 횟수 (낮을수록 자주 안 쓰인 종목)
            ohlcv_count: 제거된 종목의 OHLCV 일수
            capacity: 캐시 최대 용량
        """
        if not self._logger.isEnabledFor(logging.WARNING):
            return
        self._logger.warning({
            "action": "ohlcv_evicted",
            "code": code,
            "freq": freq,
            "ohlcv_count": ohlcv_count,
            "capacity": capacity,
        })

    def log_ohlcv_invalidated(self, code: str) -> None:
        """upsert 이후 해당 종목 OHLCV 캐시 무효화 (다음 조회 시 DB에서 재로드).

        Args:
            code: 무효화된 종목코드
        """
        if not self._logger.isEnabledFor(logging.INFO):
            return
        self._logger.info({
            "action": "ohlcv_invalidated",
            "code": code,
        })

    def log_ohlcv_upsert(
        self,
        record_count: int,
        code_count: int,
        invalidated_codes: list,
    ) -> None:
        """OHLCV upsert 배치 완료 및 캐시 무효화 요약.

        Args:
            record_count: upsert된 총 레코드 수
            code_count: 영향 받은 고유 종목 수
            invalidated_codes: 캐시 무효화된 종목코드 목록
        """
        if not self._logger.isEnabledFor(logging.INFO):
            return
        self._logger.info({
            "action": "ohlcv_upsert",
            "record_count": record_count,
            "code_count": code_count,
            "invalidated_codes": sorted(invalidated_codes),
        })

    def log_today_candle(
        self,
        code: str,
        before_price,
        after_price: float,
        high: float,
        low: float,
        is_new_candle: bool,
    ) -> None:
        """WebSocket 틱으로 당일 캔들 갱신.

        Args:
            code: 종목코드
            before_price: 갱신 전 close 가격 (캔들 없으면 None)
            after_price: 갱신 후 close 가격
            high: 갱신 후 고가
            low: 갱신 후 저가
            is_new_candle: True이면 ohlcv_today 신규 생성 (기존 historical[-1] 갱신이 아님)
        """
        if not self._logger.isEnabledFor(logging.DEBUG):
            return
        self._logger.debug({
            "action": "today_candle",
            "code": code,
            "before_price": before_price,
            "after_price": after_price,
            "high": high,
            "low": low,
            "is_new_candle": is_new_candle,
        })

    # ── 통합 통계 ─────────────────────────────────────────────────────

    def log_stats(self, price_stats: dict, ohlcv_stats: dict) -> None:
        """현재가 + OHLCV 캐시 통합 hit/miss 통계 스냅샷.

        Args:
            price_stats: StockPriceRepository.get_cache_stats() 결과
            ohlcv_stats: StockOhlcvRepository.get_cache_stats() 결과
        """
        if not self._logger.isEnabledFor(logging.INFO):
            return
        total_hits = price_stats.get("hits", 0) + ohlcv_stats.get("hits", 0)
        total_misses = price_stats.get("misses", 0) + ohlcv_stats.get("misses", 0)
        total = total_hits + total_misses
        self._logger.info({
            "action": "cache_stats",
            "price": {
                "hits": price_stats.get("hits", 0),
                "misses": price_stats.get("misses", 0),
                "hit_rate": price_stats.get("hit_rate", 0.0),
                "current_size": price_stats.get("current_size", 0),
            },
            "ohlcv": {
                "hits": ohlcv_stats.get("hits", 0),
                "misses": ohlcv_stats.get("misses", 0),
                "hit_rate": ohlcv_stats.get("hit_rate", 0.0),
                "current_size": ohlcv_stats.get("current_size", 0),
            },
            "combined": {
                "hits": total_hits,
                "misses": total_misses,
                "hit_rate": round(total_hits / total * 100, 2) if total > 0 else 0.0,
            },
        })
