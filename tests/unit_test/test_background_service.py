"""
BackgroundService 단위 테스트.
전체 종목 순회 → 외국인/기관/개인 순매수/순매도 랭킹 생성 로직 검증.
"""
import pytest
import pandas as pd
from unittest.mock import MagicMock, AsyncMock, patch
from services.background_service import BackgroundService, _ETF_PREFIXES, _chunked
from common.types import ResCommonResponse, ErrorCode


def _make_program_response(ntby_tr_pbmn=0, ntby_qty=0, stck_clpr="10000", prdy_ctrt="1.0"):
    """프로그램매매추이 응답 생성 헬퍼."""
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data={
            "stck_clpr": stck_clpr,
            "prdy_ctrt": prdy_ctrt,
            "prdy_vrss": "100",
            "prdy_vrss_sign": "2",
            "acml_vol": "50000",
            "acml_tr_pbmn": "100000000",
            "whol_smtn_ntby_tr_pbmn": str(ntby_tr_pbmn),
            "whol_smtn_ntby_qty": str(ntby_qty),
            "whol_smtn_seln_tr_pbmn": "0",
            "whol_smtn_shnu_tr_pbmn": "0",
        }
    )


@pytest.fixture
def mock_deps():
    broker = MagicMock()
    # 프로그램매매추이 기본 mock (투자자 테스트에 영향 없도록 빈 응답)
    broker.get_program_trade_by_stock_daily = AsyncMock(
        return_value=ResCommonResponse(rt_cd="1", msg1="", data=None)
    )
    mapper = MagicMock()
    env = MagicMock()
    env.is_paper_trading = False  # 기본: 실전투자 모드
    logger = MagicMock()
    time_manager = MagicMock()
    time_manager.is_market_open.return_value = False  # 기본: 장 마감 상태 (갱신 허용)
    return broker, mapper, env, logger, time_manager


