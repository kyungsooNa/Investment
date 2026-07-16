# repositories/stock_ohlcv_repository.py
"""
OHLCV 일봉 데이터 및 daily_prices 스냅샷을 관리하는 Repository.
- LFU 캐시(용량 500): 자주 분석되는 종목이 캐시에 오래 남음
- historical_complete 플래그: DB가 줄 수 있는 전체를 받은 경우 표시 → 불필요한 DB 재조회 방지
- upsert_ohlcv 호출 시 해당 종목의 캐시 무효화 → 항상 신선한 DB 데이터 보장
"""
import os
import sqlite3
import time
import logging
import asyncio
import aiosqlite
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any, TYPE_CHECKING

from repositories.cache import _LFUCache

if TYPE_CHECKING:
    from core.logger import CacheEventLogger

_OHLCV_CACHE_CAPACITY = 500


class StockOhlcvRepository:
    """OHLCV 일봉 데이터 전담 저장소 (LFU 인메모리 캐시 + SQLite)."""

    def __init__(self, db_path: str = None, logger=None, cache_logger: "CacheEventLogger | None" = None):
        self._logger = logger or logging.getLogger(__name__)
        self._cache_logger = cache_logger
        self._db_path = db_path or os.path.join("data", "stocks.db")
        self._write_lock = None
        self._write_conn: Optional[aiosqlite.Connection] = None
        self._read_conn: Optional[aiosqlite.Connection] = None
        self._read_conn_lock = asyncio.Lock()

        # OHLCV 전용 LFU 캐시 — price 캐시와 물리적으로 분리
        self._ohlcv_cache = _LFUCache(
            capacity=_OHLCV_CACHE_CAPACITY,
            on_evict=self._on_ohlcv_evicted,
        )

        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db_sync()

    def _on_ohlcv_evicted(self, code: str, freq: int, ohlcv_count: int) -> None:
        if self._cache_logger:
            self._cache_logger.log_ohlcv_evicted(code, freq, ohlcv_count, capacity=self._ohlcv_cache.capacity)

    def _init_db_sync(self):
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=10000")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS ohlcv (
                        code TEXT NOT NULL,
                        date TEXT NOT NULL,
                        open INTEGER,
                        high INTEGER,
                        low INTEGER,
                        close INTEGER,
                        volume INTEGER,
                        PRIMARY KEY (code, date)
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_date ON ohlcv(date)")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS daily_prices (
                        code TEXT NOT NULL,
                        trade_date TEXT NOT NULL,
                        name TEXT,
                        current_price INTEGER,
                        open_price INTEGER,
                        high_price INTEGER,
                        low_price INTEGER,
                        prev_close INTEGER,
                        change_price INTEGER,
                        change_sign TEXT,
                        change_rate TEXT,
                        volume INTEGER,
                        trading_value INTEGER,
                        market_cap INTEGER,
                        per REAL,
                        pbr REAL,
                        eps REAL,
                        w52_high INTEGER,
                        w52_low INTEGER,
                        market TEXT,
                        iscd_stat_cls_code TEXT,
                        mang_issu_cls_code TEXT,
                        mrkt_warn_cls_code TEXT,
                        invt_caful_yn TEXT,
                        minervini_stage INTEGER,
                        minervini_reason TEXT,
                        rs_rating REAL,
                        collected_at REAL,
                        PRIMARY KEY (code, trade_date)
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_daily_prices_trade_date "
                    "ON daily_prices(trade_date)"
                )
        except Exception as e:
            self._logger.error(f"StockOhlcvRepository DB 초기화 실패: {e}")
        # 기존 DB를 사용하는 경우 컬럼이 없을 수 있으므로 ALTER TABLE 시도
        for alter_sql in [
            "ALTER TABLE daily_prices ADD COLUMN minervini_stage INTEGER",
            "ALTER TABLE daily_prices ADD COLUMN minervini_reason TEXT",
            "ALTER TABLE daily_prices ADD COLUMN rs_rating REAL",
            "ALTER TABLE daily_prices ADD COLUMN is_newhigh INTEGER",
            "ALTER TABLE daily_prices ADD COLUMN is_historical_newhigh INTEGER",
            "ALTER TABLE daily_prices ADD COLUMN iscd_stat_cls_code TEXT",
            "ALTER TABLE daily_prices ADD COLUMN mang_issu_cls_code TEXT",
            "ALTER TABLE daily_prices ADD COLUMN mrkt_warn_cls_code TEXT",
            "ALTER TABLE daily_prices ADD COLUMN invt_caful_yn TEXT",
        ]:
            try:
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute(alter_sql)
            except Exception:
                pass

    @asynccontextmanager
    async def _get_write_connection(self):
        """쓰기 전용 연결 — asyncio.Lock으로 직렬화, 커밋/롤백 보장."""
        if self._write_lock is None:
            self._write_lock = asyncio.Lock()
        async with self._write_lock:
            if self._write_conn is None:
                self._write_conn = await aiosqlite.connect(self._db_path)
                await self._write_conn.execute("PRAGMA synchronous=NORMAL")
                await self._write_conn.execute("PRAGMA busy_timeout=10000")
            try:
                yield self._write_conn
                await self._write_conn.commit()
            except Exception:
                await self._write_conn.rollback()
                raise

    @asynccontextmanager
    async def _get_read_connection(self):
        """읽기 전용 연결 — 한 번만 열고 재사용 (WAL 모드에서 안전). Double-checked locking으로 연결 누수 방지."""
        if self._read_conn is None:
            async with self._read_conn_lock:
                if self._read_conn is None:
                    conn = await aiosqlite.connect(self._db_path)
                    conn.row_factory = aiosqlite.Row
                    await conn.execute("PRAGMA busy_timeout=10000")
                    self._read_conn = conn
        yield self._read_conn

    # ── OHLCV 캐시/DB ──────────────────────────────────────────────────────────

    async def get_stock_data(self, code: str, ohlcv_limit: int = 600,
                             caller: str = "unknown") -> Optional[Dict]:
        """
        메모리 캐시 또는 로컬 DB에서 OHLCV 데이터를 반환합니다.

        historical_complete=True이면 ohlcv_limit 미달이어도 캐시 히트 처리하여
        DB가 줄 수 있는 전부를 이미 보유 중인 종목(예: 신규 상장 종목)의
        반복 DB 재조회 loop를 방지합니다.
        """
        # 1. LFU 캐시 확인 — historical_complete 플래그 기반
        # 캐시에 저장된 건수가 요청 ohlcv_limit 이상일 때만 히트 처리.
        # 예) limit=90으로 90건 캐시 후 limit=600 요청이 오면 DB에서 600건 재로드.
        cached = self._ohlcv_cache.get(code, count_stats=True, caller=caller, item_type="ohlcv")
        if cached and cached.get("historical_complete"):
            cached_count = len(cached.get("ohlcv_historical", []))
            if cached_count >= ohlcv_limit:
                ohlcv_today = cached.get("ohlcv_today")
                ohlcv = cached["ohlcv_historical"][:]
                if ohlcv_today:
                    ohlcv = ohlcv + [ohlcv_today]
                if self._cache_logger:
                    self._cache_logger.log_ohlcv_hit(
                        code, caller, len(ohlcv), has_today_candle=ohlcv_today is not None
                    )
                return {
                    "code": code,
                    "ohlcv": ohlcv,
                    "last_updated": cached["last_loaded"],
                    "historical_complete": True,
                }

        if self._cache_logger:
            self._cache_logger.log_ohlcv_miss(code, caller)

        # 2. DB에서 읽기
        try:
            async with self._get_read_connection() as conn:
                async with conn.execute(
                    "SELECT * FROM ("
                    "SELECT date, open, high, low, close, volume FROM ohlcv "
                    "WHERE code = ? ORDER BY date DESC LIMIT ?"
                    ") ORDER BY date ASC",
                    (code, ohlcv_limit)
                ) as cursor:
                    ohlcv_rows = await cursor.fetchall()

            if not ohlcv_rows:
                return None

            historical = [dict(r) for r in ohlcv_rows]
            entry = {
                "ohlcv_historical": historical,
                "ohlcv_today": None,
                "historical_complete": True,  # DB가 줄 수 있는 전부를 받았음
                "last_loaded": time.time(),
            }
            self._ohlcv_cache.put(code, entry)
            latest_date = historical[-1].get("date") if historical else None
            if self._cache_logger:
                self._cache_logger.log_ohlcv_loaded(code, caller, len(historical), latest_date)
            return {
                "code": code,
                "ohlcv": historical[:],
                "last_updated": entry["last_loaded"],
                "historical_complete": True,
            }
        except Exception as e:
            self._logger.error(f"StockOhlcvRepository OHLCV 조회 실패 ({code}): {e}")
            return None

    def update_today_candle(self, code: str, current_price: float, volume: int = 0):
        """
        WebSocket 틱 데이터로 당일 OHLCV 캔들을 갱신합니다.
        - ohlcv_today가 있으면 해당 캔들 업데이트
        - ohlcv_today가 없으면 마지막 historical 캔들을 업데이트 (기존 동작 유지)
        """
        cached = self._ohlcv_cache.get(code, count_stats=False, item_type="update_tick")
        if not cached:
            return

        is_new_candle = cached.get("ohlcv_today") is not None
        target = cached.get("ohlcv_today")
        if target is None and cached.get("ohlcv_historical"):
            target = cached["ohlcv_historical"][-1]

        if target is None:
            return

        before_price = target.get("close")
        target["close"] = current_price
        if volume > 0:
            target["volume"] = volume
        if current_price > target.get("high", current_price):
            target["high"] = current_price
        if current_price < target.get("low", current_price):
            target["low"] = current_price

        if self._cache_logger and before_price != current_price:
            self._cache_logger.log_today_candle(
                code, before_price, current_price,
                target.get("high"), target.get("low"),
                is_new_candle,
            )

    async def upsert_ohlcv(self, records: List[Dict]):
        """여러 종목의 일봉(OHLCV) 데이터를 일괄 upsert 후 관련 캐시 무효화."""
        if not records:
            return
        codes_to_invalidate = {r["code"] for r in records if "code" in r}
        try:
            async with self._get_write_connection() as conn:
                await conn.executemany(
                    """
                    INSERT OR REPLACE INTO ohlcv (
                        code, date, open, high, low, close, volume
                    ) VALUES (
                        :code, :date, :open, :high, :low, :close, :volume
                    )
                    """,
                    records,
                )
            # upsert 성공 후에만 캐시 무효화 — 다음 get_stock_data 시 신선한 DB 데이터 로드
            for code in codes_to_invalidate:
                self._ohlcv_cache.delete(code)
                if self._cache_logger:
                    self._cache_logger.log_ohlcv_invalidated(code)
            if self._cache_logger:
                self._cache_logger.log_ohlcv_upsert(
                    record_count=len(records),
                    code_count=len(codes_to_invalidate),
                    invalidated_codes=list(codes_to_invalidate),
                )
        except Exception as e:
            self._logger.error(f"StockOhlcvRepository OHLCV upsert 실패: {e}")

    async def get_ohlcv_summary(self, code: str) -> Dict[str, Any]:
        """DB에서 종목의 OHLCV 요약 정보를 반환합니다 (전체 데이터 로드 없이 메타만 조회).

        Returns:
            {"count": int, "latest_date": str|None, "oldest_date": str|None}
        """
        try:
            async with self._get_read_connection() as conn:
                async with conn.execute(
                    "SELECT COUNT(*), MAX(date), MIN(date) FROM ohlcv WHERE code = ?",
                    (code,),
                ) as cursor:
                    row = await cursor.fetchone()
            if row and row[0]:
                return {"count": row[0], "latest_date": row[1], "oldest_date": row[2]}
        except Exception as e:
            self._logger.error(f"StockOhlcvRepository OHLCV 요약 조회 실패 ({code}): {e}")
        return {"count": 0, "latest_date": None, "oldest_date": None}

    async def get_ohlcv_max_trading_days(self) -> int:
        """DB에 저장된 고유 거래일 수를 반환합니다."""
        try:
            async with self._get_read_connection() as conn:
                async with conn.execute("SELECT COUNT(DISTINCT date) FROM ohlcv") as cursor:
                    row = await cursor.fetchone()
            return row[0] if row and row[0] else 0
        except Exception as e:
            self._logger.error(f"StockOhlcvRepository 거래일 수 조회 실패: {e}")
            return 0

    # ── daily_prices (장마감 후 전종목 스냅샷) ──────────────────────────────────

    async def upsert_daily_snapshot(self, trade_date: str, records: List[Dict]):
        """장마감 후 전체 종목 현재가+펀더멘털 스냅샷을 일괄 upsert.

        ON CONFLICT DO UPDATE를 사용하여 충돌 시 가격 데이터만 덮어씁니다.
        - is_newhigh, is_historical_newhigh: DO UPDATE에 포함하지 않아 기존 값 보존
        - minervini_stage, minervini_reason, rs_rating: 새 값이 NULL이면 기존 값 유지 (COALESCE)
        """
        if not records:
            return

        now = time.time()
        try:
            async with self._get_write_connection() as conn:
                await conn.executemany(
                    """
                    INSERT INTO daily_prices (
                        code, trade_date, name,
                        current_price, open_price, high_price, low_price, prev_close,
                        change_price, change_sign, change_rate,
                        volume, trading_value, market_cap,
                        per, pbr, eps,
                        w52_high, w52_low,
                        market,
                        iscd_stat_cls_code, mang_issu_cls_code, mrkt_warn_cls_code, invt_caful_yn,
                        minervini_stage, minervini_reason, rs_rating, collected_at
                    ) VALUES (
                        :code, :trade_date, :name,
                        :current_price, :open_price, :high_price, :low_price, :prev_close,
                        :change_price, :change_sign, :change_rate,
                        :volume, :trading_value, :market_cap,
                        :per, :pbr, :eps,
                        :w52_high, :w52_low,
                        :market,
                        :iscd_stat_cls_code, :mang_issu_cls_code, :mrkt_warn_cls_code, :invt_caful_yn,
                        :minervini_stage, :minervini_reason, :rs_rating, :collected_at
                    )
                    ON CONFLICT(code, trade_date) DO UPDATE SET
                        name            = excluded.name,
                        current_price   = excluded.current_price,
                        open_price      = excluded.open_price,
                        high_price      = excluded.high_price,
                        low_price       = excluded.low_price,
                        prev_close      = excluded.prev_close,
                        change_price    = excluded.change_price,
                        change_sign     = excluded.change_sign,
                        change_rate     = excluded.change_rate,
                        volume          = excluded.volume,
                        trading_value   = excluded.trading_value,
                        market_cap      = excluded.market_cap,
                        per             = excluded.per,
                        pbr             = excluded.pbr,
                        eps             = excluded.eps,
                        w52_high        = excluded.w52_high,
                        w52_low         = excluded.w52_low,
                        market          = excluded.market,
                        iscd_stat_cls_code = COALESCE(excluded.iscd_stat_cls_code, iscd_stat_cls_code),
                        mang_issu_cls_code = COALESCE(excluded.mang_issu_cls_code, mang_issu_cls_code),
                        mrkt_warn_cls_code = COALESCE(excluded.mrkt_warn_cls_code, mrkt_warn_cls_code),
                        invt_caful_yn      = COALESCE(excluded.invt_caful_yn,      invt_caful_yn),
                        minervini_stage  = COALESCE(excluded.minervini_stage,  minervini_stage),
                        minervini_reason = COALESCE(excluded.minervini_reason, minervini_reason),
                        rs_rating        = COALESCE(excluded.rs_rating,        rs_rating),
                        collected_at    = excluded.collected_at
                    -- is_newhigh, is_historical_newhigh 는 언급하지 않으므로 기존 값 보존
                    """,
                    [{**r, "trade_date": trade_date, "collected_at": now,
                      "iscd_stat_cls_code": r.get("iscd_stat_cls_code"),
                      "mang_issu_cls_code": r.get("mang_issu_cls_code"),
                      "mrkt_warn_cls_code": r.get("mrkt_warn_cls_code"),
                      "invt_caful_yn": r.get("invt_caful_yn"),
                      "minervini_stage": r.get("minervini_stage"), "minervini_reason": r.get("minervini_reason"),
                      "rs_rating": r.get("rs_rating")}
                     for r in records],
                )
            self._logger.debug(
                f"StockOhlcvRepository: daily_prices {len(records)}건 upsert 완료 (date={trade_date})"
            )
        except Exception as e:
            self._logger.error(f"StockOhlcvRepository daily_prices upsert 실패: {e}")

    async def update_minervini_fields(self, trade_date: str, records: List[Dict]):
        """daily_prices의 minervini_stage / minervini_reason / rs_rating 컬럼만 UPDATE.

        DailyPriceCollectorTask의 INSERT OR REPLACE가 해당 컬럼을 NULL로 덮어쓰는 문제를
        방지하기 위해 MinerviniUpdateTask에서 이 메서드를 사용한다.
        대상 row가 없으면 UPDATE는 아무 동작도 하지 않는다(best-effort).
        """
        if not records:
            return
        try:
            async with self._get_write_connection() as conn:
                await conn.executemany(
                    """
                    UPDATE daily_prices
                    SET minervini_stage = :minervini_stage,
                        minervini_reason = :minervini_reason,
                        rs_rating = :rs_rating
                    WHERE code = :code AND trade_date = :trade_date
                    """,
                    [
                        {
                            "code": r.get("code"),
                            "trade_date": trade_date,
                            "minervini_stage": r.get("minervini_stage"),
                            "minervini_reason": r.get("minervini_reason"),
                            "rs_rating": r.get("rs_rating"),
                        }
                        for r in records
                    ],
                )
            self._logger.debug(
                f"StockOhlcvRepository: minervini fields {len(records)}건 update 완료 (date={trade_date})"
            )
        except Exception as e:
            self._logger.error(f"StockOhlcvRepository minervini fields update 실패: {e}")

    async def update_newhigh_fields(self, trade_date: str, records: List[Dict]):
        """daily_prices의 is_newhigh / is_historical_newhigh 컬럼을 해당 날짜 기준으로 재작성."""
        try:
            async with self._get_write_connection() as conn:
                await conn.execute(
                    """
                    UPDATE daily_prices
                    SET is_newhigh = 0,
                        is_historical_newhigh = 0
                    WHERE trade_date = ?
                    """,
                    (trade_date,),
                )
                if records:
                    await conn.executemany(
                        """
                        UPDATE daily_prices
                        SET is_newhigh = :is_newhigh,
                            is_historical_newhigh = :is_historical_newhigh
                        WHERE code = :code AND trade_date = :trade_date
                        """,
                        [
                            {
                                "code": r.get("code"),
                                "trade_date": trade_date,
                                "is_newhigh": 1 if r.get("is_newhigh") else 0,
                                "is_historical_newhigh": 1 if r.get("is_historical_new_high") else 0,
                            }
                            for r in records
                        ],
                    )
            self._logger.debug(
                f"StockOhlcvRepository: newhigh fields {len(records)}건 update 완료 (date={trade_date})"
            )
        except Exception as e:
            self._logger.error(f"StockOhlcvRepository newhigh fields update 실패: {e}")

    async def get_prices_by_date(self, trade_date: str) -> List[Dict]:
        """특정 날짜의 전체 종목 스냅샷 조회."""
        try:
            async with self._get_read_connection() as conn:
                async with conn.execute(
                    "SELECT * FROM daily_prices WHERE trade_date = ? ORDER BY code",
                    (trade_date,),
                ) as cursor:
                    rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            self._logger.error(f"StockOhlcvRepository daily_prices 날짜별 조회 실패: {e}")
            return []

    async def get_all_daily_snapshots(self, trade_date: str) -> List[Dict]:
        """특정 거래일의 전체 종목 스냅샷을 시가총액 내림차순으로 조회."""
        try:
            async with self._get_read_connection() as conn:
                async with conn.execute(
                    "SELECT * FROM daily_prices WHERE trade_date = ? ORDER BY market_cap DESC",
                    (trade_date,),
                ) as cursor:
                    rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            self._logger.error(f"StockOhlcvRepository daily_prices 전종목 조회 실패: {e}")
            return []

    async def get_ytd_return_ranking(self, limit: int = 100, market: Optional[str] = None) -> List[Dict]:
        """최신 거래일(daily_prices)과 연초 첫 종가(ohlcv)를 비교한 YTD 수익률 랭킹.

        daily_prices 스냅샷 수집은 특정 시점부터 시작되어 연초 데이터가 없을 수 있으므로,
        연초 기준가는 더 오래 축적된 ohlcv 테이블에서 조회한다.
        market: "KOSPI"/"KOSDAQ" 지정 시 해당 시장으로 필터링, None/"ALL"이면 전체.
        """
        try:
            async with self._get_read_connection() as conn:
                async with conn.execute("SELECT MAX(trade_date) FROM daily_prices") as cursor:
                    latest_row = await cursor.fetchone()
                latest_date = latest_row[0] if latest_row and latest_row[0] else None
                if not latest_date:
                    return []

                year = latest_date[:4]
                date_glob = f"{year}[0-1][0-9][0-3][0-9]"
                # 휴장일(채권 등 현재 추적 종목과 무관한 코드만 존재)이 연초 최초 날짜로
                # 잡히지 않도록, 최신 스냅샷 종목과 실제로 겹치는 코드가 있는 날짜만 후보로 삼는다.
                async with conn.execute(
                    """
                    SELECT MIN(base.date)
                    FROM ohlcv AS base
                    JOIN daily_prices AS latest
                      ON latest.code = base.code AND latest.trade_date = ?
                    WHERE base.date GLOB ?
                    """,
                    (latest_date, date_glob),
                ) as cursor:
                    base_row = await cursor.fetchone()
                base_date = base_row[0] if base_row and base_row[0] else None
                if not base_date:
                    return []

                market_filter = ""
                params = [base_date, latest_date]
                if market and market != "ALL":
                    market_filter = "AND latest.market = ?"
                    params.append(market)
                params.append(limit)

                async with conn.execute(
                    f"""
                    SELECT latest.*, base.close AS base_price
                    FROM daily_prices AS latest
                    JOIN ohlcv AS base
                      ON base.code = latest.code AND base.date = ?
                    WHERE latest.trade_date = ?
                      AND latest.current_price > 0
                      AND base.close > 0
                      {market_filter}
                    ORDER BY ((CAST(latest.current_price AS REAL) / base.close) - 1.0) DESC
                    LIMIT ?
                    """,
                    params,
                ) as cursor:
                    rows = await cursor.fetchall()

            results = []
            for rank, row in enumerate(rows, 1):
                item = dict(row)
                item["base_date"] = base_date
                item["latest_date"] = latest_date
                item["ytd_return_rate"] = round(
                    (item["current_price"] / item["base_price"] - 1.0) * 100,
                    2,
                )
                item["data_rank"] = str(rank)
                results.append(item)
            return results
        except Exception as e:
            self._logger.error(f"StockOhlcvRepository YTD 수익률 랭킹 조회 실패: {e}")
            return []

    async def get_newhigh_stocks(self, trade_date: str) -> List[Dict]:
        """특정 거래일의 신고가(is_newhigh=1) 종목을 시가총액 내림차순으로 조회."""
        try:
            async with self._get_read_connection() as conn:
                async with conn.execute(
                    "SELECT * FROM daily_prices WHERE trade_date = ? AND is_newhigh = 1 ORDER BY market_cap DESC",
                    (trade_date,),
                ) as cursor:
                    rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            self._logger.error(f"StockOhlcvRepository newhigh 조회 실패: {e}")
            return []

    async def get_minervini_stage2_stocks(self, trade_date: str) -> List[Dict]:
        """특정 거래일의 Minervini Stage2 종목을 rs_rating 내림차순으로 조회."""
        try:
            async with self._get_read_connection() as conn:
                async with conn.execute(
                    "SELECT * FROM daily_prices WHERE trade_date = ? AND minervini_stage = 2 ORDER BY rs_rating DESC",
                    (trade_date,),
                ) as cursor:
                    rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            self._logger.error(f"StockOhlcvRepository minervini stage2 조회 실패: {e}")
            return []

    async def get_price_history(self, code: str, days: int = 30) -> List[Dict]:
        """특정 종목의 최근 N일간 스냅샷 이력 조회."""
        try:
            async with self._get_read_connection() as conn:
                async with conn.execute(
                    "SELECT * FROM daily_prices WHERE code = ? "
                    "ORDER BY trade_date DESC LIMIT ?",
                    (code, days),
                ) as cursor:
                    rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            self._logger.error(f"StockOhlcvRepository daily_prices 이력 조회 실패: {e}")
            return []

    async def get_latest_trade_date(self) -> Optional[str]:
        """daily_prices에 저장된 가장 최근 거래일 반환."""
        try:
            async with self._get_read_connection() as conn:
                async with conn.execute("SELECT MAX(trade_date) FROM daily_prices") as cursor:
                    row = await cursor.fetchone()
                return row[0] if row and row[0] else None
        except Exception as e:
            self._logger.error(f"StockOhlcvRepository daily_prices 최근 거래일 조회 실패: {e}")
            return None

    async def get_count_by_date(self, trade_date: str) -> int:
        """특정 날짜에 저장된 종목 수 반환."""
        try:
            async with self._get_read_connection() as conn:
                async with conn.execute(
                    "SELECT COUNT(*) FROM daily_prices WHERE trade_date = ?",
                    (trade_date,),
                ) as cursor:
                    row = await cursor.fetchone()
                return row[0] if row else 0
        except Exception as e:
            self._logger.error(f"StockOhlcvRepository daily_prices 카운트 조회 실패: {e}")
            return 0

    async def get_minervini_stage_count(self, trade_date: str) -> int:
        """특정 날짜에 minervini_stage가 계산된 종목 수 반환."""
        try:
            async with self._get_read_connection() as conn:
                async with conn.execute(
                    "SELECT COUNT(*) FROM daily_prices WHERE trade_date = ? AND minervini_stage IS NOT NULL",
                    (trade_date,),
                ) as cursor:
                    row = await cursor.fetchone()
                return row[0] if row else 0
        except Exception as e:
            self._logger.error(f"StockOhlcvRepository minervini_stage 카운트 조회 실패: {e}")
            return 0

    async def cleanup_old_data(self, keep_days: int = 365):
        """오래된 daily_prices 데이터 정리."""
        from datetime import datetime, timedelta

        cutoff_date = (datetime.now() - timedelta(days=keep_days)).strftime("%Y%m%d")
        try:
            async with self._get_write_connection() as conn:
                async with conn.execute(
                    "DELETE FROM daily_prices WHERE trade_date < ?", (cutoff_date,)
                ) as cursor:
                    deleted = cursor.rowcount
                if deleted > 0:
                    self._logger.info(
                        f"StockOhlcvRepository: {deleted}건 오래된 daily_prices 삭제 (기준: {cutoff_date})"
                    )
        except Exception as e:
            self._logger.error(f"StockOhlcvRepository daily_prices 데이터 정리 실패: {e}")

    async def get_latest_daily_snapshot(self, code: str) -> Optional[dict]:
        """daily_prices에서 최신 스냅샷을 현재가 API 응답 포맷으로 변환하여 반환합니다."""
        try:
            async with self._get_read_connection() as conn:
                async with conn.execute(
                    "SELECT * FROM daily_prices WHERE code = ? ORDER BY trade_date DESC LIMIT 1",
                    (code,),
                ) as cursor:
                    row = await cursor.fetchone()
            if not row:
                return None
            r = dict(row)
            if r['w52_high'] == 1:
                a = 1
            output = {
                "stck_prpr":     str(r.get("current_price") or 0),
                "stck_oprc":     str(r.get("open_price") or 0),
                "stck_hgpr":     str(r.get("high_price") or 0),
                "stck_lwpr":     str(r.get("low_price") or 0),
                "stck_sdpr":     str(r.get("prev_close") or 0),
                "prdy_vrss":     str(r.get("change_price") or 0),
                "prdy_vrss_sign": str(r.get("change_sign") or ""),
                "prdy_ctrt":     str(r.get("change_rate") or "0"),
                "acml_vol":      str(r.get("volume") or 0),
                "acml_tr_pbmn":  str(r.get("trading_value") or 0),
                "hts_avls":      str(r.get("market_cap") or 0),
                "per":           str(r.get("per") or ""),
                "pbr":           str(r.get("pbr") or ""),
                "eps":           str(r.get("eps") or ""),
                "d250_hgpr":     str(r.get("w52_high") or 0),
                "d250_lwpr":     str(r.get("w52_low") or 0),
                "hts_kor_isnm":  str(r.get("name") or ""),
                "stck_bsop_date": str(r.get("trade_date") or ""),
                "stck_shrn_iscd": str(r.get("code") or ""),
                "iscd_stat_cls_code": str(r.get("iscd_stat_cls_code") or ""),
                "mang_issu_cls_code": str(r.get("mang_issu_cls_code") or ""),
                "mrkt_warn_cls_code": str(r.get("mrkt_warn_cls_code") or ""),
                "invt_caful_yn": str(r.get("invt_caful_yn") or ""),
            }
            return {"output": output, "_source": "daily_snapshot", "_trade_date": r.get("trade_date")}
        except Exception as e:
            self._logger.error(f"StockOhlcvRepository daily_prices 스냅샷 조회 실패 ({code}): {e}")
            return None

    def get_cache_stats(self, expand: bool = False, latest_trading_date: str = None) -> dict:
        """OHLCV 캐시 통계를 반환합니다."""
        return self._ohlcv_cache.get_stats(expand=expand, latest_trading_date=latest_trading_date)

    async def close(self):
        """DB 연결(쓰기/읽기)을 닫습니다."""
        if self._write_conn:
            await self._write_conn.close()
            self._write_conn = None
        if self._read_conn:
            await self._read_conn.close()
            self._read_conn = None

    def __del__(self):
        if self._write_conn or self._read_conn:
            self._logger.warning("StockOhlcvRepository was not closed explicitly.")
