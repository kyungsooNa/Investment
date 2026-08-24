"""
FavoriteService 단위 테스트.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock
from common.types import ResCommonResponse
from repositories.favorite_repository import MARKET_DOMESTIC, MARKET_OVERSEAS_US
from services.favorite_service import FavoriteService, _extract_price_rate


class DummyOutput:
    """_extract_price_rate 테스트용 더미 클래스"""
    def __init__(self, stck_prpr, prdy_ctrt):
        self.stck_prpr = stck_prpr
        self.prdy_ctrt = prdy_ctrt

@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.get_all = AsyncMock(return_value=[])
    repo.add = AsyncMock(return_value=True)
    repo.remove = AsyncMock(return_value=True)
    repo.is_favorite = AsyncMock(return_value=False)
    return repo


@pytest.fixture
def mock_stock_repo():
    repo = MagicMock()
    repo.get_current_price.return_value = None
    repo.get_latest_daily_snapshot = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def mock_stock_code_repo():
    r = MagicMock()
    r.get_name_by_code.return_value = "삼성전자"
    return r


@pytest.fixture
def service(mock_repo, mock_stock_code_repo):
    return FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
    )


async def test_get_all_delegates(service, mock_repo):
    mock_repo.get_all.return_value = ["005930"]
    assert await service.get_all() == ["005930"]
    mock_repo.get_all.assert_called_once_with(market="domestic")


async def test_get_all_normalizes_domestic_codes_for_realtime_subscription(service, mock_repo):
    mock_repo.get_all.return_value = ["5930", "000660"]

    assert await service.get_all() == ["005930", "000660"]


async def test_add_overseas_passes_market(service, mock_repo):
    assert await service.add("AAPL", market="overseas_us") is True
    mock_repo.add.assert_called_once_with("AAPL", market="overseas_us")


async def test_add_overseas_normalizes_symbol_to_uppercase(service, mock_repo):
    assert await service.add("aapl", market="overseas_us") is True
    mock_repo.add.assert_called_once_with("AAPL", market="overseas_us")


async def test_add_delegates(service, mock_repo):
    assert await service.add("5930") is True
    mock_repo.add.assert_called_once_with("005930", market="domestic")


async def test_add_duplicate_returns_false(service, mock_repo):
    mock_repo.add.return_value = False
    assert await service.add("005930") is False


async def test_remove_delegates(service, mock_repo):
    assert await service.remove("005930") is True
    mock_repo.remove.assert_called_once_with("005930", market="domestic")


async def test_remove_falls_back_to_legacy_unpadded_code(service, mock_repo):
    mock_repo.remove.side_effect = [False, True]

    assert await service.remove("5930") is True
    assert mock_repo.remove.await_args_list[0].args == ("005930",)
    assert mock_repo.remove.await_args_list[1].args == ("5930",)


async def test_is_favorite_delegates(service, mock_repo):
    mock_repo.is_favorite.return_value = True
    assert await service.is_favorite("005930") is True
    mock_repo.is_favorite.assert_called_once_with("005930", market="domestic")


async def test_is_favorite_checks_legacy_unpadded_code(service, mock_repo):
    mock_repo.is_favorite.side_effect = [False, True]

    assert await service.is_favorite("5930") is True
    assert mock_repo.is_favorite.await_args_list[0].args == ("005930",)
    assert mock_repo.is_favorite.await_args_list[1].args == ("5930",)


async def test_get_with_details_empty(service, mock_repo):
    mock_repo.get_all.return_value = []
    result = await service.get_with_details()
    assert result == []


async def test_get_with_details_no_query_service(service, mock_repo, mock_stock_code_repo):
    mock_repo.get_all.return_value = ["005930"]
    result = await service.get_with_details()
    assert len(result) == 1
    assert result[0]["code"] == "005930"
    assert result[0]["name"] == "삼성전자"
    assert result[0]["price"] is None
    assert result[0]["rate"] is None


async def test_get_with_details_with_price(mock_repo, mock_stock_code_repo):
    mock_repo.get_all.return_value = ["005930"]
    mock_query = AsyncMock()
    mock_query.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK",
        data={"output": {"stck_shrn_iscd": "005930", "stck_prpr": "75000", "prdy_ctrt": "1.5"}},
    )
    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
        stock_query_service=mock_query,
    )
    result = await svc.get_with_details()
    assert result[0]["price"] == "75000"
    assert result[0]["rate"] == "1.5"


async def test_get_with_details_prefers_stock_query_over_stale_repository_cache(
    mock_repo, mock_stock_code_repo, mock_stock_repo
):
    mock_repo.get_all.return_value = ["005935"]
    mock_stock_repo.get_current_price.return_value = {
        "output": {"stck_shrn_iscd": "005935", "stck_prpr": "205000", "prdy_ctrt": "7.22"}
    }
    mock_query = AsyncMock()
    mock_query.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK",
        data={"output": {"stck_shrn_iscd": "005935", "stck_prpr": "206500", "prdy_ctrt": "8.01"}},
    )

    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
        stock_query_service=mock_query,
        stock_repository=mock_stock_repo,
    )

    result = await svc.get_with_details()

    assert result[0]["price"] == "206500"
    assert result[0]["rate"] == "8.01"
    mock_query.get_current_price.assert_awaited_once_with(
        "005935", count_stats=False, caller="FavoriteService"
    )
    mock_stock_repo.get_current_price.assert_not_called()


async def test_get_with_details_price_api_failure(mock_repo, mock_stock_code_repo):
    """stock_query_service 예외 발생 시 price=None으로 graceful degradation."""
    mock_repo.get_all.return_value = ["005930"]
    mock_query = AsyncMock()
    mock_query.get_current_price.side_effect = Exception("API error")
    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
        stock_query_service=mock_query,
    )
    result = await svc.get_with_details()
    assert len(result) == 1
    assert result[0]["price"] is None


def test_extract_price_rate_dict_nested():
    data = {"output": {"stck_prpr": "1000", "prdy_ctrt": "1.0"}}
    assert _extract_price_rate(data) == ("1000", "1.0")


def test_extract_price_rate_dict_flat():
    data = {"stck_prpr": "2000", "prdy_ctrt": "2.0"}
    assert _extract_price_rate(data) == ("2000", "2.0")


def test_extract_price_rate_dict_output_is_dataclass():
    data = {"output": DummyOutput("3000", "3.0")}
    assert _extract_price_rate(data) == ("3000", "3.0")


def test_extract_price_rate_dataclass():
    data = DummyOutput("4000", "4.0")
    assert _extract_price_rate(data) == ("4000", "4.0")


async def test_get_with_details_step1_memory_cache_hit(mock_repo, mock_stock_code_repo, mock_stock_repo):
    mock_repo.get_all.return_value = ["005930"]
    mock_stock_repo.get_current_price.return_value = {"output": {"stck_prpr": "70000", "prdy_ctrt": "2.5"}}
    
    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
        stock_repository=mock_stock_repo,
    )
    result = await svc.get_with_details()
    
    assert len(result) == 1
    assert result[0]["price"] == "70000"
    assert result[0]["rate"] == "2.5"
    mock_stock_repo.get_current_price.assert_called_once_with("005930", max_age_sec=3.0, count_stats=False)
    mock_stock_repo.get_latest_daily_snapshot.assert_not_called()


async def test_get_with_details_step2_db_snapshot_hit(mock_repo, mock_stock_code_repo, mock_stock_repo):
    mock_repo.get_all.return_value = ["005930"]
    mock_stock_repo.get_current_price.return_value = None
    mock_stock_repo.get_latest_daily_snapshot.return_value = {"stck_prpr": "71000", "prdy_ctrt": "1.2"}
    
    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
        stock_repository=mock_stock_repo,
    )
    result = await svc.get_with_details()
    
    assert len(result) == 1
    assert result[0]["price"] == "71000"
    assert result[0]["rate"] == "1.2"
    mock_stock_repo.get_latest_daily_snapshot.assert_called_once_with("005930")


async def test_get_with_details_step2_exception(mock_repo, mock_stock_code_repo, mock_stock_repo):
    mock_repo.get_all.return_value = ["005930"]
    mock_stock_repo.get_current_price.return_value = None
    mock_stock_repo.get_latest_daily_snapshot.side_effect = Exception("DB Error")
    
    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
        stock_repository=mock_stock_repo,
    )
    result = await svc.get_with_details()
    
    assert len(result) == 1
    assert result[0]["price"] is None
    assert result[0]["rate"] is None


async def test_get_with_details_step3_api_rt_cd_not_0(mock_repo, mock_stock_code_repo):
    mock_repo.get_all.return_value = ["005930"]
    mock_query = AsyncMock()
    mock_query.get_current_price.return_value = ResCommonResponse(
        rt_cd="1", msg1="Error", data=None
    )
    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
        stock_query_service=mock_query,
    )
    result = await svc.get_with_details()
    
    assert len(result) == 1
    assert result[0]["price"] is None
    assert result[0]["rate"] is None


@pytest.mark.asyncio
async def test_get_with_details_with_rs_rating(mock_repo, mock_stock_code_repo):
    """RS rating 서비스 응답을 병합하는지 검증"""
    mock_repo.get_all.return_value = ["005930"]
    mock_rs = AsyncMock()
    mock_rs.get_rating.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=MagicMock(rs_rating=85))

    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
        rs_rating_service=mock_rs,
    )

    result = await svc.get_with_details()
    assert len(result) == 1
    assert result[0]["rs_rating"] == 85


@pytest.mark.asyncio
async def test_get_with_details_minervini_stage_various(mock_repo, mock_stock_code_repo):
    """Minervini 서비스에서 튜플/정수 반환을 모두 처리하는지 검증"""
    mock_repo.get_all.return_value = ["005930", "000660"]

    ms = MagicMock()
    async def _get_stage_a(code):
        return (2, "reason") if code == "005930" else 0

    ms.get_stage_for_code = AsyncMock(side_effect=_get_stage_a)

    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
    )
    # attach service dynamically as attribute
    svc.minervini_stage_service = ms

    result = await svc.get_with_details()
    # for 005930 stage should be 2, for 000660 should remain None/0 (no positive stage)
    mapping = {r["code"]: r for r in result}
    assert mapping["005930"]["minervini_stage"] == 2
    assert mapping["000660"]["minervini_stage"] in (None, 0)


@pytest.fixture
def mock_overseas_code_repo():
    r = MagicMock()
    r.get_meta.return_value = {"name": "Apple Inc.", "exchange": "NASD"}
    return r


class DummyOverseasSummary:
    def __init__(self, price, change_rate):
        self.price = price
        self.change_rate = change_rate


async def test_get_with_details_overseas_uses_overseas_price(
    mock_repo, mock_stock_code_repo, mock_overseas_code_repo
):
    """미국장 목록은 해외 현재가 API와 해외 심볼 메타를 사용한다."""
    mock_repo.get_all.return_value = ["AAPL"]
    mock_query = AsyncMock()
    mock_query.get_overseas_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="정상", data=DummyOverseasSummary(190.5, 1.23)
    )

    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
        stock_query_service=mock_query,
        overseas_stock_code_repository=mock_overseas_code_repo,
    )

    result = await svc.get_with_details(market="overseas_us")

    mock_repo.get_all.assert_called_once_with(market="overseas_us")
    mock_query.get_overseas_price.assert_awaited_once_with("AAPL", exchange="NASD")
    mock_query.get_current_price.assert_not_awaited()
    assert result == [
        {
            "code": "AAPL",
            "name": "Apple Inc.",
            "exchange": "NASD",
            "price": 190.5,
            "rate": 1.23,
            "rs_rating": None,
            "minervini_stage": None,
        }
    ]


async def test_get_with_details_overseas_normalizes_symbol_case(
    mock_repo, mock_stock_code_repo, mock_overseas_code_repo
):
    mock_repo.get_all.return_value = ["aapl"]
    mock_query = AsyncMock()
    mock_query.get_overseas_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="정상", data=DummyOverseasSummary(190.5, 1.23)
    )

    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
        stock_query_service=mock_query,
        overseas_stock_code_repository=mock_overseas_code_repo,
    )

    result = await svc.get_with_details(market="overseas_us")

    mock_overseas_code_repo.get_meta.assert_called_once_with("AAPL")
    mock_query.get_overseas_price.assert_awaited_once_with("AAPL", exchange="NASD")
    assert result[0]["code"] == "AAPL"


async def test_get_with_details_overseas_without_meta_falls_back(
    mock_repo, mock_stock_code_repo
):
    """심볼 메타가 없으면 심볼명 그대로, 거래소는 NASD 기본값을 쓴다."""
    mock_repo.get_all.return_value = ["ZZZZ"]
    overseas_repo = MagicMock()
    overseas_repo.get_meta.return_value = None

    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
        overseas_stock_code_repository=overseas_repo,
    )

    result = await svc.get_with_details(market="overseas_us")

    assert result[0]["name"] == "ZZZZ"
    assert result[0]["exchange"] == "NASD"
    assert result[0]["price"] is None


async def test_get_with_details_overseas_price_failure_keeps_row(
    mock_repo, mock_stock_code_repo, mock_overseas_code_repo
):
    """해외 시세 조회가 실패해도 종목 행은 유지된다."""
    mock_repo.get_all.return_value = ["AAPL"]
    mock_query = AsyncMock()
    mock_query.get_overseas_price.side_effect = RuntimeError("boom")

    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
        stock_query_service=mock_query,
        overseas_stock_code_repository=mock_overseas_code_repo,
    )

    result = await svc.get_with_details(market="overseas_us")

    assert len(result) == 1
    assert result[0]["price"] is None
    assert result[0]["rate"] is None


async def test_get_with_details_overseas_empty(mock_repo, mock_stock_code_repo):
    mock_repo.get_all.return_value = []
    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
    )
    assert await svc.get_with_details(market="overseas_us") == []


async def test_get_with_details_falls_back_when_query_service_returns_empty_price(
    mock_repo, mock_stock_code_repo, mock_stock_repo
):
    """1단계 응답이 rt_cd=0이어도 현재가가 비어 있으면 다음 단계로 폴백해야 한다."""
    mock_repo.get_all.return_value = ["005930"]
    mock_query = AsyncMock()
    mock_query.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="성공", data={"output": {"stck_prpr": "", "prdy_ctrt": ""}}
    )
    mock_stock_repo.get_current_price.return_value = {
        "output": {"stck_prpr": "70000", "prdy_ctrt": "2.5"}
    }

    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
        stock_query_service=mock_query,
        stock_repository=mock_stock_repo,
    )
    result = await svc.get_with_details()

    assert result[0]["price"] == "70000"
    assert result[0]["rate"] == "2.5"


async def test_get_with_details_keeps_price_but_retries_when_rate_missing(
    mock_repo, mock_stock_code_repo, mock_stock_repo
):
    """등락률이 없는 불완전 응답은 다음 단계에서 완전한 값으로 대체되어야 한다."""
    mock_repo.get_all.return_value = ["005930"]
    mock_query = AsyncMock()
    mock_query.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="성공", data={"output": {"stck_prpr": "69000"}}
    )
    mock_stock_repo.get_current_price.return_value = {
        "output": {"stck_prpr": "70000", "prdy_ctrt": "2.5"}
    }

    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
        stock_query_service=mock_query,
        stock_repository=mock_stock_repo,
    )
    result = await svc.get_with_details()

    assert result[0]["price"] == "70000"
    assert result[0]["rate"] == "2.5"


async def test_get_with_details_skips_outdated_daily_snapshot(
    mock_repo, mock_stock_code_repo, mock_stock_repo
):
    """최근 거래일보다 오래된 일봉 스냅샷은 현재가/등락률로 쓰지 않는다."""
    mock_repo.get_all.return_value = ["005930"]
    mock_stock_repo.get_current_price.return_value = None
    mock_stock_repo.get_latest_daily_snapshot.return_value = {
        "output": {"stck_prpr": "71000", "prdy_ctrt": "1.2"},
        "_trade_date": "20260814",
    }
    mock_mcs = MagicMock()
    mock_mcs.get_latest_trading_date = AsyncMock(return_value="20260821")

    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
        stock_repository=mock_stock_repo,
        market_calendar_service=mock_mcs,
    )
    result = await svc.get_with_details()

    assert result[0]["price"] is None
    assert result[0]["rate"] is None


async def test_get_with_details_uses_daily_snapshot_of_latest_trading_date(
    mock_repo, mock_stock_code_repo, mock_stock_repo
):
    """최근 거래일 일봉 스냅샷은 그대로 사용한다."""
    mock_repo.get_all.return_value = ["005930"]
    mock_stock_repo.get_current_price.return_value = None
    mock_stock_repo.get_latest_daily_snapshot.return_value = {
        "output": {"stck_prpr": "71000", "prdy_ctrt": "1.2"},
        "_trade_date": "20260821",
    }
    mock_mcs = MagicMock()
    mock_mcs.get_latest_trading_date = AsyncMock(return_value="20260821")

    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
        stock_repository=mock_stock_repo,
        market_calendar_service=mock_mcs,
    )
    result = await svc.get_with_details()

    assert result[0]["price"] == "71000"
    assert result[0]["rate"] == "1.2"


def _mcs(latest_trading_date="20260821", market_open=False):
    mcs = MagicMock()
    mcs.get_latest_trading_date = AsyncMock(return_value=latest_trading_date)
    mcs.is_market_open_now = AsyncMock(return_value=market_open)
    return mcs


async def test_get_with_details_prefers_daily_snapshot_when_market_closed(
    mock_repo, mock_stock_code_repo, mock_stock_repo
):
    """장 시작 전·비거래일에는 라이브 0.00% 대신 직전 세션 일봉 등락률을 쓴다."""
    mock_repo.get_all.return_value = ["005930"]
    mock_query = AsyncMock()
    # 장전 KIS 현재가 API 응답: 전일 종가 + 등락률 0.00
    mock_query.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="성공", data={"output": {"stck_prpr": "71000", "prdy_ctrt": "0.00"}}
    )
    mock_stock_repo.get_latest_daily_snapshot.return_value = {
        "output": {"stck_prpr": "71000", "prdy_ctrt": "4.03"},
        "_trade_date": "20260821",
    }

    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
        stock_query_service=mock_query,
        stock_repository=mock_stock_repo,
        market_calendar_service=_mcs(market_open=False),
    )
    result = await svc.get_with_details()

    assert result[0]["price"] == "71000"
    assert result[0]["rate"] == "4.03"
    mock_query.get_current_price.assert_not_called()


async def test_get_with_details_prefers_live_price_while_market_open(
    mock_repo, mock_stock_code_repo, mock_stock_repo
):
    """장중에는 일봉 스냅샷이 있어도 라이브 현재가를 우선한다."""
    mock_repo.get_all.return_value = ["005930"]
    mock_query = AsyncMock()
    mock_query.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="성공", data={"output": {"stck_prpr": "72500", "prdy_ctrt": "2.11"}}
    )
    mock_stock_repo.get_latest_daily_snapshot.return_value = {
        "output": {"stck_prpr": "71000", "prdy_ctrt": "4.03"},
        "_trade_date": "20260821",
    }

    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
        stock_query_service=mock_query,
        stock_repository=mock_stock_repo,
        market_calendar_service=_mcs(market_open=True),
    )
    result = await svc.get_with_details()

    assert result[0]["price"] == "72500"
    assert result[0]["rate"] == "2.11"
    mock_stock_repo.get_latest_daily_snapshot.assert_not_called()


async def test_get_with_details_falls_back_to_live_when_market_closed_and_no_snapshot(
    mock_repo, mock_stock_code_repo, mock_stock_repo
):
    """장이 닫혀 있어도 최근 거래일 스냅샷이 없으면 라이브 값으로 폴백한다."""
    mock_repo.get_all.return_value = ["005930"]
    mock_query = AsyncMock()
    mock_query.get_current_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="성공", data={"output": {"stck_prpr": "71000", "prdy_ctrt": "0.00"}}
    )
    mock_stock_repo.get_latest_daily_snapshot.return_value = None

    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
        stock_query_service=mock_query,
        stock_repository=mock_stock_repo,
        market_calendar_service=_mcs(market_open=False),
    )
    result = await svc.get_with_details()

    assert result[0]["price"] == "71000"
    assert result[0]["rate"] == "0.00"
    mock_query.get_current_price.assert_called_once()


# --- 해외 심볼 정규화 경로 --------------------------------------------------


async def test_get_all_normalizes_and_dedupes_overseas_symbols(service, mock_repo):
    mock_repo.get_all = AsyncMock(return_value=["aapl", "AAPL", "  msft  ", "", None])

    assert await service.get_all(market=MARKET_OVERSEAS_US) == ["AAPL", "MSFT"]


async def test_get_all_leaves_other_markets_untouched(service, mock_repo):
    mock_repo.get_all = AsyncMock(return_value=["aapl", "aapl"])

    assert await service.get_all(market="JP") == ["aapl", "aapl"]


async def test_remove_overseas_falls_back_to_the_stored_raw_symbol(service, mock_repo):
    mock_repo.remove = AsyncMock(side_effect=[False, True])

    assert await service.remove("aapl", market=MARKET_OVERSEAS_US) is True
    assert [c.args[0] for c in mock_repo.remove.await_args_list] == ["AAPL", "aapl"]


async def test_remove_overseas_stops_when_the_normalized_symbol_matches(service, mock_repo):
    mock_repo.remove = AsyncMock(return_value=False)

    assert await service.remove("AAPL", market=MARKET_OVERSEAS_US) is False
    assert mock_repo.remove.await_count == 1


async def test_remove_other_markets_pass_the_code_through(service, mock_repo):
    await service.remove("aapl", market="JP")

    mock_repo.remove.assert_awaited_once_with("aapl", market="JP")


async def test_is_favorite_of_a_blank_code_asks_the_repository_once(service, mock_repo):
    """빈 코드는 정규화 결과도 빈 문자열이라 재조회 없이 한 번만 묻는다."""
    assert await service.is_favorite("") is False
    mock_repo.is_favorite.assert_awaited_once_with("", market=MARKET_DOMESTIC)


async def test_is_favorite_overseas_checks_the_normalized_symbol_first(service, mock_repo):
    mock_repo.is_favorite = AsyncMock(return_value=True)

    assert await service.is_favorite("aapl", market=MARKET_OVERSEAS_US) is True
    mock_repo.is_favorite.assert_awaited_once_with("AAPL", market=MARKET_OVERSEAS_US)


async def test_is_favorite_overseas_falls_back_to_the_stored_raw_symbol(service, mock_repo):
    mock_repo.is_favorite = AsyncMock(side_effect=[False, True])

    assert await service.is_favorite("aapl", market=MARKET_OVERSEAS_US) is True
    assert [c.args[0] for c in mock_repo.is_favorite.await_args_list] == ["AAPL", "aapl"]


async def test_is_favorite_overseas_returns_false_when_the_symbol_is_already_normalized(
    service, mock_repo
):
    mock_repo.is_favorite = AsyncMock(return_value=False)

    assert await service.is_favorite("AAPL", market=MARKET_OVERSEAS_US) is False
    assert mock_repo.is_favorite.await_count == 1


async def test_is_favorite_other_markets_pass_the_code_through(service, mock_repo):
    await service.is_favorite("aapl", market="JP")

    mock_repo.is_favorite.assert_awaited_once_with("aapl", market="JP")


# --- 스냅샷 거래일 추출 -----------------------------------------------------


def test_snapshot_trade_date_reads_the_explicit_field_first():
    from services.favorite_service import _snapshot_trade_date

    assert _snapshot_trade_date({"_trade_date": "20260801"}) == "20260801"


def test_snapshot_trade_date_reads_the_output_payload():
    from services.favorite_service import _snapshot_trade_date

    assert _snapshot_trade_date({"output": {"stck_bsop_date": "20260801"}}) == "20260801"
    assert _snapshot_trade_date({"output": DummyOutput("1", "2")}) == ""


def test_snapshot_trade_date_is_blank_for_non_dict_snapshots():
    from services.favorite_service import _snapshot_trade_date

    assert _snapshot_trade_date(None) == ""
    assert _snapshot_trade_date("스냅샷 아님") == ""


def test_domestic_code_normalizer_only_pads_short_numeric_codes():
    from services.favorite_service import _normalize_domestic_code

    assert _normalize_domestic_code("5930") == "005930"
    assert _normalize_domestic_code("005930") == "005930"
    assert _normalize_domestic_code("AAPL") == "AAPL"
    assert _normalize_domestic_code("1234567") == "1234567"
