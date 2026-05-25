"""Integration test: offline pre-deploy check end-to-end.

실제 `load_configs()` 가 정상 동작하는 환경에서 offline 모드로 점검 전체를
끝까지 돌릴 수 있어야 한다. live broker / WebSocket 호출 없이 정적 검사
(config, env consistency, latest trading date SKIPPED, event shadow)만 수행한다.
"""
from __future__ import annotations

import pytest

from config.config_loader import load_configs
from services.predeploy_check_service import (
    CheckStatus,
    PreDeployCheckService,
)


async def test_offline_predeploy_check_runs_to_completion(tmp_path):
    shadow_dir = tmp_path / "event_shadow"
    shadow_dir.mkdir()

    service = PreDeployCheckService(
        config_loader=load_configs,
        market_calendar_service=None,
        broker=None,
        websocket_probe=None,
        event_shadow_dir=str(shadow_dir),
    )

    summary = await service.run_all(offline=True)

    # 모든 점검이 끝까지 실행되어야 한다
    assert len(summary.results) == 7
    names = {r.name for r in summary.results}
    assert names == {
        "config_validation",
        "broker_env_consistency",
        "latest_trading_date",
        "event_shadow_status",
        "websocket_subscription_health",
        "account_snapshot_freshness",
        "api_budget_limiter",
    }

    # offline 모드에서 broker 의존 점검은 SKIPPED
    by_name = {r.name: r for r in summary.results}
    assert by_name["websocket_subscription_health"].status == CheckStatus.SKIPPED
    assert by_name["account_snapshot_freshness"].status == CheckStatus.SKIPPED
    assert by_name["latest_trading_date"].status == CheckStatus.SKIPPED

    # config 가 로드 가능한 환경이면 config_validation 은 PASS
    assert by_name["config_validation"].status == CheckStatus.PASS

    # env consistency 는 config 가 일관되면 PASS, 불일치면 FAIL — 어느 쪽이든
    # exception 없이 분류되어야 한다는 사실만 검증한다
    assert by_name["broker_env_consistency"].status in (
        CheckStatus.PASS,
        CheckStatus.FAIL,
    )


async def test_offline_predeploy_check_aggregates_failures(tmp_path, monkeypatch):
    """config_loader 가 예외를 던지면 config_validation 이 FAIL 이 되고
    summary.has_failure 가 True 가 되어야 한다."""

    def boom():
        raise ValueError("설정 파일 유효성 검사 실패: missing api_key")

    shadow_dir = tmp_path / "event_shadow"
    shadow_dir.mkdir()

    service = PreDeployCheckService(
        config_loader=boom,
        event_shadow_dir=str(shadow_dir),
    )

    summary = await service.run_all(offline=True)
    assert summary.has_failure is True
    by_name = {r.name: r for r in summary.results}
    assert by_name["config_validation"].status == CheckStatus.FAIL
