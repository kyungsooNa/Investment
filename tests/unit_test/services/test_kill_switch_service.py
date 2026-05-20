import json
import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytz

from config.config_loader import KillSwitchConfig
from services.kill_switch_service import KillSwitchService

KST = pytz.timezone("Asia/Seoul")


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def cfg():
    return KillSwitchConfig(
        enabled=True,
        daily_loss_threshold_won=1_000_000,
        daily_loss_threshold_pct=5.0,
        max_consecutive_losses=3,
        max_consecutive_api_errors=5,
        abnormal_fill_deviation_pct=3.0,
        state_file_path="data/test_kill_switch_state.json",
    )


@pytest.fixture
def mock_notif():
    svc = MagicMock()
    svc.emit = AsyncMock()
    return svc


@pytest.fixture
def logger():
    return logging.getLogger("test_kill_switch")


def _make_ks(cfg, mock_notif, logger, tmp_path=None):
    """state_file_path를 tmp_path로 덮어쓴 KillSwitchService 생성 헬퍼."""
    if tmp_path is not None:
        cfg = cfg.model_copy(update={"state_file_path": str(tmp_path / "ks_state.json")})
    with patch("services.kill_switch_service.Path.exists", return_value=False):
        return KillSwitchService(cfg, mock_notif, logger)


# ── enabled=False 바이패스 ────────────────────────────────────────────


def test_kill_switch_config_defaults_to_disabled():
    """개발 중 안전 기본값은 Kill Switch 비활성화."""
    cfg = KillSwitchConfig()

    assert cfg.enabled is False


async def test_disabled_orders_always_allowed(cfg, mock_notif, logger):
    cfg = cfg.model_copy(update={"enabled": False})
    ks = _make_ks(cfg, mock_notif, logger)
    allowed, reason = await ks.check_orders_allowed()
    assert allowed is True
    assert reason is None


async def test_disabled_strategies_always_allowed(cfg, mock_notif, logger):
    cfg = cfg.model_copy(update={"enabled": False})
    ks = _make_ks(cfg, mock_notif, logger)
    allowed, reason = await ks.check_strategies_allowed()
    assert allowed is True
    assert reason is None


async def test_disabled_record_trade_does_not_trip(cfg, mock_notif, logger):
    cfg = cfg.model_copy(update={"enabled": False})
    ks = _make_ks(cfg, mock_notif, logger)
    for _ in range(10):
        await ks.record_trade_result(-100_000, "005930", "test")
    assert ks._is_tripped is False


# ── 정상 상태 ─────────────────────────────────────────────────────────


