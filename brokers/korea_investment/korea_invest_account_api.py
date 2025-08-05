# brokers/korea_investment/korea_invest_account_api.py

import httpx
from brokers.korea_investment.korea_invest_api_base import KoreaInvestApiBase
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv # TokenManager를 import
from typing import Optional
from common.types import ResCommonResponse


class KoreaInvestApiAccount(KoreaInvestApiBase):
    def __init__(self, env: KoreaInvestApiEnv, logger=None, async_client: Optional[httpx.AsyncClient] = None):
        super().__init__(env, logger, async_client=async_client)

    async def get_account_balance(self) -> ResCommonResponse:
        """
        모의투자 또는 실전투자 계좌의 잔고를 조회하는 메서드.
        투자환경(env)에 따라 자동 분기된다.
        """
        path = "/uapi/domestic-stock/v1/trading/inquire-balance"
        full_config = self._env.active_config

        is_paper = self._env.is_paper_trading  # 모의투자 여부 판단
        self._headers["tr_id"] = (
            full_config['tr_ids']['account']['inquire_balance_paper']
            if is_paper else
            full_config['tr_ids']['account']['inquire_balance_real']
        )
        self._headers["custtype"] = full_config['custtype']

        full_account_number = full_config['stock_account_number']
        if not is_paper and '-' in full_account_number and len(full_account_number.split('-')[1]) == 2:
            cano, acnt_prdt_cd = full_account_number.split('-')
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

        mode_str = "모의투자" if is_paper else "실전투자"
        self._logger.info(f"{mode_str} 계좌 잔고 조회 시도...")
        return await self.call_api('GET', path, params=params, retry_count=1)