@pytest.fixture
def bg_service(mock_deps):
    broker, mapper, env, logger, time_manager = mock_deps
    
    # TradingService Mock 주입 (get_latest_trading_date 필수)
    trading_service = AsyncMock()
    trading_service.get_latest_trading_date.return_value = "20250101"
    
    return BackgroundService(
        broker_api_wrapper=broker,
        stock_code_mapper=mapper,
        env=env,
        logger=logger,
        time_manager=time_manager,
        trading_service=trading_service,
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

    mock_loop = MagicMock()
    with patch.object(asyncio, "get_running_loop", return_value=mock_loop), \
         patch.object(asyncio, "create_task") as mock_create_task:
        resp = bg_service.get_foreign_net_buy_ranking()
        assert resp.data == []
        mock_create_task.assert_called_once()
        # create_task에 전달된 coroutine을 close하여 "coroutine never awaited" 경고 방지
        mock_create_task.call_args[0][0].close()


def test_no_trigger_when_already_refreshing(bg_service):
    """이미 갱신 중이면 추가 트리거하지 않음."""
    import asyncio

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

    # 40개 종목 생성 (우선주 필터 통과를 위해 끝자리 0 보장)
    stocks = [(f"{i:05d}0", f"종목{i}", "KOSPI") for i in range(40)]
    mapper.df = _make_stock_df(stocks)

    async def mock_trend(code, date=None):
        qty = int(code)  # 코드가 곧 순매수수량
        return _make_investor_response(qty, qty * 2, -qty)

    broker.get_investor_trade_by_stock_daily = AsyncMock(side_effect=mock_trend)

    await bg_service.refresh_investor_ranking()

    buy_resp = bg_service.get_foreign_net_buy_ranking()
    assert len(buy_resp.data) == 30
    # 가장 큰 값이 1위여야 함 (종목 39 -> 코드 000390 -> 수량 390)
    assert buy_resp.data[0]["frgn_ntby_qty"] == "390"

    sell_resp = bg_service.get_foreign_net_sell_ranking()
    assert len(sell_resp.data) == 30
    # 가장 작은 값이 1위여야 함 (종목 0 -> 코드 000000 -> 수량 0)
    assert sell_resp.data[0]["frgn_ntby_qty"] == "0"

    # 기관도 30개 제한
    inst_buy = bg_service.get_inst_net_buy_ranking()
    assert len(inst_buy.data) == 30
    assert inst_buy.data[0]["orgn_ntby_qty"] == "780"  # 390*2


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
    # refresh_investor_ranking 호출 시 필요
    trading_service.get_latest_trading_date = AsyncMock(return_value="20250101")

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

def test_load_all_stocks_filters_etf(bg_service, mock_deps):
    """_load_all_stocks 메서드가 ETF/ETN 종목을 사전에 필터링하는지 검증"""
    _, mapper, _, _, _ = mock_deps

    mapper.df = _make_stock_df([
        ("005930", "삼성전자", "KOSPI"),
        ("000660", "SK하이닉스", "KOSPI"),
        ("123456", "KODEX 200", "KOSPI"),
        ("654321", "TIGER 200", "KOSPI"),
    ])

    stocks = bg_service._load_all_stocks()
    
    # 결과에서 코드와 이름 추출
    codes = [s[0] for s in stocks]
    names = [s[1] for s in stocks]
    
    # 일반 종목은 포함되어야 함
    assert "005930" in codes
    assert "000660" in codes
    
    # ETF 종목은 제외되어야 함
    assert "123456" not in codes
    assert "654321" not in codes
    
    # 필터링된 목록에 ETF 접두사가 포함된 종목이 없는지 재확인
    for name in names:
        assert not any(name.startswith(p) for p in _ETF_PREFIXES)

def test_load_all_stocks_filters_preferred_and_spac(bg_service, mock_deps):
    """_load_all_stocks가 우선주와 스팩(SPAC) 종목을 필터링하는지 검증."""
    _, mapper, _, _, _ = mock_deps

    # Mock DataFrame with common, preferred, and SPAC stocks
    mapper.df = _make_stock_df([
        ("005930", "삼성전자", "KOSPI"),
        ("005935", "삼성전자우", "KOSPI"),  # Preferred stock (ends with non-zero)
        ("123456", "미래에셋스팩1호", "KOSDAQ"), # SPAC
        ("000660", "SK하이닉스", "KOSDAQ"),
    ])

    # Call the method to be tested
    loaded_stocks = bg_service._load_all_stocks()

    # Extract codes for easier assertion
    loaded_codes = [stock[0] for stock in loaded_stocks]

    assert "005930" in loaded_codes
    assert "000660" in loaded_codes
    assert "005935" not in loaded_codes
    assert "123456" not in loaded_codes

@pytest.mark.asyncio
async def test_refresh_investor_ranking_optimization(bg_service, mock_deps):
    """refresh_investor_ranking 실행 시 필터링된 종목에 대해서만 API를 호출하는지 검증"""
    broker, mapper, _, _, _ = mock_deps

    mapper.df = _make_stock_df([
        ("005930", "삼성전자", "KOSPI"),
        ("000660", "SK하이닉스", "KOSPI"),
        ("123456", "KODEX 200", "KOSPI"),
    ])
    
    # API 응답 Mock (성공 케이스)
    broker.get_investor_trade_by_stock_daily = AsyncMock(return_value=ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="OK",
        data={"frgn_ntby_qty": "100", "orgn_ntby_qty": "200"}
    ))

    # 메서드 실행
    await bg_service.refresh_investor_ranking()

    # API 호출 횟수 검증 (ETF 제외된 2개 종목만 호출되어야 함)
    assert broker.get_investor_trade_by_stock_daily.call_count == 2
    
    # 실제로 호출된 종목 코드 확인
    called_codes = [c.args[0] for c in broker.get_investor_trade_by_stock_daily.call_args_list]
    assert "005930" in called_codes
    assert "000660" in called_codes
    assert "123456" not in called_codes


@pytest.mark.asyncio
async def test_refresh_investor_ranking_skipped_during_market_open(bg_service, mock_deps):
    """장 중에는 투자자 랭킹 갱신(직접 호출)을 스킵해야 한다."""
    broker, _, _, logger, time_manager = mock_deps
    time_manager.is_market_open.return_value = True  # 장 중으로 설정

    await bg_service.refresh_investor_ranking()

    broker.get_investor_trade_by_stock_daily.assert_not_called()
    logger.info.assert_any_call("장 운영 중이므로 투자자 랭킹 전체 갱신을 건너뜁니다.")


