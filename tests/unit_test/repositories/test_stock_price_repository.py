"""
StockRepository daily_prices 단위 테스트.
SQLite 기반 전체 종목 일별 스냅샷(현재가+펀더멘털) 저장/조회 검증.
"""
import time
import pytest
import pytest_asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from repositories.stock_repository import StockRepository
from repositories.stock_price_repository import StockPriceRepository


@pytest_asyncio.fixture
async def repo(tmp_path):
    db_path = str(tmp_path / "test_stocks.db")
    r = StockRepository(db_path=db_path)
    yield r
    await r.close()


def _make_record(code="005930", name="삼성전자", market="KOSPI", price=70000):
    return {
        "code": code,
        "name": name,
        "current_price": price,
        "open_price": 69000,
        "high_price": 71000,
        "low_price": 68500,
        "prev_close": 69500,
        "change_price": 500,
        "change_sign": "2",
        "change_rate": "0.72",
        "volume": 10000000,
        "trading_value": 700000000000,
        "market_cap": 4200000000000000,
        "per": 12.5,
        "pbr": 1.3,
        "eps": 5600.0,
        "w52_high": 80000,
        "w52_low": 55000,
        "market": market,
    }


class TestUpsertAndGet:
    """upsert 후 날짜별 조회 테스트."""

    @pytest.mark.asyncio
    async def test_upsert_and_get_prices(self, repo):
        records = [
            _make_record("005930", "삼성전자", "KOSPI", 70000),
            _make_record("000660", "SK하이닉스", "KOSPI", 130000),
        ]
        await repo.upsert_daily_snapshot("20260318", records)

        result = await repo.get_prices_by_date("20260318")
        assert len(result) == 2
        codes = {r["code"] for r in result}
        assert codes == {"005930", "000660"}

    @pytest.mark.asyncio
    async def test_upsert_idempotent(self, repo):
        """동일 (code, date) 중복 삽입 시 최신값으로 갱신."""
        await repo.upsert_daily_snapshot("20260318", [_make_record("005930", price=70000)])
        await repo.upsert_daily_snapshot("20260318", [_make_record("005930", price=72000)])

        result = await repo.get_prices_by_date("20260318")
        assert len(result) == 1
        assert result[0]["current_price"] == 72000

    @pytest.mark.asyncio
    async def test_get_prices_empty_date(self, repo):
        """존재하지 않는 날짜는 빈 리스트 반환."""
        result = await repo.get_prices_by_date("20200101")
        assert result == []


class TestPriceHistory:
    """종목별 이력 조회 테스트."""

    @pytest.mark.asyncio
    async def test_get_price_history(self, repo):
        for i, date in enumerate(["20260316", "20260317", "20260318"]):
            await repo.upsert_daily_snapshot(date, [_make_record("005930", price=70000 + i * 1000)])

        history = await repo.get_price_history("005930", days=30)
        assert len(history) == 3
        # 최신 날짜가 먼저 (DESC)
        assert history[0]["trade_date"] == "20260318"
        assert history[0]["current_price"] == 72000

    @pytest.mark.asyncio
    async def test_get_price_history_limit(self, repo):
        for date in ["20260314", "20260315", "20260316", "20260317", "20260318"]:
            await repo.upsert_daily_snapshot(date, [_make_record("005930")])

        history = await repo.get_price_history("005930", days=2)
        assert len(history) == 2

    @pytest.mark.asyncio
    async def test_get_price_history_nonexistent(self, repo):
        result = await repo.get_price_history("999999")
        assert result == []


class TestLatestTradeDate:
    """최신 거래일 반환 테스트."""

    @pytest.mark.asyncio
    async def test_get_latest_trade_date(self, repo):
        await repo.upsert_daily_snapshot("20260316", [_make_record()])
        await repo.upsert_daily_snapshot("20260318", [_make_record()])
        await repo.upsert_daily_snapshot("20260317", [_make_record()])

        assert await repo.get_latest_trade_date() == "20260318"

    @pytest.mark.asyncio
    async def test_get_latest_trade_date_empty(self, repo):
        assert await repo.get_latest_trade_date() is None


class TestCountByDate:
    """날짜별 종목 수 조회 테스트."""

    @pytest.mark.asyncio
    async def test_get_count_by_date(self, repo):
        records = [
            _make_record("005930"),
            _make_record("000660"),
            _make_record("035420"),
        ]
        await repo.upsert_daily_snapshot("20260318", records)
        assert await repo.get_count_by_date("20260318") == 3
        assert await repo.get_count_by_date("20200101") == 0


