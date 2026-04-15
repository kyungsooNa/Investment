"""
StockOhlcvRepository 단위 테스트.
"""
import pytest
import pytest_asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

from repositories.stock_ohlcv_repository import StockOhlcvRepository


@pytest_asyncio.fixture
async def repo(tmp_path):
    db_path = str(tmp_path / "test_ohlcv.db")
    r = StockOhlcvRepository(db_path=db_path)
    yield r
    await r.close()


def _make_snapshot(code, name="테스트", stage=2, rs_rating=80.0, price=50000, market_cap=1_000_000):
    return {
        "code": code,
        "name": name,
        "current_price": price,
        "open_price": None,
        "high_price": None,
        "low_price": None,
        "prev_close": None,
        "change_price": None,
        "change_sign": None,
        "change_rate": "1.5",
        "volume": None,
        "trading_value": None,
        "market_cap": market_cap,
        "per": None,
        "pbr": None,
        "eps": None,
        "w52_high": None,
        "w52_low": None,
        "market": "KOSPI",
        "minervini_stage": stage,
        "minervini_reason": "test",
        "rs_rating": rs_rating,
    }


class TestGetMinerviniStage2Stocks:
    """get_minervini_stage2_stocks: Stage2 필터링 및 rs_rating 정렬 검증."""

    @pytest.mark.asyncio
    async def test_returns_only_stage2(self, repo):
        """Stage2 종목만 반환하고 Stage1/0은 제외된다."""
        await repo.upsert_daily_snapshot("20260414", [
            _make_snapshot("A001", stage=2, rs_rating=90.0),
            _make_snapshot("A002", stage=1, rs_rating=70.0),
            _make_snapshot("A003", stage=2, rs_rating=80.0),
            _make_snapshot("A004", stage=0, rs_rating=50.0),
        ])

        result = await repo.get_minervini_stage2_stocks("20260414")
        codes = {r["code"] for r in result}
        assert codes == {"A001", "A003"}

    @pytest.mark.asyncio
    async def test_ordered_by_rs_rating_desc(self, repo):
        """RS Rating 내림차순으로 정렬된다."""
        await repo.upsert_daily_snapshot("20260414", [
            _make_snapshot("A001", stage=2, rs_rating=60.0),
            _make_snapshot("A002", stage=2, rs_rating=95.0),
            _make_snapshot("A003", stage=2, rs_rating=78.0),
        ])

        result = await repo.get_minervini_stage2_stocks("20260414")
        ratings = [r["rs_rating"] for r in result]
        assert ratings == [95.0, 78.0, 60.0]

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_stage2(self, repo):
        """Stage2 종목이 없으면 빈 리스트 반환."""
        await repo.upsert_daily_snapshot("20260414", [
            _make_snapshot("A001", stage=1, rs_rating=70.0),
        ])

        result = await repo.get_minervini_stage2_stocks("20260414")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_for_missing_date(self, repo):
        """데이터가 없는 날짜는 빈 리스트 반환."""
        result = await repo.get_minervini_stage2_stocks("20200101")
        assert result == []

    @pytest.mark.asyncio
    async def test_rs_rating_persisted(self, repo):
        """upsert 시 rs_rating이 DB에 올바르게 저장된다."""
        await repo.upsert_daily_snapshot("20260414", [
            _make_snapshot("A001", stage=2, rs_rating=92.5),
        ])

        result = await repo.get_minervini_stage2_stocks("20260414")
        assert len(result) == 1
        assert result[0]["rs_rating"] == 92.5

    @pytest.mark.asyncio
    async def test_rs_rating_none_allowed(self, repo):
        """rs_rating이 None인 Stage2 종목도 조회 결과에 포함된다."""
        await repo.upsert_daily_snapshot("20260414", [
            _make_snapshot("A001", stage=2, rs_rating=None),
        ])

        result = await repo.get_minervini_stage2_stocks("20260414")
        assert len(result) == 1
        assert result[0]["rs_rating"] is None

    @pytest.mark.asyncio
    async def test_date_isolation(self, repo):
        """다른 거래일의 데이터는 반환되지 않는다."""
        await repo.upsert_daily_snapshot("20260413", [
            _make_snapshot("A001", stage=2, rs_rating=80.0),
        ])
        await repo.upsert_daily_snapshot("20260414", [
            _make_snapshot("A002", stage=2, rs_rating=85.0),
        ])

        result = await repo.get_minervini_stage2_stocks("20260414")
        assert len(result) == 1
        assert result[0]["code"] == "A002"

    @pytest.mark.asyncio
    async def test_rs_rating_upsert_overwrite(self, repo):
        """동일 (code, date) 재 upsert 시 rs_rating이 최신값으로 갱신된다."""
        await repo.upsert_daily_snapshot("20260414", [
            _make_snapshot("A001", stage=2, rs_rating=70.0),
        ])
        await repo.upsert_daily_snapshot("20260414", [
            _make_snapshot("A001", stage=2, rs_rating=88.0),
        ])

        result = await repo.get_minervini_stage2_stocks("20260414")
        assert len(result) == 1
        assert result[0]["rs_rating"] == 88.0


