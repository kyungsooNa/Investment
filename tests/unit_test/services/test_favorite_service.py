"""
FavoriteService 단위 테스트.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock
from common.types import ResCommonResponse
from services.favorite_service import FavoriteService


@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.get_all.return_value = []
    repo.add.return_value = True
    repo.remove.return_value = True
    repo.is_favorite.return_value = False
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


def test_get_all_delegates(service, mock_repo):
    mock_repo.get_all.return_value = ["005930"]
    assert service.get_all() == ["005930"]
    mock_repo.get_all.assert_called_once()


def test_add_delegates(service, mock_repo):
    assert service.add("005930") is True
    mock_repo.add.assert_called_once_with("005930")


def test_add_duplicate_returns_false(service, mock_repo):
    mock_repo.add.return_value = False
    assert service.add("005930") is False


def test_remove_delegates(service, mock_repo):
    assert service.remove("005930") is True
    mock_repo.remove.assert_called_once_with("005930")


def test_is_favorite_delegates(service, mock_repo):
    mock_repo.is_favorite.return_value = True
    assert service.is_favorite("005930") is True
    mock_repo.is_favorite.assert_called_once_with("005930")


@pytest.mark.asyncio
async def test_get_with_details_empty(service, mock_repo):
    mock_repo.get_all.return_value = []
    result = await service.get_with_details()
    assert result == []


@pytest.mark.asyncio
async def test_get_with_details_no_query_service(service, mock_repo, mock_stock_code_repo):
    mock_repo.get_all.return_value = ["005930"]
    result = await service.get_with_details()
    assert len(result) == 1
    assert result[0]["code"] == "005930"
    assert result[0]["name"] == "삼성전자"
    assert result[0]["price"] is None
    assert result[0]["rate"] is None


@pytest.mark.asyncio
async def test_get_with_details_with_price(mock_repo, mock_stock_code_repo):
    mock_repo.get_all.return_value = ["005930"]
    mock_query = AsyncMock()
    mock_query.get_multi_price.return_value = ResCommonResponse(
        rt_cd="0", msg1="OK",
        data=[{"stck_shrn_iscd": "005930", "stck_prpr": "75000", "prdy_ctrt": "1.5"}],
    )
    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
        stock_query_service=mock_query,
    )
    result = await svc.get_with_details()
    assert result[0]["price"] == "75000"
    assert result[0]["rate"] == "1.5"


@pytest.mark.asyncio
async def test_get_with_details_price_api_failure(mock_repo, mock_stock_code_repo):
    """stock_query_service 예외 발생 시 price=None으로 graceful degradation."""
    mock_repo.get_all.return_value = ["005930"]
    mock_query = AsyncMock()
    mock_query.get_multi_price.side_effect = Exception("API error")
    svc = FavoriteService(
        repository=mock_repo,
        stock_code_repository=mock_stock_code_repo,
        stock_query_service=mock_query,
    )
    result = await svc.get_with_details()
    assert len(result) == 1
    assert result[0]["price"] is None