async def test_not_tripped_orders_allowed(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    allowed, reason = await ks.check_orders_allowed()
    assert allowed is True
    assert reason is None


async def test_not_tripped_strategies_allowed(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    allowed, reason = await ks.check_strategies_allowed()
    assert allowed is True
    assert reason is None


# ── 연속 손실 트립 ────────────────────────────────────────────────────


async def test_consecutive_losses_trip(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    for i in range(cfg.max_consecutive_losses):
        await ks.record_trade_result(-10_000, "005930", "momentum")

    assert ks._is_tripped is True
    assert "연속 손실" in ks._trip_reason
    mock_notif.emit.assert_awaited_once()


async def test_profit_resets_consecutive_counter(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    await ks.record_trade_result(-10_000, "005930", "momentum")
    await ks.record_trade_result(5_000, "005930", "momentum")  # 이익 → 초기화
    assert ks._consecutive_losses == 0
    assert ks._is_tripped is False


async def test_overnight_loss_does_not_increment_consecutive_counter_but_counts_daily_loss(cfg, mock_notif, logger):
    """전일 보유분 청산 손실은 일손실에는 반영하되 전역 연속손실 카운터에서는 제외한다."""
    ks = _make_ks(cfg, mock_notif, logger)

    for _ in range(cfg.max_consecutive_losses):
        await ks.record_trade_result(
            -10_000,
            "005930",
            "momentum",
            count_for_consecutive_loss=False,
        )

    assert ks._consecutive_losses == 0
    assert ks._daily_realized_loss_won == -30_000
    assert ks._is_tripped is False


# ── 일손실 한도 트립 ──────────────────────────────────────────────────


async def test_daily_loss_won_threshold_trips(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    # max_consecutive_losses 미만으로 유지하면서 일손실 초과
    await ks.record_trade_result(-500_000, "005930", "momentum")
    await ks.record_trade_result(-600_000, "000660", "momentum")  # 총 -1,100,000

    assert ks._is_tripped is True
    assert "일손실" in ks._trip_reason


async def test_daily_loss_pct_threshold_trips(cfg, mock_notif, logger):
    cfg = cfg.model_copy(update={
        "daily_loss_threshold_won": 999_999_999,  # 원화 한도는 사실상 무한
        "daily_loss_threshold_pct": 5.0,
        "max_consecutive_losses": 999,
    })
    ks = _make_ks(cfg, mock_notif, logger)
    # 잔고 1,000,000원에서 5% = 50,000원 초과
    await ks.record_trade_result(-60_000, "005930", "momentum", account_balance_won=1_000_000)

    assert ks._is_tripped is True
    assert "%" in ks._trip_reason


# ── API 오류 트립 ─────────────────────────────────────────────────────


async def test_api_failures_trip(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    for i in range(cfg.max_consecutive_api_errors):
        await ks.record_api_failure("HTTP 500")

    assert ks._is_tripped is True
    assert "API 오류" in ks._trip_reason


async def test_api_success_resets_error_counter(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    await ks.record_api_failure("HTTP 500")
    await ks.record_api_failure("HTTP 500")
    await ks.record_api_success()
    assert ks._consecutive_api_errors == 0
    assert ks._is_tripped is False


# ── 체결 이상 트립 ────────────────────────────────────────────────────


async def test_fill_deviation_trips(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    # 10,000원 주문 → 10,400원 체결 (4% 이탈, 한도 3%)
    await ks.record_fill_event(order_price=10_000, fill_price=10_400, code="005930", qty=10)

    assert ks._is_tripped is True
    assert "비정상 체결" in ks._trip_reason


async def test_favorable_buy_fill_deviation_does_not_trip(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    # 매수 주문가보다 낮은 체결은 유리한 체결이므로 계좌 Kill Switch를 트립하지 않는다.
    await ks.record_fill_event(
        order_price=10_000,
        fill_price=9_500,
        code="005930",
        qty=10,
        side="BUY",
    )

    assert ks._is_tripped is False
    mock_notif.emit.assert_not_awaited()


async def test_adverse_sell_fill_deviation_trips(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    await ks.record_fill_event(
        order_price=10_000,
        fill_price=9_500,
        code="005930",
        qty=10,
        side="SELL",
    )

    assert ks._is_tripped is True
    assert "비정상 체결" in ks._trip_reason


async def test_fill_within_tolerance_does_not_trip(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    # 10,000원 주문 → 10,200원 체결 (2% 이탈, 한도 3%)
    await ks.record_fill_event(order_price=10_000, fill_price=10_200, code="005930", qty=10)

    assert ks._is_tripped is False


# ── 수동 제어 ─────────────────────────────────────────────────────────


async def test_manual_trip(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    await ks.manual_trip("테스트용 수동 트립", "operator1")

    assert ks._is_tripped is True
    assert "operator1" in ks._trip_reason
    mock_notif.emit.assert_awaited_once()


async def test_manual_reset(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    await ks.manual_trip("테스트", "op")
    mock_notif.emit.reset_mock()

    await ks.manual_reset("op")

    assert ks._is_tripped is False
    assert ks._trip_reason is None
    mock_notif.emit.assert_awaited_once()


async def test_manual_reset_when_not_tripped_is_noop(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    await ks.manual_reset("op")
    mock_notif.emit.assert_not_awaited()


# ── 일별 카운터 초기화 ────────────────────────────────────────────────


async def test_reset_daily_counters_clears_loss_not_trip(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    await ks.manual_trip("테스트", "op")
    ks._daily_realized_loss_won = -500_000
    ks._consecutive_losses = 2

    await ks.reset_daily_counters()

    assert ks._daily_realized_loss_won == 0
    assert ks._consecutive_losses == 0
    assert ks._is_tripped is True  # trip 상태는 유지


# ── 주문/전략 차단 확인 ───────────────────────────────────────────────


async def test_tripped_blocks_orders(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    await ks.manual_trip("테스트", "op")

    allowed, reason = await ks.check_orders_allowed()
    assert allowed is False
    assert reason is not None


async def test_tripped_blocks_strategies(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    await ks.manual_trip("테스트", "op")

    allowed, reason = await ks.check_strategies_allowed()
    assert allowed is False
    assert reason is not None


async def test_notify_only_trip_does_not_block_orders_or_strategies(cfg, mock_notif, logger):
    """notify_only=True이면 트립 상태/알림은 유지하되 주문과 전략 실행은 차단하지 않는다."""
    cfg = cfg.model_copy(update={"notify_only": True})
    ks = _make_ks(cfg, mock_notif, logger)

    await ks.manual_trip("테스트", "op")

    order_allowed, order_reason = await ks.check_orders_allowed()
    strategy_allowed, strategy_reason = await ks.check_strategies_allowed()

    assert ks._is_tripped is True
    assert order_allowed is True
    assert order_reason is None
    assert strategy_allowed is True
    assert strategy_reason is None
    mock_notif.emit.assert_awaited_once()


# ── get_status ────────────────────────────────────────────────────────


async def test_get_status_shape(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    status = ks.get_status()

    assert "is_tripped" in status
    assert "trip_reason" in status
    assert "thresholds" in status
    assert status["thresholds"]["max_consecutive_losses"] == cfg.max_consecutive_losses


# ── 알림 동작 (operator_alert_service 미주입 시 직접 emit) ──────────────


async def test_trip_emits_directly_when_no_operator_alert_service(cfg, mock_notif, logger):
    """operator_alert_service가 없으면 trip 시 notification_service.emit 직접 호출."""
    ks = _make_ks(cfg, mock_notif, logger)
    await ks._trip("첫 트립", {})
    mock_notif.emit.assert_awaited_once()


async def test_second_trip_also_emits_when_no_operator_alert_service(cfg, mock_notif, logger):
    """operator_alert_service 미주입 시 dedup 없음 — 두 번 trip 모두 emit.

    dedup 로직은 OperatorAlertService에 위임(test_operator_alert_service.py 참조).
    """
    ks = _make_ks(cfg, mock_notif, logger)
    await ks._trip("첫 트립", {})
    await ks._trip("두 번째 트립", {})
    assert mock_notif.emit.await_count == 2


# ── JSON 상태 영속 ────────────────────────────────────────────────────


async def test_state_persistence_round_trip(cfg, mock_notif, logger, tmp_path):
    ks = _make_ks(cfg, mock_notif, logger, tmp_path)
    await ks.manual_trip("영속 테스트", "op")

    state_file = tmp_path / "ks_state.json"
    assert state_file.exists()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["is_tripped"] is True
    assert data["trip_reason"] is not None


async def test_state_load_restores_trip(cfg, mock_notif, logger, tmp_path):
    state_file = tmp_path / "ks_state.json"
    state_file.write_text(json.dumps({
        "is_tripped": True,
        "trip_reason": "복원 테스트",
        "trip_timestamp": "2026-01-01T10:00:00+09:00",
        "trip_metadata": {},
        "consecutive_losses": 2,
        "consecutive_api_errors": 3,
        "daily_realized_loss_won": -200_000,
    }))

    cfg2 = cfg.model_copy(update={"state_file_path": str(state_file)})
    ks = KillSwitchService(cfg2, mock_notif, logger)

    assert ks._is_tripped is True
    assert ks._trip_reason == "복원 테스트"
    assert ks._consecutive_losses == 2
    assert ks._daily_realized_loss_won == -200_000


async def test_state_load_skips_persisted_trip_when_disabled(cfg, mock_notif, logger, tmp_path):
    state_file = tmp_path / "ks_state.json"
    state_file.write_text(json.dumps({
        "is_tripped": True,
        "trip_reason": "비활성화 시 복원하지 않음",
        "trip_timestamp": "2026-01-01T10:00:00+09:00",
        "trip_metadata": {},
        "consecutive_losses": 2,
        "consecutive_api_errors": 3,
        "daily_realized_loss_won": -200_000,
    }))

    cfg2 = cfg.model_copy(update={"enabled": False, "state_file_path": str(state_file)})
    ks = KillSwitchService(cfg2, mock_notif, logger)

    assert ks._is_tripped is False
    assert ks._trip_reason is None
    assert ks._consecutive_losses == 0
    assert ks._daily_realized_loss_won == 0


async def test_state_load_missing_file_is_noop(cfg, mock_notif, logger, tmp_path):
    cfg2 = cfg.model_copy(update={"state_file_path": str(tmp_path / "nonexistent.json")})
    ks = KillSwitchService(cfg2, mock_notif, logger)
    assert ks._is_tripped is False
