import pytest
from unittest.mock import AsyncMock, MagicMock

from common.operator_alert_types import AlertSource
from services.market_status_alert_service import MarketStatusAlertService


@pytest.mark.asyncio
async def test_market_status_alert_service_reports_stock_vi_trigger():
    operator_alert = AsyncMock()
    service = MarketStatusAlertService(
        operator_alert_service=operator_alert,
        logger=MagicMock(),
    )

    await service.on_market_status({
        "유가증권단축종목코드": "080220",
        "거래정지여부": "N",
        "거래정지사유내용": "",
        "VI적용구분코드": "1",
        "거래소구분코드": "KRX",
    })

    operator_alert.report.assert_awaited_once()
    args = operator_alert.report.await_args.args
    kwargs = operator_alert.report.await_args.kwargs
    assert args[0] == AlertSource.MARKET_STATUS
    assert args[1] == "market_status:stock_vi:KRX:080220"
    assert args[2] == "error"
    assert args[3] == "개별종목 VI 발동 감지"
    assert "080220" in args[4]
    assert kwargs["metadata"]["event_type"] == "stock_vi"
    assert kwargs["metadata"]["vi_code"] == "1"
    assert kwargs["metadata"]["telegram_channel"] == "report"


@pytest.mark.asyncio
async def test_market_status_alert_service_resolves_stock_vi_on_normal_status():
    operator_alert = AsyncMock()
    service = MarketStatusAlertService(
        operator_alert_service=operator_alert,
        logger=MagicMock(),
    )

    await service.on_market_status({
        "유가증권단축종목코드": "080220",
        "거래정지여부": "N",
        "거래정지사유내용": "",
        "VI적용구분코드": "1",
        "거래소구분코드": "KRX",
    })
    await service.on_market_status({
        "유가증권단축종목코드": "080220",
        "거래정지여부": "N",
        "거래정지사유내용": "",
        "VI적용구분코드": "0",
        "거래소구분코드": "KRX",
    })

    operator_alert.resolve.assert_awaited_once_with(
        AlertSource.MARKET_STATUS,
        "market_status:stock_vi:KRX:080220",
        "장운영정보 정상화",
    )


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


# ── 종목코드 정규화 계약 ──────────────────────────────────────
# 관심종목 알림에서 두 번(#755·#771) 고쳤던 것과 같은 결함 계열이다. dedup 키에 코드가
# 그대로 박히므로, 발동과 해제가 다른 표기로 오면 키가 어긋나 알림이 해제되지 않고 재발동한다.

@pytest.mark.asyncio
async def test_dedup_key_uses_zero_padded_stock_code():
    operator_alert = AsyncMock()
    service = MarketStatusAlertService(operator_alert_service=operator_alert, logger=MagicMock())

    await service.on_market_status({
        "유가증권단축종목코드": "5930",           # 선행 0 이 빠진 표기
        "거래정지여부": "Y",
        "거래정지사유내용": "서킷브레이커 발동으로 매매거래중단",
        "거래소구분코드": "KRX",
    })

    assert operator_alert.report.await_args.args[1] == "market_status:circuit_breaker:KRX:005930"
    assert operator_alert.report.await_args.kwargs["metadata"]["stock_code"] == "005930"