def test_chunked_helper():
    """_chunked 헬퍼 함수 테스트."""
    lst = [1, 2, 3, 4, 5]
    chunks = list(_chunked(lst, 2))
    assert chunks == [[1, 2], [3, 4], [5]]

    chunks = list(_chunked([], 2))
    assert chunks == []

    chunks = list(_chunked(lst, 10))
    assert chunks == [[1, 2, 3, 4, 5]]


@pytest.mark.asyncio
async def test_refresh_basic_ranking_exception_and_notification(bg_service, mock_deps):
    """기본 랭킹 갱신 중 예외 발생 시 로그 및 알림 테스트."""
    _, _, _, logger, _ = mock_deps
    bg_service._nm = AsyncMock()

    # asyncio.gather가 예외를 던지도록 설정하여 try-except 블록 진입 유도
    with patch("services.background_service.asyncio.gather", side_effect=Exception("Critical Error")):
        await bg_service.refresh_basic_ranking()

    logger.error.assert_called()
    bg_service._nm.emit.assert_awaited_with("SYSTEM", "error", "기본 랭킹 갱신 실패", "Critical Error")


@pytest.mark.asyncio
async def test_refresh_basic_ranking_success_notification(bg_service, mock_deps):
    """기본 랭킹 갱신 성공 시 알림 테스트."""
    bg_service._nm = AsyncMock()

    # Mock success responses
    bg_service._trading_service.get_top_rise_fall_stocks.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=[])
    bg_service._trading_service.get_top_volume_stocks.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=[])
    bg_service._trading_service.get_top_trading_value_stocks.return_value = ResCommonResponse(rt_cd="0", msg1="OK", data=[])

    await bg_service.refresh_basic_ranking()

    bg_service._nm.emit.assert_awaited()
    args = bg_service._nm.emit.call_args[0]
    assert args[0] == "API"
    assert args[1] == "info"
    assert "기본 랭킹 갱신 완료" in args[2]


@pytest.mark.asyncio
async def test_refresh_investor_ranking_notification(bg_service, mock_deps):
    """투자자 랭킹 갱신 성공/실패 알림 테스트."""
    broker, mapper, _, _, _ = mock_deps
    bg_service._nm = AsyncMock()
    mapper.df = _make_stock_df([("005930", "삼성전자", "KOSPI")])

    # 1. Success case
    broker.get_investor_trade_by_stock_daily = AsyncMock(return_value=_make_investor_response(100))
    broker.get_program_trade_by_stock_daily = AsyncMock(return_value=_make_program_response(100))

    await bg_service.refresh_investor_ranking()

    bg_service._nm.emit.assert_awaited()
    args = bg_service._nm.emit.call_args[0]
    assert args[0] == "API"
    assert "투자자 랭킹 갱신 완료" in args[2]

    # 2. Failure case
    bg_service._nm.reset_mock()
    with patch("services.background_service.asyncio.gather", side_effect=Exception("API Fail")):
        await bg_service.refresh_investor_ranking()

    bg_service._nm.emit.assert_awaited()
    args = bg_service._nm.emit.call_args[0]
    assert args[0] == "SYSTEM"
    assert "투자자 랭킹 갱신 실패" in args[2]


def test_check_and_trigger_refresh_no_loop(bg_service):
    """이벤트 루프가 없을 때 온디맨드 갱신 트리거 실패 처리 테스트."""

    bg_service._foreign_net_buy_cache = []
    bg_service._is_refreshing = False

    # asyncio.get_running_loop가 RuntimeError를 던지도록 설정
    with patch("asyncio.get_running_loop", side_effect=RuntimeError("No running loop")):
        with patch("asyncio.create_task") as mock_create_task:
            bg_service._check_and_trigger_refresh()

            mock_create_task.assert_not_called()
            bg_service._logger.warning.assert_called_with("이벤트 루프 없음 — 온디맨드 갱신 스킵")