class TestGetMinerviniStageCount:
    """get_minervini_stage_count: minervini_stage IS NOT NULL 종목 수 검증."""

    @pytest.mark.asyncio
    async def test_counts_only_non_null_stages(self, repo):
        """minervini_stage가 있는 종목만 카운트한다 (NULL 제외)."""
        await repo.upsert_daily_snapshot("20260414", [
            _make_snapshot("A001", stage=2),
            _make_snapshot("A002", stage=1),
            _make_snapshot("A003", stage=4),
        ])
        # _make_snapshot은 항상 stage 값을 설정하므로 3개
        assert await repo.get_minervini_stage_count("20260414") == 3

    @pytest.mark.asyncio
    async def test_null_stage_not_counted(self, repo):
        """minervini_stage가 NULL인 종목은 카운트에서 제외된다."""
        # stage 있는 종목 upsert
        await repo.upsert_daily_snapshot("20260414", [
            _make_snapshot("A001", stage=2),
        ])
        # stage 없는 종목 upsert (update_minervini_fields로 stage만 따로 주입)
        # stage=None인 레코드를 만들기 위해 직접 DB 조작
        import aiosqlite
        async with aiosqlite.connect(repo._db_path) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO daily_prices (trade_date, code, name, minervini_stage) VALUES (?, ?, ?, NULL)",
                ("20260414", "A002", "테스트2"),
            )
            await conn.commit()

        assert await repo.get_minervini_stage_count("20260414") == 1

    @pytest.mark.asyncio
    async def test_returns_zero_for_missing_date(self, repo):
        """데이터가 없는 날짜는 0 반환."""
        assert await repo.get_minervini_stage_count("20200101") == 0

    @pytest.mark.asyncio
    async def test_date_isolation(self, repo):
        """다른 날짜의 stage 데이터는 카운트에 포함되지 않는다."""
        await repo.upsert_daily_snapshot("20260413", [
            _make_snapshot("A001", stage=2),
            _make_snapshot("A002", stage=1),
        ])
        await repo.upsert_daily_snapshot("20260414", [
            _make_snapshot("A003", stage=2),
        ])

        assert await repo.get_minervini_stage_count("20260414") == 1
        assert await repo.get_minervini_stage_count("20260413") == 2

    @pytest.mark.asyncio
    async def test_all_stages_counted(self, repo):
        """Stage 1~4 모두 NULL이 아니므로 카운트에 포함된다."""
        await repo.upsert_daily_snapshot("20260414", [
            _make_snapshot("A001", stage=1),
            _make_snapshot("A002", stage=2),
            _make_snapshot("A003", stage=3),
            _make_snapshot("A004", stage=4),
        ])
        assert await repo.get_minervini_stage_count("20260414") == 4


# ── 공통 헬퍼 ──────────────────────────────────────────────────────────────────

def _broken_read_ctx(repo, monkeypatch):
    """_get_read_connection을 예외를 던지는 stub으로 교체."""
    @asynccontextmanager
    async def _broken():
        raise Exception("forced read error")
        yield  # noqa
    monkeypatch.setattr(repo, "_get_read_connection", _broken)


def _broken_write_ctx(repo, monkeypatch):
    """_get_write_connection을 예외를 던지는 stub으로 교체."""
    @asynccontextmanager
    async def _broken():
        raise Exception("forced write error")
        yield  # noqa
    monkeypatch.setattr(repo, "_get_write_connection", _broken)


def _make_ohlcv(code, date, close=50000):
    return {"code": code, "date": date, "open": close, "high": close, "low": close, "close": close, "volume": 1000}


# ── update_minervini_fields ───────────────────────────────────────────────────

