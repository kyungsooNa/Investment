"""
BackgroundService 단위 테스트.
전체 종목 순회 → 외국인 순매수/순매도 랭킹 생성 로직 검증.
"""
import pytest
import pandas as pd
from unittest.mock import MagicMock, AsyncMock
from services.background_service import BackgroundService
from common.types import ResCommonResponse, ErrorCode


@pytest.fixture
def mock_deps():
    broker = MagicMock()
    mapper = MagicMock()
    logger = MagicMock()
    time_manager = MagicMock()
    return broker, mapper, logger, time_manager


@pytest.fixture
def bg_service(mock_deps):
    broker, mapper, logger, time_manager = mock_deps
    return BackgroundService(
        broker_api_wrapper=broker,
        stock_code_mapper=mapper,
        logger=logger,
        time_manager=time_manager,
    )


def _make_stock_df(stocks):
    """테스트용 종목 DataFrame 생성. stocks: [(code, name, market), ...]"""
    return pd.DataFrame(stocks, columns=["종목코드", "종목명", "시장구분"])


def _make_foreign_response(glob_ntby_qty, stck_prpr="10000", prdy_ctrt="1.0"):
    """외국계 순매수추이 응답 생성 헬퍼."""
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data={
            "glob_ntby_qty": str(glob_ntby_qty),
            "stck_prpr": stck_prpr,
            "prdy_ctrt": prdy_ctrt,
            "prdy_vrss": "100",
            "prdy_vrss_sign": "2",
            "acml_vol": "50000",
            "frgn_ntby_qty_icdc": "10",
        }
    )


# ── 캐시 미준비 시 빈 결과 반환 ──────────────────────────────

def test_get_foreign_net_buy_ranking_empty_cache(bg_service):
    """캐시가 비어있으면 빈 data + 안내 메시지 반환."""
    resp = bg_service.get_foreign_net_buy_ranking()
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert resp.data == []
    assert "수집 중" in resp.msg1


def test_get_foreign_net_sell_ranking_empty_cache(bg_service):
    """캐시가 비어있으면 빈 data + 안내 메시지 반환."""
    resp = bg_service.get_foreign_net_sell_ranking()
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert resp.data == []
    assert "수집 중" in resp.msg1


# ── refresh_foreign_ranking 로직 검증 ────────────────────────

@pytest.mark.asyncio
async def test_refresh_foreign_ranking_basic(bg_service, mock_deps):
    """기본 순회 → 순매수/순매도 정렬 검증."""
    broker, mapper, logger, _ = mock_deps

    # 3개 종목 설정
    mapper.df = _make_stock_df([
        ("005930", "삼성전자", "KOSPI"),
        ("000660", "SK하이닉스", "KOSPI"),
        ("035420", "NAVER", "KOSDAQ"),
    ])

    # 각 종목의 외국인 순매수수량: 삼성(500), SK(-200), NAVER(300)
    async def mock_trend(code):
        data = {
            "005930": _make_foreign_response(500),
            "000660": _make_foreign_response(-200),
            "035420": _make_foreign_response(300),
        }
        return data.get(code, ResCommonResponse(rt_cd="1", msg1="Error", data=None))

    broker.get_foreign_trading_trend = AsyncMock(side_effect=mock_trend)

    await bg_service.refresh_foreign_ranking()

    # 순매수 상위: 삼성(500) > NAVER(300) > SK(-200)
    buy_resp = bg_service.get_foreign_net_buy_ranking()
    assert buy_resp.rt_cd == ErrorCode.SUCCESS.value
    assert len(buy_resp.data) == 3
    assert buy_resp.data[0]["hts_kor_isnm"] == "삼성전자"
    assert buy_resp.data[0]["glob_ntby_qty"] == "500"
    assert buy_resp.data[0]["data_rank"] == "1"
    assert buy_resp.data[1]["hts_kor_isnm"] == "NAVER"
    assert buy_resp.data[2]["hts_kor_isnm"] == "SK하이닉스"

    # 순매도 상위: SK(-200) > NAVER(300) > 삼성(500)
    sell_resp = bg_service.get_foreign_net_sell_ranking()
    assert sell_resp.rt_cd == ErrorCode.SUCCESS.value
    assert sell_resp.data[0]["hts_kor_isnm"] == "SK하이닉스"
    assert sell_resp.data[0]["data_rank"] == "1"


@pytest.mark.asyncio
async def test_refresh_foreign_ranking_etf_excluded(bg_service, mock_deps):
    """ETF/ETN 종목은 제외되어야 한다."""
    broker, mapper, _, _ = mock_deps

    mapper.df = _make_stock_df([
        ("005930", "삼성전자", "KOSPI"),
        ("069500", "KODEX 200", "KOSPI"),  # ETF
        ("102110", "TIGER 200", "KOSPI"),  # ETF
    ])

    async def mock_trend(code):
        return _make_foreign_response(1000)

    broker.get_foreign_trading_trend = AsyncMock(side_effect=mock_trend)

    await bg_service.refresh_foreign_ranking()

    buy_resp = bg_service.get_foreign_net_buy_ranking()
    names = [item["hts_kor_isnm"] for item in buy_resp.data]
    assert "삼성전자" in names
    assert "KODEX 200" not in names
    assert "TIGER 200" not in names


