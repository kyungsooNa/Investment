# api/quotations.py
from api.base import _KoreaInvestAPIBase


class KoreaInvestQuotationsAPI(_KoreaInvestAPIBase):
    """
    한국투자증권 Open API의 시세 관련 기능을 담당하는 클래스입니다.
    """

    def get_current_price(self, stock_code):
        """
        주식 현재가를 조회하는 메서드.
        """
        path = "/uapi/domestic-stock/v1/quotations/inquire-price"

        # 각 API 호출 전에 필요한 TR_ID를 헤더에 업데이트
        self._headers["tr_id"] = "FHKST01010100"
        self._headers["custtype"] = self._config['custtype']

        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": stock_code
        }
        print(f"INFO: {stock_code} 현재가 조회 시도...")
        return self._call_api('GET', path, params=params)