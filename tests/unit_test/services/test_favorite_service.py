"""
FavoriteService 단위 테스트.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock
from common.types import ResCommonResponse
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
    mock_repo.get_all.assert_called_once()


async def test_add_delegates(service, mock_repo):
    assert await service.add("005930") is True
    mock_repo.add.assert_called_once_with("005930")


async def test_add_duplicate_returns_false(service, mock_repo):
    mock_repo.add.return_value = False
    assert await service.add("005930") is False


async def test_remove_delegates(service, mock_repo):
    assert await service.remove("005930") is True
    mock_repo.remove.assert_called_once_with("005930")


async def test_is_favorite_delegates(service, mock_repo):
    mock_repo.is_favorite.return_value = True
    assert await service.is_favorite("005930") is True
    mock_repo.is_favorite.assert_called_once_with("005930")


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
    mock_stock_repo.get_current_price.assert_called_once_with("005930", max_age_sec=float("inf"), count_stats=False)
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
