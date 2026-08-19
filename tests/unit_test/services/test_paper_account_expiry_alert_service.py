from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.notification_service import NotificationCategory, NotificationLevel
from services.paper_account_expiry_alert_service import PaperAccountExpiryAlertService


def _clock(now: datetime):
    clock = MagicMock()
    clock.get_current_kst_time.return_value = now
    return clock


@pytest.mark.asyncio
async def test_emits_daily_warning_from_seven_days_before_expiry(tmp_path):
    ns = AsyncMock()
    service = PaperAccountExpiryAlertService(
        expires_at="2026/11/19",
        warning_days=7,
        notification_service=ns,
        market_clock=_clock(datetime(2026, 11, 12, 8, 30)),
        state_file=str(tmp_path / "state.json"),
        logger=MagicMock(),
    )

    result = await service.check_and_notify(
        is_paper_trading=True,
        account_number="50202454",
    )

    assert result["alerted"] is True
    assert result["days_left"] == 7
    ns.emit.assert_awaited_once()
    args, kwargs = ns.emit.await_args
    assert args[:3] == (
        NotificationCategory.SYSTEM,
        NotificationLevel.WARNING,
        "모의투자 계좌 만료 예정",
    )
    assert "2026-11-19" in args[3]
    assert "7일" in args[3]
    assert kwargs["metadata"]["force_external"] is True


@pytest.mark.asyncio
async def test_deduplicates_same_account_and_expiry_once_per_day(tmp_path):
    ns = AsyncMock()
    service = PaperAccountExpiryAlertService(
        expires_at="2026-11-19",
        notification_service=ns,
        market_clock=_clock(datetime(2026, 11, 18, 8, 30)),
        state_file=str(tmp_path / "state.json"),
        logger=MagicMock(),
    )

    first = await service.check_and_notify(True, "50202454")
    second = await service.check_and_notify(True, "50202454")

    assert first["alerted"] is True
    assert second["alerted"] is False
    assert second["reason"] == "already_alerted_today"
    ns.emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_skips_when_not_paper_or_outside_warning_window(tmp_path):
    ns = AsyncMock()
    service = PaperAccountExpiryAlertService(
        expires_at="2026-11-19",
        notification_service=ns,
        market_clock=_clock(datetime(2026, 11, 11, 8, 30)),
        state_file=str(tmp_path / "state.json"),
        logger=MagicMock(),
    )

    real_result = await service.check_and_notify(False, "50202454")
    early_result = await service.check_and_notify(True, "50202454")

    assert real_result == {"alerted": False, "reason": "not_paper_trading"}
    assert early_result["alerted"] is False
    assert early_result["reason"] == "outside_warning_window"
    assert early_result["days_left"] == 8
    ns.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_account_still_alerts_daily(tmp_path):
    ns = AsyncMock()
    service = PaperAccountExpiryAlertService(
        expires_at="2026.11.19",
        notification_service=ns,
        market_clock=_clock(datetime(2026, 11, 20, 8, 30)),
        state_file=str(tmp_path / "state.json"),
        logger=MagicMock(),
    )

    result = await service.check_and_notify(True, "50202454")

    assert result["alerted"] is True
    assert result["days_left"] == -1
    args, _ = ns.emit.await_args
    assert args[1] == NotificationLevel.ERROR
    assert args[2] == "모의투자 계좌 만료"
