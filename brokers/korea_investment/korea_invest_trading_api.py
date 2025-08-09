# brokers/korea_investment/korea_invest_trading_api.py
import json
import os
import certifi
import asyncio  # 비동기 처리를 위해 추가
import httpx

from brokers.korea_investment.korea_invest_api_base import KoreaInvestApiBase
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from brokers.korea_investment.korea_invest_params_provider import Params
from typing import Optional
from common.types import ResCommonResponse, ErrorCode


class KoreaInvestApiTrading(KoreaInvestApiBase):
    def __init__(self, env: KoreaInvestApiEnv, logger, async_client: Optional[httpx.AsyncClient] = None):
        super().__init__(env, logger, async_client=async_client)

    async def _get_hashkey(self, data):  # async def로 변경됨
        """
        주문 요청 Body를 기반으로 Hashkey를 생성하여 반환합니다.
        이는 별도의 API 호출을 통해 이루어집니다.
        """
        full_config = self._env.active_config

        path = f"{full_config['base_url']}/uapi/hashkey"
        response = None

        try:
            response : ResCommonResponse = await self.call_api('POST', path, data=data, retry_count=1)

            if response.rt_cd != ErrorCode.SUCCESS.value:
                return response

            response.data.raise_for_status()
            hash_data = response.data.json()
            calculated_hashkey = hash_data.get('HASH')

            if not calculated_hashkey:
                self._logger.error(f"Hashkey API 응답에 HASH 값이 없습니다: {hash_data}")
                return None

            self._logger.info(f"Hashkey 계산 성공: {calculated_hashkey}")
            return calculated_hashkey

        except httpx.TimeoutException as e:
            self._logger.error(f"Hashkey API 타임아웃: {e}")
            return None
        except httpx.HTTPStatusError as e:
            status = e.response.data.status_code if e.response is not None else "unknown"
            body = e.response.data.text if e.response is not None else ""
            self._logger.error(f"Hashkey API HTTP 오류: {status}, 응답: {body!r}")
            return None
        except json.JSONDecodeError:
            self._logger.error(f"Hashkey API 응답 JSON 디코딩 실패: {response.data.text!r}")
            return None
        except Exception as e:
            self._logger.error(f"Hashkey API 호출 중 알 수 없는 오류: {e}")
            return None

    async def place_stock_order(self, stock_code, order_price, order_qty,
                                is_buy: bool) -> ResCommonResponse:  # async def로 변경됨
        path = "/uapi/domestic-stock/v1/trading/order-cash"

        full_config = self._env.active_config

        if is_buy:
            tr_id = full_config['tr_ids']['trading']['order_cash_buy_paper'] if full_config['is_paper_trading'] else \
                full_config['tr_ids']['trading']['order_cash_buy_real']
        else:
            tr_id = full_config['tr_ids']['trading']['order_cash_sell_paper'] if full_config['is_paper_trading'] else \
                full_config['tr_ids']['trading']['order_cash_sell_real']


        self._headers["tr_id"] = tr_id
        self._headers["custtype"] = full_config['custtype']
        self._headers["gt_uid"] = os.urandom(16).hex()

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

        self._headers["hashkey"] = calculated_hashkey
        order_type = "매수" if is_buy else "매도"
        self._logger.info(f"주식 {order_type} 주문 시도 - 종목: {stock_code}, 수량: {order_qty}, 가격: {order_price}")
        return await self.call_api('POST', path, data=data, retry_count=1)