@pytest.mark.asyncio
async def test_start_after_market_scheduler_exception_handling(bg_service, mock_deps):
    """스케줄러 루프 내 일반 예외 발생 시 처리 테스트."""
    import asyncio

    _, _, _, logger, time_manager = mock_deps
    time_manager.is_market_open.return_value = False

    # refresh_basic_ranking에서 예외 발생
    bg_service.refresh_basic_ranking = AsyncMock(side_effect=Exception("Scheduler Error"))

    # sleep을 모킹하여 루프를 한 번 돌고 종료(CancelledError)하도록 설정
    with patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError]):
         try:
             await bg_service.start_after_market_scheduler()
         except asyncio.CancelledError:
             pass

    logger.error.assert_called()
    assert "장마감 후 스케줄러 오류" in logger.error.call_args[0][0]


@pytest.mark.asyncio
async def test_refresh_investor_ranking_invalid_response_data(bg_service, mock_deps):
    """API 응답 데이터가 None이거나 dict가 아닐 때 처리 테스트."""
    broker, mapper, _, _, _ = mock_deps
    mapper.df = _make_stock_df([("005930", "삼성전자", "KOSPI")])

    # 1. data is None
    broker.get_investor_trade_by_stock_daily = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data=None))
    await bg_service.refresh_investor_ranking()
    assert len(bg_service._foreign_net_buy_cache) == 0

    # 2. data is not dict
    broker.get_investor_trade_by_stock_daily = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data="invalid"))
    await bg_service.refresh_investor_ranking()
    assert len(bg_service._foreign_net_buy_cache) == 0

    # 3. program data invalid
    broker.get_investor_trade_by_stock_daily = AsyncMock(return_value=_make_investor_response(100))
    broker.get_program_trade_by_stock_daily = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK", data="invalid"))
    await bg_service.refresh_investor_ranking()
    # 투자자 데이터는 수집되었으나 프로그램 데이터는 수집되지 않음
    assert len(bg_service._foreign_net_buy_cache) == 1
    assert len(bg_service._program_net_buy_cache) == 0


@pytest.mark.asyncio
async def test_on_demand_trigger_skipped_during_market_open(bg_service, mock_deps):
    """장 중에는 온디맨드(on-demand) 랭킹 갱신이 트리거되지 않아야 한다."""
    import asyncio

    _, _, _, logger, time_manager = mock_deps
    time_manager.is_market_open.return_value = True  # 장 중으로 설정

    # 캐시가 비어있는 상태
    bg_service._foreign_net_buy_cache = []
    bg_service._is_refreshing = False

    with patch.object(asyncio, "create_task") as mock_create_task:
        # 랭킹 조회를 시도하면 내부적으로 _check_and_trigger_refresh가 호출됨
        resp = bg_service.get_foreign_net_buy_ranking()

        # 장 중이므로 온디맨드 갱신(create_task)이 호출되지 않아야 함
        mock_create_task.assert_not_called()
        # 갱신이 트리거되지 않고 "수집 중" 메시지만 반환되어야 함
        assert "수집 중" in resp.msg1


# ── 프로그램 순매수/순매도 랭킹 ──────────────────────────────

def test_get_program_net_buy_ranking_empty_cache(bg_service):
    """프로그램 캐시가 비어있으면 빈 data 반환."""
    resp = bg_service.get_program_net_buy_ranking()
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert resp.data == []