class TestUpdateMinerviniFields:
    """update_minervini_fields: minervini 컬럼만 UPDATE 검증."""

    @pytest.mark.asyncio
    async def test_updates_existing_row(self, repo):
        """기존 row의 minervini_stage / reason / rs_rating이 갱신된다."""
        await repo.upsert_daily_snapshot("20260414", [
            _make_snapshot("A001", stage=1, rs_rating=50.0),
        ])
        await repo.update_minervini_fields("20260414", [
            {"code": "A001", "minervini_stage": 2, "minervini_reason": "updated", "rs_rating": 99.0},
        ])
        result = await repo.get_minervini_stage2_stocks("20260414")
        assert len(result) == 1
        assert result[0]["minervini_stage"] == 2
        assert result[0]["rs_rating"] == 99.0
        assert result[0]["minervini_reason"] == "updated"

    @pytest.mark.asyncio
    async def test_empty_records_is_noop(self, repo):
        """빈 records 전달 시 아무 동작도 하지 않는다."""
        await repo.update_minervini_fields("20260414", [])  # should not raise

    @pytest.mark.asyncio
    async def test_missing_row_is_ignored(self, repo):
        """대상 row가 없으면 UPDATE는 조용히 무시된다."""
        await repo.update_minervini_fields("20260414", [
            {"code": "NONE", "minervini_stage": 2, "minervini_reason": "x", "rs_rating": 80.0},
        ])
        result = await repo.get_minervini_stage2_stocks("20260414")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_is_handled(self, repo, monkeypatch):
        """쓰기 오류 시 예외를 삼키고 로거에 기록한다."""
        _broken_write_ctx(repo, monkeypatch)
        await repo.update_minervini_fields("20260414", [
            {"code": "A001", "minervini_stage": 2, "minervini_reason": "x", "rs_rating": 80.0},
        ])  # should not raise


# ── update_newhigh_fields ────────────────────────────────────────────────────

class TestUpdateNewhighFields:
    """update_newhigh_fields: is_newhigh / is_historical_newhigh UPDATE 검증."""

    @pytest.mark.asyncio
    async def test_updates_newhigh_flags(self, repo):
        """is_newhigh / is_historical_newhigh 플래그가 올바르게 저장된다."""
        await repo.upsert_daily_snapshot("20260414", [
            _make_snapshot("A001", stage=2),
            _make_snapshot("A002", stage=2),
        ])
        await repo.update_newhigh_fields("20260414", [
            {"code": "A001", "is_newhigh": True,  "is_historical_new_high": True},
            {"code": "A002", "is_newhigh": False, "is_historical_new_high": False},
        ])
        result = await repo.get_newhigh_stocks("20260414")
        assert len(result) == 1
        assert result[0]["code"] == "A001"
        assert result[0]["is_newhigh"] == 1
        assert result[0]["is_historical_newhigh"] == 1

    @pytest.mark.asyncio
    async def test_false_flags_stored_as_zero(self, repo):
        """is_newhigh=False 는 0으로 저장된다."""
        await repo.upsert_daily_snapshot("20260414", [
            _make_snapshot("A001", stage=2),
        ])
        await repo.update_newhigh_fields("20260414", [
            {"code": "A001", "is_newhigh": False, "is_historical_new_high": False},
        ])
        newhighs = await repo.get_newhigh_stocks("20260414")
        assert newhighs == []

    @pytest.mark.asyncio
    async def test_empty_records_is_noop(self, repo):
        """빈 records 전달 시 아무 동작도 하지 않는다."""
        await repo.update_newhigh_fields("20260414", [])

    @pytest.mark.asyncio
    async def test_exception_is_handled(self, repo, monkeypatch):
        """쓰기 오류 시 예외를 삼키고 로거에 기록한다."""
        _broken_write_ctx(repo, monkeypatch)
        await repo.update_newhigh_fields("20260414", [
            {"code": "A001", "is_newhigh": True, "is_historical_new_high": False},
        ])  # should not raise


# ── get_newhigh_stocks ───────────────────────────────────────────────────────

