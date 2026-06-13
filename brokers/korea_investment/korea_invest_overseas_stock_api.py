from __future__ import annotations

from typing import Optional

import httpx

from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from brokers.korea_investment.korea_invest_header_provider import KoreaInvestHeaderProvider
from brokers.korea_investment.korea_invest_trading_api import KoreaInvestApiTrading
from brokers.korea_investment.korea_invest_trid_provider import KoreaInvestTrIdProvider
from brokers.korea_investment.korea_invest_url_keys import EndpointKey
from brokers.korea_investment.korea_invest_url_provider import KoreaInvestUrlProvider
from common.overseas_types import OverseasExchange, OverseasOrderReport, OverseasPriceSummary
from common.types import ErrorCode, ResCommonResponse


_PRICE_EXCHANGE_CODES = {
    OverseasExchange.NASD: "NAS",
    OverseasExchange.NYSE: "NYS",
    OverseasExchange.AMEX: "AMS",
}
_PERIOD_CODES = {"D": "0", "W": "1", "M": "2"}


class KoreaInvestOverseasStockApi(KoreaInvestApiTrading):
    """KIS 해외주식 REST API v1 surface.

    국내 주식 API 클래스의 응답 모델과 파라미터가 많이 달라서 별도 클래스로 둔다.
    v1은 미국 3시장 조회 + 수동 지정가 주문만 지원한다.
    """

    def __init__(
        self,
        env: KoreaInvestApiEnv,
        logger=None,
        market_clock=None,
        async_client: Optional[httpx.AsyncClient] = None,
        header_provider: Optional[KoreaInvestHeaderProvider] = None,
        url_provider: Optional[KoreaInvestUrlProvider] = None,
        trid_provider: Optional[KoreaInvestTrIdProvider] = None,
    ):
        super().__init__(
            env,
            logger,
            market_clock,
            async_client=async_client,
            header_provider=header_provider,
            url_provider=url_provider,
            trid_provider=trid_provider,
        )

    @staticmethod
    def _split_account(full_account_number: str) -> tuple[str, str]:
        text = str(full_account_number or "").strip()
        if "-" in text and len(text.split("-", 1)[1]) == 2:
            cano, acnt_prdt_cd = text.split("-", 1)
            return cano, acnt_prdt_cd
        return text, "01"

    @staticmethod
    def _exchange_price_code(exchange: OverseasExchange) -> str:
        return _PRICE_EXCHANGE_CODES[exchange]

    @staticmethod
    def _as_exchange(value: OverseasExchange | str) -> OverseasExchange:
        if isinstance(value, OverseasExchange):
            return value
        return OverseasExchange(str(value).upper())

    @staticmethod
    def _to_float(value, default: float = 0.0) -> float:
        try:
            text = str(value or "").replace(",", "").strip()
            return float(text) if text else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_int(value, default: int = 0) -> int:
        try:
            text = str(value or "").replace(",", "").strip()
            return int(float(text)) if text else default
        except (TypeError, ValueError):
            return default

    def _account_parts(self) -> tuple[str, str]:
        full_config = self._env.active_config or {}
        return self._split_account(full_config.get("stock_account_number", ""))

    def _set_tr_header(self, tr_id: str) -> None:
        full_config = self._env.active_config or {}
        self._headers.set_tr_id(tr_id)
        self._headers.set_custtype(full_config.get("custtype", "P"))

    def _normalize_price_summary(
        self,
        *,
        symbol: str,
        exchange: OverseasExchange,
        output: dict,
    ) -> OverseasPriceSummary:
        price = self._to_float(
            output.get("last")
            or output.get("ovrs_nmix_prpr")
            or output.get("base")
            or output.get("clos")
        )
        change_rate = self._to_float(
            output.get("rate")
            or output.get("prdy_ctrt")
            or output.get("diff_rate")
        )
        volume = self._to_int(output.get("tvol") or output.get("acml_vol"))
        timestamp = str(
            output.get("xymd")
            or output.get("trdt")
            or output.get("stck_bsop_date")
            or ""
        )
        return OverseasPriceSummary(
            symbol=symbol,
            exchange=exchange,
            currency="USD",
            price=price,
            change_rate=change_rate,
            volume=volume,
            timestamp=timestamp,
            raw=output,
        )

    async def get_overseas_price(
        self,
        symbol: str,
        *,
        exchange: OverseasExchange | str = OverseasExchange.NASD,
    ) -> ResCommonResponse:
        exchange = self._as_exchange(exchange)
        tr_id = self._trid_provider.overseas_stock("price")
        self._set_tr_header(tr_id)
        params = {
            "AUTH": "",
            "EXCD": self._exchange_price_code(exchange),
            "SYMB": str(symbol).upper(),
        }
        resp = await self.call_api(
            "GET",
            EndpointKey.OVERSEAS_STOCK_PRICE,
            params=params,
            retry_count=1,
        )
        if resp.rt_cd != ErrorCode.SUCCESS.value:
            return resp
        raw = resp.data if isinstance(resp.data, dict) else {}
        output = raw.get("output") or raw
        if not isinstance(output, dict):
            output = {}
        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1=resp.msg1,
            data=self._normalize_price_summary(symbol=str(symbol).upper(), exchange=exchange, output=output),
        )

    async def get_overseas_dailyprice(
        self,
        symbol: str,
        *,
        exchange: OverseasExchange | str = OverseasExchange.NASD,
        start_date: str = "",
        end_date: str = "",
        period: str = "D",
        adjusted: bool = True,
    ) -> ResCommonResponse:
        exchange = self._as_exchange(exchange)
        period_code = _PERIOD_CODES.get(str(period).upper())
        if period_code is None:
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value,
                msg1=f"지원하지 않는 해외주식 기간 구분: {period}",
                data=[],
            )
        tr_id = self._trid_provider.overseas_stock("dailyprice")
        self._set_tr_header(tr_id)
        params = {
            "AUTH": "",
            "EXCD": self._exchange_price_code(exchange),
            "SYMB": str(symbol).upper(),
            "GUBN": period_code,
            "BYMD": end_date or start_date,
            "MODP": "1" if adjusted else "0",
        }
        return await self.call_api(
            "GET",
            EndpointKey.OVERSEAS_STOCK_DAILYPRICE,
            params=params,
            retry_count=1,
        )

    async def get_overseas_balance(
        self,
        *,
        exchange: OverseasExchange | str = OverseasExchange.NASD,
        currency: str = "USD",
        ctx_area_fk200: str = "",
        ctx_area_nk200: str = "",
    ) -> ResCommonResponse:
        exchange = self._as_exchange(exchange)
        cano, acnt_prdt_cd = self._account_parts()
        self._set_tr_header(self._trid_provider.overseas_stock_inquire_balance())
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_EXCG_CD": exchange.value,
            "TR_CRCY_CD": currency,
            "CTX_AREA_FK200": ctx_area_fk200,
            "CTX_AREA_NK200": ctx_area_nk200,
        }
        return await self.call_api(
            "GET",
            EndpointKey.OVERSEAS_STOCK_INQUIRE_BALANCE,
            params=params,
            retry_count=1,
        )

    async def inquire_overseas_ccnl(
        self,
        *,
        symbol: str = "%",
        exchange: OverseasExchange | str = OverseasExchange.NASD,
        start_date: str,
        end_date: str,
        side_code: str = "00",
        ccld_nccs_dvsn: str = "00",
    ) -> ResCommonResponse:
        exchange = self._as_exchange(exchange)
        cano, acnt_prdt_cd = self._account_parts()
        self._set_tr_header(self._trid_provider.overseas_stock_inquire_ccnl())
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "PDNO": symbol,
            "ORD_STRT_DT": start_date,
            "ORD_END_DT": end_date,
            "SLL_BUY_DVSN": side_code,
            "CCLD_NCCS_DVSN": ccld_nccs_dvsn,
            "OVRS_EXCG_CD": exchange.value,
            "SORT_SQN": "DS",
            "ORD_DT": "",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "CTX_AREA_NK200": "",
            "CTX_AREA_FK200": "",
        }
        return await self.call_api(
            "GET",
            EndpointKey.OVERSEAS_STOCK_INQUIRE_CCNL,
            params=params,
            retry_count=1,
        )

    async def inquire_overseas_unfilled(
        self,
        *,
        exchange: OverseasExchange | str = OverseasExchange.NASD,
    ) -> ResCommonResponse:
        exchange = self._as_exchange(exchange)
        cano, acnt_prdt_cd = self._account_parts()
        self._set_tr_header(self._trid_provider.overseas_stock_inquire_nccs())
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_EXCG_CD": exchange.value,
            "SORT_SQN": "DS",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        }
        return await self.call_api(
            "GET",
            EndpointKey.OVERSEAS_STOCK_INQUIRE_NCCS,
            params=params,
            retry_count=1,
        )

    async def place_overseas_limit_order(
        self,
        *,
        symbol: str,
        exchange: OverseasExchange | str,
        side: str,
        qty: int,
        limit_price: str,
    ) -> ResCommonResponse:
        exchange = self._as_exchange(exchange)
        side_value = str(side).lower()
        if side_value not in ("buy", "sell"):
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value,
                msg1="side는 buy 또는 sell이어야 합니다.",
                data=None,
            )
        if qty <= 0:
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value,
                msg1="주문수량은 0보다 커야 합니다.",
                data=None,
            )
        if self._to_float(limit_price) <= 0:
            return ResCommonResponse(
                rt_cd=ErrorCode.ORDER_POLICY_BLOCKED.value,
                msg1="해외주식 v1은 지정가 주문만 지원합니다.",
                data={"rule": "overseas_market_order_not_supported"},
            )

        cano, acnt_prdt_cd = self._account_parts()
        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_EXCG_CD": exchange.value,
            "PDNO": str(symbol).upper(),
            "ORD_QTY": str(qty),
            "OVRS_ORD_UNPR": str(limit_price),
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00",
        }
        hashkey = await self._get_hashkey(body)
        if not hashkey or isinstance(hashkey, ResCommonResponse):
            return ResCommonResponse(
                rt_cd=ErrorCode.MISSING_KEY.value,
                msg1=f"hashkey 계산 실패 - {hashkey}",
                data=None,
            )

        tr_id = self._trid_provider.overseas_stock_order(is_buy=side_value == "buy")
        self._set_tr_header(tr_id)
        self._headers.set_hashkey(hashkey)
        self._headers.set_gt_uid()
        resp = await self.call_api(
            "POST",
            EndpointKey.OVERSEAS_STOCK_ORDER,
            data=body,
            retry_count=3,
        )
        if resp.rt_cd != ErrorCode.SUCCESS.value:
            return resp
        raw = resp.data if isinstance(resp.data, dict) else {}
        output = raw.get("output") if isinstance(raw, dict) else {}
        output = output if isinstance(output, dict) else {}
        report = OverseasOrderReport(
            symbol=str(symbol).upper(),
            exchange=exchange,
            side=side_value,
            qty=qty,
            limit_price=str(limit_price),
            broker_order_no=str(output.get("ODNO") or output.get("odno") or ""),
            raw=raw,
        )
        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1=resp.msg1, data=report)

    async def cancel_overseas_order(
        self,
        *,
        symbol: str,
        exchange: OverseasExchange | str,
        original_order_no: str,
        qty: int,
        limit_price: str,
        rvse_cncl_dvsn_cd: str = "02",
    ) -> ResCommonResponse:
        exchange = self._as_exchange(exchange)
        cano, acnt_prdt_cd = self._account_parts()
        body = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "OVRS_EXCG_CD": exchange.value,
            "PDNO": str(symbol).upper(),
            "ORGN_ODNO": original_order_no,
            "RVSE_CNCL_DVSN_CD": rvse_cncl_dvsn_cd,
            "ORD_QTY": str(qty),
            "OVRS_ORD_UNPR": str(limit_price),
            "ORD_SVR_DVSN_CD": "0",
        }
        hashkey = await self._get_hashkey(body)
        if not hashkey or isinstance(hashkey, ResCommonResponse):
            return ResCommonResponse(
                rt_cd=ErrorCode.MISSING_KEY.value,
                msg1=f"hashkey 계산 실패 - {hashkey}",
                data=None,
            )
        self._set_tr_header(self._trid_provider.overseas_stock_order_rvsecncl())
        self._headers.set_hashkey(hashkey)
        self._headers.set_gt_uid()
        return await self.call_api(
            "POST",
            EndpointKey.OVERSEAS_STOCK_ORDER_RVSECNCL,
            data=body,
            retry_count=3,
        )
