# brokers/korea_investment/korea_invest_account_api.py

import httpx
from brokers.korea_investment.korea_invest_api_base import KoreaInvestApiBase
from brokers.korea_investment.korea_invest_token_manager import TokenManager # TokenManager를 import
from typing import Optional

class KoreaInvestApiAccount(KoreaInvestApiBase):
    def __init__(self, base_url, headers, config, token_manager: TokenManager, logger=None, async_client: Optional[httpx.AsyncClient] = None):
        super().__init__(base_url, headers, config, token_manager, logger, async_client=async_client)

    async def get_account_balance(self):  # 모의투자용
        path = "/uapi/domestic-stock/v1/trading/inquire-balance"

        full_config = self._config
        self._headers["tr_id"] = full_config['tr_ids']['account']['inquire_balance_paper']
        self._headers["custtype"] = full_config['custtype']

        cano = full_config['stock_account_number']
        acnt_prdt_div_code = "01"

        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_div_code,
            "AFHR_FLPR_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "FUND_STTL_ICLD_YN": "N",
            "INQR_DVSN": "01",
            "OFL_YN": "N",
            "PRCS_DVSN": "01",
            "UNPR_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }
        self.logger.info(f"계좌 잔고 조회 시도...")
        return await self.call_api('GET', path, params=params, retry_count=1)  # <--- retry_count 추가

    async def get_real_account_balance(self):  # 실전용
        """
        실전 계좌 잔고를 조회하는 메서드.
        """
        path = "/uapi/domestic-stock/v1/trading/inquire-balance"

        full_config = self._config
        self._headers["tr_id"] = full_config['tr_ids']['account']['inquire_balance_real']
        self._headers["custtype"] = full_config['custtype']

        full_account_number = full_config['stock_account_number']

        if '-' in full_account_number and len(full_account_number.split('-')[1]) == 2:
            cano = full_account_number.split('-')[0]
            acnt_prdt_cd = full_account_number.split('-')[1]
        else:
            cano = full_account_number
            acnt_prdt_cd = "01"

        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "FUND_STTL_ICLD_YN": "N",
            "INQR_DVSN": "01",
            "OFL_YN": "N",
            "PRCS_DVSN": "01",
            "UNPR_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }
        self.logger.info(f"실전 계좌 잔고 조회 시도...")
        return await self.call_api('GET', path, params=params, retry_count=1)  # <--- retry_count 추가
