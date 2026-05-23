"""Unit tests for PreDeployCheckService."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.predeploy_check_service import (
    CheckResult,
    CheckStatus,
    PreDeployCheckService,
    PreDeployCheckSummary,
)


# ── fixtures ──────────────────────────────────────────────────────────


def _paper_cfg(**overrides):
    base = dict(
        is_paper_trading=True,
        stock_account_number="12345678-01",
        paper_api_key="paper-key",
        paper_url="https://openapivts.koreainvestment.com:29443",
        paper_websocket_url="ws://ops.koreainvestment.com:31000",
        api_key="real-key",
        url="https://openapi.koreainvestment.com:9443",
        websocket_url="ws://ops.koreainvestment.com:21000",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _real_cfg(**overrides):
    base = dict(
        is_paper_trading=False,
        stock_account_number="12345678-01",
        api_key="real-key",
        url="https://openapi.koreainvestment.com:9443",
        websocket_url="ws://ops.koreainvestment.com:21000",
        paper_api_key="paper-key",
        paper_url="https://openapivts.koreainvestment.com:29443",
        paper_websocket_url="ws://ops.koreainvestment.com:31000",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _service(**kwargs):
    defaults = dict(
        config_loader=lambda: _paper_cfg(),
        now_provider=lambda: datetime(2026, 5, 22, 10, 0, 0),
        time_provider=lambda: 0.0,
    )
    defaults.update(kwargs)
    return PreDeployCheckService(**defaults)


# ── check_config ──────────────────────────────────────────────────────


async def test_check_config_pass():
    svc = _service()
    result = await svc.check_config()
    assert result.status == CheckStatus.PASS
    assert "paper" in result.detail
    assert result.name == "config_validation"


async def test_check_config_warns_when_expected_hash_differs():
    svc = _service(expected_config_hash="expected123")
    result = await svc.check_config()
    assert result.status == CheckStatus.WARN
    assert "config_hash diff" in result.detail


async def test_check_config_passes_when_expected_hash_matches():
    cfg = _paper_cfg()
    from common.config_hashing import compute_config_hash

    svc = _service(
        config_loader=lambda: cfg,
        expected_config_hash=compute_config_hash(cfg),
    )
    result = await svc.check_config()
    assert result.status == CheckStatus.PASS


async def test_check_config_fail_on_loader_exception():
    def boom():
        raise ValueError("설정 파일 유효성 검사 실패: ...")

    svc = _service(config_loader=boom)
    result = await svc.check_config()
    assert result.status == CheckStatus.FAIL
    assert "예외 발생" in result.detail


# ── check_token_env_consistency ───────────────────────────────────────


async def test_env_consistency_paper_pass():
    svc = _service()
    await svc.check_config()  # cache config
    result = await svc.check_token_env_consistency()
    assert result.status == CheckStatus.PASS
    assert "paper" in result.detail


async def test_env_consistency_real_pass():
    svc = _service(config_loader=lambda: _real_cfg())
    await svc.check_config()
    result = await svc.check_token_env_consistency()
    assert result.status == CheckStatus.PASS
    assert "real" in result.detail


async def test_env_consistency_paper_with_real_url_fails():
    bad = _paper_cfg(paper_url="https://openapi.koreainvestment.com:9443")
    svc = _service(config_loader=lambda: bad)
    await svc.check_config()
    result = await svc.check_token_env_consistency()
    assert result.status == CheckStatus.FAIL
    assert "paper 호스트가 아님" in result.detail


async def test_env_consistency_real_with_paper_url_fails():
    bad = _real_cfg(url="https://openapivts.koreainvestment.com:29443")
    svc = _service(config_loader=lambda: bad)
    await svc.check_config()
    result = await svc.check_token_env_consistency()
    assert result.status == CheckStatus.FAIL
    assert "real 호스트가 아님" in result.detail


async def test_env_consistency_missing_account_fails():
    bad = _paper_cfg(stock_account_number=None)
    svc = _service(config_loader=lambda: bad)
    await svc.check_config()
    result = await svc.check_token_env_consistency()
    assert result.status == CheckStatus.FAIL
    assert "stock_account_number" in result.detail


async def test_env_consistency_missing_api_key_fails():
    bad = _paper_cfg(paper_api_key=None)
    svc = _service(config_loader=lambda: bad)
    await svc.check_config()
    result = await svc.check_token_env_consistency()
    assert result.status == CheckStatus.FAIL
    assert "api_key 누락" in result.detail


# ── check_latest_trading_date ─────────────────────────────────────────


async def test_latest_trading_date_skipped_without_mcs():
    svc = _service(market_calendar_service=None)
    result = await svc.check_latest_trading_date()
    assert result.status == CheckStatus.SKIPPED


async def test_latest_trading_date_today_pass():
    mcs = MagicMock()
    mcs.get_latest_trading_date = AsyncMock(return_value="20260522")
    svc = _service(market_calendar_service=mcs)
    result = await svc.check_latest_trading_date()
    assert result.status == CheckStatus.PASS
    assert "오늘" in result.detail


async def test_latest_trading_date_3days_ago_pass():
    mcs = MagicMock()
    mcs.get_latest_trading_date = AsyncMock(return_value="20260519")
    svc = _service(market_calendar_service=mcs)
    result = await svc.check_latest_trading_date()
    assert result.status == CheckStatus.PASS
    assert "3일 전" in result.detail


async def test_latest_trading_date_too_old_warn():
    mcs = MagicMock()
    mcs.get_latest_trading_date = AsyncMock(return_value="20260501")
    svc = _service(market_calendar_service=mcs)
    result = await svc.check_latest_trading_date()
    assert result.status == CheckStatus.WARN


async def test_latest_trading_date_none_fail():
    mcs = MagicMock()
    mcs.get_latest_trading_date = AsyncMock(return_value=None)
    svc = _service(market_calendar_service=mcs)
    result = await svc.check_latest_trading_date()
    assert result.status == CheckStatus.FAIL


async def test_latest_trading_date_bad_format_fail():
    mcs = MagicMock()
    mcs.get_latest_trading_date = AsyncMock(return_value="20260")
    svc = _service(market_calendar_service=mcs)
    result = await svc.check_latest_trading_date()
    assert result.status == CheckStatus.FAIL


# ── check_event_shadow ────────────────────────────────────────────────


async def test_event_shadow_missing_dir_warn(tmp_path):
    svc = _service(event_shadow_dir=str(tmp_path / "no_such_dir"))
    result = await svc.check_event_shadow()
    assert result.status == CheckStatus.WARN
    assert "디렉터리 없음" in result.detail


async def test_event_shadow_empty_dir_warn(tmp_path):
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    svc = _service(event_shadow_dir=str(shadow))
    result = await svc.check_event_shadow()
    assert result.status == CheckStatus.WARN
    assert "jsonl 로그 없음" in result.detail


async def test_event_shadow_recent_pass(tmp_path):
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    f = shadow / "20260522.jsonl"
    f.write_text("{}\n")
    svc = _service(
        event_shadow_dir=str(shadow),
        now_provider=lambda: datetime.fromtimestamp(f.stat().st_mtime) + timedelta(hours=2),
    )
    result = await svc.check_event_shadow()
    assert result.status == CheckStatus.PASS


async def test_event_shadow_stale_fail(tmp_path):
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    f = shadow / "20260101.jsonl"
    f.write_text("{}\n")
    svc = _service(
        event_shadow_dir=str(shadow),
        now_provider=lambda: datetime.fromtimestamp(f.stat().st_mtime) + timedelta(days=10),
    )
    result = await svc.check_event_shadow()
    assert result.status == CheckStatus.FAIL


# ── check_websocket_subscription ──────────────────────────────────────


async def test_websocket_skipped_in_offline():
    probe = AsyncMock(return_value={"connected": True, "last_tick_age_sec": 1.0, "subscriptions": 10})
    svc = _service(websocket_probe=probe)
    result = await svc.check_websocket_subscription(offline=True)
    assert result.status == CheckStatus.SKIPPED
    probe.assert_not_awaited()


async def test_websocket_skipped_without_probe():
    svc = _service(websocket_probe=None)
    result = await svc.check_websocket_subscription(offline=False)
    assert result.status == CheckStatus.SKIPPED


async def test_websocket_pass():
    probe = AsyncMock(return_value={"connected": True, "last_tick_age_sec": 1.0, "subscriptions": 10})
    svc = _service(websocket_probe=probe)
    result = await svc.check_websocket_subscription(offline=False)
    assert result.status == CheckStatus.PASS


async def test_websocket_disconnected_fail():
    probe = AsyncMock(return_value={"connected": False, "last_tick_age_sec": None, "subscriptions": 0})
    svc = _service(websocket_probe=probe)
    result = await svc.check_websocket_subscription(offline=False)
    assert result.status == CheckStatus.FAIL
    assert "미연결" in result.detail


async def test_websocket_stale_tick_fail():
    probe = AsyncMock(return_value={"connected": True, "last_tick_age_sec": 30.0, "subscriptions": 10})
    svc = _service(websocket_probe=probe)
    result = await svc.check_websocket_subscription(offline=False)
    assert result.status == CheckStatus.FAIL
    assert "마지막 수신" in result.detail


# ── check_account_snapshot ────────────────────────────────────────────


async def test_account_snapshot_skipped_offline():
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(return_value=SimpleNamespace(rt_cd="0"))
    svc = _service(broker=broker)
    result = await svc.check_account_snapshot(offline=True)
    assert result.status == CheckStatus.SKIPPED
    broker.get_account_balance.assert_not_awaited()


async def test_account_snapshot_skipped_without_broker():
    svc = _service(broker=None)
    result = await svc.check_account_snapshot(offline=False)
    assert result.status == CheckStatus.SKIPPED


async def test_account_snapshot_pass():
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(return_value=SimpleNamespace(rt_cd="0", msg1="OK"))
    svc = _service(broker=broker)
    result = await svc.check_account_snapshot(offline=False)
    assert result.status == CheckStatus.PASS


async def test_account_snapshot_api_error_fail():
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(return_value=SimpleNamespace(rt_cd="1", msg1="ERR"))
    svc = _service(broker=broker)
    result = await svc.check_account_snapshot(offline=False)
    assert result.status == CheckStatus.FAIL
    assert "rt_cd='1'" in result.detail


async def test_account_snapshot_slow_warn():
    # time_provider 가 첫 호출 0.0, 두 번째 호출 35.0 을 반환하도록 만든다.
    times = iter([0.0, 0.0, 35.0, 35.0])
    broker = MagicMock()
    broker.get_account_balance = AsyncMock(return_value=SimpleNamespace(rt_cd="0"))
    svc = _service(broker=broker, time_provider=lambda: next(times))
    result = await svc.check_account_snapshot(offline=False)
    assert result.status == CheckStatus.WARN


# ── run_all + summary ─────────────────────────────────────────────────


async def test_run_all_offline_skips_live_checks():
    mcs = MagicMock()
    mcs.get_latest_trading_date = AsyncMock(return_value="20260522")
    probe = AsyncMock()
    broker = MagicMock()
    broker.get_account_balance = AsyncMock()

    svc = _service(
        market_calendar_service=mcs,
        websocket_probe=probe,
        broker=broker,
    )

    summary = await svc.run_all(offline=True)
    assert isinstance(summary, PreDeployCheckSummary)
    assert len(summary.results) == 6
    probe.assert_not_awaited()
    broker.get_account_balance.assert_not_awaited()

    names = [r.name for r in summary.results]
    assert names == [
        "config_validation",
        "broker_env_consistency",
        "latest_trading_date",
        "event_shadow_status",
        "websocket_subscription_health",
        "account_snapshot_freshness",
    ]


async def test_run_all_aggregates_failures():
    mcs = MagicMock()
    mcs.get_latest_trading_date = AsyncMock(return_value=None)  # FAIL
    svc = _service(market_calendar_service=mcs)
    summary = await svc.run_all(offline=True)
    assert summary.has_failure is True


async def test_summary_counts():
    summary = PreDeployCheckSummary(
        results=[
            CheckResult("a", CheckStatus.PASS),
            CheckResult("b", CheckStatus.PASS),
            CheckResult("c", CheckStatus.FAIL),
            CheckResult("d", CheckStatus.SKIPPED),
            CheckResult("e", CheckStatus.WARN),
        ]
    )
    assert summary.counts == {"PASS": 2, "FAIL": 1, "SKIPPED": 1, "WARN": 1}
    assert summary.has_failure is True


async def test_summary_no_failure():
    summary = PreDeployCheckSummary(
        results=[
            CheckResult("a", CheckStatus.PASS),
            CheckResult("b", CheckStatus.WARN),
            CheckResult("c", CheckStatus.SKIPPED),
        ]
    )
    assert summary.has_failure is False