@pytest.mark.asyncio
async def test_refresh_foreign_ranking_api_error_skipped(bg_service, mock_deps):
    """API 오류 종목은 스킵되어야 한다."""
    broker, mapper, _, _ = mock_deps

    mapper.df = _make_stock_df([
        ("005930", "삼성전자", "KOSPI"),
        ("000660", "SK하이닉스", "KOSPI"),
    ])

    async def mock_trend(code):
        if code == "000660":
            return ResCommonResponse(rt_cd="1", msg1="Error", data=None)
        return _make_foreign_response(500)

    broker.get_foreign_trading_trend = AsyncMock(side_effect=mock_trend)

    await bg_service.refresh_foreign_ranking()

    buy_resp = bg_service.get_foreign_net_buy_ranking()
    assert len(buy_resp.data) == 1
    assert buy_resp.data[0]["hts_kor_isnm"] == "삼성전자"


@pytest.mark.asyncio
async def test_refresh_foreign_ranking_exception_skipped(bg_service, mock_deps):
    """개별 종목 조회 중 예외 발생 시 해당 종목만 스킵."""
    broker, mapper, _, _ = mock_deps

    mapper.df = _make_stock_df([
        ("005930", "삼성전자", "KOSPI"),
        ("000660", "SK하이닉스", "KOSPI"),
    ])

    async def mock_trend(code):
        if code == "000660":
            raise Exception("Network Error")
        return _make_foreign_response(500)

    broker.get_foreign_trading_trend = AsyncMock(side_effect=mock_trend)

    await bg_service.refresh_foreign_ranking()

    buy_resp = bg_service.get_foreign_net_buy_ranking()
    assert len(buy_resp.data) == 1
    assert buy_resp.data[0]["hts_kor_isnm"] == "삼성전자"


@pytest.mark.asyncio
async def test_refresh_foreign_ranking_non_stock_market_excluded(bg_service, mock_deps):
    """KOSPI/KOSDAQ 이외 시장은 제외."""
    broker, mapper, _, _ = mock_deps

    mapper.df = _make_stock_df([
        ("005930", "삼성전자", "KOSPI"),
        ("999999", "기타종목", "KONEX"),  # KONEX → 제외
    ])

    broker.get_foreign_trading_trend = AsyncMock(return_value=_make_foreign_response(100))

    await bg_service.refresh_foreign_ranking()

    buy_resp = bg_service.get_foreign_net_buy_ranking()
    assert len(buy_resp.data) == 1
    assert buy_resp.data[0]["hts_kor_isnm"] == "삼성전자"


@pytest.mark.asyncio
async def test_refresh_foreign_ranking_duplicate_prevention(bg_service, mock_deps):
    """이미 갱신 중이면 중복 실행 방지."""
    broker, mapper, logger, _ = mock_deps

    bg_service._is_refreshing = True

    await bg_service.refresh_foreign_ranking()

    # broker가 호출되지 않아야 함
    broker.get_foreign_trading_trend.assert_not_called()
    logger.info.assert_any_call("외국인 랭킹 갱신 이미 진행 중 — 스킵")


@pytest.mark.asyncio
async def test_refresh_foreign_ranking_limit(bg_service, mock_deps):
    """상위 30개만 반환되는지 확인."""
    broker, mapper, _, _ = mock_deps

    # 40개 종목 생성
    stocks = [(f"{i:06d}", f"종목{i}", "KOSPI") for i in range(40)]
    mapper.df = _make_stock_df(stocks)

    async def mock_trend(code):
        qty = int(code)  # 코드가 곧 순매수수량
        return _make_foreign_response(qty)

    broker.get_foreign_trading_trend = AsyncMock(side_effect=mock_trend)

    await bg_service.refresh_foreign_ranking()

    buy_resp = bg_service.get_foreign_net_buy_ranking()
    assert len(buy_resp.data) == 30
    # 가장 큰 값이 1위여야 함 (종목 39)
    assert buy_resp.data[0]["glob_ntby_qty"] == "39"

    sell_resp = bg_service.get_foreign_net_sell_ranking()
    assert len(sell_resp.data) == 30
    # 가장 작은 값이 1위여야 함 (종목 0)
    assert sell_resp.data[0]["glob_ntby_qty"] == "0"


def test_get_foreign_ranking_with_limit(bg_service):
    """limit 파라미터가 정상 동작하는지 확인."""
    # 캐시에 직접 데이터 주입
    bg_service._foreign_net_buy_cache = [
        {"data_rank": str(i), "hts_kor_isnm": f"종목{i}", "glob_ntby_qty": str(100-i)}
        for i in range(1, 31)
    ]

    resp = bg_service.get_foreign_net_buy_ranking(limit=5)
    assert len(resp.data) == 5
    assert resp.data[0]["data_rank"] == "1"
