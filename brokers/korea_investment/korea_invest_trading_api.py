# brokers/korea_investment/korea_invest_trading_api.py
import requests
import json
import os
import certifi
import asyncio  # 비동기 처리를 위해 추가
import httpx

from brokers.korea_investment.korea_invest_api_base import KoreaInvestApiBase
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv  # TokenManager를 import
from typing import Optional


class KoreaInvestApiTrading(KoreaInvestApiBase):
    def __init__(self, env: KoreaInvestApiEnv, logger, async_client: Optional[httpx.AsyncClient] = None):
        super().__init__(env, logger, async_client=async_client)

    async def _get_hashkey(self, data):  # async def로 변경됨
        """
        주문 요청 Body를 기반으로 Hashkey를 생성하여 반환합니다.
        이는 별도의 API 호출을 통해 이루어집니다.
        """
        full_config = self._env.active_config

        body_json_str = json.dumps(data)
        hashkey_url = f"{full_config['base_url']}/uapi/hashkey"

        hashkey_headers = self._headers.copy()
        hashkey_headers["appkey"] = full_config['api_key']
        hashkey_headers["appsecret"] = full_config['api_secret_key']
        hashkey_headers["Content-Type"] = "application/json; charset=utf-8"

        try:
            loop = asyncio.get_running_loop()
            hash_response = await loop.run_in_executor(
                None,
                lambda: requests.post(hashkey_url, headers=hashkey_headers, data=body_json_str, verify=certifi.where())
            )
            hash_response.raise_for_status()
            hash_data = hash_response.json()
            calculated_hashkey = hash_data.get('HASH')

            if not calculated_hashkey:
                self._logger.error(f"Hashkey API 응답에 HASH 값이 없습니다: {hash_data}")
                return None

            self._logger.info(f"Hashkey 계산 성공: {calculated_hashkey}")
            return calculated_hashkey

        except requests.exceptions.RequestException as e:
            self._logger.error(f"Hashkey API 호출 중 네트워크 오류: {e}")
            return None
        except json.JSONDecodeError:
            self._logger.error(f"Hashkey API 응답 JSON 디코딩 실패: {hash_response.text}")
            return None
        except Exception as e:
            self._logger.error(f"Hashkey API 호출 중 알 수 없는 오류: {e}")
            return None

    async def place_stock_order(self, stock_code, order_price, order_qty, trade_type):  # async def로 변경됨
        path = "/uapi/domestic-stock/v1/trading/order-cash"

        full_config = self._env.active_config

        if trade_type == "buy":
            tr_id = full_config['tr_ids']['trading']['order_cash_buy_paper'] if full_config['is_paper_trading'] else \
                full_config['tr_ids']['trading']['order_cash_buy_real']
        elif trade_type == "sell":
            tr_id = full_config['tr_ids']['trading']['order_cash_sell_paper'] if full_config['is_paper_trading'] else \
                full_config['tr_ids']['trading']['order_cash_sell_real']
        else:
            self._logger.error("trade_type은 'buy' 또는 'sell'여야 합니다.")
            return None

        self._headers["tr_id"] = tr_id
        self._headers["custtype"] = full_config['custtype']
        self._headers["gt_uid"] = os.urandom(16).hex()

        order_dvsn = '00' if order_price > 0 else '01'  # 00: 지정가, 01: 시장가

        data = {
            "CANO": full_config['stock_account_number'],
            "ACNT_PRDT_CD": "01",
            "PDNO": stock_code,
            "ORD_DVSN": order_dvsn,
            "ORD_QTY": str(order_qty),
            "ORD_UNPR": str(order_price),
            # "INQR_PSBL_QTY_DVN": "01",
            # "LOCL_CSHR_PRCS_DVSN": "00",
            # "RPRS_SYS_DVSN": "00",
            # "TR_DVN": trade_type
        }

        calculated_hashkey = await self._get_hashkey(data)
        if not calculated_hashkey:
            return None
        self._headers["hashkey"] = calculated_hashkey

        self._logger.info(f"주식 {trade_type} 주문 시도 - 종목: {stock_code}, 수량: {order_qty}, 가격: {order_price}")
        return await self.call_api('POST', path, data=data, retry_count=1)
