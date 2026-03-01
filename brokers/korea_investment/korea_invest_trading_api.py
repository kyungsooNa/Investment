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
from typing import Optional
from common.types import ResCommonResponse, ErrorCode


class KoreaInvestApiTrading(KoreaInvestApiBase):
    def __init__(self,
                 env: KoreaInvestApiEnv,
                 logger,
                 time_manager,
                 async_client: Optional[httpx.AsyncClient] = None,
                 header_provider: Optional[KoreaInvestHeaderProvider] = None,
                 url_provider: Optional[KoreaInvestUrlProvider] = None,
                 trid_provider: Optional[KoreaInvestTrIdProvider] = None):
        super().__init__(env,
                         logger,
                         time_manager,
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
                                                              data=data, expect_standard_schema=False, retry_count=1)

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
                                is_buy: bool) -> ResCommonResponse:  # async def로 변경됨
        full_config = self._env.active_config

        tr_id = self._trid_provider.trading_order_cash(is_buy)  # 모드에 따라 자동

        order_dvsn = '00' if int(order_price) > 0 else '01'  # 00: 지정가, 01: 시장가

        data = Params.order_cash_body(
            cano=full_config['stock_account_number'],
            acnt_prdt_cd="01",
            pdno=stock_code,
            ord_dvsn=order_dvsn,
            ord_qty=order_qty,
            ord_unpr=order_price,
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
            return await self.call_api('POST', EndpointKey.ORDER_CASH, data=data, retry_count=1)
