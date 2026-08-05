from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from repositories.favorite_repository import MARKET_DOMESTIC, MARKET_OVERSEAS_US
from services.favorite_price_alert_service import FavoritePriceAlertService
from services.notification_service import NotificationCategory, NotificationLevel


@pytest.mark.asyncio
async def test_alerts_when_favorite_rate_crosses_positive_five_percent():
    repo = MagicMock()
    repo.get_all = AsyncMock(return_value=["005930"])
    notifications = MagicMock()
    notifications.emit = AsyncMock()
    stock_codes = MagicMock()
    stock_codes.get_name_by_code.return_value = "삼성전자"
    svc = FavoritePriceAlertService(repo, notifications, stock_codes)

    await svc.handle_price_tick("005930", price="75000", rate="5.12")

    notifications.emit.assert_awaited_once()
    args, kwargs = notifications.emit.call_args
    assert args[:2] == (NotificationCategory.SYSTEM, NotificationLevel.WARNING)
    assert "삼성전자" in args[2]
    assert "+5%" in args[2]
    assert "75,000원" in args[3]
    assert kwargs["metadata"]["threshold_pct"] == 5
    assert kwargs["metadata"]["force_external"] is True
    assert kwargs["metadata"]["telegram_channel"] == "report"


@pytest.mark.asyncio
async def test_alerts_when_saved_favorite_code_is_not_zero_padded():
    repo = MagicMock()
    repo.get_all = AsyncMock(return_value=["5930"])
    notifications = MagicMock()
    notifications.emit = AsyncMock()
    svc = FavoritePriceAlertService(repo, notifications)

    await svc.handle_price_tick("005930", price="75000", rate="5.12")

    notifications.emit.assert_awaited_once()
    assert notifications.emit.call_args.kwargs["metadata"]["code"] == "005930"


@pytest.mark.asyncio
async def test_does_not_repeat_same_five_percent_bucket_until_next_bucket():
    repo = MagicMock()
    repo.get_all = AsyncMock(return_value=["005930"])
    notifications = MagicMock()
    notifications.emit = AsyncMock()
    svc = FavoritePriceAlertService(repo, notifications)

    await svc.handle_price_tick("005930", price="75000", rate="5.12")
    await svc.handle_price_tick("005930", price="75200", rate="5.44")
    await svc.handle_price_tick("005930", price="78500", rate="10.02")

    assert notifications.emit.await_count == 2
    assert notifications.emit.await_args_list[1].kwargs["metadata"]["threshold_pct"] == 10


@pytest.mark.asyncio
async def test_does_not_repeat_bucket_when_rate_chatters_around_threshold():
    """5% 경계를 짧게 왕복해도 같은 구간 알림은 쿨다운 동안 한 번만 발행한다."""
    repo = MagicMock()
    repo.get_all = AsyncMock(return_value=["005930"])
    notifications = MagicMock()
    notifications.emit = AsyncMock()
    svc = FavoritePriceAlertService(repo, notifications)

    await svc.handle_price_tick("005930", price="70000", rate="-5.01")
    await svc.handle_price_tick("005930", price="70050", rate="-4.99")
    await svc.handle_price_tick("005930", price="70000", rate="-5.02")

    notifications.emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_does_not_repeat_five_percent_after_retracing_without_another_alert_bucket():
    """+5% 알림 뒤 +6%를 거쳐 +5%로 돌아와도 같은 알림을 반복하지 않는다."""
    repo = MagicMock()
    repo.get_all = AsyncMock(return_value=["005930"])
    notifications = MagicMock()
    notifications.emit = AsyncMock()
    svc = FavoritePriceAlertService(repo, notifications)

    await svc.handle_price_tick("005930", price="75000", rate="5.01")
    await svc.handle_price_tick("005930", price="75600", rate="6.00")
    await svc.handle_price_tick("005930", price="75000", rate="5.02")

    notifications.emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_does_not_alert_lower_positive_threshold_after_ten_percent_alert():
    """+10% 도달 뒤 +5% 구간으로 내려와도 상승 알림을 재발행하지 않는다."""
    repo = MagicMock()
    repo.get_all = AsyncMock(return_value=["005930"])
    notifications = MagicMock()
    notifications.emit = AsyncMock()
    svc = FavoritePriceAlertService(repo, notifications)

    await svc.handle_price_tick("005930", price="75000", rate="5.01")
    await svc.handle_price_tick("005930", price="78500", rate="10.01")
    await svc.handle_price_tick("005930", price="75000", rate="5.02")

    assert [
        call.kwargs["metadata"]["threshold_pct"]
        for call in notifications.emit.await_args_list
    ] == [5, 10]


@pytest.mark.asyncio
async def test_does_not_chatter_between_ten_and_five_percent_thresholds():
    """+10% 경계 부근의 틱 변동은 +5% 또는 +10% 알림을 반복하지 않는다."""
    repo = MagicMock()
    repo.get_all = AsyncMock(return_value=["009150"])
    notifications = MagicMock()
    notifications.emit = AsyncMock()
    svc = FavoritePriceAlertService(repo, notifications)

    await svc.handle_price_tick("009150", price="1304000", rate="10.04")
    await svc.handle_price_tick("009150", price="1303000", rate="9.96")
    await svc.handle_price_tick("009150", price="1304000", rate="10.04")

    assert [
        call.kwargs["metadata"]["threshold_pct"]
        for call in notifications.emit.await_args_list
    ] == [10]


