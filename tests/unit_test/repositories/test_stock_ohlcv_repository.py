"""
StockOhlcvRepository.get_minervini_stage2_stocks 및 rs_rating 컬럼 단위 테스트.
"""
import pytest
import pytest_asyncio

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
