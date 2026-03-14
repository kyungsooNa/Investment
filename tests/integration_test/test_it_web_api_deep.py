# tests/integration_test/test_it_web_api_deep.py
"""
중간 깊이(Mid-depth) Web API 통합 테스트.

기존 얕은 IT(서비스 레이어를 MagicMock)와 달리,
실제 서비스 → 브로커 → API 계층을 모두 실행하고
httpx 네트워크 호출만 mock한다.

검증 범위:
  HTTP Request → FastAPI Route → StockQueryService / OrderExecutionService
    → TradingService → BrokerAPIWrapper → KoreaInvestApiClient
      → KoreaInvestApiBase._execute_request → [MOCK: _async_session.get/post]
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from managers.market_date_manager import MarketDateManager
from tests.integration_test.conftest import (
    _get_quotations_api_from_ctx,
    _get_account_api_from_ctx,
    _get_trading_api_from_ctx,
    patch_session_get,
    patch_session_post,
    make_http_response,
)


# ============================================================================
# 테스트 데이터 팩토리
# ============================================================================

def _make_stock_price_payload():
    """현재가 조회 API 응답 (KIS /inquire-price output)."""
    return {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리 되었습니다.",
        "output": {
            "stck_prpr": "70500",
            "prdy_vrss": "1200",
            "prdy_vrss_sign": "2",
            "prdy_ctrt": "1.73",
            "acml_vol": "15000000",
            "acml_tr_pbmn": "1050000000000",
            "stck_oprc": "69800",
            "stck_hgpr": "71000",
            "stck_lwpr": "69500",
            "stck_sdpr": "69300",
            "stck_shrn_iscd": "005930",
            "stck_mxpr": "90090",
            "stck_sspr": "48510",
            "stck_llam": "4200000000000",
            "stck_dryy_hgpr": "75000",
            "stck_dryy_lwpr": "60000",
            "stck_fcam": "100",
            "per": "12.50",
            "pbr": "1.30",
            "eps": "5640",
            "bps": "54230",
            "d250_hgpr": "78000",
            "d250_hgpr_date": "20260101",
            "d250_hgpr_vrss_prpr_rate": "-9.62",
            "d250_lwpr": "55000",
            "d250_lwpr_date": "20250601",
            "d250_lwpr_vrss_prpr_rate": "28.18",
            "w52_hgpr": "78000",
            "w52_hgpr_date": "20260101",
            "w52_hgpr_vrss_prpr_ctrt": "-9.62",
            "w52_lwpr": "55000",
            "w52_lwpr_date": "20250601",
            "w52_lwpr_vrss_prpr_ctrt": "28.18",
            "hts_avls": "4200000",
            "hts_frgn_ehrt": "55.30",
            "frgn_hldn_qty": "3200000000",
            "frgn_ntby_qty": "500000",
            "pgtr_ntby_qty": "200000",
            "prdy_vrss_vol_rate": "120.50",
            "lstn_stcn": "5969782550",
            "aspr_unit": "100",
            "cpfn": "4685",
            "cpfn_cnnm": "4685 억",
            "fcam_cnnm": "100 원",
            "hts_deal_qty_unit_val": "1",
            "marg_rate": "20",
            "bstp_kor_isnm": "전기전자",
            "iscd_stat_cls_code": "55",
            "rprs_mrkt_kor_name": "코스피",
            "new_hgpr_lwpr_cls_code": "",
            "crdt_able_yn": "Y",
            "short_over_yn": "N",
            "sltr_yn": "N",
            "ssts_yn": "N",
            "mang_issu_cls_code": "",
            "mrkt_warn_cls_code": "00",
            "elw_pblc_yn": "Y",
            "stac_month": "12",
            "grmn_rate_cls_code": "40",
            "invt_caful_yn": "N",
            "clpr_rang_cont_yn": "N",
            "oprc_rang_cont_yn": "N",
            "ovtm_vi_cls_code": "0",
            "vi_cls_code": "N",
            "vol_tnrt": "0.25",
            "wghn_avrg_stck_prc": "70100",
            "whol_loan_rmnd_rate": "0.15",
            "last_ssts_cntg_qty": "100",
            "temp_stop_yn": "N",
            "rstc_wdth_prc": "41580",
            "dmrs_val": "70600",
            "dmsp_val": "70400",
            "pvt_pont_val": "70333",
            "pvt_frst_dmrs_prc": "71167",
            "pvt_frst_dmsp_prc": "69667",
            "pvt_scnd_dmrs_prc": "71833",
            "pvt_scnd_dmsp_prc": "68833",
            "dryy_hgpr_date": "20260215",
            "dryy_hgpr_vrss_prpr_rate": "-6.00",
            "dryy_lwpr_date": "20260110",
            "dryy_lwpr_vrss_prpr_rate": "17.50",
        }
    }


def _make_balance_payload():
    """계좌 잔고 조회 API 응답."""
    return {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리 되었습니다.",
        "output1": [
            {
                "pdno": "005930",
                "prdt_name": "삼성전자",
                "hldg_qty": "10",
                "pchs_avg_pric": "68000.0000",
                "pchs_amt": "680000",
                "prpr": "70500",
                "evlu_amt": "705000",
                "evlu_pfls_amt": "25000",
                "evlu_pfls_rt": "3.68",
                "evlu_erng_rt": "3.68",
            }
        ],
        "output2": [
            {
                "dnca_tot_amt": "5000000",
                "nxdy_excc_amt": "5000000",
                "prvs_rcdl_excc_amt": "5000000",
                "cma_evlu_amt": "0",
                "bfdy_buy_amt": "0",
                "thdt_buy_amt": "0",
                "nass_amt": "5705000",
                "pchs_amt_smtl_amt": "680000",
                "evlu_amt_smtl_amt": "705000",
                "evlu_pfls_smtl_amt": "25000",
                "tot_evlu_amt": "5705000",
                "tot_stln_slng_chgs": "0",
            }
        ],
    }


def _make_order_success_payload():
    """주문 성공 API 응답."""
    return {
        "rt_cd": "0",
        "msg_cd": "APBK0013",
        "msg1": "주문 전송 완료 되었습니다.",
        "output": {
            "KRX_FWDG_ORD_ORGNO": "91252",
            "ODNO": "0000123456",
            "ORD_TMD": "101530",
        }
    }


def _make_ranking_rise_payload():
    """상승률 랭킹 API 응답."""
    return {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리 되었습니다.",
        "output": [
            {
                "stck_shrn_iscd": "005930",
                "data_rank": "1",
                "hts_kor_isnm": "삼성전자",
                "stck_prpr": "70500",
                "prdy_vrss": "2100",
                "prdy_vrss_sign": "2",
                "prdy_ctrt": "3.07",
                "acml_vol": "30000000",
                "stck_hgpr": "71000",
                "hgpr_hour": "100500",
                "acml_hgpr_date": "20260308",
                "stck_lwpr": "69000",
                "lwpr_hour": "090500",
                "acml_lwpr_date": "20260308",
                "lwpr_vrss_prpr_rate": "2.17",
                "dsgt_date_clpr_vrss_prpr_rate": "3.07",
                "cnnt_ascn_dynu": "3",
                "hgpr_vrss_prpr_rate": "-0.70",
                "cnnt_down_dynu": "0",
                "oprc_vrss_prpr_sign": "2",
                "oprc_vrss_prpr": "700",
                "oprc_vrss_prpr_rate": "1.00",
                "prd_rsfl": "2100",
                "prd_rsfl_rate": "3.07",
            },
            {
                "stck_shrn_iscd": "000660",
                "data_rank": "2",
                "hts_kor_isnm": "SK하이닉스",
                "stck_prpr": "180000",
                "prdy_vrss": "5000",
                "prdy_vrss_sign": "2",
                "prdy_ctrt": "2.86",
                "acml_vol": "5000000",
                "stck_hgpr": "181000",
                "hgpr_hour": "133000",
                "acml_hgpr_date": "20260308",
                "stck_lwpr": "176000",
                "lwpr_hour": "091000",
                "acml_lwpr_date": "20260308",
                "lwpr_vrss_prpr_rate": "2.27",
                "dsgt_date_clpr_vrss_prpr_rate": "2.86",
                "cnnt_ascn_dynu": "2",
                "hgpr_vrss_prpr_rate": "-0.55",
                "cnnt_down_dynu": "0",
                "oprc_vrss_prpr_sign": "2",
                "oprc_vrss_prpr": "3000",
                "oprc_vrss_prpr_rate": "1.69",
                "prd_rsfl": "5000",
                "prd_rsfl_rate": "2.86",
            },
        ],
    }


def _make_volume_ranking_payload():
    """거래량 랭킹 API 응답."""
    return {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리 되었습니다.",
        "output": [
            {
                "hts_kor_isnm": "삼성전자",
                "mksc_shrn_iscd": "005930",
                "data_rank": "01",
                "stck_prpr": "70500",
                "prdy_vrss_sign": "2",
                "prdy_vrss": "1200",
                "prdy_ctrt": "1.73",
                "acml_vol": "30000000",
                "prdy_vol": "25000000",
                "lstn_stcn": "5969782550",
                "avrg_vol": "20000000",
                "n_befr_clpr_vrss_prpr_rate": "1.50",
                "vol_inrt": "20.00",
                "vol_tnrt": "0.50",
                "nday_vol_tnrt": "0.45",
                "avrg_tr_pbmn": "1400000000000",
                "tr_pbmn_tnrt": "75.00",
                "nday_tr_pbmn_tnrt": "72.00",
                "acml_tr_pbmn": "2100000000000",
            },
        ],
    }


# ============================================================================
# 1. 현재가 조회 — 전체 스택
# ============================================================================

class TestDeepStockPrice:
    """
    GET /api/stock/{code}
    Route → StockQueryService → TradingService → BrokerAPIWrapper
      → KoreaInvestApiQuotations.get_current_price → [Mock HTTP]
    """

    @pytest.mark.asyncio
    async def test_stock_price_full_stack(self, deep_paper_ctx, mocker):
        """전체 스택을 통해 현재가를 조회하고 뷰모델로 변환되는지 검증."""
        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)
        payload = _make_stock_price_payload()
        patch_session_get(quot_api, mocker, payload)

        # StockCodeMapper.get_name_by_code mock (CSV 파일 미사용)
        deep_paper_ctx.broker._stock_mapper.get_name_by_code = MagicMock(return_value="삼성전자")

        client = deep_paper_ctx._test_client
        resp = client.get("/api/stock/005930")

        assert resp.status_code == 200
        body = resp.json()
        assert body["rt_cd"] == "0"

        # StockQueryService가 뷰모델로 가공한 결과 검증
        data = body["data"]
        assert data["code"] == "005930"
        assert data["name"] == "삼성전자"
        assert data["price"] == "70500"
        assert data["change"] == "1200"
        assert data["rate"] == "1.73"
        assert data["sign"] == "+"  # prdy_vrss_sign "2" → "+"
        assert data["bstp_kor_isnm"] == "전기전자"
        assert data["per"] == "12.50"
        assert data["pbr"] == "1.30"
        assert data["open"] == "69800"
        assert data["high"] == "71000"
        assert data["low"] == "69500"

    @pytest.mark.asyncio
    async def test_stock_price_api_error(self, deep_paper_ctx, mocker):
        """API 에러 응답이 전체 스택을 통해 올바르게 전파된다."""
        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)
        error_payload = {
            "rt_cd": "1",
            "msg_cd": "EGW00000",
            "msg1": "초당 거래건수를 초과하였습니다.",
        }
        patch_session_get(quot_api, mocker, error_payload)

        client = deep_paper_ctx._test_client
        resp = client.get("/api/stock/005930")

        assert resp.status_code == 200
        body = resp.json()
        assert body["rt_cd"] != "0"


# ============================================================================
# 2. 계좌 잔고 조회 — 전체 스택
# ============================================================================

class TestDeepBalance:
    """
    GET /api/balance
    Route → StockQueryService → TradingService → BrokerAPIWrapper
      → KoreaInvestApiAccount.get_account_balance → [Mock HTTP]
    """

    @pytest.mark.asyncio
    async def test_balance_full_stack(self, deep_paper_ctx, mocker):
        """전체 스택을 통해 잔고를 조회하고 계좌 정보가 포함되는지 검증."""
        account_api = _get_account_api_from_ctx(deep_paper_ctx)
        payload = _make_balance_payload()
        patch_session_get(account_api, mocker, payload)

        client = deep_paper_ctx._test_client
        resp = client.get("/api/balance")

        assert resp.status_code == 200
        body = resp.json()
        assert body["rt_cd"] == "0"

        # 계좌 정보 (route handler가 env에서 추출)
        assert body["account_info"]["type"] == "모의투자"
        assert body["account_info"]["number"] == "99887766-01"

    @pytest.mark.asyncio
    async def test_balance_api_error(self, deep_paper_ctx, mocker):
        """잔고 조회 API 에러 시에도 계좌 정보는 포함된다."""
        account_api = _get_account_api_from_ctx(deep_paper_ctx)
        error_payload = {
            "rt_cd": "1",
            "msg_cd": "EGW00000",
            "msg1": "잔고 조회 실패",
        }
        patch_session_get(account_api, mocker, error_payload)

        client = deep_paper_ctx._test_client
        resp = client.get("/api/balance")

        assert resp.status_code == 200
        body = resp.json()
        assert body["rt_cd"] != "0"
        assert "account_info" in body


# ============================================================================
# 3. 매수 주문 — 전체 스택
# ============================================================================

class TestDeepBuyOrder:
    """
    POST /api/order (side=buy)
    Route → OrderExecutionService → TradingService → BrokerAPIWrapper
      → KoreaInvestApiTrading.place_stock_order → [Mock HTTP: hashkey + order]
    """

    @pytest.mark.asyncio
    async def test_buy_order_full_stack(self, deep_paper_ctx, mocker):
        """매수 주문이 hashkey → order 2단계 HTTP 호출을 거쳐 성공한다."""
        trading_api = _get_trading_api_from_ctx(deep_paper_ctx)
        assert trading_api is not None, "Trading API를 찾을 수 없습니다"

        order_payload = _make_order_success_payload()

        # [Fix] MarketDateManager.is_market_open_now()가 비동기 메서드이므로 AsyncMock으로 설정
        # 이 메서드가 True를 반환하도록 하여 "장 중" 상태를 시뮬레이션합니다.
        mock_mdm = AsyncMock(spec=MarketDateManager)
        mock_mdm.is_market_open_now.return_value = True
        deep_paper_ctx.order_execution_service.market_date_manager = mock_mdm
    
        # hashkey + order POST 모킹
        from brokers.korea_investment.korea_invest_url_keys import EndpointKey
        expected_order_url = trading_api.url(EndpointKey.ORDER_CASH)

        async def _side_effect(url, *args, **kwargs):
            u = str(url)
            if "hashkey" in u:
                return make_http_response({"HASH": "test-hash-key-123"})
            if u == expected_order_url:
                return make_http_response(order_payload)
            return make_http_response({"rt_cd": "0", "msg1": "ok"})

        mocker.patch.object(
            trading_api._async_session, "post",
            new_callable=AsyncMock, side_effect=_side_effect
        )

        client = deep_paper_ctx._test_client
        resp = client.post("/api/order", json={
            "code": "005930", "price": "70000", "qty": "10", "side": "buy"
        })

        assert resp.status_code == 200
        body = resp.json()
        assert body["rt_cd"] == "0"

        # virtual_manager에 수동매매 기록 검증
        deep_paper_ctx.virtual_manager.log_buy.assert_called_once_with("수동매매", "005930", 70000)

    @pytest.mark.asyncio
    async def test_buy_order_market_closed(self, deep_paper_ctx, mocker):
        """장 마감 시 매수 주문이 거부된다."""
        deep_paper_ctx.order_execution_service.market_date_manager.is_market_open_now = AsyncMock(return_value=False)

        client = deep_paper_ctx._test_client
        resp = client.post("/api/order", json={
            "code": "005930", "price": "70000", "qty": "10", "side": "buy"
        })

        assert resp.status_code == 200
        body = resp.json()
        # MARKET_CLOSED 에러코드 → rt_cd != "0"
        assert body["rt_cd"] != "0"
        deep_paper_ctx.virtual_manager.log_buy.assert_not_called()


# ============================================================================
# 4. 매도 주문 — 전체 스택
# ============================================================================

class TestDeepSellOrder:
    """
    POST /api/order (side=sell)
    """

    @pytest.mark.asyncio
    async def test_sell_order_full_stack(self, deep_paper_ctx, mocker):
        """매도 주문이 전체 스택을 통해 성공한다."""
        trading_api = _get_trading_api_from_ctx(deep_paper_ctx)
        assert trading_api is not None

        order_payload = _make_order_success_payload()
        # [Fix] MarketDateManager.is_market_open_now()가 비동기 메서드이므로 AsyncMock으로 설정
        # 이 메서드가 True를 반환하도록 하여 "장 중" 상태를 시뮬레이션합니다.
        mock_mdm = AsyncMock(spec=MarketDateManager)
        mock_mdm.is_market_open_now.return_value = True
        deep_paper_ctx.order_execution_service.market_date_manager = mock_mdm
    
        from brokers.korea_investment.korea_invest_url_keys import EndpointKey
        expected_order_url = trading_api.url(EndpointKey.ORDER_CASH)

        async def _side_effect(url, *args, **kwargs):
            u = str(url)
            if "hashkey" in u:
                return make_http_response({"HASH": "test-hash-key-456"})
            if u == expected_order_url:
                return make_http_response(order_payload)
            return make_http_response({"rt_cd": "0", "msg1": "ok"})

        mocker.patch.object(
            trading_api._async_session, "post",
            new_callable=AsyncMock, side_effect=_side_effect
        )

        client = deep_paper_ctx._test_client
        resp = client.post("/api/order", json={
            "code": "005930", "price": "71000", "qty": "5", "side": "sell"
        })

        assert resp.status_code == 200
        body = resp.json()
        assert body["rt_cd"] == "0"

        deep_paper_ctx.virtual_manager.log_sell.assert_called_once_with("005930", 71000)

    @pytest.mark.asyncio
    async def test_sell_order_hashkey_failure(self, deep_paper_ctx, mocker):
        """hashkey 발급 실패 시 주문이 실패한다."""
        trading_api = _get_trading_api_from_ctx(deep_paper_ctx)
        assert trading_api is not None

        deep_paper_ctx.order_execution_service.market_date_manager.is_market_open_now = AsyncMock(return_value=True)

        # hashkey 응답에 HASH 값이 없는 경우
        async def _side_effect(url, *args, **kwargs):
            if "hashkey" in str(url):
                return make_http_response({"rt_cd": "1", "msg1": "hashkey 실패"})
            return make_http_response({"rt_cd": "0", "msg1": "ok"})

        mocker.patch.object(
            trading_api._async_session, "post",
            new_callable=AsyncMock, side_effect=_side_effect
        )

        client = deep_paper_ctx._test_client
        resp = client.post("/api/order", json={
            "code": "005930", "price": "71000", "qty": "5", "side": "sell"
        })

        assert resp.status_code == 200
        body = resp.json()
        # hashkey 실패 → 주문 미전송
        assert body["rt_cd"] != "0"
        deep_paper_ctx.virtual_manager.log_sell.assert_not_called()


# ============================================================================
# 5. 상승률 랭킹 — 전체 스택
# ============================================================================

class TestDeepRanking:
    """
    GET /api/ranking/{category}
    Route → StockQueryService.handle_get_top_stocks → TradingService
      → BrokerAPIWrapper → KoreaInvestApiQuotations → [Mock HTTP]
    """

    @pytest.mark.asyncio
    async def test_rise_ranking_full_stack(self, deep_paper_ctx, mocker):
        """상승률 랭킹이 전체 스택을 통해 조회된다."""
        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)
        payload = _make_ranking_rise_payload()
        patch_session_get(quot_api, mocker, payload)

        # background_service의 캐시 비활성화 (실시간 API 호출 유도)
        deep_paper_ctx.stock_query_service.background_service = None

        client = deep_paper_ctx._test_client
        resp = client.get("/api/ranking/rise")

        assert resp.status_code == 200
        body = resp.json()
        assert body["rt_cd"] == "0"

        # ResFluctuation 객체가 직렬화되어 반환
        assert len(body["data"]) == 2
        # to_dict() 또는 모델 직렬화 결과 검증
        first = body["data"][0]
        assert first["hts_kor_isnm"] == "삼성전자"
        assert first["stck_prpr"] == "70500"

    @pytest.mark.asyncio
    async def test_volume_ranking_full_stack(self, deep_paper_ctx, mocker):
        """거래량 랭킹이 전체 스택을 통해 조회된다."""
        quot_api = _get_quotations_api_from_ctx(deep_paper_ctx)
        payload = _make_volume_ranking_payload()
        patch_session_get(quot_api, mocker, payload)

        deep_paper_ctx.stock_query_service.background_service = None

        client = deep_paper_ctx._test_client
        resp = client.get("/api/ranking/volume")

        assert resp.status_code == 200
        body = resp.json()
        assert body["rt_cd"] == "0"
        assert len(body["data"]) == 1


# ============================================================================
# 6. 시장 상태 — 전체 스택 (실제 WebAppContext 메서드)
# ============================================================================

class TestDeepStatus:
    """
    GET /api/status
    실제 WebAppContext.is_market_open_now(), get_env_type() 등이 호출된다.
    """

    @pytest.mark.asyncio
    async def test_status_with_real_context(self, deep_paper_ctx):
        """실제 WebAppContext로 /api/status가 올바르게 응답한다."""
        client = deep_paper_ctx._test_client
        resp = client.get("/api/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["initialized"] is True
        assert body["env_type"] == "모의투자"
        # is_market_open_now는 실제 MarketDateManager 결과 (주말/평일에 따라 다를 수 있음)
        assert isinstance(body["market_open"], bool)
        assert body["current_time"] != ""


# ============================================================================
# 7. 잘못된 입력 — 전체 스택
# ============================================================================

class TestDeepInputValidation:
    """입력 검증이 서비스 레이어에서 올바르게 처리되는지 검증."""

    @pytest.mark.asyncio
    async def test_order_invalid_side(self, deep_paper_ctx):
        """잘못된 side 값은 400을 반환한다."""
        client = deep_paper_ctx._test_client
        resp = client.post("/api/order", json={
            "code": "005930", "price": "70000", "qty": "10", "side": "hold"
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_order_invalid_price_format(self, deep_paper_ctx, mocker):
        """가격이 숫자가 아닌 경우 서비스에서 INVALID_INPUT으로 처리."""
        deep_paper_ctx.order_execution_service.market_date_manager.is_market_open_now = AsyncMock(return_value=True)

        client = deep_paper_ctx._test_client
        resp = client.post("/api/order", json={
            "code": "005930", "price": "abc", "qty": "10", "side": "buy"
        })
        assert resp.status_code == 200
        body = resp.json()
        # OrderExecutionService가 ValueError 처리 → INVALID_INPUT
        assert body["rt_cd"] != "0"

    @pytest.mark.asyncio
    async def test_ranking_invalid_category(self, deep_paper_ctx):
        """잘못된 랭킹 카테고리는 400을 반환한다."""
        client = deep_paper_ctx._test_client
        resp = client.get("/api/ranking/nonexistent")
        assert resp.status_code == 400
