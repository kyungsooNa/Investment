"""KillSwitchService — P3-4 Phase 2 PR 2b strategy_id compat layer.

Phase 2b 의 첫 단계: KillSwitch 의 in-memory state (_strategy_tripped /
_strategy_consecutive_losses / _strategy_daily_loss_won) 와 JSON state file
의 key 를 strategy_id 로 정규화한다.

- 모든 public 메서드는 입력 strategy_name 을 strategy_id 로 정규화
- _load_state 는 legacy 한국어 키를 strategy_id 로 마이그레이션 (one-time on load)
- _get_strategy_limit 은 config 의 한국어/id 키 모두 dual-key lookup
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from config.config_loader import (
    KillSwitchConfig,
    RiskGateConfig,
    RiskGateStrategyLimitConfig,
)
from services.kill_switch_service import KillSwitchService


@pytest.fixture
def cfg(tmp_path):
    return KillSwitchConfig(
        enabled=True,
        daily_loss_threshold_won=1_000_000,
        daily_loss_threshold_pct=5.0,
        max_consecutive_losses=3,
        max_consecutive_api_errors=5,
        abnormal_fill_deviation_pct=3.0,
        state_file_path=str(tmp_path / "ks_state.json"),
    )


@pytest.fixture
def mock_notif():
    svc = MagicMock()
    svc.emit = AsyncMock()
    return svc


@pytest.fixture
def logger():
    return logging.getLogger("test_kill_switch_strategy_id")


def _make_ks(cfg, mock_notif, logger, risk_gate_config=None):
    return KillSwitchService(
        cfg, mock_notif, logger, risk_gate_config=risk_gate_config
    )


# ───────────── trip / reset / record 입력 정규화 ─────────────


async def test_trip_strategy_with_korean_input_stores_strategy_id_key(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    await ks.trip_strategy("거래량돌파", reason="테스트")
    assert "volume_breakout_live" in ks._strategy_tripped
    assert "거래량돌파" not in ks._strategy_tripped


async def test_trip_strategy_with_id_input_stores_strategy_id_key(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    await ks.trip_strategy("volume_breakout_live", reason="테스트")
    assert "volume_breakout_live" in ks._strategy_tripped


async def test_is_strategy_tripped_finds_state_with_either_input(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    await ks.trip_strategy("거래량돌파", reason="테스트")
    # id query
    assert ks.is_strategy_tripped("volume_breakout_live") is not None
    # display query
    assert ks.is_strategy_tripped("거래량돌파") is not None


async def test_reset_strategy_clears_state_with_korean_input(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    await ks.trip_strategy("volume_breakout_live", reason="테스트")
    ks._strategy_consecutive_losses["volume_breakout_live"] = 5
    ks._strategy_daily_loss_won["volume_breakout_live"] = -100000

    await ks.reset_strategy("거래량돌파", operator="op")

    assert "volume_breakout_live" not in ks._strategy_tripped
    assert "volume_breakout_live" not in ks._strategy_consecutive_losses
    assert "volume_breakout_live" not in ks._strategy_daily_loss_won


async def test_record_strategy_trade_result_normalizes_input(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    await ks.record_strategy_trade_result("거래량돌파", pnl_won=-50000)
    assert ks._strategy_consecutive_losses.get("volume_breakout_live") == 1
    assert ks._strategy_daily_loss_won.get("volume_breakout_live") == -50000
    # 한국어 키는 없어야 함
    assert "거래량돌파" not in ks._strategy_consecutive_losses


async def test_record_strategy_trade_result_resets_consec_on_win(cfg, mock_notif, logger):
    ks = _make_ks(cfg, mock_notif, logger)
    await ks.record_strategy_trade_result("거래량돌파", pnl_won=-50000)
    await ks.record_strategy_trade_result("volume_breakout_live", pnl_won=30000)
    assert ks._strategy_consecutive_losses.get("volume_breakout_live") == 0


# ───────────── _load_state 마이그레이션 ─────────────


def test_load_state_migrates_korean_keys_to_strategy_id(cfg, mock_notif, logger):
    state_path = Path(cfg.state_file_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_state = {
        "is_tripped": False,
        "strategy_tripped": {
            "거래량돌파": {
                "strategy_name": "거래량돌파",
                "trip_reason": "legacy",
                "trip_timestamp": "2026-05-22T10:00:00+09:00",
            }
        },
        "strategy_consecutive_losses": {"거래량돌파": 2, "하이타이트플래그": 1},
        "strategy_daily_loss_won": {"거래량돌파": -75000},
    }
    state_path.write_text(json.dumps(legacy_state, ensure_ascii=False), encoding="utf-8")

    ks = _make_ks(cfg, mock_notif, logger)

    # 로드 후 in-memory 는 모두 strategy_id 키
    assert "volume_breakout_live" in ks._strategy_tripped
    assert "거래량돌파" not in ks._strategy_tripped
    assert ks._strategy_consecutive_losses.get("volume_breakout_live") == 2
    assert ks._strategy_consecutive_losses.get("high_tight_flag") == 1
    assert ks._strategy_daily_loss_won.get("volume_breakout_live") == -75000


def test_load_state_preserves_unknown_strategy_keys(cfg, mock_notif, logger):
    state_path = Path(cfg.state_file_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_state = {
        "is_tripped": False,
        "strategy_tripped": {},
        "strategy_consecutive_losses": {"custom_research_001": 3},
        "strategy_daily_loss_won": {},
    }
    state_path.write_text(json.dumps(legacy_state, ensure_ascii=False), encoding="utf-8")

    ks = _make_ks(cfg, mock_notif, logger)

    # 미지값은 그대로 passthrough
    assert ks._strategy_consecutive_losses.get("custom_research_001") == 3


# ───────────── _get_strategy_limit dual-key config ─────────────


def test_get_strategy_limit_finds_korean_config_with_id_query(cfg, mock_notif, logger):
    custom_limit = RiskGateStrategyLimitConfig(max_consecutive_losses_for_kill=2)
    rg_cfg = RiskGateConfig(strategy_limits={"거래량돌파": custom_limit})
    ks = _make_ks(cfg, mock_notif, logger, risk_gate_config=rg_cfg)
    limit = ks._get_strategy_limit("volume_breakout_live")
    assert limit is not None
    assert limit.max_consecutive_losses_for_kill == 2


def test_get_strategy_limit_finds_id_config_with_korean_query(cfg, mock_notif, logger):
    custom_limit = RiskGateStrategyLimitConfig(max_consecutive_losses_for_kill=2)
    rg_cfg = RiskGateConfig(strategy_limits={"volume_breakout_live": custom_limit})
    ks = _make_ks(cfg, mock_notif, logger, risk_gate_config=rg_cfg)
    limit = ks._get_strategy_limit("거래량돌파")
    assert limit is not None
    assert limit.max_consecutive_losses_for_kill == 2


def test_get_strategy_limit_falls_back_to_default(cfg, mock_notif, logger):
    default = RiskGateStrategyLimitConfig(max_consecutive_losses_for_kill=5)
    rg_cfg = RiskGateConfig(default_strategy_limit=default)
    ks = _make_ks(cfg, mock_notif, logger, risk_gate_config=rg_cfg)
    limit = ks._get_strategy_limit("custom_xyz")
    assert limit is default
