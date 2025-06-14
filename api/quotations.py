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
        self._headers["tr_id"] = "FHKST01010100"
        self._headers["custtype"] = self._config['custtype']

        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": stock_code
        }
        print(f"INFO: {stock_code} 현재가 조회 시도...")
        return self._call_api('GET', path, params=params)

    def get_top_market_cap_stocks(self, market_code="0000"):
        """
        국내주식 시가총액 상위 종목 목록을 조회하는 메서드.
        이 API는 모의투자를 지원하지 않습니다. (실전 환경에서만 사용 가능)

        :param market_code: 시장 구분 코드 (0000: 전체, 0001: 거래소, 1001: 코스닥, 2001: 코스피200)
        :return: 시가총액 상위 종목 목록 (최대 30건)
        """
        path = "/uapi/domestic-stock/v1/ranking/market-cap"

        # TR_ID는 '국내주식 시가총액 상위' API용으로 설정
        self._headers["tr_id"] = "FHPST01740000"
        self._headers["custtype"] = self._config['custtype']

        params = {
            "fid_input_price_2": "",  # 입력 가격2 (전체)
            "fid_cond_mrkt_div_code": "J",  # 조건 시장 분류 코드 (주식 J)
            "fid_cond_scr_div_code": "20174",  # 조건 화면 분류 코드 (문서에 명시된 고정값)
            "fid_div_cls_code": "0",  # 분류 구분 코드 (0: 전체, 1:보통주, 2:우선주)
            "fid_input_iscd": market_code,  # 입력 종목코드 (시장 구분)
            "fid_trgt_cls_code": "20",  # 대상 구분 코드 (전체)
            "fid_trgt_exls_cls_code": "20",  # 대상 제외 구분 코드 (전체)
            "fid_input_price_1": "",  # 입력 가격1 (전체)
            "fid_vol_cnt": ""  # 거래량 수 (전체)
        }
        print(f"INFO: 시가총액 상위 {market_code} 종목 조회 시도...")
        response = self._call_api('GET', path, params=params)

        if response and response.get('rt_cd') == '0' and response.get('output'):
            print(f"INFO: 시가총액 상위 종목 조회 성공.")
            # 응답의 'output' 필드가 리스트 형태로 여러 종목 정보를 담고 있음
            return response
        else:
            print(f"ERROR: 시가총액 상위 종목 조회 실패 또는 정보 없음: {response}")
            return None