@pytest.mark.asyncio
async def test_alert_resolves_even_when_the_code_arrives_padded_differently():
    """짧은 코드로 발동하고 패딩된 코드로 정상화가 와도 해제돼야 한다."""
    operator_alert = AsyncMock()
    service = MarketStatusAlertService(operator_alert_service=operator_alert, logger=MagicMock())

    await service.on_market_status({
        "유가증권단축종목코드": "5930",
        "거래정지여부": "Y",
        "거래정지사유내용": "서킷브레이커 발동으로 매매거래중단",
        "거래소구분코드": "KRX",
    })
    await service.on_market_status({
        "유가증권단축종목코드": "005930",        # 같은 종목, 다른 표기
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
async def test_non_numeric_codes_are_left_alone():
    """선물 코드·미상(UNKNOWN) 처럼 숫자가 아닌 값은 건드리지 않는다."""
    operator_alert = AsyncMock()
    service = MarketStatusAlertService(operator_alert_service=operator_alert, logger=MagicMock())

    await service.on_market_status({
        "종목코드": "K200F",
        "거래정지여부": "Y",
        "거래정지사유내용": "서킷브레이커 발동으로 매매거래중단",
        "거래소구분코드": "KRX",
    })

    assert operator_alert.report.await_args.args[1] == "market_status:circuit_breaker:KRX:K200F"


# --- notification_service 폴백 경로 -----------------------------------------


def _notify_service(**kwargs):
    notifier = AsyncMock()
    service = MarketStatusAlertService(
        operator_alert_service=None,
        notification_service=notifier,
        logger=MagicMock(),
        **kwargs,
    )
    return service, notifier


@pytest.mark.asyncio
async def test_market_status_alerts_fall_back_to_the_notification_service():
    service, notifier = _notify_service()

    await service.on_market_status({
        "유가증권단축종목코드": "080220",
        "거래정지여부": "N",
        "VI적용구분코드": "1",
        "거래소구분코드": "KRX",
    })

    notifier.emit.assert_awaited_once()
    assert notifier.emit.await_args.args[2] == "개별종목 VI 발동 감지"


@pytest.mark.asyncio
async def test_index_alerts_fall_back_to_the_notification_service():
    service, notifier = _notify_service()

    await service.on_index_change("0001", "코스피", -8.5)

    notifier.emit.assert_awaited()
    titles = [call.args[2] for call in notifier.emit.await_args_list]
    assert any("코스피" in title for title in titles)


@pytest.mark.asyncio
async def test_futures_sidecar_alert_falls_back_to_the_notification_service():
    service, notifier = _notify_service()

    await service.on_futures_contract({
        "선물단축종목코드": "101TEST", "영업시간": "121800",
        "선물전일대비율": "5.01", "전일대비부호": "2", "선물현재가": "460.00",
    })
    await service.on_futures_contract({
        "선물단축종목코드": "101TEST", "영업시간": "121900",
        "선물전일대비율": "5.03", "전일대비부호": "2", "선물현재가": "460.25",
    })

    notifier.emit.assert_awaited_once()
    assert "선물 매수 사이드카" in notifier.emit.await_args.args[2]


# --- 선물 틱 경계 -----------------------------------------------------------


@pytest.mark.asyncio
async def test_futures_tick_without_a_business_time_uses_the_wall_clock():
    operator_alert = AsyncMock()
    service = MarketStatusAlertService(
        operator_alert_service=operator_alert, logger=MagicMock()
    )

    await service.on_futures_contract({
        "선물단축종목코드": "101TEST", "영업시간": "",
        "선물전일대비율": "5.01", "전일대비부호": "2",
    })

    # 첫 틱은 시작 시각만 기록하고 알림은 내지 않는다.
    operator_alert.report.assert_not_awaited()
    assert "101TEST" in service._futures_sidecar_started_sec_by_code


@pytest.mark.asyncio
async def test_futures_rate_below_threshold_clears_the_watch_and_resolves():
    operator_alert = AsyncMock()
    service = MarketStatusAlertService(
        operator_alert_service=operator_alert, logger=MagicMock()
    )
    service._futures_sidecar_started_sec_by_code["101TEST"] = 0
    service._active_futures_keys_by_code["101TEST"] = {"market_futures:buy_sidecar:101TEST:1"}

    await service.on_futures_contract({
        "선물단축종목코드": "101TEST", "영업시간": "121900",
        "선물전일대비율": "0.5", "전일대비부호": "2",
    })

    assert "101TEST" not in service._futures_sidecar_started_sec_by_code
    operator_alert.resolve.assert_awaited_once()


@pytest.mark.asyncio
async def test_futures_duration_wraps_across_midnight():
    operator_alert = AsyncMock()
    service = MarketStatusAlertService(
        operator_alert_service=operator_alert, logger=MagicMock()
    )
    # 시작 시각이 23:59:00, 현재 틱이 00:00:30 → 90초 경과로 계산돼야 한다.
    service._futures_sidecar_started_sec_by_code["101TEST"] = 23 * 3600 + 59 * 60

    await service.on_futures_contract({
        "선물단축종목코드": "101TEST", "영업시간": "000030",
        "선물전일대비율": "5.01", "전일대비부호": "2",
    })

    assert operator_alert.report.await_args.kwargs["metadata"]["duration_sec"] == 90


@pytest.mark.asyncio
async def test_resolvers_are_noops_without_an_operator_alert_service():
    service, _ = _notify_service()

    await service._resolve_for_code({"유가증권단축종목코드": "080220"})
    await service._resolve_futures_alerts("101TEST")


# --- 값 파싱 헬퍼 -----------------------------------------------------------


def test_signed_rate_parser_handles_signs_and_unusable_values():
    service = MarketStatusAlertService(operator_alert_service=None, logger=MagicMock())
    parse = service._signed_rate

    assert parse({"전일대비율": "1,234.5"}, rate_keys=("전일대비율",), sign_keys=("부호",)) == 1234.5
    assert parse(
        {"전일대비율": "3.3", "부호": "5"}, rate_keys=("전일대비율",), sign_keys=("부호",)
    ) == -3.3
    assert parse({"전일대비율": "숫자아님"}, rate_keys=("전일대비율",), sign_keys=()) is None
    assert parse({}, rate_keys=("전일대비율",), sign_keys=()) is None


def test_business_time_parser_rejects_malformed_values():
    service = MarketStatusAlertService(operator_alert_service=None, logger=MagicMock())

    assert service._hhmmss_to_seconds("121900") == 12 * 3600 + 19 * 60
    assert service._hhmmss_to_seconds("1219") is None
    assert service._hhmmss_to_seconds("12190X") is None


def test_buy_sidecar_watch_history_is_reset_per_trading_date():
    service = MarketStatusAlertService(operator_alert_service=None, logger=MagicMock())
    service._buy_sidecar_watch_reported_keys_by_date["20260731"] = {"어제키"}
    service._buy_sidecar_watch_reported_keys_by_date["20260801"] = {"오늘키"}

    service._reset_buy_sidecar_watch_history("20260801")

    assert set(service._buy_sidecar_watch_reported_keys_by_date) == {"20260801"}