@pytest.mark.asyncio
async def test_refresh_program_ranking_basic(bg_service, mock_deps):
    """프로그램 순매수/순매도 랭킹 정렬 검증."""
    broker, mapper, _, _, _ = mock_deps

    mapper.df = _make_stock_df([
        ("005930", "삼성전자", "KOSPI"),
        ("000660", "SK하이닉스", "KOSPI"),
        ("035420", "NAVER", "KOSDAQ"),
    ])

    broker.get_investor_trade_by_stock_daily = AsyncMock(
        return_value=_make_investor_response(100, 50, -30)
    )

    # 프로그램 순매수대금: 삼성(500만) > NAVER(300만) > SK(-200만)
    async def mock_program(code, date=None):
        data = {
            "005930": _make_program_response(ntby_tr_pbmn=5000000, ntby_qty=500),
            "000660": _make_program_response(ntby_tr_pbmn=-2000000, ntby_qty=-200),
            "035420": _make_program_response(ntby_tr_pbmn=3000000, ntby_qty=300),
        }
        return data.get(code, ResCommonResponse(rt_cd="1", msg1="Error", data=None))

    broker.get_program_trade_by_stock_daily = AsyncMock(side_effect=mock_program)

    await bg_service.refresh_investor_ranking()

    # 프로그램 순매수 상위: 삼성(500만) > NAVER(300만) > SK(-200만)
    buy_resp = bg_service.get_program_net_buy_ranking()
    assert buy_resp.rt_cd == ErrorCode.SUCCESS.value
    assert len(buy_resp.data) == 3
    assert buy_resp.data[0]["hts_kor_isnm"] == "삼성전자"
    assert buy_resp.data[0]["whol_smtn_ntby_tr_pbmn"] == "5000000"
    assert buy_resp.data[0]["data_rank"] == "1"
    assert buy_resp.data[1]["hts_kor_isnm"] == "NAVER"

    # 프로그램 순매도 상위: SK(-200만)
    sell_resp = bg_service.get_program_net_sell_ranking()
    assert sell_resp.rt_cd == ErrorCode.SUCCESS.value
    assert sell_resp.data[0]["hts_kor_isnm"] == "SK하이닉스"
    assert sell_resp.data[0]["data_rank"] == "1"


# ── 거래대금 랭킹 및 예외 처리 추가 테스트 ────────────────────

def test_get_trading_value_ranking_empty_cache(bg_service):
    """거래대금 랭킹 캐시가 비어있으면 빈 data + 안내 메시지 반환."""
    resp = bg_service.get_trading_value_ranking()
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert resp.data == []
    assert "수집 중" in resp.msg1


def test_get_trading_value_ranking_populated(bg_service):
    """거래대금 랭킹 캐시가 있으면 해당 데이터 반환."""
    bg_service._trading_value_cache = [{"hts_kor_isnm": "TestStock", "data_rank": "1"}]
    resp = bg_service.get_trading_value_ranking()
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert len(resp.data) == 1
    assert resp.data[0]["hts_kor_isnm"] == "TestStock"


@pytest.mark.asyncio
async def test_refresh_investor_ranking_corrects_acml_tr_pbmn(bg_service, mock_deps):
    """투자자 데이터의 거래대금이 0일 때 프로그램 데이터로 보정되는지 검증."""
    broker, mapper, _, _, _ = mock_deps
    
    mapper.df = _make_stock_df([("005930", "삼성전자", "KOSPI")])
    
    # 투자자 데이터: acml_tr_pbmn = 0
    async def mock_investor(code, date=None):
        resp = _make_investor_response(100)
        resp.data["acml_tr_pbmn"] = "0"
        return resp
    
    # 프로그램 데이터: acml_tr_pbmn = 100000000 (헬퍼 기본값)
    async def mock_program(code, date=None):
        return _make_program_response(ntby_tr_pbmn=500)

    broker.get_investor_trade_by_stock_daily = AsyncMock(side_effect=mock_investor)
    broker.get_program_trade_by_stock_daily = AsyncMock(side_effect=mock_program)

    await bg_service.refresh_investor_ranking()

    # 거래대금 랭킹 확인
    tv_resp = bg_service.get_trading_value_ranking()
    assert len(tv_resp.data) == 1
    # 보정된 값 확인 (헬퍼 기본값 1억)
    assert tv_resp.data[0]["acml_tr_pbmn"] == "100000000"