class TestMinerviniStageCount:
    """minervini_stage 계산 완료 여부 확인 테스트."""

    @pytest.mark.asyncio
    async def test_returns_count_of_staged_records(self, repo):
        """minervini_stage가 설정된 종목 수를 반환한다."""
        records = [
            {**_make_record("005930"), "minervini_stage": 2, "minervini_reason": "트렌드 충족"},
            {**_make_record("000660"), "minervini_stage": 1, "minervini_reason": "횡보"},
            {**_make_record("035420"), "minervini_stage": 4, "minervini_reason": "하락"},
        ]
        await repo.upsert_daily_snapshot("20260318", records)
        assert await repo.get_minervini_stage_count("20260318") == 3

    @pytest.mark.asyncio
    async def test_returns_zero_for_missing_date(self, repo):
        """데이터가 없는 날짜는 0을 반환한다."""
        assert await repo.get_minervini_stage_count("20200101") == 0

    @pytest.mark.asyncio
    async def test_date_isolation(self, repo):
        """다른 날짜의 stage 데이터는 카운트에 포함되지 않는다."""
        await repo.upsert_daily_snapshot("20260317", [
            {**_make_record("005930"), "minervini_stage": 2},
            {**_make_record("000660"), "minervini_stage": 1},
        ])
        await repo.upsert_daily_snapshot("20260318", [
            {**_make_record("035420"), "minervini_stage": 2},
        ])

        assert await repo.get_minervini_stage_count("20260318") == 1
        assert await repo.get_minervini_stage_count("20260317") == 2


class TestCleanup:
    """오래된 데이터 삭제 테스트."""

    @pytest.mark.asyncio
    async def test_cleanup_old_data(self, repo):
        await repo.upsert_daily_snapshot("20240101", [_make_record()])  # 오래된 데이터
        await repo.upsert_daily_snapshot("20260318", [_make_record()])  # 최근 데이터

        await repo.cleanup_old_data(keep_days=365)

        # 2024-01-01은 365일 이상 → 삭제됨
        assert await repo.get_count_by_date("20240101") == 0
        assert await repo.get_count_by_date("20260318") == 1


class TestUpsertEmpty:
    """빈 레코드 리스트 upsert 시 에러 없음."""

    @pytest.mark.asyncio
    async def test_upsert_empty(self, repo):
        await repo.upsert_daily_snapshot("20260318", [])
        assert await repo.get_count_by_date("20260318") == 0


class TestGetLatestDailySnapshot:
    """get_latest_daily_snapshot: daily_prices → KIS API 포맷 변환 테스트."""

    @pytest.mark.asyncio
    async def test_returns_none_when_no_data(self, repo):
        """데이터 없을 때 None 반환."""
        assert await repo.get_latest_daily_snapshot("005930") is None

    @pytest.mark.asyncio
    async def test_returns_latest_when_multiple_dates(self, repo):
        """여러 날짜가 있을 때 가장 최신 날짜 데이터 반환."""
        await repo.upsert_daily_snapshot("20260316", [_make_record("005930", price=70000)])
        await repo.upsert_daily_snapshot("20260318", [_make_record("005930", price=72000)])
        await repo.upsert_daily_snapshot("20260317", [_make_record("005930", price=71000)])

        result = await repo.get_latest_daily_snapshot("005930")
        assert result is not None
        assert result["output"]["stck_prpr"] == "72000"
        assert result["_trade_date"] == "20260318"

    @pytest.mark.asyncio
    async def test_field_mapping(self, repo):
        """daily_prices 컬럼 → KIS API output 필드명 매핑 검증."""
        await repo.upsert_daily_snapshot("20260318", [_make_record("005930", price=70000)])

        result = await repo.get_latest_daily_snapshot("005930")
        output = result["output"]
        assert output["stck_prpr"] == "70000"          # current_price
        assert output["stck_oprc"] == "69000"          # open_price
        assert output["stck_hgpr"] == "71000"          # high_price
        assert output["stck_lwpr"] == "68500"          # low_price
        assert output["stck_sdpr"] == "69500"          # prev_close
        assert output["prdy_vrss"] == "500"            # change_price
        assert output["prdy_vrss_sign"] == "2"         # change_sign
        assert output["prdy_ctrt"] == "0.72"           # change_rate
        assert output["acml_vol"] == "10000000"        # volume
        assert output["acml_tr_pbmn"] == "700000000000"  # trading_value
        assert output["d250_hgpr"] == "80000"          # w52_high
        assert output["d250_lwpr"] == "55000"          # w52_low
        assert output["hts_kor_isnm"] == "삼성전자"    # name
        assert output["stck_bsop_date"] == "20260318"  # trade_date

    @pytest.mark.asyncio
    async def test_source_metadata(self, repo):
        """반환값에 _source, _trade_date 메타 포함 여부 검증."""
        await repo.upsert_daily_snapshot("20260318", [_make_record("005930")])
        result = await repo.get_latest_daily_snapshot("005930")
        assert result["_source"] == "daily_snapshot"
        assert result["_trade_date"] == "20260318"

    @pytest.mark.asyncio
    async def test_handles_none_numeric_fields(self, repo):
        """nullable 숫자 컬럼이 None일 때 '0' 변환 검증."""
        record = _make_record("005930")
        record["market_cap"] = None
        record["trading_value"] = None
        record["w52_high"] = None
        await repo.upsert_daily_snapshot("20260318", [record])

        result = await repo.get_latest_daily_snapshot("005930")
        assert result["output"]["hts_avls"] == "0"
        assert result["output"]["acml_tr_pbmn"] == "0"
        assert result["output"]["d250_hgpr"] == "0"

    @pytest.mark.asyncio
    async def test_handles_none_string_fields(self, repo):
        """nullable 문자 컬럼이 None일 때 빈 문자열 변환 검증."""
        record = _make_record("005930")
        record["per"] = None
        record["pbr"] = None
        await repo.upsert_daily_snapshot("20260318", [record])

        result = await repo.get_latest_daily_snapshot("005930")
        assert result["output"]["per"] == ""
        assert result["output"]["pbr"] == ""

    @pytest.mark.asyncio
    async def test_different_code_isolated(self, repo):
        """다른 종목 코드로 조회 시 None 반환."""
        await repo.upsert_daily_snapshot("20260318", [_make_record("005930")])
        assert await repo.get_latest_daily_snapshot("000660") is None


