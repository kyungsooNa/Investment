"""
BackgroundService 단위 테스트.
전체 종목 순회 → 외국인/기관/개인 순매수/순매도 랭킹 생성 로직 검증.
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
    env = MagicMock()
    env.is_paper_trading = False  # 기본: 실전투자 모드
    logger = MagicMock()
    time_manager = MagicMock()
    return broker, mapper, env, logger, time_manager


@pytest.fixture
def bg_service(mock_deps):
    broker, mapper, env, logger, time_manager = mock_deps
    return BackgroundService(
        broker_api_wrapper=broker,
        stock_code_mapper=mapper,
        env=env,
        logger=logger,
        time_manager=time_manager,
    )


def _make_stock_df(stocks):
    """테스트용 종목 DataFrame 생성. stocks: [(code, name, market), ...]"""
    return pd.DataFrame(stocks, columns=["종목코드", "종목명", "시장구분"])


def _make_investor_response(
    frgn_qty, orgn_qty=0, prsn_qty=0,
    stck_prpr="10000", prdy_ctrt="1.0",
    frgn_pbmn=None, orgn_pbmn=None, prsn_pbmn=None,
):
    """투자자 매매동향 응답 생성 헬퍼. pbmn 미지정 시 qty * 10000 으로 자동 산출."""
    if frgn_pbmn is None:
        frgn_pbmn = frgn_qty * 10000
    if orgn_pbmn is None:
        orgn_pbmn = orgn_qty * 10000
    if prsn_pbmn is None:
        prsn_pbmn = prsn_qty * 10000
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data={
            "stck_prpr": stck_prpr,
            "prdy_ctrt": prdy_ctrt,
            "prdy_vrss": "100",
            "prdy_vrss_sign": "2",
            "acml_vol": "50000",
            "frgn_ntby_qty": str(frgn_qty),
            "orgn_ntby_qty": str(orgn_qty),
            "prsn_ntby_qty": str(prsn_qty),
            "frgn_ntby_tr_pbmn": str(frgn_pbmn),
            "orgn_ntby_tr_pbmn": str(orgn_pbmn),
            "prsn_ntby_tr_pbmn": str(prsn_pbmn),
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


def test_get_inst_net_buy_ranking_empty_cache(bg_service):
    """기관 캐시가 비어있으면 빈 data 반환."""
    resp = bg_service.get_inst_net_buy_ranking()
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert resp.data == []


def test_get_prsn_net_buy_ranking_empty_cache(bg_service):
    """개인 캐시가 비어있으면 빈 data 반환."""
    resp = bg_service.get_prsn_net_buy_ranking()
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert resp.data == []


# ── 모의투자 모드에서도 정상 동작 (조회 API는 항상 실전 인증) ──

def test_paper_trading_still_works(bg_service, mock_deps):
    """모의투자 모드에서도 투자자 랭킹 조회 가능 (조회 API는 항상 실전 URL/인증 사용)."""
    _, _, env, _, _ = mock_deps
    env.is_paper_trading = True

    resp = bg_service.get_foreign_net_buy_ranking()
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    # 캐시 없으면 "데이터 수집 중..." 반환 (차단하지 않음)
    assert "데이터 수집 중" in resp.msg1
    assert resp.data == []


# ── 온디맨드 트리거 ─────────────────────────────────────────

def test_on_demand_trigger_when_cache_empty(bg_service, mock_deps):
    """캐시 비어있고 갱신 중이 아니면 온디맨드 갱신이 트리거되어야 함."""
    import asyncio
    from unittest.mock import patch

    mock_loop = MagicMock()
    with patch.object(asyncio, "get_running_loop", return_value=mock_loop), \
         patch.object(asyncio, "create_task") as mock_create_task:
        resp = bg_service.get_foreign_net_buy_ranking()
        assert resp.data == []
        mock_create_task.assert_called_once()


def test_no_trigger_when_already_refreshing(bg_service):
    """이미 갱신 중이면 추가 트리거하지 않음."""
    import asyncio
    from unittest.mock import patch

    mock_loop = MagicMock()
    bg_service._is_refreshing = True
    with patch.object(asyncio, "get_running_loop", return_value=mock_loop), \
         patch.object(asyncio, "create_task") as mock_create_task:
        resp = bg_service.get_foreign_net_buy_ranking()
        assert resp.data == []
        mock_create_task.assert_not_called()


# ── refresh_investor_ranking 로직 검증 ────────────────────────

@pytest.mark.asyncio
async def test_refresh_investor_ranking_basic(bg_service, mock_deps):
    """기본 순회 → 외국인/기관/개인 순매수/순매도 정렬 검증."""
    broker, mapper, _, logger, _ = mock_deps

    # 3개 종목 설정
    mapper.df = _make_stock_df([
        ("005930", "삼성전자", "KOSPI"),
        ("000660", "SK하이닉스", "KOSPI"),
        ("035420", "NAVER", "KOSDAQ"),
    ])

    # 삼성(외500,기관200,개인-300), SK(외-200,기관100,개인400), NAVER(외300,기관-100,개인50)
    async def mock_trend(code, date=None):
        data = {
            "005930": _make_investor_response(500, 200, -300),
            "000660": _make_investor_response(-200, 100, 400),
            "035420": _make_investor_response(300, -100, 50),
        }
        return data.get(code, ResCommonResponse(rt_cd="1", msg1="Error", data=None))

    broker.get_investor_trade_by_stock_daily = AsyncMock(side_effect=mock_trend)

    await bg_service.refresh_investor_ranking()

    # 외국인 순매수 상위: 삼성(500) > NAVER(300) > SK(-200)
    buy_resp = bg_service.get_foreign_net_buy_ranking()
    assert buy_resp.rt_cd == ErrorCode.SUCCESS.value
    assert len(buy_resp.data) == 3
    assert buy_resp.data[0]["hts_kor_isnm"] == "삼성전자"
    assert buy_resp.data[0]["frgn_ntby_qty"] == "500"
    assert buy_resp.data[0]["data_rank"] == "1"
    assert buy_resp.data[1]["hts_kor_isnm"] == "NAVER"
    assert buy_resp.data[2]["hts_kor_isnm"] == "SK하이닉스"

    # 외국인 순매도 상위: SK(-200) > NAVER(300) > 삼성(500)
    sell_resp = bg_service.get_foreign_net_sell_ranking()
    assert sell_resp.rt_cd == ErrorCode.SUCCESS.value
    assert sell_resp.data[0]["hts_kor_isnm"] == "SK하이닉스"
    assert sell_resp.data[0]["data_rank"] == "1"

    # 기관 순매수 상위: 삼성(200) > SK(100) > NAVER(-100)
    inst_buy = bg_service.get_inst_net_buy_ranking()
    assert inst_buy.data[0]["hts_kor_isnm"] == "삼성전자"
    assert inst_buy.data[0]["orgn_ntby_qty"] == "200"
    assert inst_buy.data[1]["hts_kor_isnm"] == "SK하이닉스"

    # 기관 순매도 상위: NAVER(-100)
    inst_sell = bg_service.get_inst_net_sell_ranking()
    assert inst_sell.data[0]["hts_kor_isnm"] == "NAVER"

    # 개인 순매수 상위: SK(400) > NAVER(50) > 삼성(-300)
    prsn_buy = bg_service.get_prsn_net_buy_ranking()
    assert prsn_buy.data[0]["hts_kor_isnm"] == "SK하이닉스"
    assert prsn_buy.data[0]["prsn_ntby_qty"] == "400"

    # 개인 순매도 상위: 삼성(-300)
    prsn_sell = bg_service.get_prsn_net_sell_ranking()
    assert prsn_sell.data[0]["hts_kor_isnm"] == "삼성전자"


@pytest.mark.asyncio
async def test_refresh_investor_ranking_etf_excluded(bg_service, mock_deps):
    """ETF/ETN 종목은 제외되어야 한다."""
    broker, mapper, _, _, _ = mock_deps

    mapper.df = _make_stock_df([
        ("005930", "삼성전자", "KOSPI"),
        ("069500", "KODEX 200", "KOSPI"),  # ETF
        ("102110", "TIGER 200", "KOSPI"),  # ETF
    ])

    async def mock_trend(code, date=None):
        return _make_investor_response(1000, 500, -200)

    broker.get_investor_trade_by_stock_daily = AsyncMock(side_effect=mock_trend)

    await bg_service.refresh_investor_ranking()

    buy_resp = bg_service.get_foreign_net_buy_ranking()
    names = [item["hts_kor_isnm"] for item in buy_resp.data]
    assert "삼성전자" in names
    assert "KODEX 200" not in names
    assert "TIGER 200" not in names


@pytest.mark.asyncio
async def test_refresh_investor_ranking_api_error_skipped(bg_service, mock_deps):
    """API 오류 종목은 스킵되어야 한다."""
    broker, mapper, _, _, _ = mock_deps

    mapper.df = _make_stock_df([
        ("005930", "삼성전자", "KOSPI"),
        ("000660", "SK하이닉스", "KOSPI"),
    ])

    async def mock_trend(code, date=None):
        if code == "000660":
            return ResCommonResponse(rt_cd="1", msg1="Error", data=None)
        return _make_investor_response(500, 200, -100)

    broker.get_investor_trade_by_stock_daily = AsyncMock(side_effect=mock_trend)

    await bg_service.refresh_investor_ranking()

    buy_resp = bg_service.get_foreign_net_buy_ranking()
    assert len(buy_resp.data) == 1
    assert buy_resp.data[0]["hts_kor_isnm"] == "삼성전자"


@pytest.mark.asyncio
async def test_refresh_investor_ranking_exception_skipped(bg_service, mock_deps):
    """개별 종목 조회 중 예외 발생 시 해당 종목만 스킵."""
    broker, mapper, _, _, _ = mock_deps

    mapper.df = _make_stock_df([
        ("005930", "삼성전자", "KOSPI"),
        ("000660", "SK하이닉스", "KOSPI"),
    ])

    async def mock_trend(code, date=None):
        if code == "000660":
            raise Exception("Network Error")
        return _make_investor_response(500, 200, -100)

    broker.get_investor_trade_by_stock_daily = AsyncMock(side_effect=mock_trend)

    await bg_service.refresh_investor_ranking()

    buy_resp = bg_service.get_foreign_net_buy_ranking()
    assert len(buy_resp.data) == 1
    assert buy_resp.data[0]["hts_kor_isnm"] == "삼성전자"


@pytest.mark.asyncio
async def test_refresh_investor_ranking_non_stock_market_excluded(bg_service, mock_deps):
    """KOSPI/KOSDAQ 이외 시장은 제외."""
    broker, mapper, _, _, _ = mock_deps

    mapper.df = _make_stock_df([
        ("005930", "삼성전자", "KOSPI"),
        ("999999", "기타종목", "KONEX"),  # KONEX → 제외
    ])

    broker.get_investor_trade_by_stock_daily = AsyncMock(
        return_value=_make_investor_response(100, 50, -30)
    )

    await bg_service.refresh_investor_ranking()

    buy_resp = bg_service.get_foreign_net_buy_ranking()
    assert len(buy_resp.data) == 1
    assert buy_resp.data[0]["hts_kor_isnm"] == "삼성전자"


@pytest.mark.asyncio
async def test_refresh_investor_ranking_duplicate_prevention(bg_service, mock_deps):
    """이미 갱신 중이면 중복 실행 방지."""
    broker, mapper, _, logger, _ = mock_deps

    bg_service._is_refreshing = True

    await bg_service.refresh_investor_ranking()

    # broker가 호출되지 않아야 함
    broker.get_investor_trade_by_stock_daily.assert_not_called()
    logger.info.assert_any_call("투자자 랭킹 갱신 이미 진행 중 — 스킵")


@pytest.mark.asyncio
async def test_refresh_investor_ranking_limit(bg_service, mock_deps):
    """상위 30개만 반환되는지 확인."""
    broker, mapper, _, _, _ = mock_deps

    # 40개 종목 생성
    stocks = [(f"{i:06d}", f"종목{i}", "KOSPI") for i in range(40)]
    mapper.df = _make_stock_df(stocks)

    async def mock_trend(code, date=None):
        qty = int(code)  # 코드가 곧 순매수수량
        return _make_investor_response(qty, qty * 2, -qty)

    broker.get_investor_trade_by_stock_daily = AsyncMock(side_effect=mock_trend)

    await bg_service.refresh_investor_ranking()

    buy_resp = bg_service.get_foreign_net_buy_ranking()
    assert len(buy_resp.data) == 30
    # 가장 큰 값이 1위여야 함 (종목 39)
    assert buy_resp.data[0]["frgn_ntby_qty"] == "39"

    sell_resp = bg_service.get_foreign_net_sell_ranking()
    assert len(sell_resp.data) == 30
    # 가장 작은 값이 1위여야 함 (종목 0)
    assert sell_resp.data[0]["frgn_ntby_qty"] == "0"

    # 기관도 30개 제한
    inst_buy = bg_service.get_inst_net_buy_ranking()
    assert len(inst_buy.data) == 30
    assert inst_buy.data[0]["orgn_ntby_qty"] == "78"  # 39*2


def test_get_foreign_ranking_with_limit(bg_service):
    """limit 파라미터가 정상 동작하는지 확인."""
    # 캐시에 직접 데이터 주입
    bg_service._foreign_net_buy_cache = [
        {"data_rank": str(i), "hts_kor_isnm": f"종목{i}",
         "frgn_ntby_qty": str(100-i), "orgn_ntby_qty": "0", "prsn_ntby_qty": "0"}
        for i in range(1, 31)
    ]

    resp = bg_service.get_foreign_net_buy_ranking(limit=5)
    assert len(resp.data) == 5
    assert resp.data[0]["data_rank"] == "1"


# ── 순매수대금 기준 정렬 검증 ────────────────────────────────

@pytest.mark.asyncio
async def test_ranking_sorted_by_trade_amount(bg_service, mock_deps):
    """순위는 순매수대금(tr_pbmn) 기준으로 정렬되어야 한다."""
    broker, mapper, _, _, _ = mock_deps

    mapper.df = _make_stock_df([
        ("005930", "삼성전자", "KOSPI"),
        ("000660", "SK하이닉스", "KOSPI"),
    ])

    # 삼성: 수량 많지만 대금 적음, SK: 수량 적지만 대금 많음
    async def mock_trend(code, date=None):
        if code == "005930":
            return _make_investor_response(1000, 0, 0, frgn_pbmn=50000)
        else:
            return _make_investor_response(100, 0, 0, frgn_pbmn=200000)

    broker.get_investor_trade_by_stock_daily = AsyncMock(side_effect=mock_trend)
    await bg_service.refresh_investor_ranking()

    buy_resp = bg_service.get_foreign_net_buy_ranking()
    # 대금이 큰 SK하이닉스가 1위
    assert buy_resp.data[0]["hts_kor_isnm"] == "SK하이닉스"
    assert buy_resp.data[0]["frgn_ntby_tr_pbmn"] == "200000"
    assert buy_resp.data[0]["frgn_ntby_qty"] == "100"
    # 삼성전자는 2위
    assert buy_resp.data[1]["hts_kor_isnm"] == "삼성전자"


# ── 기본 랭킹 캐시 (장마감 후) ──────────────────────────────

@pytest.mark.asyncio
async def test_refresh_basic_ranking(mock_deps):
    """기본 랭킹 캐시 갱신 테스트."""
    broker, mapper, env, logger, time_manager = mock_deps
    trading_service = MagicMock()
    trading_service.get_top_rise_fall_stocks = AsyncMock(
        return_value=ResCommonResponse(rt_cd="0", msg1="OK", data=[{"name": "rise"}])
    )
    trading_service.get_top_volume_stocks = AsyncMock(
        return_value=ResCommonResponse(rt_cd="0", msg1="OK", data=[{"name": "vol"}])
    )
    trading_service.get_top_trading_value_stocks = AsyncMock(
        return_value=ResCommonResponse(rt_cd="0", msg1="OK", data=[{"name": "tv"}])
    )

    bg = BackgroundService(
        broker_api_wrapper=broker, stock_code_mapper=mapper,
        env=env, logger=logger, time_manager=time_manager,
        trading_service=trading_service,
    )

    await bg.refresh_basic_ranking()

    assert "rise" in bg._basic_ranking_cache
    assert "fall" in bg._basic_ranking_cache
    assert "volume" in bg._basic_ranking_cache
    assert "trading_value" in bg._basic_ranking_cache
    assert bg._basic_ranking_updated_at is not None


def test_get_basic_ranking_cache_miss(bg_service):
    """캐시 없으면 None 반환."""
    assert bg_service.get_basic_ranking_cache("rise") is None


@pytest.mark.asyncio
async def test_refresh_basic_ranking_no_trading_service(bg_service):
    """TradingService 없으면 스킵."""
    bg_service._trading_service = None
    await bg_service.refresh_basic_ranking()
    assert bg_service._basic_ranking_cache == {}


# ── 장마감 후 스케줄러 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_after_market_scheduler_triggers_refresh(mock_deps):
    """장마감 상태에서 스케줄러가 갱신을 트리거하는지 검증."""
    import asyncio
    from unittest.mock import patch

    broker, mapper, env, logger, time_manager = mock_deps
    time_manager.is_market_open.return_value = False  # 장마감

    trading_service = MagicMock()
    trading_service.get_top_rise_fall_stocks = AsyncMock(
        return_value=ResCommonResponse(rt_cd="0", msg1="OK", data=[])
    )
    trading_service.get_top_volume_stocks = AsyncMock(
        return_value=ResCommonResponse(rt_cd="0", msg1="OK", data=[])
    )
    trading_service.get_top_trading_value_stocks = AsyncMock(
        return_value=ResCommonResponse(rt_cd="0", msg1="OK", data=[])
    )

    bg = BackgroundService(
        broker_api_wrapper=broker, stock_code_mapper=mapper,
        env=env, logger=logger, time_manager=time_manager,
        trading_service=trading_service,
    )

    # asyncio.sleep를 1회만 실행 후 CancelledError로 종료
    call_count = 0
    async def mock_sleep(sec):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=mock_sleep):
        await bg.start_after_market_scheduler()

    # 기본 랭킹과 투자자 랭킹 모두 갱신 시도됨
    assert bg._basic_ranking_updated_at is not None


# ── 진행률 조회 ──────────────────────────────────────────

def test_get_investor_ranking_progress_initial(bg_service):
    """초기 상태에서 진행률은 running=False, 0/0."""
    p = bg_service.get_investor_ranking_progress()
    assert p["running"] is False
    assert p["processed"] == 0
    assert p["total"] == 0


@pytest.mark.asyncio
async def test_progress_updates_during_refresh(bg_service, mock_deps):
    """갱신 중 진행률이 업데이트되는지 확인."""
    broker, mapper, _, _, _ = mock_deps
    mapper.df = _make_stock_df([
        ("005930", "삼성전자", "KOSPI"),
        ("000660", "SK하이닉스", "KOSPI"),
    ])
    broker.get_investor_trade_by_stock_daily = AsyncMock(
        return_value=_make_investor_response(100, 50, -30)
    )

    await bg_service.refresh_investor_ranking()

    p = bg_service.get_investor_ranking_progress()
    assert p["running"] is False
    assert p["processed"] == 2
    assert p["total"] == 2
    assert p["collected"] == 2
    assert p["elapsed"] >= 0
