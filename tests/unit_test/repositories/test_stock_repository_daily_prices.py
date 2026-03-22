"""
StockRepository daily_prices 단위 테스트.
SQLite 기반 전체 종목 일별 스냅샷(현재가+펀더멘털) 저장/조회 검증.
"""
import pytest

from repositories.stock_repository import StockRepository


@pytest.fixture
def repo(tmp_path):
    db_path = str(tmp_path / "test_stocks.db")
    r = StockRepository(db_path=db_path)
    yield r
    r.close()


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

    def test_upsert_and_get_prices(self, repo):
        records = [
            _make_record("005930", "삼성전자", "KOSPI", 70000),
            _make_record("000660", "SK하이닉스", "KOSPI", 130000),
        ]
        repo.upsert_daily_snapshot("20260318", records)

        result = repo.get_prices_by_date("20260318")
        assert len(result) == 2
        codes = {r["code"] for r in result}
        assert codes == {"005930", "000660"}

    def test_upsert_idempotent(self, repo):
        """동일 (code, date) 중복 삽입 시 최신값으로 갱신."""
        repo.upsert_daily_snapshot("20260318", [_make_record("005930", price=70000)])
        repo.upsert_daily_snapshot("20260318", [_make_record("005930", price=72000)])

        result = repo.get_prices_by_date("20260318")
        assert len(result) == 1
        assert result[0]["current_price"] == 72000

    def test_get_prices_empty_date(self, repo):
        """존재하지 않는 날짜는 빈 리스트 반환."""
        result = repo.get_prices_by_date("20200101")
        assert result == []


class TestPriceHistory:
    """종목별 이력 조회 테스트."""

    def test_get_price_history(self, repo):
        for i, date in enumerate(["20260316", "20260317", "20260318"]):
            repo.upsert_daily_snapshot(date, [_make_record("005930", price=70000 + i * 1000)])

        history = repo.get_price_history("005930", days=30)
        assert len(history) == 3
        # 최신 날짜가 먼저 (DESC)
        assert history[0]["trade_date"] == "20260318"
        assert history[0]["current_price"] == 72000

    def test_get_price_history_limit(self, repo):
        for date in ["20260314", "20260315", "20260316", "20260317", "20260318"]:
            repo.upsert_daily_snapshot(date, [_make_record("005930")])

        history = repo.get_price_history("005930", days=2)
        assert len(history) == 2

    def test_get_price_history_nonexistent(self, repo):
        result = repo.get_price_history("999999")
        assert result == []


class TestLatestTradeDate:
    """최신 거래일 반환 테스트."""

    def test_get_latest_trade_date(self, repo):
        repo.upsert_daily_snapshot("20260316", [_make_record()])
        repo.upsert_daily_snapshot("20260318", [_make_record()])
        repo.upsert_daily_snapshot("20260317", [_make_record()])

        assert repo.get_latest_trade_date() == "20260318"

    def test_get_latest_trade_date_empty(self, repo):
        assert repo.get_latest_trade_date() is None


class TestCountByDate:
    """날짜별 종목 수 조회 테스트."""

    def test_get_count_by_date(self, repo):
        records = [
            _make_record("005930"),
            _make_record("000660"),
            _make_record("035420"),
        ]
        repo.upsert_daily_snapshot("20260318", records)
        assert repo.get_count_by_date("20260318") == 3
        assert repo.get_count_by_date("20200101") == 0


class TestCleanup:
    """오래된 데이터 삭제 테스트."""

    def test_cleanup_old_data(self, repo):
        repo.upsert_daily_snapshot("20240101", [_make_record()])  # 오래된 데이터
        repo.upsert_daily_snapshot("20260318", [_make_record()])  # 최근 데이터

        repo.cleanup_old_data(keep_days=365)

        # 2024-01-01은 365일 이상 → 삭제됨
        assert repo.get_count_by_date("20240101") == 0
        assert repo.get_count_by_date("20260318") == 1


class TestUpsertEmpty:
    """빈 레코드 리스트 upsert 시 에러 없음."""

    def test_upsert_empty(self, repo):
        repo.upsert_daily_snapshot("20260318", [])
        assert repo.get_count_by_date("20260318") == 0


class TestGetLatestDailySnapshot:
    """get_latest_daily_snapshot: daily_prices → KIS API 포맷 변환 테스트."""

    def test_returns_none_when_no_data(self, repo):
        """데이터 없을 때 None 반환."""
        assert repo.get_latest_daily_snapshot("005930") is None

    def test_returns_latest_when_multiple_dates(self, repo):
        """여러 날짜가 있을 때 가장 최신 날짜 데이터 반환."""
        repo.upsert_daily_snapshot("20260316", [_make_record("005930", price=70000)])
        repo.upsert_daily_snapshot("20260318", [_make_record("005930", price=72000)])
        repo.upsert_daily_snapshot("20260317", [_make_record("005930", price=71000)])

        result = repo.get_latest_daily_snapshot("005930")
        assert result is not None
        assert result["output"]["stck_prpr"] == "72000"
        assert result["_trade_date"] == "20260318"

    def test_field_mapping(self, repo):
        """daily_prices 컬럼 → KIS API output 필드명 매핑 검증."""
        repo.upsert_daily_snapshot("20260318", [_make_record("005930", price=70000)])

        result = repo.get_latest_daily_snapshot("005930")
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

    def test_source_metadata(self, repo):
        """반환값에 _source, _trade_date 메타 포함 여부 검증."""
        repo.upsert_daily_snapshot("20260318", [_make_record("005930")])
        result = repo.get_latest_daily_snapshot("005930")
        assert result["_source"] == "daily_snapshot"
        assert result["_trade_date"] == "20260318"

    def test_handles_none_numeric_fields(self, repo):
        """nullable 숫자 컬럼이 None일 때 '0' 변환 검증."""
        record = _make_record("005930")
        record["market_cap"] = None
        record["trading_value"] = None
        record["w52_high"] = None
        repo.upsert_daily_snapshot("20260318", [record])

        result = repo.get_latest_daily_snapshot("005930")
        assert result["output"]["hts_avls"] == "0"
        assert result["output"]["acml_tr_pbmn"] == "0"
        assert result["output"]["d250_hgpr"] == "0"

    def test_handles_none_string_fields(self, repo):
        """nullable 문자 컬럼이 None일 때 빈 문자열 변환 검증."""
        record = _make_record("005930")
        record["per"] = None
        record["pbr"] = None
        repo.upsert_daily_snapshot("20260318", [record])

        result = repo.get_latest_daily_snapshot("005930")
        assert result["output"]["per"] == ""
        assert result["output"]["pbr"] == ""

    def test_different_code_isolated(self, repo):
        """다른 종목 코드로 조회 시 None 반환."""
        repo.upsert_daily_snapshot("20260318", [_make_record("005930")])
        assert repo.get_latest_daily_snapshot("000660") is None


class TestAllFields:
    """모든 필드가 정확히 저장/조회되는지 검증."""

    def test_all_fields_persisted(self, repo):
        record = _make_record("005930", "삼성전자", "KOSPI", 70000)
        repo.upsert_daily_snapshot("20260318", [record])

        result = repo.get_prices_by_date("20260318")
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