@pytest.mark.asyncio
async def test_persists_alert_bucket_across_service_restart_for_same_day(tmp_path):
    """프로세스 재시작 뒤에도 당일 동일 임계치 알림은 반복하지 않는다."""
    repo = MagicMock()
    repo.get_all = AsyncMock(return_value=["009150"])
    notifications = MagicMock()
    notifications.emit = AsyncMock()
    state_file = tmp_path / "favorite_alert_state.json"
    kwargs = {
        "state_file": str(state_file),
        "today_provider": lambda: "20260803",
    }

    first = FavoritePriceAlertService(repo, notifications, **kwargs)
    await first.handle_price_tick("009150", price="1200000", rate="5.08")

    restarted = FavoritePriceAlertService(repo, notifications, **kwargs)
    await restarted.handle_price_tick("009150", price="1200000", rate="5.08")

    notifications.emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_alerts_negative_five_percent_bucket():
    repo = MagicMock()
    repo.get_all = AsyncMock(return_value=["005930"])
    notifications = MagicMock()
    notifications.emit = AsyncMock()
    svc = FavoritePriceAlertService(repo, notifications)

    await svc.handle_price_tick("005930", price="70000", rate="-5.01")

    notifications.emit.assert_awaited_once()
    args, kwargs = notifications.emit.call_args
    assert "-5%" in args[2]
    assert kwargs["metadata"]["threshold_pct"] == -5


@pytest.mark.asyncio
async def test_applies_kis_negative_sign_to_unsigned_rate():
    repo = MagicMock()
    repo.get_all = AsyncMock(return_value=["005930"])
    notifications = MagicMock()
    notifications.emit = AsyncMock()
    svc = FavoritePriceAlertService(repo, notifications)

    await svc.handle_price_tick("005930", price="70000", rate="5.01", sign="5")

    notifications.emit.assert_awaited_once()
    args, kwargs = notifications.emit.call_args
    assert "-5%" in args[2]
    assert kwargs["metadata"]["threshold_pct"] == -5
    assert kwargs["metadata"]["rate"] == -5.01


@pytest.mark.asyncio
async def test_alerts_upper_limit_separately_from_twenty_five_percent_bucket():
    repo = MagicMock()
    repo.get_all = AsyncMock(return_value=["005930"])
    notifications = MagicMock()
    notifications.emit = AsyncMock()
    svc = FavoritePriceAlertService(repo, notifications)

    await svc.handle_price_tick("005930", price="82000", rate="25.10", sign="2")
    await svc.handle_price_tick("005930", price="85000", rate="29.92", sign="1")

    assert notifications.emit.await_count == 2
    second_args, second_kwargs = notifications.emit.await_args_list[1]
    assert "상한가" in second_args[2]
    assert second_kwargs["metadata"]["alert_type"] == "favorite_upper_limit"
    assert second_kwargs["metadata"]["is_upper_limit"] is True


@pytest.mark.asyncio
async def test_ignores_non_favorite_and_invalid_rate():
    repo = MagicMock()
    repo.get_all = AsyncMock(return_value=["000660"])
    notifications = MagicMock()
    notifications.emit = AsyncMock()
    svc = FavoritePriceAlertService(repo, notifications)

    await svc.handle_price_tick("005930", price="75000", rate="6.00")
    await svc.handle_price_tick("000660", price="150000", rate="bad")

    notifications.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_reads_favorites_from_domestic_market_by_default():
    repo = MagicMock()
    repo.get_all = AsyncMock(return_value=["005930"])
    notifications = MagicMock()
    notifications.emit = AsyncMock()
    svc = FavoritePriceAlertService(repo, notifications)

    await svc.handle_price_tick("005930", price="75000", rate="5.12")

    repo.get_all.assert_awaited_with(market=MARKET_DOMESTIC)


@pytest.mark.asyncio
async def test_alerts_overseas_favorite_with_usd_price():
    """미국장 인스턴스는 overseas_us 관심종목을 읽고 달러 표기로 알린다."""
    repo = MagicMock()
    repo.get_all = AsyncMock(return_value=["AAPL"])
    notifications = MagicMock()
    notifications.emit = AsyncMock()
    svc = FavoritePriceAlertService(repo, notifications, market=MARKET_OVERSEAS_US)

    await svc.handle_price_tick("AAPL", price="189.5", rate="5.30")

    repo.get_all.assert_awaited_with(market=MARKET_OVERSEAS_US)
    notifications.emit.assert_awaited_once()
    args, kwargs = notifications.emit.call_args
    assert "AAPL" in args[2]
    assert "+5%" in args[2]
    assert "$189.50" in args[3]
    assert kwargs["metadata"]["threshold_pct"] == 5


@pytest.mark.asyncio
async def test_overseas_market_never_emits_upper_limit_alert():
    """미국장은 상한가 제도가 없으므로 30% 구간도 일반 임계치 알림으로 처리한다."""
    repo = MagicMock()
    repo.get_all = AsyncMock(return_value=["TSLA"])
    notifications = MagicMock()
    notifications.emit = AsyncMock()
    svc = FavoritePriceAlertService(repo, notifications, market=MARKET_OVERSEAS_US)

    await svc.handle_price_tick("TSLA", price="300", rate="30.20")

    notifications.emit.assert_awaited_once()
    metadata = notifications.emit.call_args.kwargs["metadata"]
    assert metadata["alert_type"] == "favorite_price_threshold"
    assert metadata["threshold_pct"] == 30


@pytest.mark.asyncio
async def test_add_and_remove_favorite_update_cache_and_reset_alert_state():
    repo = MagicMock()
    repo.get_all = AsyncMock(return_value=[])
    notifications = MagicMock()
    notifications.emit = AsyncMock()
    svc = FavoritePriceAlertService(repo, notifications)

    await svc.add_favorite("005930")
    await svc.handle_price_tick("005930", price="75000", rate="5.10")
    await svc.remove_favorite("005930")
    await svc.handle_price_tick("005930", price="75100", rate="10.10")

    notifications.emit.assert_awaited_once()
