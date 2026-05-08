# tests/integration_test/test_it_strategy_scan.py
"""전략 scan() 중간 깊이 통합 테스트.

실제 서비스 객체(StockQueryService → TradingService → BrokerAPIWrapper → API)를 사용하고,
HTTP 네트워크 호출만 mock하여 전략 → 서비스 → 브로커 전체 스택을 검증한다.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock

from tests.integration_test.conftest import (
    _get_quotations_api_from_ctx,
    patch_session_get,
    patch_session_get_router,
    make_http_response,
)


# ============================================================================
# 테스트 데이터 팩토리
# ============================================================================

def _make_trading_value_stock(code="005930", name="삼성전자", vol="5000000"):
    """거래대금 상위 종목 1건."""
    return {
        "hts_kor_isnm": name,
        "mksc_shrn_iscd": code,
        "stck_shrn_iscd": code,
        "acml_vol": vol,
        "data_rank": "1",
        "stck_prpr": "70000",
        "prdy_vrss": "1000",
        "prdy_vrss_sign": "2",
        "prdy_ctrt": "1.45",
        "acml_tr_pbmn": "500000000000",
    }


def _make_trading_value_response(stocks=None):
    """거래대금 상위 응답 payload."""
    if stocks is None:
        stocks = [_make_trading_value_stock()]
    return {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리 되었습니다.",
        "output": stocks,
    }


def _make_current_price_output(
    code="005930", price="72000", open_price="70000",
    high="73000", low="69500", vol="5000000",
    pgtr_ntby_qty="50000", prev_close="70000",
    acml_tr_pbmn="500000000000",
):
    """현재가 조회 output (ResStockFullInfoApiOutput 전체 필드)."""
    return {
        "iscd_stat_cls_code": "55",
        "marg_rate": "20",
        "rprs_mrkt_kor_name": "코스피",
        "bstp_kor_isnm": "전기전자",
        "temp_stop_yn": "N",
        "oprc_rang_cont_yn": "N",
        "clpr_rang_cont_yn": "N",
        "crdt_able_yn": "Y",
        "grmn_rate_cls_code": "20",
        "elw_pblc_yn": "N",
        "stck_prpr": price,
        "prdy_vrss": str(int(price) - int(prev_close)),
        "prdy_vrss_sign": "2",
        "prdy_ctrt": str(round((int(price) - int(prev_close)) / int(prev_close) * 100, 2)),
        "acml_tr_pbmn": acml_tr_pbmn,
        "acml_vol": vol,
        "prdy_vrss_vol_rate": "125.00",
        "stck_oprc": open_price,
        "stck_hgpr": high,
        "stck_lwpr": low,
        "stck_mxpr": "91000",
        "stck_llam": "49000",
        "stck_sdpr": prev_close,
        "wghn_avrg_stck_prc": "71000",
        "hts_frgn_ehrt": "55.00",
        "frgn_ntby_qty": "100000",
        "pgtr_ntby_qty": pgtr_ntby_qty,
        "pvt_scnd_dmrs_prc": "69000",
        "pvt_frst_dmrs_prc": "69500",
        "pvt_pont_val": "70500",
        "pvt_frst_dmsp_prc": "71500",
        "pvt_scnd_dmsp_prc": "72000",
        "dmrs_val": "69000",
        "dmsp_val": "72000",
        "cpfn": "5000",
        "rstc_wdth_prc": "0",
        "stck_fcam": "5000",
        "stck_sspr": "5000",
        "aspr_unit": "100",
        "hts_deal_qty_unit_val": "1",
        "lstn_stcn": "5969782550",
        "hts_avls": "417885",
        "per": "12.50",
        "pbr": "1.30",
        "stck_dryy_hgpr": "77000",
        "stck_dryy_lwpr": "55000",
        "w52_hgpr": "77000",
        "w52_lwpr": "55000",
        "w52_hgpr_date": "20260101",
        "w52_lwpr_date": "20250601",
        "w52_hgpr_vrss_prpr_ctrt": "-6.49",
        "w52_lwpr_vrss_prpr_ctrt": "30.91",
        "stck_prdy_clpr": prev_close,
        "whol_loan_rmnd_rate": "0.50",
        "ssts_yn": "Y",
        "stck_shrn_iscd": code,
        "fcam_cnnm": "원",
        "cpfn_cnnm": "원",
        "frgn_hldn_qty": "3283380000",
        "vi_cls_code": "N",
        "ovtm_vi_cls_code": "N",
        "last_ssts_cntg_qty": "0",
        "invt_caful_yn": "N",
        "mrkt_warn_cls_code": "00",
        "short_over_yn": "N",
        "sltr_yn": "N",
        # 추가 필수 필드
        "bps": "35000",
        "eps": "5600",
        "d250_hgpr": "80000",
        "d250_hgpr_date": "20250901",
        "d250_hgpr_vrss_prpr_rate": "-10.00",
        "d250_lwpr": "50000",
        "d250_lwpr_date": "20250301",
        "d250_lwpr_vrss_prpr_rate": "40.00",
        "dryy_hgpr_date": "20260101",
        "dryy_hgpr_vrss_prpr_rate": "-6.49",
        "dryy_lwpr_date": "20260201",
        "dryy_lwpr_vrss_prpr_rate": "30.91",
        "stac_month": "12",
        "vol_tnrt": "0.08",
        "mang_issu_cls_code": "",
        "new_hgpr_lwpr_cls_code": "",
    }


def _make_current_price_response(**kwargs):
    """현재가 조회 전체 응답."""
    return {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리 되었습니다.",
        "output": _make_current_price_output(**kwargs),
    }


def _make_conclusion_response(tday_rltv="135.50"):
    """체결 정보 응답 (체결강도 포함)."""
    return {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리 되었습니다.",
        "output": [
            {
                "stck_cntg_hour": "130500",
                "stck_prpr": "72000",
                "prdy_vrss": "2000",
                "prdy_vrss_sign": "2",
                "cntg_vol": "500",
                "tday_rltv": tday_rltv,
                "prdy_ctrt": "2.86",
            }
        ],
    }


def _make_ohlcv_data(days=25, base_close=70000, base_vol=1000000):
    """일봉 OHLCV 데이터 생성."""
    data = []
    for i in range(days):
        close = base_close + (i * 100)
        data.append({
            "date": f"2026{(2 + i // 28):02d}{(1 + i % 28):02d}",
            "open": close - 200,
            "high": close + 500,
            "low": close - 500,
            "close": close,
            "volume": base_vol + (i * 10000),
        })
    return data


def _make_ohlcv_api_response(ohlcv_list):
    """OHLCV 리스트를 한국투자증권 API 일봉 조회(inquire-daily-itemchartprice) 응답 포맷으로 변환."""
    output2 = []
    # KIS API는 최신 데이터가 상단에 위치하므로(내림차순) 원본(오름차순)을 뒤집어준다.
    for row in reversed(ohlcv_list):
        output2.append({
            "stck_bsop_date": row["date"],
            "stck_oprc": str(row["open"]),
            "stck_hgpr": str(row["high"]),
            "stck_lwpr": str(row["low"]),
            "stck_clpr": str(row["close"]),
            "acml_vol": str(row["volume"]),
            "date": row["date"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
        })
    return {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리 되었습니다.",
        "output1": {},
        "output2": output2
    }


# ============================================================================
# VolumeBreakoutLiveStrategy scan() IT
# ============================================================================

class TestVolumeBreakoutLiveScan:
    """거래량 돌파 라이브 전략 scan() 통합 테스트."""

    async def test_scan_generates_buy_signal(self, deep_paper_ctx, mocker):
        """시가 대비 상승 조건 충족 시 BUY 시그널 생성."""
        from strategies.volume_breakout_live_strategy import VolumeBreakoutLiveStrategy
        from strategies.volume_breakout_strategy import VolumeBreakoutConfig

        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)
        # 거래대금/거래량 랭킹은 실전 전용 API. HTTP 스택 검증이 목적이므로 임시 해제.
        quot_api._env.is_paper_trading = False

        # 거래대금 상위 → 현재가 조회 순서대로 응답
        trading_value_resp = _make_trading_value_response([
            _make_trading_value_stock("005930", "삼성전자", "5000000"),
        ])
        # 시가 70000, 현재가 74000 → 시가대비 +5.7% (trigger_pct=3.0 기본값 통과)
        price_resp = _make_current_price_response(
            code="005930", price="74000", open_price="70000",
            high="74500", vol="5000000",
        )

        call_count = 0
        async def _side_effect(url, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            u = str(url)
            if "volume-rank" in u or "ranking" in u:
                return make_http_response(trading_value_resp)
            return make_http_response(price_resp)

        mocker.patch.object(
            quot_api._async_session, "get",
            new_callable=AsyncMock, side_effect=_side_effect,
        )
        deep_paper_ctx.broker._stock_mapper.get_name_by_code = MagicMock(return_value="삼성전자")

        config = VolumeBreakoutConfig(trigger_pct=3.0)
        strategy = VolumeBreakoutLiveStrategy(
            stock_query_service=deep_paper_ctx.stock_query_service,
            market_clock=deep_paper_ctx.market_clock,
            config=config,
        )

        signals = await strategy.scan()

        assert len(signals) >= 1
        sig = signals[0]
        assert sig.code == "005930"
        assert sig.action == "BUY"
        assert sig.strategy_name == "거래량돌파"
        assert sig.price == 74000

    async def test_scan_no_signal_when_below_trigger(self, deep_paper_ctx, mocker):
        """시가 대비 변동률이 trigger_pct 미만이면 시그널 없음."""
        from strategies.volume_breakout_live_strategy import VolumeBreakoutLiveStrategy
        from strategies.volume_breakout_strategy import VolumeBreakoutConfig

        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)

        trading_value_resp = _make_trading_value_response()
        # 시가 70000, 현재가 70500 → +0.7% (trigger_pct=3.0 미달)
        price_resp = _make_current_price_response(
            code="005930", price="70500", open_price="70000",
        )

        async def _side_effect(url, *args, **kwargs):
            u = str(url)
            if "volume-rank" in u or "ranking" in u:
                return make_http_response(trading_value_resp)
            return make_http_response(price_resp)

        mocker.patch.object(
            quot_api._async_session, "get",
            new_callable=AsyncMock, side_effect=_side_effect,
        )
        deep_paper_ctx.broker._stock_mapper.get_name_by_code = MagicMock(return_value="삼성전자")

        config = VolumeBreakoutConfig(trigger_pct=3.0)
        strategy = VolumeBreakoutLiveStrategy(
            stock_query_service=deep_paper_ctx.stock_query_service,
            market_clock=deep_paper_ctx.market_clock,
            config=config,
        )

        signals = await strategy.scan()
        assert len(signals) == 0


# ============================================================================
# ProgramBuyFollowStrategy scan() IT
# ============================================================================

class TestProgramBuyFollowScan:
    """프로그램 매수 추종 전략 scan() 통합 테스트."""

    async def test_scan_generates_signal_with_positive_pgtr(self, deep_paper_ctx, mocker):
        """프로그램 순매수가 양수인 종목에 BUY 시그널 생성."""
        from strategies.program_buy_follow_strategy import ProgramBuyFollowStrategy, ProgramBuyFollowConfig

        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)
        # 거래대금 랭킹은 실전 전용 API. HTTP 스택 검증이 목적이므로 임시 해제.
        quot_api._env.is_paper_trading = False
    
        trading_value_resp = _make_trading_value_response([
            _make_trading_value_stock("005930", "삼성전자"),
        ])
        # pgtr_ntby_qty=50000 (양수 → 매수 시그널)
        # [수정] acml_tr_pbmn (누적거래대금) 필드를 추가하여 비율 계산이 가능하도록 함
        # PG매수금액 = 50000 * 72000 = 36억
        # PG비율 = 36억 / 700억 * 100 = 5.14% (기준 5% 통과)
        price_resp = _make_current_price_response(
            code="005930", price="72000", pgtr_ntby_qty="50000",
            acml_tr_pbmn="70000000000"
        )
    
        async def _side_effect(url, *args, **kwargs):
            u = str(url)
            if "volume-rank" in u or "ranking" in u:
                return make_http_response(trading_value_resp)
            return make_http_response(price_resp)
    
        mocker.patch.object(
            quot_api._async_session, "get",
            new_callable=AsyncMock, side_effect=_side_effect,
        )
    
        # [수정] 테스트의 의도를 명확히 하기 위해 min_program_buy_ratio 설정 추가
        config = ProgramBuyFollowConfig(min_program_net_buy=0, min_program_buy_ratio=5.0)
        strategy = ProgramBuyFollowStrategy(
            stock_query_service=deep_paper_ctx.stock_query_service,
            market_clock=deep_paper_ctx.market_clock,
            config=config,
        )
    
        signals = await strategy.scan()
    
        assert len(signals) >= 1

    async def test_scan_no_signal_when_pgtr_negative(self, deep_paper_ctx, mocker):
        """프로그램 순매수가 음수면 시그널 없음."""
        from strategies.program_buy_follow_strategy import ProgramBuyFollowStrategy, ProgramBuyFollowConfig

        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)

        trading_value_resp = _make_trading_value_response()
        price_resp = _make_current_price_response(
            code="005930", price="72000", pgtr_ntby_qty="-10000",
        )

        async def _side_effect(url, *args, **kwargs):
            u = str(url)
            if "volume-rank" in u or "ranking" in u:
                return make_http_response(trading_value_resp)
            return make_http_response(price_resp)

        mocker.patch.object(
            quot_api._async_session, "get",
            new_callable=AsyncMock, side_effect=_side_effect,
        )

        config = ProgramBuyFollowConfig(min_program_net_buy=0)
        strategy = ProgramBuyFollowStrategy(
            stock_query_service=deep_paper_ctx.stock_query_service,
            market_clock=deep_paper_ctx.market_clock,
            config=config,
        )

        signals = await strategy.scan()
        assert len(signals) == 0


# ============================================================================
# TraditionalVolumeBreakoutStrategy scan() IT
# ============================================================================

class TestTraditionalVolumeBreakoutScan:
    """전통적 거래량 돌파 전략 scan() 통합 테스트."""

    async def test_scan_generates_signal_on_breakout(self, deep_paper_ctx, mocker):
        """20일 최고가 돌파 + 거래량 돌파 시 BUY 시그널."""
        from strategies.traditional_volume_breakout_strategy import (
            TraditionalVolumeBreakoutStrategy, TraditionalVBConfig,
        )

        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)
        quot_api._env.is_paper_trading = False

        # 1. 기초 데이터 준비 (HTTP API 응답)
        ohlcv_list = _make_ohlcv_data(days=25, base_close=70000, base_vol=1000000)
        ohlcv_api_resp = _make_ohlcv_api_response(ohlcv_list)

        # 현재가 응답
        price_resp = _make_current_price_response(
            code="005930", price="75000", vol="3000000", high="75500"
        )

        # 거래대금 상위 종목 응답
        trading_value_resp = _make_trading_value_response([
            _make_trading_value_stock("005930")
        ])

        # 2. HTTP 네트워크 레이어 Mocking (서비스 레이어 Mock 제거)
        patch_session_get_router(quot_api, mocker, {
            "volume-rank": trading_value_resp,
            "ranking": trading_value_resp,
            "inquire-daily-itemchartprice": ohlcv_api_resp,
            "inquire-price": price_resp,
        })

        # 3. 시간 및 환경 설정
        from repositories.stock_code_repository import StockCodeRepository
        mock_mapper = MagicMock(spec=StockCodeRepository)
        mock_mapper.get_name_by_code = MagicMock(return_value="삼성전자")
        mock_mapper.is_kosdaq = MagicMock(return_value=True)
        
        from datetime import datetime
        from pytz import timezone
        kst = timezone("Asia/Seoul")
        mock_tm = MagicMock()
        mock_tm.get_current_kst_time.return_value = datetime(2026, 3, 8, 12, 15, tzinfo=kst)
        mock_tm.get_market_open_time.return_value = datetime(2026, 3, 8, 9, 0, tzinfo=kst)
        mock_tm.get_market_close_time.return_value = datetime(2026, 3, 8, 15, 30, tzinfo=kst)

        config = TraditionalVBConfig(
            min_avg_trading_value_5d=0,
            near_high_pct=100.0,
        )

        # DB-first 경로 차단: 실제 DB OHLCV 데이터가 HTTP mock을 우회하지 않도록
        deep_paper_ctx.market_data_service._stock_repo = None

        # 4. 전략 실행
        strategy = TraditionalVolumeBreakoutStrategy(
            stock_query_service=deep_paper_ctx.stock_query_service,
            stock_code_repository=mock_mapper,
            market_clock=mock_tm,
            config=config,
        )
        strategy._watchlist_date = ""
        strategy._position_state.clear()

        signals = await strategy.scan()

        # 5. 검증
        assert len(signals) >= 1
        sig = signals[0]
        assert sig.code == "005930"
        assert sig.action == "BUY"
        assert sig.price == 75000

    async def test_scan_no_signal_price_below_high(self, deep_paper_ctx, mocker):
        """현재가가 20일 최고가 이하면 시그널 없음."""
        from strategies.traditional_volume_breakout_strategy import (
            TraditionalVolumeBreakoutStrategy, TraditionalVBConfig,
        )
        from repositories.stock_code_repository import StockCodeRepository

        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)

        trading_value_resp = _make_trading_value_response()
        ohlcv_data = _make_ohlcv_data(days=25, base_close=70000, base_vol=1000000)

        # 현재가 71000 → 20일 최고가(~72900 high) 이하 → 돌파 안됨
        price_resp = _make_current_price_response(
            code="005930", price="71000", open_price="70000",
            high="71500", vol="3000000",
        )

        ohlcv_api_resp = _make_ohlcv_api_response(ohlcv_data)

        patch_session_get_router(quot_api, mocker, {
            "volume-rank": trading_value_resp,
            "ranking": trading_value_resp,
            "inquire-daily-itemchartprice": ohlcv_api_resp,
            "inquire-price": price_resp,
        })

        mock_mapper = MagicMock(spec=StockCodeRepository)
        mock_mapper.get_name_by_code = MagicMock(return_value="삼성전자")
        mock_mapper.is_kosdaq = MagicMock(return_value=True)

        from datetime import datetime
        from pytz import timezone
        kst = timezone("Asia/Seoul")
        mock_tm = MagicMock()
        mock_tm.get_current_kst_time.return_value = datetime(2026, 3, 8, 12, 15, tzinfo=kst)
        mock_tm.get_market_open_time.return_value = datetime(2026, 3, 8, 9, 0, tzinfo=kst)
        mock_tm.get_market_close_time.return_value = datetime(2026, 3, 8, 15, 30, tzinfo=kst)

        config = TraditionalVBConfig(
            min_avg_trading_value_5d=0,
            near_high_pct=100.0,
        )
        strategy = TraditionalVolumeBreakoutStrategy(
            stock_query_service=deep_paper_ctx.stock_query_service,
            stock_code_repository=mock_mapper,
            market_clock=mock_tm,
            config=config,
        )
        strategy._watchlist_date = ""

        signals = await strategy.scan()
        assert len(signals) == 0


# ============================================================================
# OneilSqueezeBreakoutStrategy scan() IT
# ============================================================================

class TestOneilSqueezeBreakoutScan:
    """오닐 스퀴즈 돌파 전략 scan() 통합 테스트."""

    async def test_scan_generates_signal_all_gates_pass(self, deep_paper_ctx, mocker):
        """모든 관문(가격돌파, 거래량, 스마트머니, 체결강도) 통과 시 BUY."""
        from strategies.oneil_squeeze_breakout_strategy import OneilSqueezeBreakoutStrategy
        from strategies.oneil_common_types import OneilBreakoutConfig, OSBWatchlistItem
        from services.oneil_universe_service import OneilUniverseService

        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)

        # 현재가 75000, 거래량 3M, 프로그램순매수 100000주, 거래대금 500B
        price_resp = _make_current_price_response(
            code="005930", price="75000", high="75500", low="73000",
            vol="3000000",
            pgtr_ntby_qty="100000", acml_tr_pbmn="500000000000",
        )
        # 체결강도 135%
        conclusion_resp = _make_conclusion_response(tday_rltv="135.50")

        patch_session_get_router(quot_api, mocker, {
            "inquire-ccnl": conclusion_resp,
            "conclusion": conclusion_resp,
            "inquire-price": price_resp,
        })

        # 실제 UniverseService를 활용하여 서비스 간 통합 검증 (HTTP만 모킹)
        watchlist_item = OSBWatchlistItem(
            code="005930", name="삼성전자", market="KOSPI",
            high_20d=74000,  # 현재가 75000 > 74000 → 돌파, max_entry=74000*1.02=75480 (within 2%)
            ma_20d=70000.0, ma_50d=68000.0,
            avg_vol_20d=1000000.0,  # 환산거래량 6M > 1M*1.5 → 통과
            bb_width_min_20d=0.03, prev_bb_width=0.035,  # squeeze gate: 0.035 <= 0.03*1.2
            w52_hgpr=77000, avg_trading_value_5d=500_000_000_000,
            market_cap=400_000_000_000,  # 4000억 (대형주 동적 허들 우회)
        )
        mocker.patch.object(
            deep_paper_ctx.oneil_universe_service, "get_watchlist",
            new_callable=AsyncMock, return_value={"005930": watchlist_item}
        )
        mocker.patch.object(
            deep_paper_ctx.oneil_universe_service, "is_market_timing_ok",
            new_callable=AsyncMock, return_value=True
        )

        from datetime import datetime
        from pytz import timezone
        kst = timezone("Asia/Seoul")
        mock_tm = MagicMock()
        mock_tm.get_current_kst_time.return_value = datetime(2026, 3, 8, 12, 15, tzinfo=kst)
        mock_tm.get_market_open_time.return_value = datetime(2026, 3, 8, 9, 0, tzinfo=kst)
        mock_tm.get_market_close_time.return_value = datetime(2026, 3, 8, 15, 30, tzinfo=kst)

        config = OneilBreakoutConfig(
            program_net_buy_min=0,
            program_to_trade_value_pct=0.0,  # 필터 완화
            program_to_market_cap_pct=0.0,
        )
        strategy = OneilSqueezeBreakoutStrategy(
            stock_query_service=deep_paper_ctx.stock_query_service,
            universe_service=deep_paper_ctx.oneil_universe_service,
            market_clock=mock_tm,
            config=config,
        )
        strategy._position_state.clear()  # 이전 테스트 잔여 상태 제거

        signals = await strategy.scan()

        assert len(signals) == 1
        sig = signals[0]
        assert sig.code == "005930"
        assert sig.action == "BUY"
        assert sig.strategy_name == "오닐스퀴즈돌파"
        assert "강도" in sig.reason

    async def test_scan_no_signal_low_execution_strength(self, deep_paper_ctx, mocker):
        """체결강도 120% 미만이면 시그널 없음."""
        from strategies.oneil_squeeze_breakout_strategy import OneilSqueezeBreakoutStrategy
        from strategies.oneil_common_types import OneilBreakoutConfig, OSBWatchlistItem
        from services.oneil_universe_service import OneilUniverseService

        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)

        price_resp = _make_current_price_response(
            code="005930", price="75000", high="75500", low="73000",
            vol="3000000",
            pgtr_ntby_qty="100000", acml_tr_pbmn="500000000000",
        )
        # 체결강도 95% → 120% 미만
        conclusion_resp = _make_conclusion_response(tday_rltv="95.00")

        patch_session_get_router(quot_api, mocker, {
            "inquire-ccnl": conclusion_resp,
            "conclusion": conclusion_resp,
            "inquire-price": price_resp,
        })

        watchlist_item = OSBWatchlistItem(
            code="005930", name="삼성전자", market="KOSPI",
            high_20d=72000, ma_20d=70000.0, ma_50d=68000.0,
            avg_vol_20d=1000000.0,
            bb_width_min_20d=0.03, prev_bb_width=0.04,
            w52_hgpr=77000, avg_trading_value_5d=500_000_000_000,
            market_cap=400_000_000_000,  # 4000억
        )
        mocker.patch.object(
            deep_paper_ctx.oneil_universe_service, "get_watchlist",
            new_callable=AsyncMock, return_value={"005930": watchlist_item}
        )
        mocker.patch.object(
            deep_paper_ctx.oneil_universe_service, "is_market_timing_ok",
            new_callable=AsyncMock, return_value=True
        )

        from datetime import datetime
        from pytz import timezone
        kst = timezone("Asia/Seoul")
        mock_tm = MagicMock()
        mock_tm.get_current_kst_time.return_value = datetime(2026, 3, 8, 12, 15, tzinfo=kst)
        mock_tm.get_market_open_time.return_value = datetime(2026, 3, 8, 9, 0, tzinfo=kst)
        mock_tm.get_market_close_time.return_value = datetime(2026, 3, 8, 15, 30, tzinfo=kst)

        config = OneilBreakoutConfig(
            execution_strength_min=120.0,  # 🌟 기준 설정
            program_net_buy_min=0,
            program_to_trade_value_pct=0.0,
            program_to_market_cap_pct=0.0,
        )
        strategy = OneilSqueezeBreakoutStrategy(
            stock_query_service=deep_paper_ctx.stock_query_service,
            universe_service=deep_paper_ctx.oneil_universe_service,
            market_clock=mock_tm,
            config=config,
        )

        signals = await strategy.scan()
        assert len(signals) == 0


# ============================================================================
# OneilPocketPivotStrategy scan() IT
# ============================================================================

class TestOneilPocketPivotScan:
    """오닐 포켓 피봇 / BGU 전략 scan() 통합 테스트."""

    async def test_scan_bgu_generates_signal(self, deep_paper_ctx, mocker):
        """BGU 조건(갭4%+거래량+스마트머니+체결강도) 충족 시 BUY."""
        from strategies.oneil_pocket_pivot_strategy import OneilPocketPivotStrategy
        from strategies.oneil_common_types import OneilPocketPivotConfig, OSBWatchlistItem
        from services.oneil_universe_service import OneilUniverseService

        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)

        # 전일종가 70000 → 시가 73500 (갭 +5%) → 현재가 74000
        price_output = _make_current_price_output(
            code="005930", price="74000", open_price="73500",
            high="74500", low="73000", vol="5000000",
            pgtr_ntby_qty="80000", prev_close="70000",
            acml_tr_pbmn="500000000000",
        )
        conclusion_resp = _make_conclusion_response(tday_rltv="130.00")

        # 60일 OHLCV (BGU 거래량 비교용)
        ohlcv_data = _make_ohlcv_data(days=60, base_close=68000, base_vol=500000)
        ohlcv_api_resp = _make_ohlcv_api_response(ohlcv_data)

        # HTTP 통신 계층 Mocking (서비스 레이어 Mock 제거)
        patch_session_get_router(quot_api, mocker, {
            "inquire-ccnl": conclusion_resp,
            "conclusion": conclusion_resp,
            "inquire-daily-itemchartprice": ohlcv_api_resp,
            "inquire-price": {"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상", "output": price_output},
        })

        watchlist_item = OSBWatchlistItem(
            code="005930", name="삼성전자", market="KOSPI",
            high_20d=72000, ma_20d=70000.0, ma_50d=68000.0,
            avg_vol_20d=600000.0,
            bb_width_min_20d=0.03, prev_bb_width=0.04,
            w52_hgpr=77000, avg_trading_value_5d=500_000_000_000,
            market_cap=400_000_000_000,  # 4000억
        )
        mocker.patch.object(
            deep_paper_ctx.oneil_universe_service, "get_watchlist",
            new_callable=AsyncMock, return_value={"005930": watchlist_item}
        )
        mocker.patch.object(
            deep_paper_ctx.oneil_universe_service, "is_market_timing_ok",
            new_callable=AsyncMock, return_value=True
        )

        from datetime import datetime
        from pytz import timezone
        kst = timezone("Asia/Seoul")
        mock_tm = MagicMock()
        # 장 시작 후 30분 경과 (09:30) → BGU whipsaw_after_minutes=10 통과
        mock_tm.get_current_kst_time.return_value = datetime(2026, 3, 8, 9, 30, tzinfo=kst)
        mock_tm.get_market_open_time.return_value = datetime(2026, 3, 8, 9, 0, tzinfo=kst)
        mock_tm.get_market_close_time.return_value = datetime(2026, 3, 8, 15, 30, tzinfo=kst)

        config = OneilPocketPivotConfig(
            program_to_trade_value_pct=0.0,
            program_to_market_cap_pct=0.0,
            execution_strength_min=120.0,
            bgu_gap_pct=4.0,
            bgu_volume_multiplier=1.0,  # 완화: 50일 평균 100%만 넘으면 통과
            bgu_min_pg_tv_pct=0.0,      # 테스트 PG 비율(~1.2%)이 기본값(8%)보다 낮아 완화
        )
        strategy = OneilPocketPivotStrategy(
            stock_query_service=deep_paper_ctx.stock_query_service,
            universe_service=deep_paper_ctx.oneil_universe_service,
            market_clock=mock_tm,
            config=config,
        )
        strategy._position_state.clear()  # 이전 테스트 잔여 상태 제거

        signals = await strategy.scan()

        assert len(signals) == 1
        sig = signals[0]
        assert sig.code == "005930"
        assert sig.action == "BUY"
        assert sig.strategy_name == "오닐PP/BGU"
        assert "BGU" in sig.reason or "PP" in sig.reason

    async def test_scan_no_signal_market_timing_bad(self, deep_paper_ctx, mocker):
        """마켓 타이밍 불량 시 시그널 없음."""
        from strategies.oneil_pocket_pivot_strategy import OneilPocketPivotStrategy
        from strategies.oneil_common_types import OneilPocketPivotConfig, OSBWatchlistItem
        from services.oneil_universe_service import OneilUniverseService

        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)

        # mock 응답은 사실상 호출되지 않지만 설정
        mocker.patch.object(
            quot_api._async_session, "get",
            new_callable=AsyncMock,
            return_value=make_http_response({"rt_cd": "0", "msg1": "ok"}),
        )

        watchlist_item = OSBWatchlistItem(
            code="005930", name="삼성전자", market="KOSPI",
            high_20d=72000, ma_20d=70000.0, ma_50d=68000.0,
            avg_vol_20d=600000.0,
            bb_width_min_20d=0.03, prev_bb_width=0.04,
            w52_hgpr=77000, avg_trading_value_5d=500_000_000_000,
            market_cap=400_000_000_000,  # 4000억
        )
        mocker.patch.object(
            deep_paper_ctx.oneil_universe_service, "get_watchlist",
            new_callable=AsyncMock, return_value={"005930": watchlist_item}
        )
        # 마켓 타이밍 불량
        mocker.patch.object(
            deep_paper_ctx.oneil_universe_service, "is_market_timing_ok",
            new_callable=AsyncMock, return_value=False
        )

        from datetime import datetime
        from pytz import timezone
        kst = timezone("Asia/Seoul")
        mock_tm = MagicMock()
        mock_tm.get_current_kst_time.return_value = datetime(2026, 3, 8, 12, 15, tzinfo=kst)
        mock_tm.get_market_open_time.return_value = datetime(2026, 3, 8, 9, 0, tzinfo=kst)
        mock_tm.get_market_close_time.return_value = datetime(2026, 3, 8, 15, 30, tzinfo=kst)

        strategy = OneilPocketPivotStrategy(
            stock_query_service=deep_paper_ctx.stock_query_service,
            universe_service=deep_paper_ctx.oneil_universe_service,
            market_clock=mock_tm,
        )

        signals = await strategy.scan()
        assert len(signals) == 0


# ============================================================================
# RSI2PullbackStrategy scan() IT
# ============================================================================

class TestRSI2PullbackScan:
    """래리 코너스 RSI(2) 눌림목 전략 scan() 통합 테스트."""

    async def test_scan_emits_buy_when_stage2_and_rsi_oversold(self, deep_paper_ctx, mocker, tmp_path):
        """Stage 2 + RSI(2) ≤ 10 + 15:10 이후 → BUY 시그널 1건."""
        from datetime import datetime, timedelta
        from strategies.rsi2_pullback_strategy import RSI2PullbackStrategy
        from strategies.rsi2_pullback_types import RSI2PullbackConfig
        from strategies.oneil_common_types import OSBWatchlistItem
        from services.indicator_service import IndicatorService

        # 마지막 2영업일에 큰 음봉을 배치하여 IndicatorService가 RSI(2) ≤ 10 을 산출하도록 구성
        base_dt = datetime(2026, 3, 7)
        ohlcv = []
        for i in range(30):
            dt = base_dt - timedelta(days=29 - i)
            date_str = dt.strftime("%Y%m%d")
            if i < 28:
                price = int(10000 * (1.0 + 0.005 * i))
            else:
                prev = ohlcv[-1]["close"]
                price = int(prev * 0.97)
            ohlcv.append({
                "date": date_str,
                "open": price - 50, "high": price + 100,
                "low": price - 100, "close": price, "volume": 500000,
            })
        last_close = ohlcv[-1]["close"]

        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)
        deep_paper_ctx.stock_query_service.market_data_service._stock_repo = None
        deep_paper_ctx.stock_query_service.market_data_service._mcs.is_market_open_now = AsyncMock(return_value=False)
        
        ohlcv_api_resp = _make_ohlcv_api_response(ohlcv)
        price_output = _make_current_price_output(
            code="005930", price=str(last_close), open_price=str(last_close),
            high=str(last_close + 100), low=str(last_close - 100),
            prev_close=str(int(last_close / 0.97)),
        )

        patch_session_get_router(quot_api, mocker, {
            "inquire-daily-itemchartprice": ohlcv_api_resp,
            "inquire-price": {"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상", "output": price_output},
        })

        watchlist_item = OSBWatchlistItem(
            code="005930", name="삼성전자", market="KOSPI",
            high_20d=int(last_close * 1.5), ma_20d=float(last_close * 1.05),
            ma_50d=float(last_close * 1.02), avg_vol_20d=600000.0,
            bb_width_min_20d=0.03, prev_bb_width=0.04,
            w52_hgpr=int(last_close * 1.6), avg_trading_value_5d=500_000_000_000,
            market_cap=400_000_000_000,
            ma_200d=float(last_close * 0.85), minervini_stage=2,
        )
        mocker.patch.object(
            deep_paper_ctx.oneil_universe_service, "get_watchlist",
            new_callable=AsyncMock, return_value={"005930": watchlist_item}
        )
        mocker.patch.object(
            deep_paper_ctx.oneil_universe_service, "is_market_timing_ok",
            new_callable=AsyncMock, return_value=True
        )

        from pytz import timezone
        kst = timezone("Asia/Seoul")
        mock_tm = MagicMock()
        # 15:15 → cutoff 15:10 통과
        mock_tm.get_current_kst_time.return_value = datetime(2026, 3, 9, 15, 15, tzinfo=kst)
        mock_tm.get_market_open_time.return_value = datetime(2026, 3, 9, 9, 0, tzinfo=kst)
        mock_tm.get_market_close_time.return_value = datetime(2026, 3, 9, 15, 30, tzinfo=kst)

        strategy = RSI2PullbackStrategy(
            stock_query_service=deep_paper_ctx.stock_query_service,
            universe_service=deep_paper_ctx.oneil_universe_service,
            indicator_service=IndicatorService(deep_paper_ctx.stock_query_service),
            market_clock=mock_tm,
            config=RSI2PullbackConfig(),
            state_file=str(tmp_path / "rsi2_state.json"),
        )
        strategy._save_state = MagicMock()

        signals = await strategy.scan()

        assert len(signals) == 1
        sig = signals[0]
        assert sig.code == "005930"
        assert sig.action == "BUY"
        assert sig.strategy_name == "RSI2눌림목"
        assert "RSI" in sig.reason

    async def test_scan_skips_before_cutoff_time(self, deep_paper_ctx, mocker, tmp_path):
        """15:10 이전이면 watchlist 조회 자체를 건너뛴다."""
        from datetime import datetime
        from strategies.rsi2_pullback_strategy import RSI2PullbackStrategy
        from strategies.rsi2_pullback_types import RSI2PullbackConfig

        mocker.patch.object(
            deep_paper_ctx.oneil_universe_service, "get_watchlist",
            new_callable=AsyncMock, return_value={}
        )
        mocker.patch.object(
            deep_paper_ctx.oneil_universe_service, "is_market_timing_ok",
            new_callable=AsyncMock, return_value=True
        )

        from pytz import timezone
        kst = timezone("Asia/Seoul")
        mock_tm = MagicMock()
        mock_tm.get_current_kst_time.return_value = datetime(2026, 3, 9, 14, 0, tzinfo=kst)
        mock_tm.get_market_open_time.return_value = datetime(2026, 3, 9, 9, 0, tzinfo=kst)
        mock_tm.get_market_close_time.return_value = datetime(2026, 3, 9, 15, 30, tzinfo=kst)

        strategy = RSI2PullbackStrategy(
            stock_query_service=deep_paper_ctx.stock_query_service,
            universe_service=deep_paper_ctx.oneil_universe_service,
            indicator_service=deep_paper_ctx.indicator_service,
            market_clock=mock_tm,
            config=RSI2PullbackConfig(),
            state_file=str(tmp_path / "rsi2_state.json"),
        )
        strategy._save_state = MagicMock()

        signals = await strategy.scan()
        assert signals == []
        # get_watchlist 호출이 일어나지 않았어야 함
        deep_paper_ctx.oneil_universe_service.get_watchlist.assert_not_called()


# ============================================================================
# scan() 청크 기반 병렬화 안전성 테스트
# ============================================================================

class TestScanChunkParallelism:
    """asyncio.gather 청크 병렬화 — 예외 격리 및 다중 종목 처리 검증."""

    def _make_osb_strategy(self, deep_paper_ctx, mock_tm, config=None):
        """OneilSqueezeBreakoutStrategy 인스턴스 생성 헬퍼."""
        from strategies.oneil_squeeze_breakout_strategy import OneilSqueezeBreakoutStrategy
        from strategies.oneil_common_types import OneilBreakoutConfig

        cfg = config or OneilBreakoutConfig(
            program_net_buy_min=0,
            program_to_trade_value_pct=0.0,
            program_to_market_cap_pct=0.0,
        )
        strategy = OneilSqueezeBreakoutStrategy(
            stock_query_service=deep_paper_ctx.stock_query_service,
            universe_service=deep_paper_ctx.oneil_universe_service,
            market_clock=mock_tm,
            config=cfg,
        )
        strategy._position_state.clear()
        strategy._save_state = MagicMock()
        return strategy

    def _make_watchlist_item(self, code, **kwargs):
        from strategies.oneil_common_types import OSBWatchlistItem
        defaults = dict(
            name=f"종목{code}", market="KOSPI",
            high_20d=70000, ma_20d=68000.0, ma_50d=65000.0,
            avg_vol_20d=1_000_000.0, bb_width_min_20d=0.03, prev_bb_width=0.035,  # squeeze gate: 0.035 <= 0.03*1.2
            w52_hgpr=77000, avg_trading_value_5d=500_000_000_000,
            market_cap=400_000_000_000,  # 4000억
        )
        defaults.update(kwargs)
        return OSBWatchlistItem(code=code, **defaults)

    async def test_exception_in_one_candidate_does_not_block_others(
        self, deep_paper_ctx, mocker
    ):
        """scan: asyncio.gather — 한 종목 API 예외 발생 시 다른 종목 시그널은 정상 반환."""
        from services.oneil_universe_service import OneilUniverseService
        from datetime import datetime
        from pytz import timezone

        kst = timezone("Asia/Seoul")
        mock_tm = MagicMock()
        mock_tm.get_current_kst_time.return_value = datetime(2026, 3, 8, 12, 0, tzinfo=kst)
        mock_tm.get_market_open_time.return_value = datetime(2026, 3, 8, 9, 0, tzinfo=kst)
        mock_tm.get_market_close_time.return_value = datetime(2026, 3, 8, 15, 30, tzinfo=kst)

        # 2개 종목: 000001은 API 예외 유발, 005930은 정상 돌파
        mocker.patch.object(
            deep_paper_ctx.oneil_universe_service, "get_watchlist",
            new_callable=AsyncMock, return_value={
                "000001": self._make_watchlist_item("000001", high_20d=74000),
                "005930": self._make_watchlist_item("005930", high_20d=74000),
            }
        )
        mocker.patch.object(
            deep_paper_ctx.oneil_universe_service, "is_market_timing_ok",
            new_callable=AsyncMock, return_value=True
        )

        strategy = self._make_osb_strategy(deep_paper_ctx, mock_tm)

        # 000001: 예외, 005930: 모든 관문 통과
        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)

        conclusion_resp = _make_conclusion_response(tday_rltv="135.0")

        async def _side_effect(url, *args, **kwargs):
            u = str(url)
            params = kwargs.get("params", {})
            param_str = str(params)
            
            # KIS API의 경우 종목코드가 params 딕셔너리로 전달될 수 있음
            if "000001" in u or "000001" in param_str:
                raise Exception("의도적 타임아웃")
                
            if "inquire-ccnl" in u or "conclusion" in u:
                return make_http_response(conclusion_resp)
            
            req_code = "005930"
            if params and "FID_INPUT_ISCD" in params:
                req_code = params["FID_INPUT_ISCD"]
            price_output = _make_current_price_output(
                code=req_code, price="75000", vol="3000000", pgtr_ntby_qty="100000",
                acml_tr_pbmn="500000000000", high="75500", low="73000"
            )
            return make_http_response({"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상", "output": price_output})

        mocker.patch.object(
            quot_api._async_session, "get",
            new_callable=AsyncMock, side_effect=_side_effect,
        )

        signals = await strategy.scan()

        # 005930 시그널 생성
        assert len(signals) == 1
        assert signals[0].code == "005930"
        assert signals[0].action == "BUY"

    async def test_multiple_candidates_all_processed_within_chunk(
        self, deep_paper_ctx, mocker
    ):
        """scan: 워치리스트 내 여러 종목이 동일 청크(≤10)에서 모두 처리됨."""
        from services.oneil_universe_service import OneilUniverseService
        from datetime import datetime
        from pytz import timezone

        kst = timezone("Asia/Seoul")
        mock_tm = MagicMock()
        mock_tm.get_current_kst_time.return_value = datetime(2026, 3, 8, 12, 0, tzinfo=kst)
        mock_tm.get_market_open_time.return_value = datetime(2026, 3, 8, 9, 0, tzinfo=kst)
        mock_tm.get_market_close_time.return_value = datetime(2026, 3, 8, 15, 30, tzinfo=kst)

        codes = [f"00{i:04d}" for i in range(1, 4)]  # 3개 종목 (1 청크 내)
        mocker.patch.object(
            deep_paper_ctx.oneil_universe_service, "get_watchlist",
            new_callable=AsyncMock, return_value={
                code: self._make_watchlist_item(code, high_20d=74000) for code in codes
            }
        )
        mocker.patch.object(
            deep_paper_ctx.oneil_universe_service, "is_market_timing_ok",
            new_callable=AsyncMock, return_value=True
        )

        strategy = self._make_osb_strategy(deep_paper_ctx, mock_tm)

        # 모든 종목 돌파 성공
        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)

        conclusion_resp = _make_conclusion_response(tday_rltv="135.0")

        async def _side_effect(url, *args, **kwargs):
            u = str(url)
            params = kwargs.get("params", {})
            if "inquire-ccnl" in u or "conclusion" in u:
                return make_http_response(conclusion_resp)
            
            req_code = "005930"
            if params and "FID_INPUT_ISCD" in params:
                req_code = params["FID_INPUT_ISCD"]
            price_output = _make_current_price_output(
                code=req_code, price="75000", vol="3000000", pgtr_ntby_qty="100000",
                acml_tr_pbmn="500000000000", high="75500", low="73000"
            )
            return make_http_response({"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상", "output": price_output})

        mocker.patch.object(
            quot_api._async_session, "get",
            new_callable=AsyncMock, side_effect=_side_effect,
        )

        signals = await strategy.scan()

        # 3개 종목 모두 시그널 생성
        assert len(signals) == len(codes)
        assert {s.code for s in signals} == set(codes)


# ============================================================================
# API 실패 시 전략 안전성 테스트
# ============================================================================

class TestStrategyScanApiFailure:
    """API 실패 시 전략이 안전하게 빈 시그널을 반환하는지 검증."""

    async def test_volume_breakout_handles_api_failure(self, deep_paper_ctx, mocker):
        """거래대금 상위 API 실패 시 빈 시그널."""
        from strategies.volume_breakout_live_strategy import VolumeBreakoutLiveStrategy

        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)
        mocker.patch.object(
            quot_api._async_session, "get",
            new_callable=AsyncMock,
            return_value=make_http_response({"rt_cd": "1", "msg1": "서버 오류"}, 500),
        )

        strategy = VolumeBreakoutLiveStrategy(
            stock_query_service=deep_paper_ctx.stock_query_service,
            market_clock=deep_paper_ctx.market_clock,
        )

        signals = await strategy.scan()
        assert signals == []

    async def test_program_buy_follow_handles_api_failure(self, deep_paper_ctx, mocker):
        """거래대금 상위 API 실패 시 빈 시그널."""
        from strategies.program_buy_follow_strategy import ProgramBuyFollowStrategy

        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)
        mocker.patch.object(
            quot_api._async_session, "get",
            new_callable=AsyncMock,
            return_value=make_http_response({"rt_cd": "1", "msg1": "서버 오류"}, 500),
        )

        strategy = ProgramBuyFollowStrategy(
            stock_query_service=deep_paper_ctx.stock_query_service,
            market_clock=deep_paper_ctx.market_clock,
        )

        signals = await strategy.scan()
        assert signals == []


# ============================================================================
# LarryWilliamsChannelBreakoutStrategy scan() / check_exits() IT
# ============================================================================

class TestLarryWilliamsCBScan:
    """LWCB 전략 scan/check_exits — 실제 StockQueryService 스택 사용, HTTP mock."""

    _CODE = "005930"

    def _make_watchlist_item(self, code: str = "005930", **kwargs):
        from strategies.oneil_common_types import OSBWatchlistItem
        defaults = dict(
            name=f"종목{code}", market="KOSPI",
            high_20d=74_000, ma_20d=68_000.0, ma_50d=65_000.0,
            avg_vol_20d=1_000_000.0, bb_width_min_20d=0.03, prev_bb_width=0.04,
            w52_hgpr=80_000, avg_trading_value_5d=500_000_000_000,
            rs_rating=85,
        )
        defaults.update(kwargs)
        return OSBWatchlistItem(code=code, **defaults)

    def _make_ohlcv_data(self, days: int = 35, base_low: int = 65_000) -> list:
        data = []
        for i in range(days):
            close = 70_000 + i * 100
            data.append({
                "date": f"20260{1 + i // 28:02d}{1 + i % 28:02d}",
                "open": close - 100,
                "high": close + 500,
                "low": base_low,
                "close": close,
                "volume": 1_000_000,
            })
        return data

    def _build_strategy(self, deep_paper_ctx, mock_tm, tmp_path):
        from strategies.larry_williams_channel_breakout_strategy import (
            LarryWilliamsChannelBreakoutStrategy,
        )
        from strategies.larry_williams_cb_types import LarryWilliamsCBConfig

        return LarryWilliamsChannelBreakoutStrategy(
            stock_query_service=deep_paper_ctx.stock_query_service,
            universe_service=deep_paper_ctx.oneil_universe_service,
            indicator_service=deep_paper_ctx.indicator_service,
            market_clock=mock_tm,
            config=LarryWilliamsCBConfig(cooldown_days=2),
            state_file=str(tmp_path / "lwcb_state.json"),
        )

    async def test_scan_generates_buy_signal_via_real_sqs(
        self, deep_paper_ctx, mocker, tmp_path
    ):
        """전체 조건 충족 시 실제 StockQueryService 스택을 통해 BUY 신호 생성.

        StockQueryService → MarketDataService 경계를 실 객체로 유지하되,
        universe/indicator는 mock으로 주입하여 전략 진입 흐름 전체를 검증.
        """
        from datetime import datetime
        from pytz import timezone
        from common.types import ResCommonResponse
        from services.indicator_service import IndicatorService
        from services.oneil_universe_service import OneilUniverseService

        kst = timezone("Asia/Seoul")
        mock_tm = MagicMock()
        mock_tm.get_current_kst_time.return_value = datetime(2026, 4, 30, 15, 15, tzinfo=kst)

        # indicator: ADX 조건 통과 (indicator service 실제 로직에 주입)
        mocker.patch.object(
            deep_paper_ctx.indicator_service, "calc_adx_sync",
            return_value={"adx": 30.0, "plus_di": 25.0, "minus_di": 15.0, "adx_rising": True}
        )

        # universe: 종목 1개, RS=85, high_20d=74_000
        mocker.patch.object(
            deep_paper_ctx.oneil_universe_service, "get_watchlist",
            new_callable=AsyncMock, return_value={self._CODE: self._make_watchlist_item(self._CODE)}
        )

        # HTTP 계층 Mocking (StockQueryService 실제 작동 보장)
        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)
        
        # OHLCV: 35봉, low=65_000 (채널 하단 예측 가능)
        ohlcv_data = self._make_ohlcv_data(35, 65_000)
        ohlcv_api_resp = _make_ohlcv_api_response(ohlcv_data)

        # 현재가: 75_000 > high_20d 74_000, 거래량 2_000_000 ≥ 1_000_000 × 1.5
        price_output = _make_current_price_output(code=self._CODE, price="75000", vol="2000000")

        patch_session_get_router(quot_api, mocker, {
            "inquire-daily-itemchartprice": ohlcv_api_resp,
            "inquire-price": {"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상", "output": price_output},
        })

        strategy = self._build_strategy(deep_paper_ctx, mock_tm, tmp_path)
        signals = await strategy.scan()

        assert len(signals) == 1
        sig = signals[0]
        assert sig.code == self._CODE
        assert sig.action == "BUY"
        assert sig.price == 75_000
        assert sig.strategy_name == "LarryWilliamsCB"
        assert self._CODE in strategy._position_state

    async def test_check_exits_hard_stop_via_real_sqs(
        self, deep_paper_ctx, mocker, tmp_path
    ):
        """칼손절 조건 충족 시 SELL 신호 + 쿨다운 등록 (실제 SQS 스택 경유)."""
        from datetime import datetime
        from pytz import timezone
        from common.types import ResCommonResponse
        from services.indicator_service import IndicatorService
        from services.oneil_universe_service import OneilUniverseService
        from strategies.larry_williams_cb_types import LarryWilliamsCBPositionState

        kst = timezone("Asia/Seoul")
        mock_tm = MagicMock()
        mock_tm.get_current_kst_time.return_value = datetime(2026, 4, 30, 15, 15, tzinfo=kst)

        sqs = deep_paper_ctx.stock_query_service
        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)

        price_output = _make_current_price_output(code=self._CODE, price="69000", vol="1000000")
        patch_session_get_router(quot_api, mocker, {
            "inquire-price": {"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상", "output": price_output},
        })

        strategy = self._build_strategy(deep_paper_ctx, mock_tm, tmp_path)
        strategy._position_state[self._CODE] = LarryWilliamsCBPositionState(
            entry_price=75_000, entry_date="20260429",
            hard_stop_price=69_750, channel_low_10d=65_000,
        )

        holdings = [{"code": self._CODE, "name": "삼성전자", "buy_price": 75_000, "qty": 1}]
        signals = await strategy.check_exits(holdings)

        assert len(signals) == 1
        assert signals[0].action == "SELL"
        assert "칼손절" in signals[0].reason
        assert self._CODE not in strategy._position_state
        assert self._CODE in strategy._cooldown

    async def test_scan_before_cutoff_no_signal(self, deep_paper_ctx, mocker, tmp_path):
        """15:10 이전이면 SQS 호출 없이 빈 리스트 반환."""
        from datetime import datetime
        from pytz import timezone
        from services.indicator_service import IndicatorService
        from services.oneil_universe_service import OneilUniverseService

        kst = timezone("Asia/Seoul")
        mock_tm = MagicMock()
        mock_tm.get_current_kst_time.return_value = datetime(2026, 4, 30, 14, 50, tzinfo=kst)

        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)
        spy_get = mocker.spy(quot_api._async_session, "get")

        strategy = self._build_strategy(deep_paper_ctx, mock_tm, tmp_path)
        signals = await strategy.scan()

        assert signals == []
        spy_get.assert_not_called()
