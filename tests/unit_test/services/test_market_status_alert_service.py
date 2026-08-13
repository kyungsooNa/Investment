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
async def test_market_status_alert_service_reports_sidecar_as_error_for_immediate_delivery():
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
    kwargs = operator_alert.report.await_args.kwargs
    assert args[1] == "market_status:sidecar:sell:KRX:005930"
    assert args[2] == "error"
    assert kwargs["metadata"]["telegram_channel"] == "report"


@pytest.mark.asyncio
async def test_market_status_alert_service_reports_kosdaq_sidecar_when_reason_uses_program_quote_stop():
    """장운영정보가 '사이드카' 대신 프로그램매매 호가 정지 문구를 주는 경우도 감지한다."""
    operator_alert = AsyncMock()
    service = MarketStatusAlertService(
        operator_alert_service=operator_alert,
        logger=MagicMock(),
    )

    await service.on_market_status({
        "유가증권단축종목코드": "000000",
        "거래정지여부": "N",
        "거래정지사유내용": "코스닥시장 프로그램매매 매수호가 일시효력정지",
        "거래소구분코드": "KOSDAQ",
    })

    args = operator_alert.report.await_args.args
    assert args[1] == "market_status:sidecar:buy:KOSDAQ:000000"
    assert args[2] == "error"
    assert args[3] == "매수 사이드카 감지"


@pytest.mark.asyncio
async def test_market_status_alert_service_distinguishes_buy_and_sell_sidecars():
    operator_alert = AsyncMock()
    service = MarketStatusAlertService(
        operator_alert_service=operator_alert,
        logger=MagicMock(),
    )

    await service.on_market_status({
        "유가증권단축종목코드": "000000",
        "거래정지사유내용": "매수 사이드카 발동",
        "거래소구분코드": "KOSPI",
    })
    await service.on_market_status({
        "유가증권단축종목코드": "000000",
        "거래정지사유내용": "매도 사이드카 발동",
        "거래소구분코드": "KOSPI",
    })

    assert operator_alert.report.await_count == 2
    assert operator_alert.report.await_args_list[0].args[1] == "market_status:sidecar:buy:KOSPI:000000"
    assert operator_alert.report.await_args_list[1].args[1] == "market_status:sidecar:sell:KOSPI:000000"
    assert operator_alert.report.await_args_list[0].args[3] == "매수 사이드카 감지"
    assert operator_alert.report.await_args_list[1].args[3] == "매도 사이드카 감지"


@pytest.mark.asyncio
async def test_market_status_alert_service_reports_buy_sidecar_watch_near_five_percent():
    operator_alert = AsyncMock()
    service = MarketStatusAlertService(
        operator_alert_service=operator_alert,
        logger=MagicMock(),
    )

    await service.on_index_change("0001", "코스피", 4.50)

    args = operator_alert.report.await_args.args
    kwargs = operator_alert.report.await_args.kwargs
    assert args[0] == AlertSource.MARKET_STATUS
    assert args[1].startswith("market_index:buy_sidecar_watch:0001:")
    assert args[2] == "warning"
    assert args[3] == "코스피 매수 사이드카 가능 구간"
    assert kwargs["metadata"]["event_type"] == "buy_sidecar_watch"
    assert kwargs["metadata"]["force_external"] is True


@pytest.mark.asyncio
async def test_market_status_alert_service_reports_buy_sidecar_watch_once_per_day():
    operator_alert = AsyncMock()
    service = MarketStatusAlertService(
        operator_alert_service=operator_alert,
        logger=MagicMock(),
    )

    await service.on_index_change("0001", "코스피", 4.62)
    await service.on_index_change("0001", "코스피", 4.40)
    await service.on_index_change("0001", "코스피", 4.61)

    assert operator_alert.report.await_count == 1
    assert operator_alert.resolve.await_count == 1


