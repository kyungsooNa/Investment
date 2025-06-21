# api/quotations.py
from api.base import _KoreaInvestAPIBase


class KoreaInvestQuotationsAPI(_KoreaInvestAPIBase):
    def __init__(self, base_url, headers, config, logger):
        super().__init__(base_url, headers, config, logger)

    async def get_current_price(self, stock_code):
        path = "/uapi/domestic-stock/v1/quotations/inquire-price"

        full_config = self._config
        self._headers["tr_id"] = full_config['tr_ids']['quotations']['inquire_price']
        self._headers["custtype"] = full_config['custtype']

        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": stock_code
        }
        self.logger.info(f"{stock_code} 현재가 조회 시도...")
        return await self._call_api('GET', path, params=params, retry_count=1)  # <--- retry_count 추가

    async def get_market_cap(self, stock_code):
        path = "/uapi/domestic-stock/v1/quotations/search-info"

        full_config = self._config
        self._headers["tr_id"] = full_config['tr_ids']['quotations']['search_info']
        self._headers["custtype"] = full_config['custtype']

        params = {
            "PDNO": stock_code,
            "FID_DIV_CLS_CODE": "2"
        }
        self.logger.info(f"{stock_code} 시가총액 조회 시도...")
        response = await self._call_api('GET', path, params=params, retry_count=1)  # <--- retry_count 추가

        if response and response.get('rt_cd') == '0' and response.get('output'):
            market_cap = response['output'].get('stck_prpr_smkl_amt')
            if market_cap:
                self.logger.info(f"{stock_code} 시가총액: {market_cap}")
            return response
        else:
            self.logger.error(f"{stock_code} 시가총액 조회 실패 또는 정보 없음: {response}")
            return None

    async def get_top_market_cap_stocks(self, market_code="0000"):
        path = "/uapi/domestic-stock/v1/ranking/market-cap"

        full_config = self._config
        self._headers["tr_id"] = full_config['tr_ids']['quotations']['top_market_cap']
        self._headers["custtype"] = full_config['custtype']

        params = {
            "fid_input_price_2": "",
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20174",
            "fid_div_cls_code": "0",
            "fid_input_iscd": market_code,
            "fid_trgt_cls_code": "20",
            "fid_trgt_exls_cls_code": "20",
            "fid_input_price_1": "",
            "fid_vol_cnt": ""
        }
        self.logger.info(f"시가총액 상위 {market_code} 종목 조회 시도...")
        response = await self._call_api('GET', path, params=params, retry_count=1)  # <--- retry_count 추가

        if response and response.get('rt_cd') == '0' and response.get('output'):
            self.logger.info(f"시가총액 상위 종목 조회 성공.")
            return response
        else:
            self.logger.error(f"시가총액 상위 종목 조회 실패 또는 정보 없음: {response}")
            return None
