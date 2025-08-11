# brokers/korea_investment/korea_invest_account_api.py

import httpx
from brokers.korea_investment.korea_invest_api_base import KoreaInvestApiBase
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from brokers.korea_investment.korea_invest_params_provider import Params
from brokers.korea_investment.korea_invest_header_provider import KoreaInvestHeaderProvider
from brokers.korea_investment.korea_invest_url_provider import KoreaInvestUrlProvider
from brokers.korea_investment.korea_invest_url_keys import EndpointKey
from typing import Optional
from common.types import ResCommonResponse


class KoreaInvestApiAccount(KoreaInvestApiBase):
    def __init__(self, env: KoreaInvestApiEnv, logger=None,
                 async_client: Optional[httpx.AsyncClient] = None,
                 header_provider: Optional[KoreaInvestHeaderProvider] = None,
                 url_provider: Optional[KoreaInvestUrlProvider] = None):
        super().__init__(env,
                         logger,
                         async_client=async_client,
                         header_provider=header_provider,
                         url_provider=url_provider)

    async def get_account_balance(self) -> ResCommonResponse:
        """
        모의투자 또는 실전투자 계좌의 잔고를 조회하는 메서드.
        투자환경(env)에 따라 자동 분기된다.
        """
        full_config = self._env.active_config

        is_paper = self._env.is_paper_trading  # 모의투자 여부 판단
        tr_id = (
            full_config['tr_ids']['account']['inquire_balance_paper']
            if is_paper else
            full_config['tr_ids']['account']['inquire_balance_real']
        )

        # ✅ 요청 단위 임시 헤더 주입
        self._headers.set_tr_id(tr_id)
        self._headers.set_custtype(full_config['custtype'])

        full_account_number = full_config['stock_account_number']
        if not is_paper and '-' in full_account_number and len(full_account_number.split('-')[1]) == 2:
            cano, acnt_prdt_cd = full_account_number.split('-')
        else:
            cano = full_account_number
            acnt_prdt_cd = "01"

        params = Params.account_balance(cano=cano, acnt_prdt_cd=acnt_prdt_cd)

        mode_str = "모의투자" if is_paper else "실전투자"
        self._logger.info(f"{mode_str} 계좌 잔고 조회 시도...")
        return await self.call_api('GET', EndpointKey.INQUIRE_BALANCE, params=params, retry_count=1)