@pytest.mark.asyncio
async def test_market_status_alert_service_reports_futures_buy_sidecar_after_one_minute():
    operator_alert = AsyncMock()
    service = MarketStatusAlertService(
        operator_alert_service=operator_alert,
        logger=MagicMock(),
    )

    await service.on_futures_contract({
        "선물단축종목코드": "101TEST",
        "영업시간": "121800",
        "선물전일대비율": "5.01",
        "전일대비부호": "2",
        "선물현재가": "460.00",
    })
    operator_alert.report.assert_not_awaited()

    await service.on_futures_contract({
        "선물단축종목코드": "101TEST",
        "영업시간": "121900",
        "선물전일대비율": "5.03",
        "전일대비부호": "2",
        "선물현재가": "460.25",
    })

    args = operator_alert.report.await_args.args
    kwargs = operator_alert.report.await_args.kwargs
    assert args[0] == AlertSource.MARKET_STATUS
    assert args[1].startswith("market_futures:buy_sidecar:101TEST:")
    assert args[2] == "error"
    assert args[3] == "코스피200 선물 매수 사이드카 발동 조건"
    assert kwargs["metadata"]["event_type"] == "futures_buy_sidecar"
    assert kwargs["metadata"]["duration_sec"] == 60


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


@pytest.mark.asyncio
async def test_market_status_alert_service_reports_index_thresholds_and_resolves_them():
    operator_alert = AsyncMock()
    service = MarketStatusAlertService(
        operator_alert_service=operator_alert,
        logger=MagicMock(),
    )

    await service.on_index_change("0001", "코스피", -8.2)

    assert operator_alert.report.await_count == 2
    assert operator_alert.report.await_args_list[0].args[1] == "market_index:move_5:down:0001"
    assert operator_alert.report.await_args_list[0].args[2] == "error"
    assert operator_alert.report.await_args_list[1].args[1] == "market_index:fall_8:0001"
    assert operator_alert.report.await_args_list[1].args[2] == "critical"

    await service.on_index_change("0001", "코스피", -1.0)

    assert {call.args[1] for call in operator_alert.resolve.await_args_list} == {
        "market_index:fall_8:0001",
    }

    await service.on_index_change("0001", "코스피", -1.0)
    await service.on_index_change("0001", "코스피", -1.0)

    assert {call.args[1] for call in operator_alert.resolve.await_args_list} == {
        "market_index:move_5:down:0001",
        "market_index:fall_8:0001",
    }


@pytest.mark.asyncio
async def test_market_status_alert_service_resolves_move_5_after_three_recovery_samples():
    """-5% 경보는 정상 범위가 3회 연속 관측되어야 해제한다."""
    operator_alert = AsyncMock()
    service = MarketStatusAlertService(
        operator_alert_service=operator_alert,
        logger=MagicMock(),
    )

    await service.on_index_change("0001", "코스피", -5.03)
    await service.on_index_change("0001", "코스피", -4.90)

    operator_alert.report.assert_awaited_once()
    operator_alert.resolve.assert_not_awaited()

    await service.on_index_change("0001", "코스피", -4.50)
    await service.on_index_change("0001", "코스피", -4.40)

    operator_alert.resolve.assert_not_awaited()

    await service.on_index_change("0001", "코스피", -4.30)

    operator_alert.resolve.assert_awaited_once_with(
        AlertSource.MARKET_STATUS,
        "market_index:move_5:down:0001",
        "지수 등락률 정상화",
    )


@pytest.mark.asyncio
async def test_market_status_alert_service_resets_move_5_recovery_count_on_relapse():
    """회복 확인 중 다시 -5%에 닿으면 해제 확인 횟수를 처음부터 센다."""
    operator_alert = AsyncMock()
    service = MarketStatusAlertService(
        operator_alert_service=operator_alert,
        logger=MagicMock(),
    )

    await service.on_index_change("0001", "코스피", -5.03)
    await service.on_index_change("0001", "코스피", -4.40)
    await service.on_index_change("0001", "코스피", -5.01)
    await service.on_index_change("0001", "코스피", -4.40)
    await service.on_index_change("0001", "코스피", -4.30)

    operator_alert.resolve.assert_not_awaited()

    await service.on_index_change("0001", "코스피", -4.20)

    operator_alert.resolve.assert_awaited_once_with(
        AlertSource.MARKET_STATUS,
        "market_index:move_5:down:0001",
        "지수 등락률 정상화",
    )
