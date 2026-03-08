"""
BackgroundService 단위 테스트.
전체 종목 순회 → 외국인/기관/개인 순매수/순매도 랭킹 생성 로직 검증.
"""
import pytest
import pandas as pd
from unittest.mock import MagicMock, AsyncMock
from services.background_service import BackgroundService, _ETF_PREFIXES
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
    from unittest.mock import patch

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