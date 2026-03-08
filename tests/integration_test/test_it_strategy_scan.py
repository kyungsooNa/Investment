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


# ============================================================================
# 헬퍼: HTTP mock에 side_effect 패턴 적용
# ============================================================================

def _build_get_side_effect(url_responses: dict):
    """URL 패턴 매칭 기반 side_effect 함수 생성.

    url_responses: {url_substring: payload_dict, ...}
    """
    async def _side_effect(url, *args, **kwargs):
        u = str(url)
        for pattern, payload in url_responses.items():
            if pattern in u:
                return make_http_response(payload)
        # fallback
        return make_http_response({"rt_cd": "0", "msg1": "ok"})
    return _side_effect


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
            time_manager=deep_paper_ctx.time_manager,
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
            time_manager=deep_paper_ctx.time_manager,
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

        trading_value_resp = _make_trading_value_response([
            _make_trading_value_stock("005930", "삼성전자"),
        ])
        # pgtr_ntby_qty=50000 (양수 → 매수 시그널)
        price_resp = _make_current_price_response(
            code="005930", price="72000", pgtr_ntby_qty="50000",
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
            time_manager=deep_paper_ctx.time_manager,
            config=config,
        )

        signals = await strategy.scan()

        assert len(signals) >= 1
        sig = signals[0]
        assert sig.code == "005930"
        assert sig.action == "BUY"
        assert sig.strategy_name == "프로그램매수추종"

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
            time_manager=deep_paper_ctx.time_manager,
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
        from market_data.stock_code_mapper import StockCodeMapper

        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)

        trading_value_resp = _make_trading_value_response([
            _make_trading_value_stock("005930", "삼성전자", "5000000"),
        ])

        # 20일 OHLCV 데이터 (전략이 list 형태로 기대)
        ohlcv_data = _make_ohlcv_data(days=25, base_close=70000, base_vol=1000000)

        # 현재가 75000 → 20일 최고가(~72400+500=72900 high) 돌파
        # 누적거래량 3000000, 장중 50% 경과 → 환산 6000000 > avg(~1120000)*1.5
        price_resp = _make_current_price_response(
            code="005930", price="75000", open_price="72000",
            high="75500", vol="3000000",
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

        mock_mapper = MagicMock(spec=StockCodeMapper)
        mock_mapper.get_name_by_code = MagicMock(return_value="삼성전자")
        deep_paper_ctx.broker._stock_mapper = mock_mapper

        # get_recent_daily_ohlcv를 서비스 레벨에서 mock (전략이 list를 기대하는 인터페이스 불일치 우회)
        mocker.patch.object(
            deep_paper_ctx.stock_query_service, "get_recent_daily_ohlcv",
            new_callable=AsyncMock, return_value=ohlcv_data,
        )

        from datetime import datetime
        from pytz import timezone
        kst = timezone("Asia/Seoul")
        mock_tm = MagicMock()
        mock_tm.get_current_kst_time.return_value = datetime(2026, 3, 8, 12, 15, tzinfo=kst)
        mock_tm.get_market_open_time.return_value = datetime(2026, 3, 8, 9, 0, tzinfo=kst)
        mock_tm.get_market_close_time.return_value = datetime(2026, 3, 8, 15, 30, tzinfo=kst)

        config = TraditionalVBConfig(
            min_avg_trading_value_5d=0,
            near_high_pct=100.0,  # 필터 완화
        )
        strategy = TraditionalVolumeBreakoutStrategy(
            stock_query_service=deep_paper_ctx.stock_query_service,
            stock_code_mapper=mock_mapper,
            time_manager=mock_tm,
            config=config,
        )
        strategy._watchlist_date = ""
        strategy._position_state.clear()  # 이전 테스트 잔여 상태 제거

        signals = await strategy.scan()

        assert len(signals) >= 1
        sig = signals[0]
        assert sig.code == "005930"
        assert sig.action == "BUY"
        assert sig.strategy_name == "거래량돌파(전통)"
        assert sig.price == 75000

    async def test_scan_no_signal_price_below_high(self, deep_paper_ctx, mocker):
        """현재가가 20일 최고가 이하면 시그널 없음."""
        from strategies.traditional_volume_breakout_strategy import (
            TraditionalVolumeBreakoutStrategy, TraditionalVBConfig,
        )
        from market_data.stock_code_mapper import StockCodeMapper

        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)

        trading_value_resp = _make_trading_value_response()
        ohlcv_data = _make_ohlcv_data(days=25, base_close=70000, base_vol=1000000)

        # 현재가 71000 → 20일 최고가(~72900 high) 이하 → 돌파 안됨
        price_resp = _make_current_price_response(
            code="005930", price="71000", open_price="70000",
            high="71500", vol="3000000",
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

        mock_mapper = MagicMock(spec=StockCodeMapper)
        mock_mapper.get_name_by_code = MagicMock(return_value="삼성전자")

        mocker.patch.object(
            deep_paper_ctx.stock_query_service, "get_recent_daily_ohlcv",
            new_callable=AsyncMock, return_value=ohlcv_data,
        )

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
            stock_code_mapper=mock_mapper,
            time_manager=mock_tm,
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
            code="005930", price="75000", vol="3000000",
            pgtr_ntby_qty="100000", acml_tr_pbmn="500000000000",
        )
        # 체결강도 135%
        conclusion_resp = _make_conclusion_response(tday_rltv="135.50")

        async def _side_effect(url, *args, **kwargs):
            u = str(url)
            if "inquire-ccnl" in u or "conclusion" in u:
                return make_http_response(conclusion_resp)
            return make_http_response(price_resp)

        mocker.patch.object(
            quot_api._async_session, "get",
            new_callable=AsyncMock, side_effect=_side_effect,
        )

        # Mock universe service
        mock_universe = MagicMock(spec=OneilUniverseService)
        watchlist_item = OSBWatchlistItem(
            code="005930", name="삼성전자", market="KOSPI",
            high_20d=72000,  # 현재가 75000 > 72000 → 돌파
            ma_20d=70000.0, ma_50d=68000.0,
            avg_vol_20d=1000000.0,  # 환산거래량 6M > 1M*1.5 → 통과
            bb_width_min_20d=0.03, prev_bb_width=0.04,
            w52_hgpr=77000, avg_trading_value_5d=500_000_000_000,
            market_cap=400_000_000_000_000,  # 400조
        )
        mock_universe.get_watchlist = AsyncMock(return_value={"005930": watchlist_item})
        mock_universe.is_market_timing_ok = AsyncMock(return_value=True)

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
            universe_service=mock_universe,
            time_manager=mock_tm,
            config=config,
        )
        strategy._position_state.clear()  # 이전 테스트 잔여 상태 제거

        signals = await strategy.scan()

        assert len(signals) == 1
        sig = signals[0]
        assert sig.code == "005930"
        assert sig.action == "BUY"
        assert sig.strategy_name == "오닐스퀴즈돌파"
        assert "체결강도" in sig.reason

    async def test_scan_no_signal_low_execution_strength(self, deep_paper_ctx, mocker):
        """체결강도 120% 미만이면 시그널 없음."""
        from strategies.oneil_squeeze_breakout_strategy import OneilSqueezeBreakoutStrategy
        from strategies.oneil_common_types import OneilBreakoutConfig, OSBWatchlistItem
        from services.oneil_universe_service import OneilUniverseService

        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)

        price_resp = _make_current_price_response(
            code="005930", price="75000", vol="3000000",
            pgtr_ntby_qty="100000", acml_tr_pbmn="500000000000",
        )
        # 체결강도 95% → 120% 미만
        conclusion_resp = _make_conclusion_response(tday_rltv="95.00")

        async def _side_effect(url, *args, **kwargs):
            u = str(url)
            if "inquire-ccnl" in u or "conclusion" in u:
                return make_http_response(conclusion_resp)
            return make_http_response(price_resp)

        mocker.patch.object(
            quot_api._async_session, "get",
            new_callable=AsyncMock, side_effect=_side_effect,
        )

        mock_universe = MagicMock(spec=OneilUniverseService)
        watchlist_item = OSBWatchlistItem(
            code="005930", name="삼성전자", market="KOSPI",
            high_20d=72000, ma_20d=70000.0, ma_50d=68000.0,
            avg_vol_20d=1000000.0,
            bb_width_min_20d=0.03, prev_bb_width=0.04,
            w52_hgpr=77000, avg_trading_value_5d=500_000_000_000,
            market_cap=400_000_000_000_000,
        )
        mock_universe.get_watchlist = AsyncMock(return_value={"005930": watchlist_item})
        mock_universe.is_market_timing_ok = AsyncMock(return_value=True)

        from datetime import datetime
        from pytz import timezone
        kst = timezone("Asia/Seoul")
        mock_tm = MagicMock()
        mock_tm.get_current_kst_time.return_value = datetime(2026, 3, 8, 12, 15, tzinfo=kst)
        mock_tm.get_market_open_time.return_value = datetime(2026, 3, 8, 9, 0, tzinfo=kst)
        mock_tm.get_market_close_time.return_value = datetime(2026, 3, 8, 15, 30, tzinfo=kst)

        config = OneilBreakoutConfig(
            program_net_buy_min=0,
            program_to_trade_value_pct=0.0,
            program_to_market_cap_pct=0.0,
        )
        strategy = OneilSqueezeBreakoutStrategy(
            stock_query_service=deep_paper_ctx.stock_query_service,
            universe_service=mock_universe,
            time_manager=mock_tm,
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

        async def _side_effect(url, *args, **kwargs):
            u = str(url)
            if "inquire-ccnl" in u or "conclusion" in u:
                return make_http_response(conclusion_resp)
            return make_http_response({"rt_cd": "0", "msg1": "ok", "output": {}})

        mocker.patch.object(
            quot_api._async_session, "get",
            new_callable=AsyncMock, side_effect=_side_effect,
        )

        # get_current_price를 서비스 레벨에서 mock (전략이 stck_prdy_clpr를 사용하는데
        # ResStockFullInfoApiOutput에는 해당 필드가 없어서 dict 형태로 반환)
        mock_price_resp = MagicMock()
        mock_price_resp.rt_cd = "0"
        mock_price_resp.data = {"output": price_output}
        mocker.patch.object(
            deep_paper_ctx.stock_query_service, "get_current_price",
            new_callable=AsyncMock, return_value=mock_price_resp,
        )

        # get_recent_daily_ohlcv를 서비스 레벨에서 mock
        # 전략이 ohlcv_resp.rt_cd / ohlcv_resp.data 로 접근하므로 ResCommonResponse-like 객체 반환
        mock_ohlcv_resp = MagicMock()
        mock_ohlcv_resp.rt_cd = "0"
        mock_ohlcv_resp.data = ohlcv_data
        mocker.patch.object(
            deep_paper_ctx.stock_query_service, "get_recent_daily_ohlcv",
            new_callable=AsyncMock, return_value=mock_ohlcv_resp,
        )

        mock_universe = MagicMock(spec=OneilUniverseService)
        watchlist_item = OSBWatchlistItem(
            code="005930", name="삼성전자", market="KOSPI",
            high_20d=72000, ma_20d=70000.0, ma_50d=68000.0,
            avg_vol_20d=600000.0,
            bb_width_min_20d=0.03, prev_bb_width=0.04,
            w52_hgpr=77000, avg_trading_value_5d=500_000_000_000,
            market_cap=400_000_000_000_000,
        )
        mock_universe.get_watchlist = AsyncMock(return_value={"005930": watchlist_item})
        mock_universe.is_market_timing_ok = AsyncMock(return_value=True)

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
        )
        strategy = OneilPocketPivotStrategy(
            stock_query_service=deep_paper_ctx.stock_query_service,
            universe_service=mock_universe,
            time_manager=mock_tm,
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

        mock_universe = MagicMock(spec=OneilUniverseService)
        watchlist_item = OSBWatchlistItem(
            code="005930", name="삼성전자", market="KOSPI",
            high_20d=72000, ma_20d=70000.0, ma_50d=68000.0,
            avg_vol_20d=600000.0,
            bb_width_min_20d=0.03, prev_bb_width=0.04,
            w52_hgpr=77000, avg_trading_value_5d=500_000_000_000,
            market_cap=400_000_000_000_000,
        )
        mock_universe.get_watchlist = AsyncMock(return_value={"005930": watchlist_item})
        # 마켓 타이밍 불량
        mock_universe.is_market_timing_ok = AsyncMock(return_value=False)

        from datetime import datetime
        from pytz import timezone
        kst = timezone("Asia/Seoul")
        mock_tm = MagicMock()
        mock_tm.get_current_kst_time.return_value = datetime(2026, 3, 8, 12, 15, tzinfo=kst)
        mock_tm.get_market_open_time.return_value = datetime(2026, 3, 8, 9, 0, tzinfo=kst)
        mock_tm.get_market_close_time.return_value = datetime(2026, 3, 8, 15, 30, tzinfo=kst)

        strategy = OneilPocketPivotStrategy(
            stock_query_service=deep_paper_ctx.stock_query_service,
            universe_service=mock_universe,
            time_manager=mock_tm,
        )

        signals = await strategy.scan()
        assert len(signals) == 0


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
            time_manager=deep_paper_ctx.time_manager,
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
            time_manager=deep_paper_ctx.time_manager,
        )

        signals = await strategy.scan()
        assert signals == []
