# brokers/korea_investment/korea_invest_account_api.py

from datetime import datetime

import httpx
from brokers.korea_investment.korea_invest_api_base import KoreaInvestApiBase
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from brokers.korea_investment.korea_invest_params_provider import Params
from brokers.korea_investment.korea_invest_header_provider import KoreaInvestHeaderProvider
from brokers.korea_investment.korea_invest_url_provider import KoreaInvestUrlProvider
from brokers.korea_investment.korea_invest_url_keys import EndpointKey
from brokers.korea_investment.korea_invest_trid_provider import KoreaInvestTrIdProvider
from typing import Optional
from common.types import ResCommonResponse, Exchange


class KoreaInvestApiAccount(KoreaInvestApiBase):
    def __init__(self,
                 env: KoreaInvestApiEnv,
                 logger=None,
                 market_clock=None,
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

    async def get_account_balance(self, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """
        모의투자 또는 실전투자 계좌의 잔고를 조회하는 메서드.
        투자환경(env)에 따라 자동 분기된다.
        exchange=Exchange.NXT 인 경우 시간외단일가 파라미터(AFHR_FLPR_YN)를 "X"로 설정한다.
        """
        full_config = self._env.active_config

        is_paper = self._env.is_paper_trading  # 모의투자 여부 판단
        tr_id = self._trid_provider.account_inquire_balance()  # 모드에 따라 자동

        # ✅ 요청 단위 임시 헤더 주입
        self._headers.set_tr_id(tr_id)
        self._headers.set_custtype(full_config['custtype'])

        full_account_number = full_config['stock_account_number']
        if '-' in full_account_number and len(full_account_number.split('-')[1]) == 2:
            cano, acnt_prdt_cd = full_account_number.split('-')
        else:
            cano = full_account_number
            acnt_prdt_cd = "01"

        # NXT 거래소는 AFHR_FLPR_YN을 "X"로 설정
        afhr_flpr_yn = "X" if exchange == Exchange.NXT else "N"
        params = Params.account_balance(cano=cano, acnt_prdt_cd=acnt_prdt_cd, afhr_flpr_yn=afhr_flpr_yn)

        mode_str = "모의투자" if is_paper else "실전투자"
        self._logger.info(f"{mode_str} 계좌 잔고 조회 시도...")
        return await self.call_api('GET', EndpointKey.INQUIRE_BALANCE, params=params, retry_count=3)

    async def inquire_daily_ccld(
        self,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        side_code: str = "00",
        stock_code: str = "",
        ccld_dvsn: str = "00",
        order_no: str = "",
        exchange: Exchange = Exchange.KRX,
    ) -> ResCommonResponse:
        """주문체결내역을 조회합니다. 활성 주문 polling 보정에 사용합니다."""
        today = datetime.now().strftime("%Y%m%d")
        start_date = start_date or today
        end_date = end_date or today

        full_config = self._env.active_config
        tr_id = self._trid_provider.account_inquire_daily_ccld()
        self._headers.set_tr_id(tr_id)
        self._headers.set_custtype(full_config["custtype"])

        full_account_number = full_config["stock_account_number"]
        if "-" in full_account_number and len(full_account_number.split("-")[1]) == 2:
            cano, acnt_prdt_cd = full_account_number.split("-")
        else:
            cano = full_account_number
            acnt_prdt_cd = "01"

        exchange_code = "NX" if exchange == Exchange.NXT else ""
        params = Params.inquire_daily_ccld(
            cano=cano,
            acnt_prdt_cd=acnt_prdt_cd,
            start_date=start_date,
            end_date=end_date,
            side_code=side_code,
            stock_code=stock_code,
            ccld_dvsn=ccld_dvsn,
            order_no=order_no,
            exchange_code=exchange_code,
        )

        self._logger.info(f"주문체결내역 조회 시도: 종목={stock_code or 'ALL'}, 주문번호={order_no or 'ALL'}")
        return await self.call_api("GET", EndpointKey.INQUIRE_DAILY_CCLD, params=params, retry_count=1)