@pytest.mark.asyncio
async def test_progress_running_flag_lifecycle(bg_service, mock_deps):
    """진행률의 running 플래그가 갱신 시작/종료 시 올바르게 전환되는지 검증.

    프론트엔드 진행률 폴링은 running=True → False 전환을 감지하여 자동 새로고침하므로,
    이 전환이 정확해야 한다.
    """
    broker, mapper, _, _, _ = mock_deps
    mapper.df = _make_stock_df([("005930", "삼성전자", "KOSPI")])

    # 갱신 중 progress 상태 캡처
    captured_progress = []
    original_gather = __import__('asyncio').gather

    async def mock_investor(code, date=None):
        # API 호출 시점의 progress 상태 캡처
        captured_progress.append(dict(bg_service._progress))
        return _make_investor_response(100, 50, -30)

    broker.get_investor_trade_by_stock_daily = AsyncMock(side_effect=mock_investor)

    # 초기 상태
    assert bg_service._progress["running"] is False

    await bg_service.refresh_investor_ranking()

    # API 호출 시점에 running=True였어야 함
    assert len(captured_progress) > 0
    assert captured_progress[0]["running"] is True
    assert captured_progress[0]["total"] > 0

    # 완료 후 running=False, processed >= total
    p = bg_service.get_investor_ranking_progress()
    assert p["running"] is False
    assert p["processed"] > 0
    assert p["processed"] >= p["total"]
    assert p["collected"] > 0


@pytest.mark.asyncio
async def test_progress_reset_on_failure(bg_service, mock_deps):
    """갱신 중 예외 발생 시에도 running=False로 복원되어야 한다.

    프론트엔드 폴링이 무한루프에 빠지지 않도록 finally에서 반드시 해제.
    """
    broker, mapper, _, _, _ = mock_deps
    mapper.df = _make_stock_df([("005930", "삼성전자", "KOSPI")])

    # API 호출 시 예외 발생
    broker.get_investor_trade_by_stock_daily = AsyncMock(side_effect=Exception("Network Error"))
    broker.get_program_trade_by_stock_daily = AsyncMock(side_effect=Exception("Network Error"))

    await bg_service.refresh_investor_ranking()

    p = bg_service.get_investor_ranking_progress()
    assert p["running"] is False
    assert bg_service._is_refreshing is False


@pytest.mark.asyncio
async def test_progress_total_set_before_api_calls(bg_service, mock_deps):
    """total이 API 호출 전에 설정되어 프론트에서 0/0이 아닌 진행률을 보여줄 수 있는지 검증."""
    broker, mapper, _, _, _ = mock_deps
    mapper.df = _make_stock_df([
        ("005930", "삼성전자", "KOSPI"),
        ("000660", "SK하이닉스", "KOSPI"),
        ("035420", "NAVER", "KOSDAQ"),
    ])

    total_at_first_call = None

    async def mock_investor(code, date=None):
        nonlocal total_at_first_call
        if total_at_first_call is None:
            total_at_first_call = bg_service._progress["total"]
        return _make_investor_response(100, 50, -30)

    broker.get_investor_trade_by_stock_daily = AsyncMock(side_effect=mock_investor)

    await bg_service.refresh_investor_ranking()

    # 첫 API 호출 시점에 이미 total이 설정되어 있어야 함
    assert total_at_first_call == 3


@pytest.mark.asyncio
async def test_refresh_investor_ranking_no_target_date(bg_service, mock_deps):
    """최근 거래일 조회 실패 시 갱신 중단."""
    _, _, _, logger, _ = mock_deps
    
    # target_date None 설정
    bg_service._trading_service.get_latest_trading_date = AsyncMock(return_value=None)
    
    await bg_service.refresh_investor_ranking()
    
    logger.error.assert_called_with("최근 거래일을 확인할 수 없어 투자자 랭킹 갱신을 중단합니다.")
    assert bg_service._is_refreshing is False


@pytest.mark.asyncio
async def test_refresh_basic_ranking_partial_failure(bg_service, mock_deps):
    """기본 랭킹 조회 중 일부 실패 시 성공한 것만 캐시."""
    _, _, _, logger, _ = mock_deps
    
    # TradingService Mock 설정
    # get_top_rise_fall_stocks는 2번 호출됨 (True, False)
    bg_service._trading_service.get_top_rise_fall_stocks = AsyncMock(side_effect=[
        ResCommonResponse(rt_cd="0", msg1="OK", data=[{"name": "rise"}]), # rise success
        Exception("Fall API Error") # fall fail
    ])
    bg_service._trading_service.get_top_volume_stocks = AsyncMock(
        return_value=ResCommonResponse(rt_cd="0", msg1="OK", data=[{"name": "vol"}])
    )
    bg_service._trading_service.get_top_trading_value_stocks = AsyncMock(
        return_value=ResCommonResponse(rt_cd="0", msg1="OK", data=[{"name": "tv"}])
    )

    await bg_service.refresh_basic_ranking()

    # 성공한 것은 캐시에 있어야 함
    assert "rise" in bg_service._basic_ranking_cache
    assert "volume" in bg_service._basic_ranking_cache
    assert "trading_value" in bg_service._basic_ranking_cache
    
    # 실패한 것은 캐시에 없어야 함
    assert "fall" not in bg_service._basic_ranking_cache
    
    # 에러 로그 확인
    logger.error.assert_any_call("기본 랭킹 'fall' 조회 실패: Fall API Error")


