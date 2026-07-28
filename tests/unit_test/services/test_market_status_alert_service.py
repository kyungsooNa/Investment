import pytest
from unittest.mock import AsyncMock, MagicMock

from common.operator_alert_types import AlertSource
from services.market_status_alert_service import MarketStatusAlertService


@pytest.mark.asyncio
async def test_market_status_alert_service_reports_circuit_breaker():
    operator_alert = AsyncMock()
    service = MarketStatusAlertService(
        operator_alert_service=operator_alert,
        logger=MagicMock(),
    )

    await service.on_market_status({
        "유가증권단축종목코드": "005930",
        "거래정지여부": "Y",
        "거래정지사유내용": "서킷브레이커 발동으로 매매거래중단",
        "거래소구분코드": "KRX",
    })

    operator_alert.report.assert_awaited_once()
    args = operator_alert.report.await_args.args
    kwargs = operator_alert.report.await_args.kwargs
    assert args[0] == AlertSource.MARKET_STATUS
    assert args[1] == "market_status:circuit_breaker:KRX:005930"
    assert args[2] == "critical"
    assert "서킷브레이커" in args[3]
    assert kwargs["metadata"]["event_type"] == "circuit_breaker"


@pytest.mark.asyncio
async def test_market_status_alert_service_reports_sidecar_warning():
    operator_alert = AsyncMock()
    service = MarketStatusAlertService(
        operator_alert_service=operator_alert,
        logger=MagicMock(),
    )

    await service.on_market_status({
        "유가증권단축종목코드": "005930",
        "거래정지여부": "N",
        "거래정지사유내용": "매도 사이드카 발동",
        "거래소구분코드": "KRX",
    })

    args = operator_alert.report.await_args.args
    assert args[1] == "market_status:sidecar:KRX:005930"
    assert args[2] == "warning"


@pytest.mark.asyncio
async def test_market_status_alert_service_ignores_normal_status():
    operator_alert = AsyncMock()
    service = MarketStatusAlertService(
        operator_alert_service=operator_alert,
        logger=MagicMock(),
    )

    await service.on_market_status({
        "유가증권단축종목코드": "005930",
        "거래정지여부": "N",
        "거래정지사유내용": "",
        "거래소구분코드": "KRX",
    })

    operator_alert.report.assert_not_awaited()


@pytest.mark.asyncio
async def test_market_status_alert_service_resolves_active_alert_on_normal_status():
    operator_alert = AsyncMock()
    service = MarketStatusAlertService(
        operator_alert_service=operator_alert,
        logger=MagicMock(),
    )

    await service.on_market_status({
        "유가증권단축종목코드": "005930",
        "거래정지여부": "Y",
        "거래정지사유내용": "서킷브레이커 발동",
        "거래소구분코드": "KRX",
    })
    await service.on_market_status({
        "유가증권단축종목코드": "005930",
        "거래정지여부": "N",
        "거래정지사유내용": "",
        "거래소구분코드": "KRX",
    })

    operator_alert.resolve.assert_awaited_once_with(
        AlertSource.MARKET_STATUS,
        "market_status:circuit_breaker:KRX:005930",
        "장운영정보 정상화",
    )