class TestAllFields:
    """모든 필드가 정확히 저장/조회되는지 검증."""

    @pytest.mark.asyncio
    async def test_all_fields_persisted(self, repo):
        record = _make_record("005930", "삼성전자", "KOSPI", 70000)
        await repo.upsert_daily_snapshot("20260318", [record])

        result = await repo.get_prices_by_date("20260318")
        assert len(result) == 1
        r = result[0]
        assert r["code"] == "005930"
        assert r["name"] == "삼성전자"
        assert r["current_price"] == 70000
        assert r["open_price"] == 69000
        assert r["high_price"] == 71000
        assert r["low_price"] == 68500
        assert r["prev_close"] == 69500
        assert r["change_price"] == 500
        assert r["change_sign"] == "2"
        assert r["change_rate"] == "0.72"
        assert r["volume"] == 10000000
        assert r["trading_value"] == 700000000000
        assert r["market_cap"] == 4200000000000000
        assert r["per"] == 12.5
        assert r["pbr"] == 1.3
        assert r["eps"] == 5600.0
        assert r["w52_high"] == 80000
        assert r["w52_low"] == 55000
        assert r["market"] == "KOSPI"
        assert r["collected_at"] is not None


@pytest.fixture
def price_repo():
    cache_logger = MagicMock()
    return StockPriceRepository(logger=MagicMock(), cache_logger=cache_logger)


