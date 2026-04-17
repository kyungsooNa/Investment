import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from services.newhigh_service import NewHighService
from common.types import ResCommonResponse


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_repo():
    repo = AsyncMock()
    repo.get_latest_trade_date.return_value = "20260414"
    repo.get_newhigh_stocks.return_value = [
        {
            "code": "005930",
            "name": "삼성전자",
            "current_price": 75000,
            "change_rate": 2.5,
            "rs_rating": 90,
            "market_cap": 5000000,
            "trading_value": 300000,
            "w52_high": 75000,
            "is_historical_newhigh": True,
            "minervini_stage": 2,
        }
    ]
    return repo


@pytest.fixture
def mock_task():
    task = AsyncMock()
    task.get_newhigh_cache.return_value = [
        {
            "code": "000660",
            "name": "SK하이닉스",
            "current_price": 180000,
            "change_rate": 1.2,
            "rs_rating": 85,
            "market_cap": 2000000,
            "trading_value": 150000,
            "w52_high": 180000,
            "is_historical_new_high": False,
            "minervini_stage": 2,
        }
    ]
    # get_progress는 동기 메서드이므로 MagicMock으로 별도 설정
    task.get_progress = MagicMock(return_value={"running": False})
    task.force_run = AsyncMock()
    return task


def make_service(repo=None, task=None):
    return NewHighService(stock_repository=repo, newhigh_task=task)


# ── DB 조회 성공 ──────────────────────────────────────────────────────────────

async def test_get_newhigh_list_db_success(mock_repo):
    """DB에서 신고가 종목을 정상 조회하면 포매팅된 데이터를 반환한다."""
    service = make_service(repo=mock_repo)
    result = await service.get_newhigh_list()

    assert result.rt_cd == "0"
    assert result.msg1 == "성공"
    assert len(result.data) == 1

    item = result.data[0]
    assert item["code"] == "005930"
    assert item["name"] == "삼성전자"
    assert item["stck_prpr"] == "75000"
    assert item["prdy_ctrt"] == "2.5"
    assert item["rs_rating"] == 90
    assert item["market_cap"] == 5000000
    assert item["trading_value"] == 300000
    assert item["w52_high"] == 75000
    assert item["is_historical_new_high"] is True
    assert item["minervini_stage"] == 2

    mock_repo.get_latest_trade_date.assert_called_once()
    mock_repo.get_newhigh_stocks.assert_called_once_with("20260414")


async def test_get_newhigh_list_db_null_fields(mock_repo):
    """DB 조회 시 None 필드는 기본값(0/False)으로 대체된다."""
    mock_repo.get_newhigh_stocks.return_value = [
        {
            "code": "005930",
            "name": "삼성전자",
            "current_price": None,
            "change_rate": None,
            "rs_rating": None,
            "market_cap": None,
            "trading_value": None,
            "w52_high": None,
            "is_historical_newhigh": None,
            "minervini_stage": None,
        }
    ]
    service = make_service(repo=mock_repo)
    result = await service.get_newhigh_list()

    assert result.rt_cd == "0"
    item = result.data[0]
    assert item["stck_prpr"] == "0"
    assert item["prdy_ctrt"] == "0"
    assert item["rs_rating"] == 0
    assert item["market_cap"] == 0
    assert item["trading_value"] == 0
    assert item["w52_high"] == 0
    assert item["is_historical_new_high"] is False
    assert item["minervini_stage"] == 0


# ── DB fallback 시나리오 ───────────────────────────────────────────────────────

async def test_get_newhigh_list_db_no_latest_date_falls_to_cache(mock_repo, mock_task):
    """DB에서 latest_date가 None이면 캐시로 폴백한다."""
    mock_repo.get_latest_trade_date.return_value = None
    service = make_service(repo=mock_repo, task=mock_task)
    result = await service.get_newhigh_list()

    assert result.rt_cd == "0"
    assert result.data[0]["code"] == "000660"


async def test_get_newhigh_list_db_empty_items_falls_to_cache(mock_repo, mock_task):
    """DB 종목 목록이 비어있으면 캐시로 폴백한다."""
    mock_repo.get_newhigh_stocks.return_value = []
    service = make_service(repo=mock_repo, task=mock_task)
    result = await service.get_newhigh_list()

    assert result.rt_cd == "0"
    assert result.data[0]["code"] == "000660"


