# api/kr_quotations.py
from api.invest_api_base import _KoreaInvestAPIBase


class Quotations(_KoreaInvestAPIBase):
    def __init__(self, base_url, headers, config, logger):
        super().__init__(base_url, headers, config, logger)

    async def get_stock_info_by_code(self, stock_code: str) -> dict:
        """
        종목코드로 종목의 전체 정보 (이름, 현재가, 시가총액 등)를 가져옵니다.
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
            return response["output"]
        else:
            self.logger.warning(f"{stock_code} 종목 정보 조회 실패: {response}")
            return {}

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
        return await self.call_api('GET', path, params=params, retry_count=1)  # <--- retry_count 추가

    async def get_price_summary(self, stock_code: str) -> dict:
        """
        주어진 종목코드에 대해 시가/현재가/등락률(%) 요약 정보 반환
        """
        response = await self.get_current_price(stock_code)
        if not response or "output" not in response:
            self.logger.warning(f"API 응답 없음 또는 형식 오류: {stock_code}")
            return {
                "symbol": stock_code,
                "open": 0,
                "current": 0,
                "change_rate": 0.0
            }
        output = response.get("output", {})

        open_price = int(output.get("stck_oprc", 0))
        current_price = int(output.get("stck_prpr", 0))

        change_rate = (current_price - open_price) / open_price * 100 if open_price else 0

        return {
            "symbol": stock_code,
            "open": open_price,
            "current": current_price,
            "change_rate": round(change_rate, 2)
        }

    async def get_market_cap(self, stock_code: str) -> int:
        """
        종목코드로 시가총액을 반환합니다. (단위: 원)
        """
        info = await self.get_stock_info_by_code(stock_code)
        market_cap_str = info.get("stck_prpr_smkl_amt")  # 문자열로 반환됨
        if market_cap_str and market_cap_str.isdigit():
            return int(market_cap_str)
        else:
            self.logger.warning(f"{stock_code} 시가총액 정보 없음 또는 형식 오류")
            return 0

    async def get_top_market_cap_stocks(self, market_code: str, count: int = 30) -> list[dict]:
        """
        시가총액 상위 종목 목록을 반환합니다. 최대 30개까지만 지원됩니다.

        :param market_code: 시장 코드 (0000: 전체, 1001: 코스피, 1002: 코스닥 등)
        :param count: 반환할 종목 수 (기본값 20개, 최대 30개)
        """
        if count <= 0:
            self.logger.warning(f"요청된 count가 0 이하입니다. count={count}")
            return []

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
            self.logger.warning("시가총액 응답 오류 또는 비어 있음")
            return []

        batch = response["output"][:count]
        self.logger.info(f"API로부터 수신한 종목 수: {len(batch)}")

        results = []
        for item in batch:
            code = item.get("iscd") or item.get("mksc_shrn_iscd")
            raw_market_cap = item.get("stck_avls")

            if not code or not raw_market_cap:
                continue

            name = await self.get_stock_name_by_code(code)
            market_cap = int(raw_market_cap.replace(",", "")) if raw_market_cap.replace(",", "").isdigit() else 0

            results.append({
                "code": code,
                "name": name,
                "market_cap": market_cap
            })

        return results

    def get_previous_day_info(self, code: str) -> dict:
        """
        종목의 전일 종가, 전일 거래량 조회
        """
        params = {
            "fid_cond_mrkt_div_code": "J",  # 주식시장
            "fid_input_iscd": code,
        }
        response = self._client.request("get", "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                                        params)
        data = response.json()
        # 마지막 1개만 추출
        return {
            "prev_close": float(data["output"]["stck_clpr"]),
            "prev_volume": int(data["output"]["acml_vol"])
        }

    async def get_filtered_stocks_by_momentum(
            self, count=20, min_change_rate=10.0, min_volume_ratio=2.0
    ) -> list:
        """
        거래량 급증 + 등락률 조건 기반 모멘텀 종목 필터링

        :param count: 조회할 시가총액 상위 종목 수
        :param min_change_rate: 필터 기준 등락률 (%)
        :param min_volume_ratio: 필터 기준 거래량 배수
        :return: 조건에 맞는 종목 리스트
        """
        top_stocks = await self.get_top_market_cap_stocks()
        if not top_stocks or "output" not in top_stocks:
            self.logger.error("시가총액 상위 종목 조회 실패")
            return []

        filtered = []
        for item in top_stocks["output"][:count]:
            symbol = item.get("isu_cd", "").replace("A", "")
            prev_info = self.get_previous_day_info(symbol)
            prev_close = prev_info.get("prev_close", 0)
            prev_volume = prev_info.get("prev_volume", 0)

            summary = await self.get_price_summary(symbol)
            change_rate = summary.get("change_rate", 0)
            current_volume = int(item.get("acc_trdvol", 0))

            # 필터 기준 적용
            if (
                    change_rate >= min_change_rate
                    and prev_volume > 0
                    and current_volume / prev_volume >= min_volume_ratio
            ):
                filtered.append({
                    "symbol": symbol,
                    "change_rate": change_rate,
                    "prev_volume": prev_volume,
                    "current_volume": current_volume
                })

        return filtered

    async def get_stock_name_by_code(self, stock_code: str) -> str:
        """
        종목코드를 입력하면 종목명을 반환합니다.
        """
        info = await self.get_stock_info_by_code(stock_code)
        if not info or "hts_kor_isnm" not in info:
            self.logger.warning(f"종목명 조회 실패 또는 응답 없음: {stock_code}")
            return ""

        return info["hts_kor_isnm"]
