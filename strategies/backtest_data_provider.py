# strategies/backtest_data_provider.py

import logging
from core.time_manager import TimeManager
from services.stock_query_service import StockQueryService


class BacktestDataProvider:
    """
    백테스트 전략 실행에 필요한 주가 데이터를 제공하는 클래스입니다.
    모의(mock) 데이터 조회 및 실제 과거 데이터 조회 로직을 포함합니다.
    """

    def __init__(self, stock_query_service: StockQueryService, time_manager: TimeManager, logger=None):
        self.stock_query_service = stock_query_service
        self._time_manager = time_manager
        self._logger = logger if logger else logging.getLogger(__name__)

    async def mock_price_lookup(self, stock_code: str) -> int:
        """
        백테스트용으로 주가 상승을 가정한 모의 가격 제공
        (실제로는 DB, CSV, 또는 API를 통해 특정 시점 데이터를 받아야 함)
        """
        try:
            resp = await self.stock_query_service.get_current_price(stock_code)
            if not resp or resp.rt_cd != "0" or not resp.data:
                return 0
            output = resp.data.get("output", {})
            current = int(output.get("stck_prpr", 0))
            return int(current * 1.05)
        except Exception as e:
            self._logger.warning(f"[백테스트] {stock_code} 모의 가격 조회 실패: {e}")
            return 0

    async def realistic_price_lookup(self, stock_code: str, base_summary: dict, minutes_after: int) -> int:
        """
        백테스트용으로, 실제 과거 분봉 데이터를 기반으로 N분 후의 가격을 조회합니다.

        :param stock_code: 종목코드
        :param base_summary: 초기 등락률이 감지된 시점의 가격 요약 정보
        :param minutes_after: 몇 분 후의 가격을 조회할지
        :return: N분 후의 실제 종가
        """
        try:
            backtest_date = self._time_manager.get_current_kst_time().strftime('%Y%m%d')

            # StockQueryService를 통해 분봉 데이터 조회 (시간 오름차순 정렬됨)
            chart_data = await self.stock_query_service.get_day_intraday_minutes_list(
                stock_code, date_ymd=backtest_date
            )

            if not isinstance(chart_data, list) or not chart_data:
                self._logger.warning(f"[백테스트] {stock_code}의 분봉 데이터가 없거나 형식이 올바르지 않습니다.")
                return base_summary.get("current", 0)

            base_price = base_summary.get("current", 0)
            base_index = -1

            for i, candle in enumerate(chart_data):
                # get_day_intraday_minutes_list는 stck_prpr(현재가)를 포함
                if int(candle.get('stck_prpr', 0)) == base_price:
                    base_index = i
                    break

            if base_index == -1:
                self._logger.warning(f"[백테스트] {stock_code}의 기준 시점 분봉({base_price})을 찾지 못했습니다.")
                return base_price

            # 데이터가 시간 오름차순(과거->미래)이므로 인덱스를 더함
            after_index = base_index + minutes_after

            if after_index >= len(chart_data):
                after_index = len(chart_data) - 1

            if after_index < 0:
                after_index = 0

            after_price = int(chart_data[after_index].get('stck_prpr', 0))

            self._logger.info(f"[백테스트] {stock_code} | 기준가: {base_price} | {minutes_after}분 후 가격: {after_price}")
            return after_price

        except Exception as e:
            self._logger.exception(f"[백테스트] {stock_code} 가격 조회 중 오류 발생: {e}")
            return base_summary.get("current", 0)
