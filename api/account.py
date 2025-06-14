# api/account.py
from api.base import _KoreaInvestAPIBase

class KoreaInvestAccountAPI(_KoreaInvestAPIBase):
    """
    한국투자증권 Open API의 계좌 관련 기능을 담당하는 클래스입니다.
    """
    def get_account_balance(self):
        """
        계좌 잔고를 조회하는 메서드.
        """
        path = "/uapi/domestic-stock/v1/trading/inquire-balance"
        self._headers["tr_id"] = "VTTC8434R" # 모의투자 잔고 조회 TR_ID
        self._headers["custtype"] = self._config['custtype']

        cano = self._config['stock_account_number']
        acnt_prdt_div_code = "01" # 8자리 계좌번호에 대한 기본값

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
        print(f"INFO: 계좌 잔고 조회 시도...")
        return self._call_api('GET', path, params=params)

    def get_real_account_balance(self):
        """
        실전 계좌 잔고를 조회하는 메서드.
        """
        if self._config['is_paper_trading']:
            print("WARNING: 현재 모의투자 환경입니다. 실전 계좌 잔고 조회는 실전 환경에서만 가능합니다.")
            return None

        path = "/uapi/domestic-stock/v1/trading/inquire-balance"
        self._headers["tr_id"] = "TTTC8434R" # <--- 실전용 TR ID
        self._headers["custtype"] = self._config['custtype']

        cano = self._config['stock_account_number']
        acnt_prdt_div_code = "01" # 실전 계좌번호의 끝 2자리 또는 고정값 (config.yaml과 일치해야 함)

        # ... (params는 get_account_balance와 동일하게 설정) ...
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
        print(f"INFO: 실전 계좌 잔고 조회 시도...")
        return self._call_api('GET', path, params=params)
