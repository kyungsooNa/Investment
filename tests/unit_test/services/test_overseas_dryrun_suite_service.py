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


class _FakeService:
    """STRATEGY_NAME 을 가진 dry-run 서비스 대역."""

    def __init__(self, name, result=None, error=None):
        self.STRATEGY_NAME = name
        self._result = result or []
        self._error = error

    async def scan_dry_run(self, exchange):
        if self._error is not None:
            raise self._error
        return self._result


@pytest.mark.asyncio
async def test_last_run_report_records_each_service_outcome():
    """전략별 실행 결과(0건 포함)를 남긴다 — 신호 수만으로는 '돌았는지'를 알 수 없다."""
    suite = OverseasDryRunSuiteService([
        _FakeService("LarryWilliamsVBO_overseas", result=[{"code": "AAA"}]),
        _FakeService("O'NeilPP_overseas", result=[]),
    ])

    await suite.scan_dry_run(OverseasExchange.NASD)

    assert suite.last_run_report == [
        {"strategy": "LarryWilliamsVBO_overseas", "ok": True, "signals": 1, "error": None},
        {"strategy": "O'NeilPP_overseas", "ok": True, "signals": 0, "error": None},
    ]


@pytest.mark.asyncio
async def test_last_run_report_marks_failed_service():
    """예외로 죽은 전략은 '0건'이 아니라 실패로 구분돼야 한다."""
    suite = OverseasDryRunSuiteService([
        _FakeService("O'NeilPP_overseas", error=RuntimeError("boom")),
        _FakeService("LarryWilliamsVBO_overseas", result=[{"code": "AAA"}]),
    ])

    await suite.scan_dry_run(OverseasExchange.NASD)

    assert suite.last_run_report == [
        {"strategy": "O'NeilPP_overseas", "ok": False, "signals": 0, "error": "boom"},
        {"strategy": "LarryWilliamsVBO_overseas", "ok": True, "signals": 1, "error": None},
    ]


@pytest.mark.asyncio
async def test_last_run_report_is_replaced_each_scan():
    """직전 실행 결과가 다음 스캔에 누적되면 '오늘 돌았는지'를 오독한다."""
    suite = OverseasDryRunSuiteService([_FakeService("LarryWilliamsVBO_overseas", result=[{"code": "AAA"}])])

    await suite.scan_dry_run(OverseasExchange.NASD)
    await suite.scan_dry_run(OverseasExchange.NASD)

    assert len(suite.last_run_report) == 1


@pytest.mark.asyncio
async def test_last_run_report_is_empty_before_first_scan():
    suite = OverseasDryRunSuiteService([_FakeService("LarryWilliamsVBO_overseas")])

    assert suite.last_run_report == []