@pytest.mark.asyncio
async def test_refresh_investor_ranking_skipped_during_market_open(bg_service, mock_deps):
    """장 중에는 투자자 랭킹 갱신을 스킵해야 한다."""
    broker, _, _, logger, time_manager = mock_deps
    time_manager.is_market_open.return_value = True  # 장 중으로 설정

    await bg_service.refresh_investor_ranking()

    broker.get_investor_trade_by_stock_daily.assert_not_called()
    logger.info.assert_any_call("장 운영 중이므로 투자자 랭킹 전체 갱신을 건너뜁니다.")


# ── 재시도 로직 및 스케줄러 최적화 테스트 ───────────────────

@pytest.mark.asyncio
async def test_fetch_with_retry_success_first_try(bg_service):
    """첫 시도 성공 시 재시도 없이 반환."""
    mock_api = AsyncMock(return_value=ResCommonResponse(rt_cd="0", msg1="OK"))

    resp = await bg_service._fetch_with_retry(mock_api, "arg1")

    assert resp.rt_cd == "0"
    assert mock_api.call_count == 1


@pytest.mark.asyncio
async def test_fetch_with_retry_fail_then_success(bg_service, mock_deps):
    """첫 시도 실패 후 두 번째 성공."""
    _, _, _, logger, _ = mock_deps

    # 1. API Error response (rt_cd != 0)
    # 2. Success response
    fail_resp = ResCommonResponse(rt_cd="1", msg1="Limit Exceeded")
    success_resp = ResCommonResponse(rt_cd="0", msg1="OK")

    mock_api = AsyncMock(side_effect=[fail_resp, success_resp])

    # sleep mock to speed up test
    with patch("asyncio.sleep", new_callable=AsyncMock):
        resp = await bg_service._fetch_with_retry(mock_api, "arg1")

    assert resp.rt_cd == "0"
    assert mock_api.call_count == 2
    logger.warning.assert_called()  # Warning logged for first failure


@pytest.mark.asyncio
async def test_fetch_with_retry_exception_then_success(bg_service, mock_deps):
    """첫 시도 예외 발생 후 두 번째 성공."""
    _, _, _, logger, _ = mock_deps

    success_resp = ResCommonResponse(rt_cd="0", msg1="OK")
    mock_api = AsyncMock(side_effect=[Exception("Network Error"), success_resp])

    with patch("asyncio.sleep", new_callable=AsyncMock):
        resp = await bg_service._fetch_with_retry(mock_api, "arg1")

    assert resp.rt_cd == "0"
    assert mock_api.call_count == 2
    logger.error.assert_called()  # Error logged for exception


@pytest.mark.asyncio
async def test_fetch_with_retry_all_fail(bg_service, mock_deps):
    """모든 시도 실패 시 None 반환."""
    _, _, _, logger, _ = mock_deps

    fail_resp = ResCommonResponse(rt_cd="1", msg1="Error")
    mock_api = AsyncMock(return_value=fail_resp)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        resp = await bg_service._fetch_with_retry(mock_api, "arg1")

    assert resp is None
    assert mock_api.call_count == 3  # Max retries default is 3
    # Check final error log
    args, _ = logger.error.call_args
    assert "최종 실패" in args[0]


