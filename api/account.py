# api/account.py
from api.base import _KoreaInvestAPIBase


class KoreaInvestAccountAPI(_KoreaInvestAPIBase):
    def __init__(self, base_url, headers, config, logger):  # <--- 인자 변경
        super().__init__(base_url, headers, config, logger)  # <--- 부모 클래스에 전달

    def get_account_balance(self):
        path = "/uapi/domestic-stock/v1/trading/inquire-balance"

        self._headers["tr_id"] = self._config['tr_ids']['account']['inquire_balance_paper']
        self._headers["custtype"] = self._config['custtype']

        cano = self._config['stock_account_number']
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
        return self._call_api('GET', path, params=params)

    def get_real_account_balance(self):
        path = "/uapi/domestic-stock/v1/trading/inquire-balance"

        self._headers["tr_id"] = self._config['tr_ids']['account']['inquire_balance_real']
        self._headers["custtype"] = self._config['custtype']

        full_account_number = self._config['stock_account_number']

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
        return self._call_api('GET', path, params=params)
