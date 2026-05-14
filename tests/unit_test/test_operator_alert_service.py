"""OperatorAlertService 단위 테스트.

TDD 기준:
- report NEW  → NotificationService.emit 1회 호출
- 동일 key 동일 severity 재호출 → emit 0회 (dedup)
- severity 상승 → ESCALATED emit 1회
- resolve → RESOLVED emit 1회
- state 파일 reload 후 active 셋 복원
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from common.operator_alert_types import AlertSource, AlertTransition
from services.operator_alert_service import OperatorAlertService


@pytest.fixture
def notification_service():
    svc = MagicMock()
    svc.emit = AsyncMock(return_value=None)
    return svc


@pytest.fixture
def market_clock():
    clk = MagicMock()
    return clk


@pytest.fixture
def state_file(tmp_path):
    return str(tmp_path / "operator_alert_state.json")


@pytest.fixture
def svc(notification_service, market_clock, state_file):
    return OperatorAlertService(
        notification_service=notification_service,
        market_clock=market_clock,
        state_file_path=state_file,
    )


# ── 신규 차단 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_report_new_emits_once(svc, notification_service):
    """최초 report → NEW 전이, emit 1회."""
    transition = await svc.report(
        AlertSource.KILL_SWITCH, "kill_switch:global",
        "critical", "Kill Switch 트립", "사유: 연속손실",
    )
    assert transition == AlertTransition.NEW
    notification_service.emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_report_new_returns_transition(svc):
    result = await svc.report(
        AlertSource.RISK_GATE, "risk_gate:max_order_amount:S1:005930",
        "block", "주문금액 초과", "최대 주문금액 한도 초과",
    )
    assert result == AlertTransition.NEW


# ── 중복 suppression ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_same_key_same_severity_no_emit(svc, notification_service):
    """동일 key, 동일 severity 재호출 → dedup, emit 0회 추가."""
    await svc.report(AlertSource.KILL_SWITCH, "kill_switch:global", "critical", "T", "M")

    notification_service.emit.reset_mock()
    result = await svc.report(AlertSource.KILL_SWITCH, "kill_switch:global", "critical", "T", "M")

    assert result is None
    notification_service.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_last_seen_updated_on_duplicate(svc):
    """중복 호출 시 active entry의 last_seen이 갱신된다."""
    await svc.report(AlertSource.KILL_SWITCH, "kill_switch:global", "critical", "T", "M")
    first_last_seen = svc._active["kill_switch:global"]["last_seen"]

    # 짧은 시간 후 재호출
    await svc.report(AlertSource.KILL_SWITCH, "kill_switch:global", "critical", "T", "M2")
    second_last_seen = svc._active["kill_switch:global"]["last_seen"]

    # last_seen >= first (같거나 이후)
    assert second_last_seen >= first_last_seen


# ── severity 상승 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_severity_escalation_emits(svc, notification_service):
    """severity 상승 시 ESCALATED emit."""
    await svc.report(AlertSource.KILL_SWITCH, "kill_switch:global", "warning", "T", "M")
    notification_service.emit.reset_mock()

    result = await svc.report(AlertSource.KILL_SWITCH, "kill_switch:global", "critical", "T2", "M2")
    assert result == AlertTransition.ESCALATED
    notification_service.emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_severity_decrease_no_emit(svc, notification_service):
    """severity 하강은 무시 — emit 없음."""
    await svc.report(AlertSource.KILL_SWITCH, "kill_switch:global", "critical", "T", "M")
    notification_service.emit.reset_mock()

    result = await svc.report(AlertSource.KILL_SWITCH, "kill_switch:global", "warning", "T", "M")
    assert result is None
    notification_service.emit.assert_not_awaited()


# ── resolve ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_emits_resolved(svc, notification_service):
    """resolve → RESOLVED emit 1회, active에서 제거."""
    await svc.report(AlertSource.KILL_SWITCH, "kill_switch:global", "critical", "T", "M")
    notification_service.emit.reset_mock()

    resolved = await svc.resolve(AlertSource.KILL_SWITCH, "kill_switch:global", "운영자 해제")
    assert resolved is True
    notification_service.emit.assert_awaited_once()
    # emit 인자에서 RESOLVED 전이 확인
    call_kwargs = notification_service.emit.call_args
    meta = call_kwargs.args[4] if len(call_kwargs.args) >= 5 else call_kwargs.kwargs.get("metadata", {})
    assert meta.get("transition") == AlertTransition.RESOLVED.value


@pytest.mark.asyncio
async def test_resolve_nonexistent_returns_false(svc):
    """active에 없는 key resolve → False."""
    result = await svc.resolve(AlertSource.KILL_SWITCH, "kill_switch:global")
    assert result is False


@pytest.mark.asyncio
async def test_key_removed_from_active_after_resolve(svc):
    await svc.report(AlertSource.KILL_SWITCH, "kill_switch:global", "critical", "T", "M")
    await svc.resolve(AlertSource.KILL_SWITCH, "kill_switch:global")
    assert "kill_switch:global" not in svc._active


# ── get_active_alerts / get_history ────────────────────────────────


@pytest.mark.asyncio
async def test_get_active_alerts(svc):
    await svc.report(AlertSource.KILL_SWITCH, "kill_switch:global", "critical", "T", "M")
    await svc.report(AlertSource.RISK_GATE, "risk_gate:daily_cap:S1:005930", "block", "T2", "M2")

    alerts = svc.get_active_alerts()
    keys = {a["dedup_key"] for a in alerts}
    assert "kill_switch:global" in keys
    assert "risk_gate:daily_cap:S1:005930" in keys


@pytest.mark.asyncio
async def test_get_history_newest_first(svc):
    await svc.report(AlertSource.KILL_SWITCH, "kill_switch:global", "critical", "T1", "M1")
    await svc.report(AlertSource.KILL_SWITCH, "kill_switch:strategy:S1", "critical", "T2", "M2")
    await svc.resolve(AlertSource.KILL_SWITCH, "kill_switch:global")

    history = svc.get_history()
    # 최신 순 — RESOLVED가 맨 앞
    assert history[0]["transition"] == AlertTransition.RESOLVED.value


# ── 상태 파일 영속화 / 복원 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_state_persisted_and_restored(notification_service, market_clock, state_file):
    """report 후 파일에 저장되고, 재생성한 인스턴스에서 active 복원."""
    svc1 = OperatorAlertService(notification_service, market_clock, state_file)
    await svc1.report(AlertSource.KILL_SWITCH, "kill_switch:global", "critical", "T", "M")
    assert Path(state_file).exists()

    # 새 인스턴스 — 파일에서 복원
    svc2 = OperatorAlertService(notification_service, market_clock, state_file)
    assert "kill_switch:global" in svc2._active
    assert svc2._active["kill_switch:global"]["severity"] == "critical"


@pytest.mark.asyncio
async def test_history_ring_buffer(notification_service, market_clock, state_file):
    """200건 초과 시 오래된 항목이 삭제된다."""
    from services.operator_alert_service import _MAX_HISTORY
    svc = OperatorAlertService(notification_service, market_clock, state_file)

    for i in range(_MAX_HISTORY + 10):
        key = f"risk_gate:rule_{i}:S1:code_{i}"
        await svc.report(AlertSource.RISK_GATE, key, "block", f"T{i}", f"M{i}")

    assert len(svc._history) <= _MAX_HISTORY


# ── emit metadata 검증 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emit_metadata_contains_transition_and_key(svc, notification_service):
    """emit 호출 시 metadata에 transition, dedup_key, source 포함."""
    await svc.report(
        AlertSource.KILL_SWITCH, "kill_switch:global",
        "critical", "Kill Switch", "사유",
        metadata={"foo": "bar"},
    )
    call_args = notification_service.emit.call_args
    # positional: category, level, title, message, metadata
    meta = call_args.args[4] if len(call_args.args) >= 5 else call_args.kwargs.get("metadata", {})
    assert meta["transition"] == AlertTransition.NEW.value
    assert meta["dedup_key"] == "kill_switch:global"
    assert meta["source"] == AlertSource.KILL_SWITCH.value
    assert meta["foo"] == "bar"


def test_strategy_perf_alert_source_value_is_stable():
    """전략 성과 저하 알림의 dedup source 값은 대문자 enum 컨벤션을 따른다."""
    assert AlertSource.STRATEGY_PERF.value == "STRATEGY_PERF"
