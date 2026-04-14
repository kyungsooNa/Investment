import pytest
from unittest.mock import AsyncMock, MagicMock

from services.minervini_stage_service import MinerviniStageService
from common.types import ResCommonResponse, ResRSRating


def _make_svc(**kwargs):
    return MinerviniStageService(stock_query_service=AsyncMock(), rs_rating_service=None, **kwargs)


def _flat_closes(price: float, n: int):
    return [float(price)] * n


def test_calculate_slope_short_list_returns_zero():
    svc = _make_svc()
    assert svc._calculate_slope([1.0]) == 0.0


def test_extract_price_series_skips_invalid_rows():
    svc = _make_svc()
    rows = [
        {"stck_clpr": "100", "stck_lwpr": "90"},
        {"stck_clpr": None, "stck_lwpr": "bad"},
        {"close": "200", "low": "190"},
        {"close": "notnum"},
    ]
    closes, lows = svc._extract_price_series(rows)
    # Only two valid numeric rows should remain
    assert closes == [100.0, 200.0]
    assert lows == [90.0, 190.0]


@pytest.mark.asyncio
async def test__fetch_rs_rating_no_service_returns_zero():
    svc = MinerviniStageService(stock_query_service=AsyncMock(), rs_rating_service=None)
    val = await svc._fetch_rs_rating("005930")
    assert val == 0


@pytest.mark.asyncio
async def test__fetch_rs_rating_handles_service_response_and_errors():
    rs_svc = AsyncMock()
    # case: successful response with data
    rs_svc.get_rating.return_value = ResCommonResponse(rt_cd="0", msg1="", data=ResRSRating(code="005930", trade_date="20260101", rs_rating=88, weighted_rs=10.1))
    svc = MinerviniStageService(stock_query_service=AsyncMock(), rs_rating_service=rs_svc)
    val = await svc._fetch_rs_rating("005930")
    assert val == 88

    # case: service raises -> returns 0
    async def raise_exc(code):
        raise Exception("boom")
    rs_svc.get_rating.side_effect = raise_exc
    val2 = await svc._fetch_rs_rating("005930")
    assert val2 == 0


@pytest.mark.asyncio
async def test_get_stage_for_code_uses_db_snapshot_when_available():
    # Prepare stock_query_service with market_data_service._stock_repo returning snapshot
    mq = MagicMock()
    stock_repo = AsyncMock()
    # snapshot has minervini_stage and reason and matching trade_date
    snapshot = {"minervini_stage": 2, "minervini_reason": "(DB)", "trade_date": "20260101"}
    stock_repo.get_latest_daily_snapshot.return_value = snapshot
    market_data_service = MagicMock()
    market_data_service._stock_repo = stock_repo
    # mcs for latest_trading_date
    mcs = AsyncMock()
    mcs.get_latest_trading_date.return_value = "20260101"
    market_data_service._mcs = mcs
    mq.market_data_service = market_data_service

    svc = MinerviniStageService(stock_query_service=mq, rs_rating_service=None)
    stage, reason = await svc.get_stage_for_code("005930")
    assert stage == 2
    assert isinstance(reason, str)


def test_describe_stage_mapping():
    svc = _make_svc()
    assert "Stage 2" in svc.describe_stage(svc.STAGE_2_ADVANCING)
    assert "미계산" in svc.describe_stage(svc.STAGE_UNKNOWN)


# ── get_stage2_list ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_stage2_list_returns_db_data_when_available():
    """stock_repository에 데이터 있으면 DB 결과 반환."""
    stock_repo = AsyncMock()
    stock_repo.get_latest_trade_date.return_value = "20260101"
    stock_repo.get_minervini_stage2_stocks.return_value = [
        {"code": "005930", "name": "삼성전자", "current_price": 70000,
         "change_rate": 1.0, "minervini_stage": 2, "rs_rating": 85, "market_cap": 1000000}
    ]
    svc = MinerviniStageService(stock_query_service=AsyncMock(),
                                stock_repository=stock_repo)
    resp = await svc.get_stage2_list()
    assert resp.rt_cd == "0"
    assert resp.msg1 == "성공"
    assert resp.data[0]["code"] == "005930"
    assert resp.data[0]["stck_prpr"] == "70000"


@pytest.mark.asyncio
async def test_get_stage2_list_falls_back_to_cache_when_db_empty():
    """DB에 데이터 없으면 in-memory 캐시 사용."""
    stock_repo = AsyncMock()
    stock_repo.get_latest_trade_date.return_value = "20260101"
    stock_repo.get_minervini_stage2_stocks.return_value = []

    update_task = AsyncMock()
    update_task.get_minervini_stage2_cache.return_value = [
        {"code": "000660", "name": "SK하이닉스"}
    ]

    svc = MinerviniStageService(stock_query_service=AsyncMock(),
                                stock_repository=stock_repo)
    svc._minervini_update_task = update_task

    resp = await svc.get_stage2_list()
    assert resp.rt_cd == "0"
    assert resp.data[0]["code"] == "000660"


@pytest.mark.asyncio
async def test_get_stage2_list_triggers_refresh_when_no_data():
    """캐시도 없고 갱신 중이 아니면 refresh 트리거 후 수집 중 반환."""
    from unittest.mock import MagicMock
    stock_repo = AsyncMock()
    stock_repo.get_latest_trade_date.return_value = None

    update_task = AsyncMock()
    update_task.get_minervini_stage2_cache.return_value = []
    update_task.get_progress = MagicMock(return_value={"running": False})

    svc = MinerviniStageService(stock_query_service=AsyncMock(),
                                stock_repository=stock_repo)
    svc._minervini_update_task = update_task

    resp = await svc.get_stage2_list()
    assert resp.rt_cd == "0"
    assert resp.msg1 == "수집 중"
    assert resp.data == []


@pytest.mark.asyncio
async def test_get_stage2_list_no_task_returns_error():
    """_minervini_update_task 미설정 시 rt_cd=1 반환."""
    svc = MinerviniStageService(stock_query_service=AsyncMock(),
                                stock_repository=None)
    svc._minervini_update_task = None
    resp = await svc.get_stage2_list()
    assert resp.rt_cd == "1"
    assert resp.data is None


@pytest.mark.asyncio
async def test_get_stage2_list_db_exception_falls_back_to_task():
    """DB 조회 예외 발생 시 task 캐시로 폴백."""
    stock_repo = AsyncMock()
    stock_repo.get_latest_trade_date.side_effect = RuntimeError("DB 오류")

    update_task = AsyncMock()
    update_task.get_minervini_stage2_cache.return_value = [{"code": "035720"}]

    svc = MinerviniStageService(stock_query_service=AsyncMock(),
                                stock_repository=stock_repo)
    svc._minervini_update_task = update_task

    resp = await svc.get_stage2_list()
    assert resp.rt_cd == "0"
    assert resp.data[0]["code"] == "035720"
