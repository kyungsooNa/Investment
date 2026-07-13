# repositories/stock_repository.py
"""
StockRepository — 현재가(StockPriceRepository)와 OHLCV(StockOhlcvRepository)를 통합하는 Facade.

기존 callers(MarketDataService, StreamingService 등)는 이 클래스만 참조하면 되며
내부적으로는 두 Repository에 위임한다.
"""
import logging
from typing import Optional, List, Dict, Any

from repositories.cache import _LRUCache, _LFUCache  # 하위호환 re-export 용
from repositories.stock_price_repository import StockPriceRepository
from repositories.stock_ohlcv_repository import StockOhlcvRepository
from core.logger import get_cache_event_logger


class StockRepository:
    """개별 종목 데이터(현재가 + OHLCV) 통합 Facade."""

    def __init__(self, db_path: str = None, logger=None):
        self._logger = logger or logging.getLogger(__name__)
        self._cache_logger = get_cache_event_logger()
        self._price_repo = StockPriceRepository(logger=self._logger, cache_logger=self._cache_logger)
        self._ohlcv_repo = StockOhlcvRepository(db_path=db_path, logger=self._logger, cache_logger=self._cache_logger)

    # ── 하위호환 프로퍼티 (테스트 및 레거시 접근용) ─────────────────────────────

    @property
    def _db_path(self) -> str:
        return self._ohlcv_repo._db_path

    @property
    def _conn(self):
        return self._ohlcv_repo._write_conn

    @_conn.setter
    def _conn(self, value):
        self._ohlcv_repo._write_conn = value

    def _get_connection(self):
        """쓰기 전용 DB 연결 컨텍스트 매니저 (테스트에서 직접 접근 시 사용)."""
        return self._ohlcv_repo._get_write_connection()

    # ── 현재가 캐시 ──────────────────────────────────────────────────────────────

    def set_current_price(self, code: str, price_data: dict):
        """현재가 API 응답 전체 데이터를 캐시에 저장합니다."""
        self._price_repo.set_current_price(code, price_data)

    def get_current_price(self, code: str, max_age_sec: float = 3.0,
                          count_stats: bool = True, caller: str = "unknown") -> Optional[dict]:
        """캐시된 현재가 데이터를 반환합니다. TTL 만료 시 None 반환."""
        return self._price_repo.get_current_price(
            code, max_age_sec=max_age_sec, count_stats=count_stats, caller=caller
        )

    # ── OHLCV 캐시/DB ──────────────────────────────────────────────────────────

    async def get_stock_data(self, code: str, ohlcv_limit: int = 600,
                             caller: str = "unknown") -> Optional[Dict]:
        """메모리 캐시 또는 DB에서 OHLCV 데이터를 반환합니다."""
        return await self._ohlcv_repo.get_stock_data(code, ohlcv_limit=ohlcv_limit, caller=caller)

    async def upsert_ohlcv(self, records: List[Dict]):
        """여러 종목의 일봉(OHLCV) 데이터를 일괄 upsert 후 해당 종목 캐시 무효화."""
        await self._ohlcv_repo.upsert_ohlcv(records)

    async def get_ohlcv_summary(self, code: str) -> Dict[str, Any]:
        """DB에서 종목의 OHLCV 요약 정보를 반환합니다."""
        return await self._ohlcv_repo.get_ohlcv_summary(code)

    async def get_ohlcv_max_trading_days(self) -> int:
        """DB에 저장된 고유 거래일 수를 반환합니다."""
        return await self._ohlcv_repo.get_ohlcv_max_trading_days()

    # ── 실시간 틱 통합 업데이트 ───────────────────────────────────────────────────

    def update_realtime_data(self, code: str, current_price: float, volume: int = 0):
        """
        장 중에 수신된 WebSocket 틱 데이터를 메모리 캐시에 즉시 반영합니다.
        - 현재가 캐시(price_repo) 갱신
        - OHLCV 당일 캔들(ohlcv_repo) 갱신
        """
        self._price_repo.update_current_price(code, current_price, volume)
        self._ohlcv_repo.update_today_candle(code, current_price, volume)

    # ── daily_prices (장마감 후 전종목 스냅샷) ──────────────────────────────────

    async def upsert_daily_snapshot(self, trade_date: str, records: List[Dict]):
        """장마감 후 전체 종목 현재가+펀더멘털 스냅샷을 일괄 upsert."""
        await self._ohlcv_repo.upsert_daily_snapshot(trade_date, records)

    async def update_minervini_fields(self, trade_date: str, records: List[Dict]):
        """minervini_stage / minervini_reason / rs_rating 컬럼만 UPDATE."""
        await self._ohlcv_repo.update_minervini_fields(trade_date, records)

    async def get_prices_by_date(self, trade_date: str) -> List[Dict]:
        """특정 날짜의 전체 종목 스냅샷 조회."""
        return await self._ohlcv_repo.get_prices_by_date(trade_date)

    async def get_all_daily_snapshots(self, trade_date: str) -> List[Dict]:
        """특정 거래일의 전체 종목 스냅샷을 시가총액 내림차순으로 조회."""
        return await self._ohlcv_repo.get_all_daily_snapshots(trade_date)

    async def get_ytd_return_ranking(self, limit: int = 100) -> List[Dict]:
        """최신 거래일 기준 YTD 수익률 랭킹 조회."""
        return await self._ohlcv_repo.get_ytd_return_ranking(limit=limit)

    async def update_newhigh_fields(self, trade_date: str, records: List[Dict]):
        """is_newhigh 및 is_historical_newhigh 컬럼 업데이트."""
        await self._ohlcv_repo.update_newhigh_fields(trade_date, records)

    async def get_newhigh_stocks(self, trade_date: str) -> List[Dict]:
        """특정 거래일의 신고가(is_newhigh=1) 종목 조회."""
        return await self._ohlcv_repo.get_newhigh_stocks(trade_date)

    async def get_minervini_stage2_stocks(self, trade_date: str) -> List[Dict]:
        """특정 거래일의 Minervini Stage2 종목을 rs_rating 내림차순으로 조회."""
        return await self._ohlcv_repo.get_minervini_stage2_stocks(trade_date)

    async def get_price_history(self, code: str, days: int = 30) -> List[Dict]:
        """특정 종목의 최근 N일간 스냅샷 이력 조회."""
        return await self._ohlcv_repo.get_price_history(code, days)

    async def get_latest_trade_date(self) -> Optional[str]:
        """daily_prices에 저장된 가장 최근 거래일 반환."""
        return await self._ohlcv_repo.get_latest_trade_date()

    async def get_count_by_date(self, trade_date: str) -> int:
        """특정 날짜에 저장된 종목 수 반환."""
        return await self._ohlcv_repo.get_count_by_date(trade_date)

    async def get_minervini_stage_count(self, trade_date: str) -> int:
        """특정 날짜에 minervini_stage가 계산된 종목 수 반환."""
        return await self._ohlcv_repo.get_minervini_stage_count(trade_date)

    async def cleanup_old_data(self, keep_days: int = 365):
        """오래된 daily_prices 데이터 정리."""
        await self._ohlcv_repo.cleanup_old_data(keep_days)

    async def get_latest_daily_snapshot(self, code: str) -> Optional[dict]:
        """daily_prices에서 최신 스냅샷을 현재가 API 응답 포맷으로 변환하여 반환합니다."""
        return await self._ohlcv_repo.get_latest_daily_snapshot(code)

    # ── 스트리밍 상태 ─────────────────────────────────────────────────────────────

    def mark_streaming(self, code: str) -> None:
        """해당 종목이 실시간 스트리밍 중임을 등록. TTL 우회 활성화."""
        self._price_repo.mark_streaming(code)

    def unmark_streaming(self, code: str) -> None:
        """실시간 스트리밍 종료. TTL 우회 해제."""
        self._price_repo.unmark_streaming(code)

    def is_streaming(self, code: str) -> bool:
        """해당 종목이 현재 스트리밍 중인지 여부."""
        return self._price_repo.is_streaming(code)

    # ── 통합 캐시 통계 ────────────────────────────────────────────────────────────

    def get_cache_stats(self, expand: bool = False, latest_trading_date: str = None, log_stats: bool = False) -> dict:
        """현재가 캐시 + OHLCV 캐시의 통합 통계를 반환합니다."""
        price_stats = self._price_repo.get_cache_stats(expand=expand)
        ohlcv_stats = self._ohlcv_repo.get_cache_stats(expand=expand, latest_trading_date=latest_trading_date)
        if log_stats:
            self._cache_logger.log_stats(price_stats, ohlcv_stats)

        total_hits = price_stats["hits"] + ohlcv_stats["hits"]
        total_misses = price_stats["misses"] + ohlcv_stats["misses"]
        total = total_hits + total_misses
        hit_rate = (total_hits / total * 100) if total > 0 else 0.0

        result = {
            "hits": total_hits,
            "misses": total_misses,
            "hit_rate": round(hit_rate, 2),
            "total_requests": total,
            "current_size": price_stats["current_size"] + ohlcv_stats["current_size"],
            "callers": price_stats.get("callers", {}),
            "price_cache": price_stats,
            "ohlcv_cache": ohlcv_stats,
        }
        if expand:
            result["items"] = price_stats.get("items", []) + ohlcv_stats.get("items", [])
        return result

    async def close(self):
        """DB 연결을 닫습니다."""
        await self._ohlcv_repo.close()

    def __del__(self):
        if self._ohlcv_repo._write_conn:
            self._logger.warning("StockRepository was not closed explicitly.")
