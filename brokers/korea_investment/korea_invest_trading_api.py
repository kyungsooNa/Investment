# brokers/korea_investment/korea_invest_trading_api.py
import json
import os
import certifi
import asyncio  # 비동기 처리를 위해 추가
import httpx

from brokers.korea_investment.korea_invest_api_base import KoreaInvestApiBase
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from brokers.korea_investment.korea_invest_params_provider import Params
from brokers.korea_investment.korea_invest_header_provider import KoreaInvestHeaderProvider
from brokers.korea_investment.korea_invest_url_provider import KoreaInvestUrlProvider
from brokers.korea_investment.korea_invest_url_keys import EndpointKey
from brokers.korea_investment.korea_invest_trid_provider import KoreaInvestTrIdProvider
from utils.korea_invest_price_utils import adjust_price
from typing import Optional
from common.types import ResCommonResponse, ErrorCode, Exchange


class KoreaInvestApiTrading(KoreaInvestApiBase):
    def __init__(self,
                 env: KoreaInvestApiEnv,
                 logger,
                 market_clock,
                 async_client: Optional[httpx.AsyncClient] = None,
                 header_provider: Optional[KoreaInvestHeaderProvider] = None,
                 url_provider: Optional[KoreaInvestUrlProvider] = None,
                 trid_provider: Optional[KoreaInvestTrIdProvider] = None):
        super().__init__(env,
                         logger,
                         market_clock,
                         async_client=async_client,
                         header_provider=header_provider,
                         url_provider=url_provider,
                         trid_provider=trid_provider)

    async def _get_hashkey(self, data):  # async def로 변경됨
        """
        주문 요청 Body를 기반으로 Hashkey를 생성하여 반환합니다.
        이는 별도의 API 호출을 통해 이루어집니다.
        """
        response = None

        try:
            response: ResCommonResponse = await self.call_api('POST', EndpointKey.HASHKEY,
                                                              data=data, expect_standard_schema=False, retry_count=3)

            if response.rt_cd != ErrorCode.SUCCESS.value:
                return response

            # response.data.raise_for_status()
            hash_data = response.data
            calculated_hashkey = hash_data.get('HASH')

            if not calculated_hashkey:
                self._logger.error(f"Hashkey API 응답에 HASH 값이 없습니다: {hash_data}")
                return None

            self._logger.info(f"Hashkey 계산 성공: {calculated_hashkey}")
            return calculated_hashkey

        except httpx.TimeoutException as e:
            self._logger.exception(f"Hashkey API 타임아웃: {e}")
            return None
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            body = e.response.text if e.response is not None else ""
            self._logger.exception(f"Hashkey API HTTP 오류: {status}, 응답: {body!r}")
            return None
        except json.JSONDecodeError:
            self._logger.exception(f"Hashkey API 응답 JSON 디코딩 실패: {response.data.text!r}")
            return None
        except Exception as e:
            self._logger.exception(f"Hashkey API 호출 중 알 수 없는 오류: {e}")
            return None

    async def place_stock_order(self, stock_code, order_price, order_qty,
                                is_buy: bool, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:  # async def로 변경됨
        full_config = self._env.active_config

        tr_id = self._trid_provider.trading_order_cash(is_buy)  # 모드에 따라 자동

        order_dvsn = '00' if int(order_price) > 0 else '01'  # 00: 지정가, 01: 시장가

        if order_dvsn == '00':  # 지정가일 때만 호가단위 보정
            adjusted = adjust_price(int(order_price))
            if adjusted != int(order_price):
                self._logger.info(f"호가단위 보정: {order_price} → {adjusted}")
            order_price = adjusted

        # NXT 거래소에서 시장가 주문은 지원하지 않음
        if exchange == Exchange.NXT and order_dvsn == '01':
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value,
                msg1="NXT 거래소에서는 시장가 주문을 지원하지 않습니다. 지정가 주문을 사용하세요.",
                data=None
            )

        data = Params.order_cash_body(
            cano=full_config['stock_account_number'],
            acnt_prdt_cd="01",
            pdno=stock_code,
            ord_dvsn=order_dvsn,
            ord_qty=order_qty,
            ord_unpr=order_price,
            excg_id_dvsn_cd=exchange.value if exchange != Exchange.KRX else "",
        )

        calculated_hashkey = await self._get_hashkey(data)
        if not calculated_hashkey:
            return ResCommonResponse(
                rt_cd=ErrorCode.MISSING_KEY.value,
                msg1=f"hashkey 계산 실패 - {calculated_hashkey}",
                data=None
            )

        with self._headers.temp(tr_id=tr_id, custtype=full_config['custtype'], hashkey=calculated_hashkey):
            # gt_uid는 temp에서 자동 생성(값 미지정 시)
            self._headers.set_gt_uid()
            self._logger.info(
                f"주식 {'매수' if is_buy else '매도'} 주문 시도 - 종목:{stock_code}, 수량:{order_qty}, 가격:{order_price}")
            return await self.call_api('POST', EndpointKey.ORDER_CASH, data=data, retry_count=10)

    async def cancel_stock_order(
        self,
        *,
        broker_order_no: str,
        order_qty: int,
        order_price: int = 0,
        order_orgno: str = "06010",
        order_dvsn: str = "00",
        qty_all_ord_yn: str = "Y",
        exchange: Exchange = Exchange.KRX,
    ) -> ResCommonResponse:
        full_config = self._env.active_config
        tr_id = self._trid_provider.trading_order_rvsecncl()
        data = Params.order_rvsecncl_body(
            cano=full_config["stock_account_number"],
            acnt_prdt_cd="01",
            order_orgno=order_orgno,
            original_order_no=broker_order_no,
            ord_dvsn=order_dvsn,
            rvse_cncl_dvsn_cd="02",
            ord_qty=order_qty,
            ord_unpr=order_price,
            qty_all_ord_yn=qty_all_ord_yn,
            excg_id_dvsn_cd=exchange.value if exchange != Exchange.KRX else "",
        )

        calculated_hashkey = await self._get_hashkey(data)
        if not calculated_hashkey:
            return ResCommonResponse(
                rt_cd=ErrorCode.MISSING_KEY.value,
                msg1=f"hashkey 怨꾩궛 ?ㅽ뙣 - {calculated_hashkey}",
                data=None,
            )

        with self._headers.temp(tr_id=tr_id, custtype=full_config["custtype"], hashkey=calculated_hashkey):
            self._headers.set_gt_uid()
            self._logger.info(
                f"二쇱떇 痍⑥냼 二쇰Ц ?쒕룄 - 二쇰Ц踰덊샇:{broker_order_no}, ?섎웾:{order_qty}"
            )
            return await self.call_api("POST", EndpointKey.ORDER_RVSECNCL, data=data, retry_count=3)