class TestGetNewhighStocks:
    """get_newhigh_stocks: is_newhigh=1 종목 필터 및 시가총액 정렬 검증."""

    @pytest.mark.asyncio
    async def test_returns_newhigh_stocks_ordered_by_market_cap(self, repo):
        """is_newhigh=1 종목을 시가총액 내림차순으로 반환한다."""
        await repo.upsert_daily_snapshot("20260414", [
            _make_snapshot("A001", stage=2, market_cap=5_000_000),
            _make_snapshot("A002", stage=2, market_cap=3_000_000),
            _make_snapshot("A003", stage=1, market_cap=9_000_000),
        ])
        await repo.update_newhigh_fields("20260414", [
            {"code": "A001", "is_newhigh": True,  "is_historical_new_high": False},
            {"code": "A002", "is_newhigh": True,  "is_historical_new_high": False},
            {"code": "A003", "is_newhigh": False, "is_historical_new_high": False},
        ])
        result = await repo.get_newhigh_stocks("20260414")
        assert [r["code"] for r in result] == ["A001", "A002"]

    @pytest.mark.asyncio
    async def test_returns_empty_for_missing_date(self, repo):
        """데이터가 없는 날짜는 빈 리스트."""
        assert await repo.get_newhigh_stocks("20200101") == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, repo, monkeypatch):
        """읽기 오류 시 빈 리스트를 반환한다."""
        _broken_read_ctx(repo, monkeypatch)
        assert await repo.get_newhigh_stocks("20260414") == []


# ── get_prices_by_date ───────────────────────────────────────────────────────

class TestGetPricesByDate:
    """get_prices_by_date: 날짜별 전체 스냅샷 조회 검증."""

    @pytest.mark.asyncio
    async def test_returns_all_records_for_date(self, repo):
        """해당 날짜의 모든 종목을 code 오름차순으로 반환한다."""
        await repo.upsert_daily_snapshot("20260414", [
            _make_snapshot("B002", stage=1),
            _make_snapshot("A001", stage=2),
        ])
        result = await repo.get_prices_by_date("20260414")
        assert len(result) == 2
        assert result[0]["code"] == "A001"
        assert result[1]["code"] == "B002"

    @pytest.mark.asyncio
    async def test_returns_empty_for_missing_date(self, repo):
        """데이터가 없는 날짜는 빈 리스트."""
        assert await repo.get_prices_by_date("20200101") == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, repo, monkeypatch):
        """읽기 오류 시 빈 리스트를 반환한다."""
        _broken_read_ctx(repo, monkeypatch)
        assert await repo.get_prices_by_date("20260414") == []


# ── get_all_daily_snapshots ──────────────────────────────────────────────────

class TestGetAllDailySnapshots:
    """get_all_daily_snapshots: 시가총액 내림차순 전종목 조회 검증."""

    @pytest.mark.asyncio
    async def test_returns_all_ordered_by_market_cap_desc(self, repo):
        """전종목을 시가총액 내림차순으로 반환한다."""
        await repo.upsert_daily_snapshot("20260414", [
            _make_snapshot("A001", stage=2, market_cap=1_000_000),
            _make_snapshot("A002", stage=1, market_cap=5_000_000),
            _make_snapshot("A003", stage=2, market_cap=3_000_000),
        ])
        result = await repo.get_all_daily_snapshots("20260414")
        caps = [r["market_cap"] for r in result]
        assert caps == [5_000_000, 3_000_000, 1_000_000]

    @pytest.mark.asyncio
    async def test_returns_empty_for_missing_date(self, repo):
        """데이터가 없는 날짜는 빈 리스트."""
        assert await repo.get_all_daily_snapshots("20200101") == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, repo, monkeypatch):
        """읽기 오류 시 빈 리스트를 반환한다."""
        _broken_read_ctx(repo, monkeypatch)
        assert await repo.get_all_daily_snapshots("20260414") == []


# ── get_price_history ────────────────────────────────────────────────────────

class TestGetPriceHistory:
    """get_price_history: 종목별 최근 N일 이력 조회 검증."""

    @pytest.mark.asyncio
    async def test_returns_recent_n_days(self, repo):
        """최근 days 개 이내 스냅샷만 반환한다."""
        for d in ["20260410", "20260411", "20260412", "20260413", "20260414"]:
            await repo.upsert_daily_snapshot(d, [_make_snapshot("A001", stage=2)])
        result = await repo.get_price_history("A001", days=3)
        assert len(result) == 3
        dates = [r["trade_date"] for r in result]
        assert "20260414" in dates

    @pytest.mark.asyncio
    async def test_returns_empty_for_missing_code(self, repo):
        """존재하지 않는 종목코드는 빈 리스트."""
        assert await repo.get_price_history("XXXX") == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, repo, monkeypatch):
        """읽기 오류 시 빈 리스트를 반환한다."""
        _broken_read_ctx(repo, monkeypatch)
        assert await repo.get_price_history("A001") == []


# ── get_latest_trade_date ────────────────────────────────────────────────────

