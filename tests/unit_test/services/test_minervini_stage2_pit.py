"""백테스트/캡처용 Minervini Stage2 point-in-time 조회 (todo 1-8)."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.minervini_stage2_pit import (
    BacktestMinerviniStageService,
    load_pit_stage2_items,
)


def _repo(*, previous_date="20261105", stage2_rows=None):
    repo = MagicMock()
    repo.get_latest_trade_date_before = AsyncMock(return_value=previous_date)
    repo.get_minervini_stage2_stocks = AsyncMock(return_value=stage2_rows or [])
    repo.get_latest_trade_date = AsyncMock(return_value="20261106")
    return repo


@pytest.mark.asyncio
async def test_load_pit_stage2_items_reads_previous_trade_date_in_live_shape():
    repo = _repo(stage2_rows=[{
        "code": "005930", "name": "삼성전자", "current_price": 70000,
        "change_rate": 1.5, "minervini_stage": 2, "rs_rating": 91, "market_cap": 4_000_000,
    }])

    trade_date, items = await load_pit_stage2_items(repo, "20261106")

    repo.get_latest_trade_date_before.assert_awaited_once_with("20261106")
    repo.get_minervini_stage2_stocks.assert_awaited_once_with("20261105")
    repo.get_latest_trade_date.assert_not_called()
    assert trade_date == "20261105"
    assert items == [{
        "code": "005930", "name": "삼성전자", "stck_prpr": "70000", "prdy_ctrt": "1.5",
        "stage": 2, "rs_rating": 91, "market_cap": 4_000_000,
    }]


@pytest.mark.asyncio
async def test_load_pit_stage2_items_is_empty_without_previous_trade_date():
    repo = _repo(previous_date=None)

    assert await load_pit_stage2_items(repo, "20261106") == (None, [])
    repo.get_minervini_stage2_stocks.assert_not_called()


@pytest.mark.asyncio
async def test_backtest_stage_service_lists_stage2_as_of_previous_trade_date():
    repo = _repo(stage2_rows=[{"code": "000660", "minervini_stage": 2, "rs_rating": 88}])
    service = BacktestMinerviniStageService(repo, classifier=MagicMock())
    service.set_backtest_date("20261106")

    response = await service.get_stage2_list()

    assert response.rt_cd == "0"
    assert [item["code"] for item in response.data] == ["000660"]
    repo.get_minervini_stage2_stocks.assert_awaited_once_with("20261105")


@pytest.mark.asyncio
async def test_backtest_stage_service_refuses_without_backtest_date():
    service = BacktestMinerviniStageService(_repo(), classifier=MagicMock())

    response = await service.get_stage2_list()

    assert response.rt_cd != "0"
    assert response.data is None


@pytest.mark.asyncio
async def test_backtest_stage_for_code_uses_previous_trade_date_stage2_membership():
    repo = _repo(stage2_rows=[{"code": "005930", "minervini_stage": 2}])
    repo.get_minervini_stage_count = AsyncMock(return_value=2500)
    service = BacktestMinerviniStageService(repo, classifier=MagicMock())
    service.set_backtest_date("20261106")

    stage, reason = await service.get_stage_for_code("005930")
    left_stage, _ = await service.get_stage_for_code("000660")

    assert stage == 2
    assert "20261105" in reason
    assert left_stage not in (0, 2)
    repo.get_minervini_stage2_stocks.assert_awaited_once_with("20261105")


@pytest.mark.asyncio
async def test_backtest_stage_for_code_unknown_when_stage_not_computed_that_day():
    repo = _repo(stage2_rows=[])
    repo.get_minervini_stage_count = AsyncMock(return_value=0)
    service = BacktestMinerviniStageService(repo, classifier=MagicMock())
    service.set_backtest_date("20261106")

    stage, _ = await service.get_stage_for_code("005930")

    assert stage == 0


def test_backtest_stage_service_delegates_pure_stage_classification():
    classifier = MagicMock()
    classifier.classify_stage_series.return_value = [1, 2]
    service = BacktestMinerviniStageService(_repo(), classifier=classifier)

    assert service.classify_stage_series([1.0], [1.0], rs_rating=90) == [1, 2]
    classifier.classify_stage_series.assert_called_once_with([1.0], [1.0], rs_rating=90)
