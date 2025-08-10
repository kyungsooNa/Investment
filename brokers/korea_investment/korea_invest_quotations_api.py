# brokers/korea_investment/korea_invest_quotations_api.py
import httpx
from typing import Dict, List, Union, Optional
from brokers.korea_investment.korea_invest_api_base import KoreaInvestApiBase
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from brokers.korea_investment.korea_invest_params_provider import Params
# common/types에서 모든 ResTypedDict와 ErrorCode 임포트
from common.types import (
    ResPriceSummary, ResMomentumStock, ResCommonResponse, ErrorCode,
    ResStockFullInfoApiOutput, ResTopMarketCapApiItem, ResDailyChartApiItem, ResFluctuation,
)


class KoreaInvestApiQuotations(KoreaInvestApiBase):
    def __init__(self, env: KoreaInvestApiEnv, logger=None,
                 async_client: Optional[httpx.AsyncClient] = None):
        super().__init__(env, logger, async_client=async_client)

    async def get_stock_info_by_code(self, stock_code: str) -> ResCommonResponse:
        """
        종목코드로 종목의 전체 정보 (이름, 현재가, 시가총액 등)를 가져옵니다.
        ResCommonResponse 형태로 반환하며, data 필드에 ResStockFullInfoApiOutput 포함.
        """
        full_config = self._env.active_config

        path = full_config["paths"]["search_info"]

        self._headers["tr_id"] = full_config['tr_ids']['quotations']['search_info']
        self._headers["custtype"] = full_config['custtype']

        params = {
            "PDNO": stock_code,
            "FID_DIV_CLS_CODE": full_config["params"]["fid_div_cls_code"]
        }

        self._logger.info(f"{stock_code} 종목 정보 조회 시도...")
        response: ResCommonResponse = await self.call_api("GET", path, params=params, retry_count=1)

        if response.rt_cd == ErrorCode.SUCCESS.value:
            try:
                stock_info_data = ResStockFullInfoApiOutput(**response.data)
                return ResCommonResponse(
                    rt_cd=ErrorCode.SUCCESS.value,  # Enum 값 사용
                    msg1="종목 정보 조회 성공",
                    data=stock_info_data
                )
            except TypeError as e:
                error_msg = f"{stock_code} 종목 정보 응답 형식 오류: {e}, 응답: {response.data}"
                self._logger.error(error_msg)
                return ResCommonResponse(
                    rt_cd=ErrorCode.PARSING_ERROR.value,  # Enum 값 사용
                    msg1=error_msg,
                    data=None
                )
        else:
            error_msg = f"{stock_code} 종목 정보 조회 실패: {response.msg1 or '알 수 없는 오류'}, 응답: {response}"
            self._logger.warning(error_msg)
            return ResCommonResponse(
                rt_cd=response.get("rt_cd", ErrorCode.API_ERROR.value) if isinstance(response,
                                                                                     dict) else ErrorCode.API_ERROR.value,
                msg1=error_msg,
                data=None
            )

    async def get_current_price(self, stock_code) -> ResCommonResponse:
        """
        현재가를 조회합니다. API 원본 응답을 ResCommonResponse의 data 필드에 담아 반환.
        """
        full_config = self._env.active_config

        path = full_config["paths"]["inquire_price"]

        self._headers["tr_id"] = full_config['tr_ids']['quotations']['inquire_price']
        self._headers["custtype"] = full_config['custtype']

        params = Params.inquire_price(stock_code=stock_code)
        self._logger.info(f"{stock_code} 현재가 조회 시도...")

        response: ResCommonResponse = await self.call_api("GET", path, params=params, retry_count=3)

        if response.rt_cd != ErrorCode.SUCCESS.value:
            self._logger.warning("현재가 조회 실패")
            return response
        response_data_dict = response.data['output']
        response.data['output'] = ResStockFullInfoApiOutput.from_dict(response_data_dict)
        return response

    async def get_price_summary(self, stock_code: str) -> ResCommonResponse:
        """
        주어진 종목코드에 대해 시가/현재가/등락률(%) 요약 정보 반환
        ResCommonResponse 형태로 반환하며, data 필드에 ResPriceSummary 포함.
        """
        response_common = await self.get_current_price(stock_code)

        if response_common.rt_cd != ErrorCode.SUCCESS.value:
            self._logger.warning(f"({stock_code}) get_current_price 실패: {response_common.msg1}")
            return ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value,
                msg1="get_current_price 실패",
                data=None
            )

        output: ResStockFullInfoApiOutput = response_common.data['output']

        if not output:
            error_msg = f"API 응답 output 데이터 없음: {stock_code}, 응답: {response_common.msg1}"
            self._logger.warning(error_msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value,  # Enum 값 사용
                msg1=error_msg,
                data=None
            )
        if not isinstance(output, ResStockFullInfoApiOutput):
            error_msg = f"Wrong Ret Type ResStockFullInfoApiOutput - Ret: {type(output)}, 응답: {response_common.msg1}"
            self._logger.warning(error_msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.WRONG_RET_TYPE.value,  # Enum 값 사용
                msg1=error_msg,
                data=None
            )

        # ✅ 필수 키 누락 체크
        required_keys = ["stck_oprc", "stck_prpr", "prdy_ctrt"]
        if not all(hasattr(output, k) and getattr(output, k) is not None for k in required_keys):
            error_msg = f"API 응답 output에 필수 가격 데이터 누락: {stock_code}, 응답: {output}"
            self._logger.warning(error_msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.PARSING_ERROR.value,
                msg1=error_msg,
                data=None
            )

        try:
            open_price = int(output.stck_oprc)
            current_price = int(output.stck_prpr)
            prdy_ctrt = float(output.prdy_ctrt)
        except (ValueError, TypeError) as e:
            error_msg = f"가격 데이터 파싱 실패: {stock_code}, 응답: {output}, 오류: {e}"
            self._logger.warning(error_msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.PARSING_ERROR.value,  # Enum 값 사용
                msg1=error_msg,
                data=None
            )

        change_rate = (current_price - open_price) / open_price * 100 if open_price else 0

        price_summary_data = ResPriceSummary(
            symbol=stock_code,
            open=open_price,
            current=current_price,
            change_rate=round(change_rate, 2),
            prdy_ctrt=prdy_ctrt
        )
        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,  # Enum 값 사용
            msg1="정상 처리되었습니다.",
            data=price_summary_data
        )

    async def get_market_cap(self, stock_code: str) -> ResCommonResponse:
        """
        종목코드로 시가총액을 반환합니다. (단위: 원)
        ResCommonResponse 형태로 반환하며, data 필드에 int 시가총액 값 포함.
        """
        response_common = await self.get_stock_info_by_code(stock_code)

        if response_common.rt_cd != ErrorCode.SUCCESS.value:  # Enum 값 사용
            return response_common

        info: ResStockFullInfoApiOutput = response_common.data

        if info is None:
            error_msg = f"{stock_code} 시가총액 정보 조회 실패: ResStockFullInfoApiOutput 데이터가 None"
            self._logger.warning(error_msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.PARSING_ERROR.value,  # Enum 값 사용
                msg1=error_msg,
                data=None
            )

        market_cap_str = info.stck_llam
        if market_cap_str and market_cap_str.isdigit():
            market_cap = int(market_cap_str)
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,  # Enum 값 사용
                msg1="시가총액 조회 성공",
                data=market_cap
            )
        else:
            error_msg = f"{stock_code} 시가총액 정보 없음 또는 형식 오류: {market_cap_str}"
            self._logger.warning(error_msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.PARSING_ERROR.value,  # Enum 값 사용
                msg1=error_msg,
                data=0
            )

    async def get_top_market_cap_stocks_code(self, market_code: str, count: int = 30) -> ResCommonResponse:
        """
        시가총액 상위 종목 목록을 반환합니다. 최대 30개까지만 지원됩니다.
        ResCommonResponse 형태로 반환하며, data 필드에 List[ResTopMarketCapApiItem] 포함.
        """
        full_config = self._env.active_config

        if count <= 0:
            error_msg = f"요청된 count가 0 이하입니다. count={count}"
            self._logger.warning(error_msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value,  # Enum 값 사용
                msg1=error_msg,
                data=[]
            )

        if count > 30:
            self._logger.warning(f"요청 수 {count}는 최대 허용값 30을 초과하므로 30개로 제한됩니다.")
            count = 30

        self._headers["tr_id"] = full_config['tr_ids']['quotations']['top_market_cap']
        self._headers["custtype"] = full_config['custtype']

        path = full_config["paths"]["market_cap"]

        params = Params.top_market_cap()

        self._logger.info(f"시가총액 상위 종목 조회 시도 (시장코드: {market_code}, 요청개수: {count})")
        response: ResCommonResponse = await self.call_api("GET", path, params=params, retry_count=1)

        if response.rt_cd != ErrorCode.SUCCESS.value:
            self._logger.warning(f"시가총액 응답 오류 또는 비어 있음: 시가총액 조회 실패")
            return ResCommonResponse(
                rt_cd=response.get("rt_cd", ErrorCode.API_ERROR.value) if isinstance(response,
                                                                                     dict) else ErrorCode.API_ERROR.value,
                # Enum 값 사용
                msg1="시가총액 조회 실패",
                data=[]
            )

        batch = response.data.get('output', '')[:count]
        self._logger.info(f"API로부터 수신한 종목 수: {len(batch)}")

        results = []
        for item in batch:
            try:
                code = item.get("iscd") or item.get("mksc_shrn_iscd")
                raw_market_cap = item.get("stck_avls")

                if not code or not raw_market_cap:
                    continue  #

                market_cap = int(raw_market_cap.replace(",", "")) if raw_market_cap.replace(",", "").isdigit() else 0

                results.append(ResTopMarketCapApiItem(
                    iscd=item.get("iscd", ""),
                    mksc_shrn_iscd=code,
                    stck_avls=raw_market_cap,
                    data_rank=item.get("data_rank", ""),
                    hts_kor_isnm=item.get("hts_kor_isnm", ""),
                    acc_trdvol=item.get("acc_trdvol", "0")
                ))
            except (ValueError, TypeError, KeyError) as e:
                self._logger.warning(f"시가총액 상위 종목 개별 항목 파싱 오류: {e}, 항목: {item}")
                continue

        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,  # Enum 값 사용
            msg1="시가총액 상위 종목 조회 성공",
            data=results
        )

    async def inquire_daily_itemchartprice(self, stock_code: str, date: str, fid_input_iscd: str = '00',
                                           fid_input_date_1: str = '', fid_input_date_2: str = '',
                                           fid_period_div_code: str = 'D',
                                           fid_org_adj_prc: str = '0') -> ResCommonResponse:
        """
        일별/주별/월별/분별/틱별 주식 시세 차트 데이터를 조회합니다.
        TRID: FHKST03010100 (일별), FHNKF03060000 (분봉)
        ResCommonResponse 형태로 반환하며, data 필드에 List[ResDailyChartApiItem] 포함.
        """
        full_config = self._env.active_config

        valid_period_codes = {"D", "M"}
        if fid_period_div_code not in valid_period_codes:
            error_msg = f"지원하지 않는 fid_period_div_code: {fid_period_div_code}"
            self._logger.error(error_msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value,
                msg1=error_msg,
                data=[]
            )

        selected_tr_id = None
        if fid_period_div_code == 'D':
            selected_tr_id = full_config['tr_ids']['quotations']['daily_itemchartprice_day']
        elif fid_period_div_code == 'M':
            self._logger.debug(f"현재 _config['tr_ids'] 내용: {full_config.get('tr_ids')}")
            selected_tr_id = full_config['tr_ids']['quotations']['daily_itemchartprice_minute']

        if not selected_tr_id:
            error_msg = f"TR_ID 설정을 찾을 수 없습니다. fid_period_div_code: {fid_period_div_code}"
            self._logger.critical(error_msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value,  # Enum 값 사용
                msg1=error_msg,
                data=[]
            )

        headers = self._headers.copy()
        headers["tr_id"] = selected_tr_id

        params = Params.daily_itemchartprice_day(stock_code=stock_code, date=date)
        # params = {
        #     "fid_cond_mrkt_div_code": "J",
        #     "fid_input_iscd": stock_code,
        #     "fid_input_date_1": date,
        #     "fid_input_date_2": date,
        #     "fid_period_div_code": fid_period_div_code,
        #     "fid_org_adj_prc": fid_org_adj_prc
        # }

        response_data: ResCommonResponse = await self.call_api(method="GET",
                                                               path=full_config["paths"][
                                                                   "inquire_daily_itemchartprice"],
                                                               params=params, data=None)

        if response_data.rt_cd != ErrorCode.SUCCESS.value:
            error_msg = f"API 응답 비정상: None, 응답: {response_data.data}"
            self._logger.error(error_msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value,
                msg1=error_msg,
                data=[]
            )

        if not response_data.data:  # None 또는 빈 리스트
            warning_msg = f"일별 시세 차트 데이터가 비어있음 (stock_code: {stock_code})"
            self._logger.warning(warning_msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.MISSING_KEY.value,
                msg1=warning_msg,
                data=[]
            )

        output_list = response_data.data
        chart_data_items: List[ResDailyChartApiItem] = []
        for item in output_list:
            try:
                chart_data_items.append(ResDailyChartApiItem(**item))
            except TypeError as e:
                self._logger.warning(f"차트 데이터 항목 파싱 오류: {e}, 항목: {item}")
                continue

        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,  # Enum 값 사용
            msg1="일별/분봉 차트 데이터 조회 성공",
            data=chart_data_items
        )

    async def get_asking_price(self, stock_code: str) -> ResCommonResponse:
        """
        종목의 실시간 호가(매도/매수 잔량 포함) 정보를 조회합니다.
        ResCommonResponse 형태로 반환되며, data는 원시 output 딕셔너리입니다.
        """
        full_config = self._env.active_config

        path = full_config["paths"]["asking_price"]
        tr_id = full_config["tr_ids"]["quotations"]["asking_price"]

        self._headers["tr_id"] = tr_id
        self._headers["custtype"] = full_config["custtype"]

        params = Params.asking_price(stock_code=stock_code)

        self._logger.info(f"{stock_code} 종목 호가잔량 조회 시도...")

        response: ResCommonResponse = await self.call_api("GET", path, params=params, retry_count=1)

        if response.rt_cd != ErrorCode.SUCCESS.value:
            self._logger.warning(f"{stock_code} 호가 정보 조회 실패: {response.msg1}")
            return response

        return response

    async def get_time_concluded_prices(self, stock_code: str) -> ResCommonResponse:
        """
        종목의 시간대별 체결가/체결량 정보를 조회합니다.
        """
        full_config = self._env.active_config

        path = full_config["paths"]["time_conclude"]
        tr_id = full_config["tr_ids"]["quotations"]["time_conclude"]

        self._headers["tr_id"] = tr_id
        self._headers["custtype"] = full_config["custtype"]

        params = Params.time_conclude(stock_code=stock_code)

        self._logger.info(f"{stock_code} 종목 체결가 조회 시도...")
        response: ResCommonResponse = await self.call_api("GET", path, params=params, retry_count=1)

        if response.rt_cd != ErrorCode.SUCCESS.value:
            self._logger.warning(f"{stock_code} 체결가 정보 조회 실패: {response.msg1}")
            return response

        return response

    async def get_top_rise_fall_stocks(self, rise: bool = True) -> ResCommonResponse:
        """
        상승률/하락률 상위 종목 조회
        """
        full_config = self._env.active_config

        path = full_config["paths"]['ranking_fluctuation']
        tr_id = full_config["tr_ids"]["quotations"]['ranking_fluctuation']

        self._headers["tr_id"] = tr_id
        self._headers["custtype"] = full_config["custtype"]

        params = (
            Params.fluctuation_rise()  # 상승률 상위
            if rise
            else Params.fluctuation_fall()  # 하락률 상위
        )

        direction = "상승" if rise else "하락"
        self._logger.info(f"{direction}률 상위 종목 조회 시도...")
        response = await self.call_api("GET", path, params=params, retry_count=1)

        try:
            stocks = [ResFluctuation.from_dict(row) for row in response.data.get("output", [])]
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,  # Enum 값 사용
                msg1="종목 정보 조회 성공",
                data=stocks
            )
        except TypeError as e:
            error_msg = f"등락률 응답 형식 오류: {e}, 응답: {response.data}"
            self._logger.error(error_msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.PARSING_ERROR.value,  # Enum 값 사용
                msg1=error_msg,
                data=None
            )

    async def get_top_volume_stocks(self) -> ResCommonResponse:
        """
        거래량 상위 종목 조회
        """
        full_config = self._env.active_config

        path = full_config["paths"]["ranking_volume"]
        tr_id = full_config["tr_ids"]["quotations"]["ranking_volume"]

        self._headers["tr_id"] = tr_id
        self._headers["custtype"] = full_config["custtype"]

        params = Params.top_market_cap()

        self._logger.info(f"거래량 상위 종목 조회 시도...")
        response = await self.call_api("GET", path, params=params, retry_count=1)

        if response.rt_cd != ErrorCode.SUCCESS.value:
            self._logger.warning(f"거래량 상위 조회 실패: {response.msg1}")
            return response

        return response
    #
    # async def get_top_foreign_buying_stocks(self) -> ResCommonResponse:
    #     """
    #     외국인 순매수 상위 종목 조회
    #     """
    #     full_config = self._env.active_config
    #
    #     path = full_config["paths"]["ranking_foreign"]
    #     tr_id = full_config["tr_ids"]["quotations"]["ranking_foreign"]
    #     market_code = full_config.get("market_code", "J")
    #
    #     self._headers["tr_id"] = tr_id
    #     self._headers["custtype"] = full_config["custtype"]
    #
    #     params = {
    #         "fid_cond_mrkt_div_code": market_code
    #     }
    #
    #     self._logger.info("외국인 순매수 상위 종목 조회 시도...")
    #     response = await self.call_api("GET", path, params=params, retry_count=1)
    #
    #     if response.rt_cd != ErrorCode.SUCCESS.value:
    #         self._logger.warning(f"외국인 순매수 조회 실패: {response.msg1}")
    #         return response
    #
    #     return response

    # async def get_stock_news(self, stock_code: str) -> ResCommonResponse:
    #     """
    #     종목 뉴스 조회
    #     """
    #     full_config = self._env.active_config
    #
    #     path = full_config["paths"]["item_news"]
    #     tr_id = full_config["tr_ids"]["quotations"]["item_news"]
    #
    #     self._headers["tr_id"] = tr_id
    #     self._headers["custtype"] = full_config["custtype"]
    #
    #     params = {
    #         "fid_input_iscd": stock_code
    #     }
    #
    #     self._logger.info(f"{stock_code} 종목 뉴스 조회 시도...")
    #     response = await self.call_api("GET", path, params=params, retry_count=1)
    #
    #     if response.rt_cd != ErrorCode.SUCCESS.value:
    #         self._logger.warning(f"{stock_code} 종목 뉴스 조회 실패: {response.msg1}")
    #         return response
    #
    #     return response

    async def get_etf_info(self, etf_code: str) -> ResCommonResponse:
        """
        ETF 정보 조회
        """
        full_config = self._env.active_config

        path = full_config["paths"]["etf_info"]
        tr_id = full_config["tr_ids"]["quotations"]["etf_info"]

        self._headers["tr_id"] = tr_id
        self._headers["custtype"] = full_config["custtype"]

        params = Params.etf_info(etf_code=etf_code)

        self._logger.info(f"{etf_code} ETF 정보 조회 시도...")
        response = await self.call_api("GET", path, params=params, retry_count=1)

        if response.rt_cd != ErrorCode.SUCCESS.value:
            self._logger.warning(f"{etf_code} ETF 조회 실패: {response.msg1}")
            return response

        return response