class TestGetLatestTradeDate:
    """get_latest_trade_date: 최근 거래일 조회 검증."""

    @pytest.mark.asyncio
    async def test_returns_max_trade_date(self, repo):
        """저장된 가장 최근 거래일을 반환한다."""
        await repo.upsert_daily_snapshot("20260413", [_make_snapshot("A001")])
        await repo.upsert_daily_snapshot("20260414", [_make_snapshot("A001")])
        assert await repo.get_latest_trade_date() == "20260414"

    @pytest.mark.asyncio
    async def test_returns_none_when_empty(self, repo):
        """데이터가 없으면 None을 반환한다."""
        assert await repo.get_latest_trade_date() is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, repo, monkeypatch):
        """읽기 오류 시 None을 반환한다."""
        _broken_read_ctx(repo, monkeypatch)
        assert await repo.get_latest_trade_date() is None


# ── get_count_by_date ────────────────────────────────────────────────────────

class TestGetCountByDate:
    """get_count_by_date: 날짜별 종목 수 조회 검증."""

    @pytest.mark.asyncio
    async def test_returns_correct_count(self, repo):
        """해당 날짜의 저장된 종목 수를 반환한다."""
        await repo.upsert_daily_snapshot("20260414", [
            _make_snapshot("A001"),
            _make_snapshot("A002"),
            _make_snapshot("A003"),
        ])
        assert await repo.get_count_by_date("20260414") == 3

    @pytest.mark.asyncio
    async def test_returns_zero_for_missing_date(self, repo):
        """데이터가 없는 날짜는 0을 반환한다."""
        assert await repo.get_count_by_date("20200101") == 0

    @pytest.mark.asyncio
    async def test_exception_returns_zero(self, repo, monkeypatch):
        """읽기 오류 시 0을 반환한다."""
        _broken_read_ctx(repo, monkeypatch)
        assert await repo.get_count_by_date("20260414") == 0


# ── cleanup_old_data ─────────────────────────────────────────────────────────

class TestCleanupOldData:
    """cleanup_old_data: 오래된 데이터 삭제 검증."""

    @pytest.mark.asyncio
    async def test_deletes_old_records_keeps_recent(self, repo):
        """keep_days보다 오래된 데이터는 삭제되고 최근 데이터는 보존된다."""
        await repo.upsert_daily_snapshot("20200101", [_make_snapshot("A001")])
        await repo.upsert_daily_snapshot("20260414", [_make_snapshot("A002")])
        await repo.cleanup_old_data(keep_days=30)
        assert await repo.get_count_by_date("20200101") == 0
        assert await repo.get_count_by_date("20260414") == 1

    @pytest.mark.asyncio
    async def test_no_error_when_nothing_to_delete(self, repo):
        """삭제 대상이 없어도 오류가 발생하지 않는다."""
        await repo.upsert_daily_snapshot("20260414", [_make_snapshot("A001")])
        await repo.cleanup_old_data(keep_days=365)

    @pytest.mark.asyncio
    async def test_exception_is_handled(self, repo, monkeypatch):
        """쓰기 오류 시 예외를 삼키고 로거에 기록한다."""
        _broken_write_ctx(repo, monkeypatch)
        await repo.cleanup_old_data(keep_days=30)  # should not raise


# ── get_latest_daily_snapshot ────────────────────────────────────────────────

class TestGetLatestDailySnapshot:
    """get_latest_daily_snapshot: 최신 스냅샷을 API 포맷으로 변환 검증."""

    @pytest.mark.asyncio
    async def test_returns_formatted_output(self, repo):
        """존재하는 종목의 최신 스냅샷을 output 키 포함 dict로 반환한다."""
        await repo.upsert_daily_snapshot("20260414", [
            _make_snapshot("A001", price=12345, rs_rating=80.0),
        ])
        result = await repo.get_latest_daily_snapshot("A001")
        assert result is not None
        assert result["_source"] == "daily_snapshot"
        assert result["_trade_date"] == "20260414"
        output = result["output"]
        assert output["stck_prpr"] == "12345"
        assert output["stck_shrn_iscd"] == "A001"

    @pytest.mark.asyncio
    async def test_w52_high_equals_one_branch(self, repo):
        """w52_high가 1인 경우 결과가 정상적으로 반환된다 (내부 debug 분기 커버)."""
        snap = _make_snapshot("A001", price=10000)
        snap["w52_high"] = 1
        await repo.upsert_daily_snapshot("20260414", [snap])
        result = await repo.get_latest_daily_snapshot("A001")
        assert result is not None
        assert result["output"]["d250_hgpr"] == "1"

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_code(self, repo):
        """존재하지 않는 종목코드는 None을 반환한다."""
        assert await repo.get_latest_daily_snapshot("XXXX") is None

    @pytest.mark.asyncio
    async def test_returns_latest_when_multiple_dates(self, repo):
        """여러 날짜 데이터 중 가장 최신 trade_date를 반환한다."""
        await repo.upsert_daily_snapshot("20260413", [_make_snapshot("A001", price=1000)])
        await repo.upsert_daily_snapshot("20260414", [_make_snapshot("A001", price=2000)])
        result = await repo.get_latest_daily_snapshot("A001")
        assert result["output"]["stck_prpr"] == "2000"
        assert result["_trade_date"] == "20260414"

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, repo, monkeypatch):
        """읽기 오류 시 None을 반환한다."""
        _broken_read_ctx(repo, monkeypatch)
        assert await repo.get_latest_daily_snapshot("A001") is None


