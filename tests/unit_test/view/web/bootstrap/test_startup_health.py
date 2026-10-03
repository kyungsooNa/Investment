"""기동 degrade 상태가 운영자 알림으로 드러나는지 고정한다 (todo M-12).

#976 은 외부 의존 실패에도 웹 기동이 죽지 않게 만들었다. 그 대가로 조용히 degrade 로
뜨면 비어 있는 유니버스로 전략이 도는 상태를 못 알아챈다. 빠진 의존은 운영자 알림
(대시보드 active alert + 텔레그램)으로 남기고, 정상 기동 시에는 지난 기동의 알림을 해제한다.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.operator_alert_types import AlertSource
from view.web.bootstrap.startup_health import (
    BROKER_KEY,
    STOCK_CODE_DB_KEY,
    report_startup_health,
)


def _alert_service():
    svc = MagicMock()
    svc.report = AsyncMock()
    svc.resolve = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_reports_minimal_stock_code_db_and_broker_failure():
    svc = _alert_service()
    repo = SimpleNamespace(is_minimal_fallback=True)

    await report_startup_health(svc, repo, services_ready=False)

    reported = {c.args[1]: c.args for c in svc.report.await_args_list}
    assert set(reported) == {STOCK_CODE_DB_KEY, BROKER_KEY}
    assert all(args[0] == AlertSource.STARTUP for args in reported.values())
    assert reported[STOCK_CODE_DB_KEY][2] == "error"
    assert reported[BROKER_KEY][2] == "critical"
    svc.resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolves_previous_alerts_when_startup_is_healthy():
    svc = _alert_service()
    repo = SimpleNamespace(is_minimal_fallback=False)

    await report_startup_health(svc, repo, services_ready=True)

    svc.report.assert_not_awaited()
    resolved = {c.args[1] for c in svc.resolve.await_args_list}
    assert resolved == {STOCK_CODE_DB_KEY, BROKER_KEY}


@pytest.mark.asyncio
async def test_without_alert_service_is_noop():
    await report_startup_health(None, SimpleNamespace(is_minimal_fallback=True), services_ready=False)


@pytest.mark.asyncio
async def test_alert_failure_does_not_break_startup():
    svc = _alert_service()
    svc.report.side_effect = RuntimeError("state file locked")
    logger = MagicMock()

    await report_startup_health(svc, SimpleNamespace(is_minimal_fallback=True), services_ready=False, logger=logger)

    logger.warning.assert_called()
