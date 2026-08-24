from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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


def _service(tmp_path, **overrides):
    kwargs = dict(
        expires_at="2026-11-19",
        warning_days=7,
        notification_service=AsyncMock(),
        market_clock=_clock(datetime(2026, 11, 12, 8, 30)),
        state_file=str(tmp_path / "state.json"),
        logger=MagicMock(),
    )
    kwargs.update(overrides)
    return PaperAccountExpiryAlertService(**kwargs)


@pytest.mark.asyncio
async def test_missing_expiry_config_reports_a_reason(tmp_path):
    service = _service(tmp_path, expires_at=None)

    result = await service.check_and_notify(is_paper_trading=True, account_number="50202454")

    assert result == {"alerted": False, "reason": "expiry_not_configured"}


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["notification_service", "market_clock"])
async def test_missing_notification_dependencies_report_a_reason(tmp_path, missing):
    service = _service(tmp_path, **{missing: None})

    result = await service.check_and_notify(is_paper_trading=True, account_number="50202454")

    assert result == {"alerted": False, "reason": "notification_dependencies_missing"}


@pytest.mark.asyncio
async def test_message_says_expires_today_on_the_expiry_date(tmp_path):
    ns = AsyncMock()
    service = _service(
        tmp_path,
        notification_service=ns,
        market_clock=_clock(datetime(2026, 11, 19, 8, 30)),
    )

    await service.check_and_notify(is_paper_trading=True, account_number="50202454")

    assert "오늘 만료" in ns.emit.await_args.args[3]


def test_state_load_absorbs_broken_files(tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text("{not json", encoding="utf-8")
    service = _service(tmp_path, state_file=str(state_file))

    assert service._load_state() == {}
    service._logger.warning.assert_called_once()


def test_state_load_ignores_non_dict_payloads(tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text("[1, 2]", encoding="utf-8")
    service = _service(tmp_path, state_file=str(state_file))

    assert service._load_state() == {}


def test_state_save_failure_is_logged(tmp_path):
    service = _service(tmp_path, state_file=str(tmp_path / "nested" / "state.json"))

    with patch(
        "services.paper_account_expiry_alert_service.os.makedirs",
        side_effect=OSError("권한 없음"),
    ):
        service._save_state(date(2026, 11, 12), "50202454")

    service._logger.warning.assert_called_once()


@pytest.mark.parametrize("raw", ["2026-11-19", "2026/11/19", "2026.11.19"])
def test_expiry_date_accepts_the_three_supported_formats(raw):
    assert PaperAccountExpiryAlertService._parse_date(raw) == date(2026, 11, 19)


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_expiry_date_is_none_for_blank_values(raw):
    assert PaperAccountExpiryAlertService._parse_date(raw) is None


def test_expiry_date_rejects_unsupported_formats():
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        PaperAccountExpiryAlertService._parse_date("19-11-2026")


@pytest.mark.parametrize(
    "account, expected", [("50202454", "50***54"), ("1234", "***"), ("", "***")]
)
def test_account_masking(account, expected):
    assert PaperAccountExpiryAlertService._mask_account(account) == expected