# ── get_stock_data (캐시 히트 + ohlcv_today) ──────────────────────────────────

class TestGetStockDataCacheHit:
    """get_stock_data: 캐시 히트 시 ohlcv_today 합산 및 예외 처리 검증."""

    @pytest.mark.asyncio
    async def test_cache_hit_includes_today_candle(self, repo):
        """캐시 히트 + ohlcv_today가 있으면 ohlcv 리스트 마지막에 today 캔들이 포함된다."""
        await repo.upsert_ohlcv([
            _make_ohlcv("A001", "20260413"),
            _make_ohlcv("A001", "20260414"),
        ])
        # DB 로드 → 캐시 저장
        first = await repo.get_stock_data("A001", ohlcv_limit=2)
        assert first is not None

        # 캐시 엔트리에 ohlcv_today 수동 삽입
        entry = repo._ohlcv_cache.get("A001", count_stats=False, item_type="test")
        entry["ohlcv_today"] = _make_ohlcv("A001", "20260415")

        # 재조회 → 캐시 히트 + today 캔들 포함
        second = await repo.get_stock_data("A001", ohlcv_limit=2)
        assert second is not None
        assert len(second["ohlcv"]) == 3
        assert second["ohlcv"][-1]["date"] == "20260415"

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, repo, monkeypatch):
        """DB 읽기 오류 시 None을 반환한다."""
        _broken_read_ctx(repo, monkeypatch)
        assert await repo.get_stock_data("A001", ohlcv_limit=10) is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_ohlcv_in_db(self, repo):
        """DB에 OHLCV 데이터가 없는 종목은 None을 반환한다 (L193 커버)."""
        result = await repo.get_stock_data("NOTEXIST", ohlcv_limit=10)
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_insufficient_count_triggers_db_reload(self, repo):
        """캐시 보유 건수 < ohlcv_limit 이면 DB에서 재로드한다 (L161→177 브랜치)."""
        await repo.upsert_ohlcv([
            _make_ohlcv("A001", "20260413"),
            _make_ohlcv("A001", "20260414"),
        ])
        # limit=1 → 캐시에 2건 저장 (historical_complete=True)
        await repo.get_stock_data("A001", ohlcv_limit=1)
        # limit=5 → cached_count(2) < ohlcv_limit(5) → DB 재로드 (브랜치 False)
        result = await repo.get_stock_data("A001", ohlcv_limit=5)
        assert result is not None
        assert len(result["ohlcv"]) == 2


# ── update_today_candle (target=None 경계 케이스) ─────────────────────────────

