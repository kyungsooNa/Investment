from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode, ResCommonResponse
from task.background.after_market.daily_investor_flow_report_task import (
    DailyInvestorFlowReportTask,
)


def _response(date: str, individual: str, foreign: str, institution: str):
    return ResCommonResponse(
        rt_cd=ErrorCode.SUCCESS.value,
        msg1="정상",
        data=[{
            "stck_bsop_date": date,
            "prsn_ntby_tr_pbmn": individual,
            "frgn_ntby_tr_pbmn": foreign,
            "orgn_ntby_tr_pbmn": institution,
        }],
    )


def _build_task(*, responses=None, last_sent=None):
    broker = MagicMock()
    broker.inquire_investor_daily_by_market = AsyncMock(
        side_effect=responses or [
            _response("20261019", "-1239300", "94700", "1037800"),
            _response("20261019", "78300", "-161800", "6800"),
        ]
    )
    reporter = MagicMock()
    reporter.send_daily_investor_flow_report = AsyncMock(return_value=True)
    store = MagicMock()
    store.load_keyed.return_value = last_sent
    task = DailyInvestorFlowReportTask(
        broker_api_wrapper=broker,
        telegram_reporter=reporter,
        scheduler_store=store,
        logger=MagicMock(),
    )
    return task, broker, reporter, store


@pytest.mark.asyncio
async def test_reports_kospi_and_kosdaq_daily_investor_flow():
    task, broker, reporter, store = _build_task()

    await task._on_market_closed("20261019")

    assert broker.inquire_investor_daily_by_market.await_args_list[0].args == ("0001",)
    assert broker.inquire_investor_daily_by_market.await_args_list[0].kwargs == {"date": "20261019"}
    assert broker.inquire_investor_daily_by_market.await_args_list[1].args == ("1001",)
    reporter.send_daily_investor_flow_report.assert_awaited_once_with(
        {
            "kospi": {"individual": -12393, "foreign": 947, "institution": 10378},
            "kosdaq": {"individual": 783, "foreign": -1618, "institution": 68},
        },
        "20261019",
    )
    store.save_keyed.assert_called_once_with(
        "daily_investor_flow_report_last_sent", "20261019"
    )


@pytest.mark.asyncio
async def test_does_not_send_when_a_market_has_no_exact_date_row():
    task, _, reporter, store = _build_task(responses=[
        _response("20261019", "100", "200", "300"),
        _response("20261016", "100", "200", "300"),
    ])

    await task._on_market_closed("20261019")

    reporter.send_daily_investor_flow_report.assert_not_awaited()
    store.save_keyed.assert_not_called()


@pytest.mark.asyncio
async def test_does_not_send_same_trading_date_twice_after_restart():
    task, broker, reporter, _ = _build_task(last_sent="20261019")

    await task._on_market_closed("20261019")

    broker.inquire_investor_daily_by_market.assert_not_awaited()
    reporter.send_daily_investor_flow_report.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_send_is_not_marked_complete():
    task, _, reporter, store = _build_task()
    reporter.send_daily_investor_flow_report.return_value = False

    await task._on_market_closed("20261019")

    store.save_keyed.assert_not_called()
