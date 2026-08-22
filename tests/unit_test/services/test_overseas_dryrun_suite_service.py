from unittest.mock import AsyncMock

import pytest

from common.overseas_types import OverseasExchange
from services.overseas_dryrun_suite_service import OverseasDryRunSuiteService


@pytest.mark.asyncio
async def test_scan_dry_run_runs_all_services_and_concatenates_signals():
    first = AsyncMock()
    first.scan_dry_run = AsyncMock(return_value=[{"code": "AAA"}])
    second = AsyncMock()
    second.scan_dry_run = AsyncMock(return_value=[{"code": "BBB"}])
    suite = OverseasDryRunSuiteService([first, second])

    signals = await suite.scan_dry_run(OverseasExchange.NYSE)

    assert signals == [{"code": "AAA"}, {"code": "BBB"}]
    first.scan_dry_run.assert_awaited_once_with(OverseasExchange.NYSE)
    second.scan_dry_run.assert_awaited_once_with(OverseasExchange.NYSE)


@pytest.mark.asyncio
async def test_scan_dry_run_continues_when_one_service_fails():
    failing = AsyncMock()
    failing.scan_dry_run = AsyncMock(side_effect=RuntimeError("boom"))
    healthy = AsyncMock()
    healthy.scan_dry_run = AsyncMock(return_value=[{"code": "AAA"}])
    suite = OverseasDryRunSuiteService([failing, healthy])

    signals = await suite.scan_dry_run(OverseasExchange.NASD)

    assert signals == [{"code": "AAA"}]