@pytest.mark.asyncio
async def test_start_after_market_scheduler_waits_efficiently(bg_service, mock_deps):
    """장 중일 때 효율적인 대기 시간(get_sleep_seconds_until_market_close)을 사용하는지 검증."""
    import asyncio

    _, _, _, logger, time_manager = mock_deps

    # 1. Market is Open
    time_manager.is_market_open.return_value = True
    # 2. TimeManager suggests waiting 1000 seconds
    time_manager.get_sleep_seconds_until_market_close.return_value = 1000.0

    # We want the loop to run once then exit via CancelledError
    async def mock_sleep(sec):
        if sec == 1000.0:
            raise asyncio.CancelledError("End Test")
        return None

    with patch("asyncio.sleep", side_effect=mock_sleep) as patched_sleep:
        try:
            await bg_service.start_after_market_scheduler()
        except asyncio.CancelledError:
            pass

    # Verify asyncio.sleep was called with the value from time_manager


@pytest.mark.asyncio
async def test_refresh_investor_ranking_calls_telegram_reporter(bg_service, mock_deps):
    """투자자 랭킹 갱신 완료 후 TelegramReporter 호출 검증"""
    broker, mapper, _, _, _ = mock_deps
    
    # Reporter Mock 주입
    mock_reporter = AsyncMock()
    bg_service._telegram_reporter = mock_reporter
    
    # 데이터 설정
    mapper.df = _make_stock_df([("005930", "삼성전자", "KOSPI")])
    broker.get_investor_trade_by_stock_daily = AsyncMock(return_value=_make_investor_response(100))
    broker.get_program_trade_by_stock_daily = AsyncMock(return_value=_make_program_response(100))

    # Act
    await bg_service.refresh_investor_ranking()

    # Assert
    mock_reporter.send_ranking_report.assert_awaited_once()
    call_args = mock_reporter.send_ranking_report.call_args
    rankings = call_args[0][0]
    report_date = call_args[1]['report_date']
    
    assert 'foreign_buy' in rankings
    assert 'trading_value' in rankings
    assert report_date == "20250101" # fixture에서 설정한 날짜

@pytest.mark.asyncio
async def test_refresh_investor_ranking_reporter_exception_handled(bg_service, mock_deps):
    """리포트 전송 중 예외가 발생해도 서비스가 중단되지 않고 로깅되는지 검증"""
    broker, mapper, _, logger, _ = mock_deps
    
    mock_reporter = AsyncMock()
    mock_reporter.send_ranking_report.side_effect = Exception("Telegram Error")
    bg_service._telegram_reporter = mock_reporter
    
    mapper.df = _make_stock_df([("005930", "삼성전자", "KOSPI")])
    broker.get_investor_trade_by_stock_daily = AsyncMock(return_value=_make_investor_response(100))

    await bg_service.refresh_investor_ranking()

    # 예외가 로깅되었는지 확인
    logger.error.assert_called()
    assert "텔레그램 랭킹 리포트 전송 중 오류" in str(logger.error.call_args)

@pytest.mark.asyncio
async def test_refresh_investor_ranking_handles_none_response(bg_service, mock_deps):
    """
    refresh_investor_ranking에서 _fetch_with_retry가 None을 반환할 때(최종 실패),
    크래시 없이 해당 종목을 스킵하는지 검증.
    """
    _, mapper, _, _, _ = mock_deps

    # 종목 2개 설정
    mapper.df = _make_stock_df([
        ("005930", "삼성전자", "KOSPI"),
        ("000660", "SK하이닉스", "KOSPI"),
    ])

    # 삼성전자 -> 성공 응답, SK하이닉스 -> None (최종 실패 시뮬레이션)
    async def mock_fetch_with_retry(api_call, code, date):
        if code == "005930":
            return _make_investor_response(100)
        return None  # SK하이닉스 실패

    # _fetch_with_retry를 Mocking하여 동작 제어
    with patch.object(bg_service, '_fetch_with_retry', side_effect=mock_fetch_with_retry):
        await bg_service.refresh_investor_ranking()

    # 결과: 삼성전자 데이터만 캐시에 있어야 함
    assert len(bg_service._foreign_net_buy_cache) == 1
    assert bg_service._foreign_net_buy_cache[0]["stck_shrn_iscd"] == "005930"