async def test_get_newhigh_list_db_exception_falls_to_cache(mock_repo, mock_task):
    """DB 조회 중 예외 발생 시 캐시로 폴백한다."""
    mock_repo.get_latest_trade_date.side_effect = Exception("DB 연결 실패")
    service = make_service(repo=mock_repo, task=mock_task)
    result = await service.get_newhigh_list()

    assert result.rt_cd == "0"
    assert result.data[0]["code"] == "000660"


# ── In-Memory 캐시 ────────────────────────────────────────────────────────────

async def test_get_newhigh_list_cache_success(mock_task):
    """repository 없이 캐시에서 데이터를 반환한다."""
    service = make_service(task=mock_task)
    result = await service.get_newhigh_list()

    assert result.rt_cd == "0"
    assert result.msg1 == "성공"
    assert len(result.data) == 1

    item = result.data[0]
    assert item["code"] == "000660"
    assert item["name"] == "SK하이닉스"
    assert item["stck_prpr"] == "180000"
    assert item["prdy_ctrt"] == "1.2"
    assert item["is_historical_new_high"] is False


async def test_get_newhigh_list_cache_null_fields(mock_task):
    """캐시 데이터의 None 숫자 필드는 0으로 대체된다. is_historical_new_high는 그대로 전달된다."""
    mock_task.get_newhigh_cache.return_value = [
        {
            "code": "000660",
            "name": "SK하이닉스",
            "current_price": None,
            "change_rate": None,
            "rs_rating": None,
            "market_cap": None,
            "trading_value": None,
            "w52_high": None,
            "is_historical_new_high": False,
            "minervini_stage": None,
        }
    ]
    service = make_service(task=mock_task)
    result = await service.get_newhigh_list()

    assert result.rt_cd == "0"
    item = result.data[0]
    assert item["stck_prpr"] == "0"
    assert item["prdy_ctrt"] == "0"
    assert item["rs_rating"] == 0
    assert item["market_cap"] == 0
    assert item["trading_value"] == 0
    assert item["w52_high"] == 0
    assert item["is_historical_new_high"] is False
    assert item["minervini_stage"] == 0


# ── force_run 트리거 ──────────────────────────────────────────────────────

async def test_get_newhigh_list_no_cache_triggers_collect(mock_task):
    """캐시가 없고 task가 실행 중이 아닐 때 force_run을 트리거하고 빈 목록을 반환한다."""
    mock_task.get_newhigh_cache.return_value = []
    mock_task.get_progress.return_value = {"running": False}

    service = make_service(task=mock_task)
    with patch("asyncio.create_task") as mock_create_task:
        result = await service.get_newhigh_list()

    assert result.rt_cd == "0"
    assert result.msg1 == "수집 중"
    assert result.data == []
    mock_create_task.assert_called_once()


async def test_get_newhigh_list_no_cache_already_running_no_duplicate_trigger(mock_task):
    """캐시가 없고 task가 이미 실행 중이면 force_run을 중복 트리거하지 않는다."""
    mock_task.get_newhigh_cache.return_value = []
    mock_task.get_progress.return_value = {"running": True}

    service = make_service(task=mock_task)
    with patch("asyncio.create_task") as mock_create_task:
        result = await service.get_newhigh_list()

    assert result.rt_cd == "0"
    assert result.msg1 == "수집 중"
    mock_create_task.assert_not_called()


# ── task 미설정 ───────────────────────────────────────────────────────────────

async def test_get_newhigh_list_no_task_returns_error():
    """repository도 task도 없을 때 에러 응답을 반환한다."""
    service = make_service()
    result = await service.get_newhigh_list()

    assert result.rt_cd == "1"
    assert "NewHighTask" in result.msg1
    assert result.data is None


async def test_get_newhigh_list_no_repo_no_task_returns_error():
    """repository가 없고 task도 없을 때 에러 응답을 반환한다."""
    service = NewHighService()
    result = await service.get_newhigh_list()

    assert result.rt_cd == "1"
    assert result.data is None
