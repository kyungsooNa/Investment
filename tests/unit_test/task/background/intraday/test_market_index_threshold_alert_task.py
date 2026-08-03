from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, ResCommonResponse
from task.background.intraday.market_index_threshold_alert_task import MarketIndexThresholdAlertTask


@pytest.mark.asyncio
async def test_tick_forwards_signed_kospi_and_kosdaq_change_rates():
    broker = MagicMock()
    broker.inquire_time_indexchartprice = AsyncMock(side_effect=[
        ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="정상",
            data={"summary": {"bstp_nmix_prdy_ctrt": "5.1", "prdy_vrss_sign": "2"}},
        ),
        ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="정상",
            data={"summary": {"bstp_nmix_prdy_ctrt": "8.2", "prdy_vrss_sign": "5"}},
        ),
    ])
    alert_service = MagicMock()
    alert_service.on_index_change = AsyncMock()
    market_clock = MagicMock()
    market_clock.get_current_kst_time.return_value = datetime(2026, 8, 3, 10, 0)
    market_clock.is_market_operating_hours.return_value = True
    market_calendar = MagicMock()
    market_calendar.is_business_day = AsyncMock(return_value=True)
    task = MarketIndexThresholdAlertTask(
        broker=broker,
        market_status_alert_service=alert_service,
        market_clock=market_clock,
        market_calendar_service=market_calendar,
        logger=MagicMock(),
    )

    await task._tick()

    assert broker.inquire_time_indexchartprice.await_args_list[0].args == ("0001", 60)
    assert broker.inquire_time_indexchartprice.await_args_list[1].args == ("1001", 60)
    assert alert_service.on_index_change.await_args_list[0].args == ("0001", "코스피", 5.1)
    assert alert_service.on_index_change.await_args_list[1].args == ("1001", "코스닥", -8.2)
