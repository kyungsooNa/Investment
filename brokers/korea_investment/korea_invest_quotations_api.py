# brokers/korea_investment/korea_invest_quotations_api.py
from typing import Dict, List, Union
from brokers.korea_investment.korea_invest_api_base import KoreaInvestApiBase
from brokers.korea_investment.korea_invest_token_manager import TokenManager
import requests  # 임시 임포트
# common/types에서 모든 ResTypedDict와 ErrorCode 임포트
from common.types import (
    ResPriceSummary, ResMomentumStock, ResCommonResponse, ErrorCode,
    ResStockFullInfoApiOutput, ResTopMarketCapApiItem, ResDailyChartApiItem,
)


class KoreaInvestApiQuotations(KoreaInvestApiBase):
    def __init__(self, base_url, headers, config, token_manager: TokenManager, logger=None):
        super().__init__(base_url, headers, config, token_manager, logger)

    async def get_stock_info_by_code(self, stock_code: str) -> ResCommonResponse:
        """
        종목코드로 종목의 전체 정보 (이름, 현재가, 시가총액 등)를 가져옵니다.
        ResCommonResponse 형태로 반환하며, data 필드에 ResStockFullInfoApiOutput 포함.
        """
        path = "/uapi/domestic-stock/v1/quotations/search-info"

        self._headers["tr_id"] = self._config['tr_ids']['quotations']['search_info']
        self._headers["custtype"] = self._config['custtype']

        params = {
            "PDNO": stock_code,
            "FID_DIV_CLS_CODE": "2"
        }

        self.logger.info(f"{stock_code} 종목 정보 조회 시도...")
        response = await self.call_api("GET", path, params=params, retry_count=1)

        if response and response.get("rt_cd") == "0" and response.get("output"):
            try:
                stock_info_data = ResStockFullInfoApiOutput(**response["output"])
                return ResCommonResponse(
                    rt_cd=ErrorCode.SUCCESS.value,  # Enum 값 사용
                    msg1="종목 정보 조회 성공",
                    data=stock_info_data
                )
            except TypeError as e:
                error_msg = f"{stock_code} 종목 정보 응답 형식 오류: {e}, 응답: {response['output']}"
                self.logger.error(error_msg)
                return ResCommonResponse(
                    rt_cd=ErrorCode.PARSING_ERROR.value,  # Enum 값 사용
                    msg1=error_msg,
                    data=None
                )
        else:
            error_msg = f"{stock_code} 종목 정보 조회 실패: {response.get('msg1', '알 수 없는 오류')}, 응답: {response}"
            self.logger.warning(error_msg)
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
        path = "/uapi/domestic-stock/v1/quotations/inquire-price"

        full_config = self._config
        self._headers["tr_id"] = full_config['tr_ids']['quotations']['inquire_price']
        self._headers["custtype"] = full_config['custtype']

        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": stock_code
        }
        self.logger.info(f"{stock_code} 현재가 조회 시도...")
        response = await self.call_api('GET', path, params=params, retry_count=3)

        if response is None:
            error_msg = f"[get_current_price] {stock_code} - API 응답 실패 (네트워크 또는 타임아웃)"
            self.logger.warning(error_msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.NETWORK_ERROR.value,  # Enum 값 사용
                msg1=error_msg,
                data=None
            )

        return ResCommonResponse(
            rt_cd=response.get("rt_cd", ErrorCode.UNKNOWN_ERROR.value),  # Enum 값 사용
            msg1=response.get("msg1", "응답 메시지 없음"),
            data=response.get("output")  # 원본 output을 그대로 data 필드에 저장
        )

    async def get_price_summary(self, stock_code: str) -> ResCommonResponse:
        """
        주어진 종목코드에 대해 시가/현재가/등락률(%) 요약 정보 반환
        ResCommonResponse 형태로 반환하며, data 필드에 ResPriceSummary 포함.
        """
        response_common = await self.get_current_price(stock_code)

        if response_common.rt_cd != ErrorCode.SUCCESS.value:
            self.logger.warning(f"({stock_code}) get_current_price 실패: {response_common.msg1}")
            return ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value,
                msg1="get_current_price 실패",
                data=None
            )

        output = response_common.data

        if not output:
            error_msg = f"API 응답 output 데이터 없음: {stock_code}, 응답: {response_common.msg1}"
            self.logger.warning(error_msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value,  # Enum 값 사용
                msg1=error_msg,
                data=None
            )

        # ✅ 필수 키 누락 체크
        required_keys = ["stck_oprc", "stck_prpr", "prdy_ctrt"]
        if not all(k in output for k in required_keys):
            error_msg = f"API 응답 output에 필수 가격 데이터 누락: {stock_code}, 응답: {output}"
            self.logger.warning(error_msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.PARSING_ERROR.value,
                msg1=error_msg,
                data=None
            )

        try:
            open_price = int(output.get("stck_oprc", 0))
            current_price = int(output.get("stck_prpr", 0))
            prdy_ctrt = float(output.get("prdy_ctrt", 0.0))
        except (ValueError, TypeError) as e:
            error_msg = f"가격 데이터 파싱 실패: {stock_code}, 응답: {output}, 오류: {e}"
            self.logger.warning(error_msg)
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
            self.logger.warning(error_msg)
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
            self.logger.warning(error_msg)
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
        if count <= 0:
            error_msg = f"요청된 count가 0 이하입니다. count={count}"
            self.logger.warning(error_msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value,  # Enum 값 사용
                msg1=error_msg,
                data=[]
            )

        if count > 30:
            self.logger.warning(f"요청 수 {count}는 최대 허용값 30을 초과하므로 30개로 제한됩니다.")
            count = 30

        self._headers["tr_id"] = self._config['tr_ids']['quotations']['top_market_cap']
        self._headers["custtype"] = self._config['custtype']

        path = "/uapi/domestic-stock/v1/ranking/market-cap"
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20174",
            "fid_div_cls_code": "0",
            "fid_input_iscd": market_code,
            "fid_trgt_cls_code": "20",
            "fid_trgt_exls_cls_code": "20",
            "fid_input_price_1": "",
            "fid_input_price_2": "",
            "fid_vol_cnt": ""
        }

        self.logger.info(f"시가총액 상위 종목 조회 시도 (시장코드: {market_code}, 요청개수: {count})")
        response = await self.call_api("GET", path, params=params, retry_count=1)

        if not response or response.get("rt_cd") != "0" or not response.get("output"):
            error_msg = response.get("msg1", "시가총액 조회 실패") if isinstance(response, dict) else "API 호출 실패"
            self.logger.warning(f"시가총액 응답 오류 또는 비어 있음: {error_msg}")
            return ResCommonResponse(
                rt_cd=response.get("rt_cd", ErrorCode.API_ERROR.value) if isinstance(response,
                                                                                     dict) else ErrorCode.API_ERROR.value,
                # Enum 값 사용
                msg1=error_msg,
                data=[]
            )

        batch = response["output"][:count]
        self.logger.info(f"API로부터 수신한 종목 수: {len(batch)}")

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
                self.logger.warning(f"시가총액 상위 종목 개별 항목 파싱 오류: {e}, 항목: {item}")
                continue

        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,  # Enum 값 사용
            msg1="시가총액 상위 종목 조회 성공",
            data=results
        )

    def get_previous_day_info(self, code: str) -> ResCommonResponse:
        """
        종목의 전일 종가, 전일 거래량 조회
        ResCommonResponse 형태로 반환하며, data 필드에 Dict[str, Union[float, int]] 포함.
        """
        params = {
            "fid_cond_mrkt_div_code": "J",  # 주식시장
            "fid_input_iscd": code,
        }
        try:
            headers_sync = self._headers.copy()
            headers_sync["tr_id"] = self._config['tr_ids']['daily_itemchartprice_day']

            response_raw = requests.get(
                self._base_url + "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                headers=headers_sync,
                params=params
            )

            data = response_raw.json()

            if not data or "output" not in data:
                error_msg = f"{code} 종목 전일 정보 응답에 'output' 데이터가 없거나 비어 있습니다. 응답: {data}"
                self.logger.error(error_msg)
                return ResCommonResponse(
                    rt_cd=ErrorCode.API_ERROR.value,
                    msg1=error_msg,
                    data={"prev_close": 0, "prev_volume": 0}
                )

            output_raw = data.get("output", {})

            try:
                output_data = ResDailyChartApiItem(**output_raw)
            except TypeError as e:
                error_msg = f"{code} 종목 전일 정보 응답 파싱 실패 (필드 누락 등): {e}, 응답: {output_raw}"
                self.logger.error(error_msg)
                return ResCommonResponse(
                    rt_cd=ErrorCode.MISSING_KEY.value,
                    msg1=error_msg,
                    data={"prev_close": 0, "prev_volume": 0}
                )

            try:
                prev_close = float(output_data.stck_clpr)
                prev_volume = int(output_data.acml_vol)
            except (ValueError, TypeError) as e:
                error_msg = f"{code} 종목 전일 정보 데이터 변환 실패: {e}, 응답: {output_data}"
                self.logger.error(error_msg)
                return ResCommonResponse(
                    rt_cd=ErrorCode.PARSING_ERROR.value,
                    msg1=error_msg,
                    data={"prev_close": 0, "prev_volume": 0}
                )

            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="전일 정보 조회 성공",
                data={
                    "prev_close": prev_close,
                    "prev_volume": prev_volume
                }
            )

        except requests.exceptions.RequestException as e:
            error_msg = f"{code} 종목 전일 정보 네트워크 오류: {e}"
            self.logger.error(error_msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.NETWORK_ERROR.value,
                msg1=error_msg,
                data={"prev_close": 0, "prev_volume": 0}
            )

    async def get_filtered_stocks_by_momentum(
            self, count=20, min_change_rate=10.0, min_volume_ratio=2.0
    ) -> ResCommonResponse:
        """
        거래량 급증 + 등락률 조건 기반 모멘텀 종목 필터링
        ResCommonResponse 형태로 반환하며, data 필드에 List[ResMomentumStock] 포함.
        """
        top_stocks_response_common = await self.get_top_market_cap_stocks_code('0000')

        if top_stocks_response_common.rt_cd != ErrorCode.SUCCESS.value:  # Enum 값 사용
            self.logger.error(f"시가총액 상위 종목 조회 실패: {top_stocks_response_common.msg1}")
            return top_stocks_response_common

        top_stocks_list: List[ResTopMarketCapApiItem] = top_stocks_response_common.data
        if not top_stocks_list:
            self.logger.warning("시가총액 상위 종목 목록이 비어있습니다.")
            return ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value,  # Enum 값 사용
                msg1="시가총액 상위 종목 목록 없음",
                data=[]
            )

        filtered: List[ResMomentumStock] = []
        for item in top_stocks_list[:count]:
            symbol = item.mksc_shrn_iscd.replace("A", "")
            if not symbol:
                self.logger.warning(f"유효하지 않은 종목 코드: {item}")
                continue

            prev_info_common = self.get_previous_day_info(symbol)

            if prev_info_common.rt_cd != ErrorCode.SUCCESS.value:  # Enum 값 사용
                self.logger.warning(f"{symbol} 종목 전일 정보 조회 실패: {prev_info_common.msg1}. 필터링에서 제외합니다.")
                continue

            prev_info_data: Dict[str, Union[float, int]] = prev_info_common.data
            prev_volume = prev_info_data.get("prev_volume", 0)

            if prev_volume <= 0:
                self.logger.warning(f"{symbol} 종목 전일 거래량 정보가 없거나 유효하지 않아 필터링에서 제외합니다.")
                continue  #

            summary_common = await self.get_price_summary(symbol)
            if summary_common.rt_cd != ErrorCode.SUCCESS.value or summary_common.data is None:  # Enum 값 사용
                self.logger.warning(f"{symbol} 종목 현재가 요약 정보를 가져오지 못했습니다. 오류: {summary_common.msg1}. 필터링 제외.")
                continue  #

            summary: ResPriceSummary = summary_common.data

            change_rate = summary.change_rate
            current_volume = int(item.acc_trdvol)

            if (
                    change_rate >= min_change_rate
                    and prev_volume > 0
                    and current_volume / prev_volume >= min_volume_ratio
            ):
                filtered.append(ResMomentumStock(
                    symbol=symbol,
                    change_rate=change_rate,
                    prev_volume=prev_volume,
                    current_volume=current_volume
                ))

        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,  # Enum 값 사용
            msg1="모멘텀 종목 필터링 성공",
            data=filtered
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
        valid_period_codes = {"D", "M"}
        if fid_period_div_code not in valid_period_codes:
            error_msg = f"지원하지 않는 fid_period_div_code: {fid_period_div_code}"
            self.logger.error(error_msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value,
                msg1=error_msg,
                data=[]
            )

        selected_tr_id = None
        if fid_period_div_code == 'D':
            selected_tr_id = self._config.get('tr_ids', {}).get('daily_itemchartprice_day')
        elif fid_period_div_code == 'M':
            self.logger.debug(f"현재 _config['tr_ids'] 내용: {self._config.get('tr_ids')}")
            selected_tr_id = self._config['tr_ids']['daily_itemchartprice_minute']

        if not selected_tr_id:
            error_msg = f"TR_ID 설정을 찾을 수 없습니다. fid_period_div_code: {fid_period_div_code}"
            self.logger.critical(error_msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value,  # Enum 값 사용
                msg1=error_msg,
                data=[]
            )

        headers = self._headers.copy()
        headers["tr_id"] = selected_tr_id

        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": stock_code,
            "fid_input_date_1": date,
            "fid_input_date_2": date,
            "fid_period_div_code": fid_period_div_code,
            "fid_org_adj_prc": fid_org_adj_prc
        }

        response_data = await self.call_api(method="GET",
                                            path="/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                                            params=params, data=None)

        if not response_data:
            error_msg = f"API 응답 비정상: None, 응답: {response_data}"
            self.logger.error(error_msg)
            return ResCommonResponse(
                rt_cd=ErrorCode.API_ERROR.value,
                msg1=error_msg,
                data=[]
            )

        if response_data.get('rt_cd') != '0':
            error_msg = f"API 응답 비정상: {response_data.get('msg1', '알 수 없는 오류')}, 응답: {response_data}"
            self.logger.error(error_msg)
            return ResCommonResponse(
                rt_cd=response_data.get('rt_cd', ErrorCode.API_ERROR.value),
                msg1=error_msg,
                data=[]
            )

        output_list = response_data.get('output', [])
        chart_data_items: List[ResDailyChartApiItem] = []
        for item in output_list:
            try:
                chart_data_items.append(ResDailyChartApiItem(**item))
            except TypeError as e:
                self.logger.warning(f"차트 데이터 항목 파싱 오류: {e}, 항목: {item}")
                continue

        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,  # Enum 값 사용
            msg1="일별/분봉 차트 데이터 조회 성공",
            data=chart_data_items
        )
