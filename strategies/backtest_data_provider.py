# strategies/backtest_data_provider.py

import logging
from brokers.broker_api_wrapper import BrokerAPIWrapper
from core.time_manager import TimeManager


class BacktestDataProvider:
    """
    백테스트 전략 실행에 필요한 주가 데이터를 제공하는 클래스입니다.
    모의(mock) 데이터 조회 및 실제 과거 데이터 조회 로직을 포함합니다.
    """

    def __init__(self, broker_api_wrapper: BrokerAPIWrapper, time_manager: TimeManager, logger=None):
        self._broker_api_wrapper = broker_api_wrapper
        self._time_manager = time_manager
        self._logger = logger if logger else logging.getLogger(__name__)

    async def mock_price_lookup(self, stock_code: str) -> int:
        """
        백테스트용으로 주가 상승을 가정한 모의 가격 제공
        (실제로는 DB, CSV, 또는 API를 통해 특정 시점 데이터를 받아야 함)
        """
        try:
            current_info = await self._broker_api_wrapper.get_price_summary(stock_code)
            return current_info.data.get('current') * 1.05
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

            # BrokerAPIWrapper를 통해 분봉 데이터 조회 시 fid_period_div_code='M' 명시
            # <<< 이 부분이 수정되었습니다.
            chart_data = await self._broker_api_wrapper.inquire_daily_itemchartprice(
                stock_code, backtest_date, fid_period_div_code='M'  # 분봉 데이터 요청을 위해 'M' 전달
            )
            # >>>

            if not isinstance(chart_data, list) or not chart_data:
                self._logger.warning(f"[백테스트] {stock_code}의 분봉 데이터가 없거나 형식이 올바르지 않습니다.")
                return base_summary.get("current", 0)

            base_price = base_summary.get("current", 0)
            base_index = -1

            for i, candle in enumerate(chart_data):
                if int(candle.get('stck_clpr', 0)) == base_price:
                    base_index = i
                    break

            if base_index == -1:
                self._logger.warning(f"[백테스트] {stock_code}의 기준 시점 분봉({base_price})을 찾지 못했습니다.")
                return base_price

            after_index = base_index - minutes_after

            if after_index < 0:
                after_index = 0

            if after_index >= len(chart_data):
                after_index = len(chart_data) - 1

            after_price = int(chart_data[after_index].get('stck_clpr', 0))

            self._logger.info(f"[백테스트] {stock_code} | 기준가: {base_price} | {minutes_after}분 후 가격: {after_price}")
            return after_price

        except Exception as e:
            self._logger.error(f"[백테스트] {stock_code} 가격 조회 중 오류 발생: {e}", exc_info=True)
            return base_summary.get("current", 0)