class TestStockPriceRepository:
    """StockPriceRepository 현재가 캐시/streaming/통계 테스트."""

    def test_on_price_evicted_logs(self, price_repo):
        price_repo._on_price_evicted("005930")
        price_repo._cache_logger.log_price_evicted.assert_called_once_with(
            "005930", capacity=price_repo._price_cache.capacity
        )

    def test_set_current_price_new_and_get_hit(self, price_repo):
        with patch("repositories.stock_price_repository.time.time", return_value=100.0):
            price_repo.set_current_price("005930", {"output": {"stck_prpr": "70000"}})

        with patch("repositories.stock_price_repository.time.time", return_value=101.0):
            result = price_repo.get_current_price("005930", caller="tester")

        assert result == {"output": {"stck_prpr": "70000"}}
        price_repo._cache_logger.log_price_set.assert_called_once_with("005930", "api", None, "70000", True)
        price_repo._cache_logger.log_price_hit.assert_called_once()

    def test_set_current_price_existing_cached_object_output(self, price_repo):
        existing_output = SimpleNamespace(stck_prpr="68000")
        price_repo._price_cache.put("005930", {"current_price_data": {"output": existing_output}, "price_updated_at": 1})

        with patch("repositories.stock_price_repository.time.time", return_value=200.0):
            price_repo.set_current_price("005930", {"stck_prpr": "71000"})

        cached = price_repo._price_cache.get("005930", count_stats=False, item_type="check")
        assert cached["current_price_data"] == {"stck_prpr": "71000"}
        price_repo._cache_logger.log_price_set.assert_called_once_with("005930", "api", "68000", "71000", False)

    def test_get_current_price_ttl_expired_and_not_found(self, price_repo):
        price_repo.set_current_price("005930", {"output": {"stck_prpr": "70000"}})
        with patch("repositories.stock_price_repository.time.time", return_value=time.time() + 10):
            assert price_repo.get_current_price("005930", max_age_sec=3.0, caller="ttl") is None

        assert price_repo.get_current_price("000660", caller="miss") is None
        reasons = [call.args[2] for call in price_repo._cache_logger.log_price_miss.call_args_list]
        assert "ttl_expired" in reasons
        assert "not_found" in reasons

    def test_get_current_price_streaming_bypasses_ttl(self, price_repo):
        with patch("repositories.stock_price_repository.time.time", return_value=100.0):
            price_repo.set_current_price("005930", {"output": {"stck_prpr": "70000"}})
        price_repo.mark_streaming("005930")

        with patch("repositories.stock_price_repository.time.time", return_value=10000.0):
            result = price_repo.get_current_price("005930", max_age_sec=0.1, caller="streaming")

        assert result == {"output": {"stck_prpr": "70000"}}
        assert price_repo.is_streaming("005930") is True

    def test_get_current_price_no_stats_no_cache_logger_calls(self, price_repo):
        with patch("repositories.stock_price_repository.time.time", return_value=100.0):
            price_repo.set_current_price("005930", {"output": {"stck_prpr": "70000"}})
        price_repo._cache_logger.reset_mock()

        with patch("repositories.stock_price_repository.time.time", return_value=101.0):
            result = price_repo.get_current_price("005930", caller="nostats", count_stats=False)

        assert result == {"output": {"stck_prpr": "70000"}}
        price_repo._cache_logger.log_price_hit.assert_not_called()
        price_repo._cache_logger.log_price_miss.assert_not_called()

    def test_update_current_price_creates_tick_cache(self, price_repo):
        with patch("repositories.stock_price_repository.time.time", return_value=123.0):
            price_repo.update_current_price("005930", 70123, volume=55)

        cached = price_repo._price_cache.get("005930", count_stats=False, item_type="check")
        assert cached["current_price_data"]["_source"] == "tick"
        assert cached["current_price_data"]["output"]["stck_prpr"] == "70123"
        assert cached["current_price_data"]["output"]["acml_vol"] == "55"

    def test_update_current_price_updates_dict_output_and_logs(self, price_repo):
        price_repo._price_cache.put(
            "005930",
            {"current_price_data": {"output": {"stck_prpr": "70000", "acml_vol": "1"}}, "price_updated_at": 1},
        )

        with patch("repositories.stock_price_repository.time.time", return_value=200.0):
            price_repo.update_current_price("005930", 71000, volume=99)

        cached = price_repo._price_cache.get("005930", count_stats=False, item_type="check")
        assert cached["current_price_data"]["output"]["stck_prpr"] == "71000"
        assert cached["current_price_data"]["output"]["acml_vol"] == "99"
        price_repo._cache_logger.log_price_update_tick.assert_called_once_with("005930", "70000", "71000", 99)

    def test_update_current_price_updates_object_output_and_swallow_exception(self, price_repo):
        output = SimpleNamespace(stck_prpr="70000", acml_vol="1")
        price_repo._price_cache.put(
            "005930",
            {"current_price_data": {"output": output}, "price_updated_at": 1},
        )

        with patch("repositories.stock_price_repository.time.time", return_value=200.0):
            price_repo.update_current_price("005930", 72000, volume=0)

        assert output.stck_prpr == "72000"
        assert output.acml_vol == "1"

        class BrokenOutput:
            def __init__(self):
                self.stck_prpr = "72000"

            def __setattr__(self, name, value):
                if name != "stck_prpr":
                    raise RuntimeError("boom")
                object.__setattr__(self, name, value)

        broken = BrokenOutput()
        price_repo._price_cache.put(
            "000660",
            {"current_price_data": {"output": broken}, "price_updated_at": 1},
        )
        with patch("repositories.stock_price_repository.time.time", return_value=300.0):
            price_repo.update_current_price("000660", 73000, volume=10)

        assert broken.stck_prpr == "73000"

    def test_mark_unmark_streaming_and_cache_stats_expand(self, price_repo):
        price_repo.set_current_price("005930", {"output": {"stck_prpr": "70000"}})
        price_repo.mark_streaming("005930")
        expanded = price_repo.get_cache_stats(expand=True)
        assert expanded["streaming_count"] == 1
        assert expanded["items"][0]["is_streaming"] is True

        compact = price_repo.get_cache_stats(expand=False)
        assert compact["streaming_count"] == 1

        price_repo.unmark_streaming("005930")
        assert price_repo.is_streaming("005930") is False
        price_repo._cache_logger.log_streaming_mark.assert_called_once()
        price_repo._cache_logger.log_streaming_unmark.assert_called_once()