class TestUpdateTodayCandle:
    """update_today_candle: 캐시 없음/target None 경계 케이스 검증."""

    def test_noop_when_not_cached(self, repo):
        """캐시에 없는 종목은 아무 동작 없이 반환된다."""
        repo.update_today_candle("NOTCACHED", 50000)  # should not raise

    @pytest.mark.asyncio
    async def test_noop_when_target_is_none(self, repo):
        """ohlcv_today=None이고 ohlcv_historical도 빈 경우 조용히 반환된다 (L232)."""
        # 캐시에 빈 entry 삽입
        repo._ohlcv_cache.put("A001", {
            "ohlcv_historical": [],
            "ohlcv_today": None,
            "historical_complete": True,
            "last_loaded": 0,
        })
        repo.update_today_candle("A001", 50000)  # should not raise

    @pytest.mark.asyncio
    async def test_updates_historical_last_when_no_today(self, repo):
        """ohlcv_today=None 이면 ohlcv_historical 마지막 캔들을 업데이트한다."""
        repo._ohlcv_cache.put("A001", {
            "ohlcv_historical": [{"date": "20260414", "open": 100, "high": 110, "low": 90, "close": 100, "volume": 500}],
            "ohlcv_today": None,
            "historical_complete": True,
            "last_loaded": 0,
        })
        repo.update_today_candle("A001", 120)
        entry = repo._ohlcv_cache.get("A001", count_stats=False, item_type="test")
        assert entry["ohlcv_historical"][-1]["close"] == 120
        assert entry["ohlcv_historical"][-1]["high"] == 120

    def test_updates_volume_and_low_when_price_drops(self, repo):
        """가격 하락 + volume > 0 → low와 volume이 갱신된다 (L237, L241 커버)."""
        repo._ohlcv_cache.put("A001", {
            "ohlcv_historical": [{"date": "20260414", "open": 100, "high": 110, "low": 90, "close": 100, "volume": 500}],
            "ohlcv_today": None,
            "historical_complete": True,
            "last_loaded": 0,
        })
        repo.update_today_candle("A001", current_price=50, volume=800)
        entry = repo._ohlcv_cache.get("A001", count_stats=False, item_type="test")
        candle = entry["ohlcv_historical"][-1]
        assert candle["close"] == 50
        assert candle["low"] == 50    # 50 < 90 → low 갱신
        assert candle["volume"] == 800  # volume > 0 → 갱신
        assert candle["high"] == 110   # 50 < 110 → high 불변


# ── get_ohlcv_summary ────────────────────────────────────────────────────────

class TestGetOhlcvSummary:
    """get_ohlcv_summary: OHLCV 메타 정보 조회 검증."""

    @pytest.mark.asyncio
    async def test_returns_count_and_dates(self, repo):
        """OHLCV가 있는 종목의 count / latest_date / oldest_date를 반환한다."""
        await repo.upsert_ohlcv([
            _make_ohlcv("A001", "20260412"),
            _make_ohlcv("A001", "20260413"),
            _make_ohlcv("A001", "20260414"),
        ])
        result = await repo.get_ohlcv_summary("A001")
        assert result["count"] == 3
        assert result["latest_date"] == "20260414"
        assert result["oldest_date"] == "20260412"

    @pytest.mark.asyncio
    async def test_returns_zero_for_missing_code(self, repo):
        """데이터가 없는 종목은 count=0, date=None을 반환한다."""
        result = await repo.get_ohlcv_summary("XXXX")
        assert result == {"count": 0, "latest_date": None, "oldest_date": None}

    @pytest.mark.asyncio
    async def test_exception_returns_default(self, repo, monkeypatch):
        """읽기 오류 시 기본값 dict를 반환한다."""
        _broken_read_ctx(repo, monkeypatch)
        result = await repo.get_ohlcv_summary("A001")
        assert result == {"count": 0, "latest_date": None, "oldest_date": None}


# ── get_ohlcv_max_trading_days ───────────────────────────────────────────────

class TestGetOhlcvMaxTradingDays:
    """get_ohlcv_max_trading_days: 고유 거래일 수 조회 검증."""

    @pytest.mark.asyncio
    async def test_returns_distinct_date_count(self, repo):
        """여러 종목에 걸쳐 고유 거래일 수만 반환한다."""
        await repo.upsert_ohlcv([
            _make_ohlcv("A001", "20260413"),
            _make_ohlcv("A002", "20260413"),  # 같은 날짜 — distinct 1
            _make_ohlcv("A001", "20260414"),
        ])
        assert await repo.get_ohlcv_max_trading_days() == 2

    @pytest.mark.asyncio
    async def test_returns_zero_when_empty(self, repo):
        """OHLCV 데이터가 없으면 0을 반환한다."""
        assert await repo.get_ohlcv_max_trading_days() == 0

    @pytest.mark.asyncio
    async def test_exception_returns_zero(self, repo, monkeypatch):
        """읽기 오류 시 0을 반환한다."""
        _broken_read_ctx(repo, monkeypatch)
        assert await repo.get_ohlcv_max_trading_days() == 0


# ── exception handlers 추가 커버 ─────────────────────────────────────────────

class TestExceptionHandlers:
    """각 조회 메서드의 exception handler 분기 커버."""

    @pytest.mark.asyncio
    async def test_get_minervini_stage2_stocks_exception(self, repo, monkeypatch):
        """읽기 오류 시 빈 리스트를 반환한다."""
        _broken_read_ctx(repo, monkeypatch)
        assert await repo.get_minervini_stage2_stocks("20260414") == []

    @pytest.mark.asyncio
    async def test_get_minervini_stage_count_exception(self, repo, monkeypatch):
        """읽기 오류 시 0을 반환한다."""
        _broken_read_ctx(repo, monkeypatch)
        assert await repo.get_minervini_stage_count("20260414") == 0


# ── get_cache_stats ──────────────────────────────────────────────────────────

class TestGetCacheStats:
    """get_cache_stats: 캐시 통계 반환 검증."""

    def test_returns_dict(self, repo):
        """통계 dict가 반환된다."""
        stats = repo.get_cache_stats()
        assert isinstance(stats, dict)


# ── upsert exception handlers ────────────────────────────────────────────────

class TestUpsertExceptionHandlers:
    """upsert 계열 메서드의 exception handler 분기 커버."""

    @pytest.mark.asyncio
    async def test_upsert_daily_snapshot_exception(self, repo, monkeypatch):
        """쓰기 오류 시 예외를 삼키고 로거에 기록한다."""
        _broken_write_ctx(repo, monkeypatch)
        await repo.upsert_daily_snapshot("20260414", [_make_snapshot("A001")])  # should not raise

    @pytest.mark.asyncio
    async def test_upsert_ohlcv_exception(self, repo, monkeypatch):
        """OHLCV upsert 쓰기 오류 시 예외를 삼킨다."""
        _broken_write_ctx(repo, monkeypatch)
        await repo.upsert_ohlcv([_make_ohlcv("A001", "20260414")])  # should not raise

    @pytest.mark.asyncio
    async def test_upsert_ohlcv_empty_is_noop(self, repo):
        """빈 records 전달 시 early return 된다."""
        await repo.upsert_ohlcv([])  # should not raise

    @pytest.mark.asyncio
    async def test_upsert_daily_snapshot_empty_is_noop(self, repo):
        """빈 records 전달 시 early return 된다."""
        await repo.upsert_daily_snapshot("20260414", [])  # should not raise


# ── cache_logger 경로 커버 ────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def repo_with_logger(tmp_path):
    """cache_logger가 주입된 repo."""
    db_path = str(tmp_path / "test_with_logger.db")
    mock_cache_logger = MagicMock()
    r = StockOhlcvRepository(db_path=db_path, cache_logger=mock_cache_logger)
    yield r, mock_cache_logger
    await r.close()


class TestCacheLoggerPaths:
    """cache_logger가 주입된 경우의 분기 커버."""

    @pytest.mark.asyncio
    async def test_ohlcv_miss_and_loaded_log(self, repo_with_logger):
        """DB 로드 시 miss → loaded 로그가 호출된다."""
        repo, logger = repo_with_logger
        await repo.upsert_ohlcv([_make_ohlcv("A001", "20260414")])
        await repo.get_stock_data("A001", ohlcv_limit=10)
        logger.log_ohlcv_miss.assert_called_once()
        logger.log_ohlcv_loaded.assert_called_once()

    @pytest.mark.asyncio
    async def test_ohlcv_hit_log(self, repo_with_logger):
        """캐시 히트 시 hit 로그가 호출된다."""
        repo, logger = repo_with_logger
        await repo.upsert_ohlcv([_make_ohlcv("A001", "20260414")])
        await repo.get_stock_data("A001", ohlcv_limit=1)  # DB 로드 → 캐시
        await repo.get_stock_data("A001", ohlcv_limit=1)  # 캐시 히트
        logger.log_ohlcv_hit.assert_called_once()

    @pytest.mark.asyncio
    async def test_upsert_ohlcv_invalidation_log(self, repo_with_logger):
        """upsert_ohlcv 후 캐시 무효화 로그가 호출된다."""
        repo, logger = repo_with_logger
        await repo.upsert_ohlcv([_make_ohlcv("A001", "20260414")])
        logger.log_ohlcv_invalidated.assert_called_once_with("A001")
        logger.log_ohlcv_upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_today_candle_log(self, repo_with_logger):
        """update_today_candle로 가격 변경 시 today_candle 로그가 호출된다."""
        repo, logger = repo_with_logger
        repo._ohlcv_cache.put("A001", {
            "ohlcv_historical": [{"date": "20260414", "open": 100, "high": 110, "low": 90, "close": 100, "volume": 500}],
            "ohlcv_today": None,
            "historical_complete": True,
            "last_loaded": 0,
        })
        repo.update_today_candle("A001", 120)
        logger.log_today_candle.assert_called_once()
