"""StrategyIdentityResolver — P3-4 Phase 2 PR 2a contract lock.

Phase 1 에서 각 LiveStrategy 에 strategy_id (영문 stable) 와 name (한국어 display)
property 가 도입됐다. Phase 2 는 risk_gate/kill_switch/virtual_trade journal/config
사용처를 strategy_id 기준으로 마이그레이션한다.

Resolver 는 양방향 변환과 미지값 passthrough 를 담당한다. 이 테스트는
- Phase 1 의 10개 활성 전략 ID/display 잠금
- 미지값 passthrough 동작 잠금
을 책임진다.
"""
from __future__ import annotations

import pytest

from common.strategy_identity import (
    STRATEGY_DISPLAY_MAP,
    STRATEGY_IDENTITY_RESOLVER,
    StrategyIdentityResolver,
)


# Phase 1 잠금 (각 strategy 파일의 name/strategy_id property 기준)
PHASE1_PAIRS = [
    ("first_pullback", "첫눌림목"),
    ("high_tight_flag", "하이타이트플래그"),
    ("inverse_etf_regime", "인버스ETF레짐"),
    ("larry_williams_cb", "LarryWilliamsCB"),
    ("larry_williams_vbo", "래리윌리엄스VBO"),
    ("oneil_pocket_pivot", "오닐PP/BGU"),
    ("oneil_squeeze_breakout", "오닐스퀴즈돌파"),
    ("program_buy_follow", "프로그램매수추종"),
    ("rsi2_pullback", "RSI2눌림목"),
    ("traditional_volume_breakout", "거래량돌파(전통)"),
    ("volume_breakout_live", "거래량돌파"),
]


def test_phase1_map_locked_exact():
    """STRATEGY_DISPLAY_MAP 이 Phase 1 잠금 매핑과 정확히 일치한다."""
    assert STRATEGY_DISPLAY_MAP == {sid: name for sid, name in PHASE1_PAIRS}


def test_phase1_display_names_are_unique():
    """display name 중복이 없어야 역방향 lookup 이 결정적이다."""
    displays = [name for _, name in PHASE1_PAIRS]
    assert len(displays) == len(set(displays))


@pytest.mark.parametrize("strategy_id,display", PHASE1_PAIRS)
def test_to_id_korean_to_english(strategy_id, display):
    r = StrategyIdentityResolver()
    assert r.to_id(display) == strategy_id


@pytest.mark.parametrize("strategy_id,_display", PHASE1_PAIRS)
def test_to_id_already_id_passthrough(strategy_id, _display):
    r = StrategyIdentityResolver()
    assert r.to_id(strategy_id) == strategy_id


@pytest.mark.parametrize("strategy_id,display", PHASE1_PAIRS)
def test_to_display_id_to_korean(strategy_id, display):
    r = StrategyIdentityResolver()
    assert r.to_display(strategy_id) == display


@pytest.mark.parametrize("strategy_id,display", PHASE1_PAIRS)
def test_to_display_already_display_passthrough(strategy_id, display):
    r = StrategyIdentityResolver()
    assert r.to_display(display) == display


def test_to_id_unknown_passthrough():
    r = StrategyIdentityResolver()
    assert r.to_id("unknown_strategy_xyz") == "unknown_strategy_xyz"
    assert r.to_id("알수없는전략") == "알수없는전략"


def test_to_display_unknown_passthrough():
    r = StrategyIdentityResolver()
    assert r.to_display("unknown_strategy_xyz") == "unknown_strategy_xyz"
    assert r.to_display("알수없는전략") == "알수없는전략"


def test_to_id_empty_or_none():
    r = StrategyIdentityResolver()
    assert r.to_id("") == ""
    assert r.to_id(None) == ""


def test_to_display_empty_or_none():
    r = StrategyIdentityResolver()
    assert r.to_display("") == ""
    assert r.to_display(None) == ""


@pytest.mark.parametrize("strategy_id,display", PHASE1_PAIRS)
def test_round_trip_id(strategy_id, display):
    """id → display → id 가 원래 id"""
    r = StrategyIdentityResolver()
    assert r.to_id(r.to_display(strategy_id)) == strategy_id


@pytest.mark.parametrize("strategy_id,display", PHASE1_PAIRS)
def test_round_trip_display(strategy_id, display):
    """display → id → display 가 원래 display"""
    r = StrategyIdentityResolver()
    assert r.to_display(r.to_id(display)) == display


@pytest.mark.parametrize("strategy_id,_display", PHASE1_PAIRS)
def test_is_known_id_true_for_phase1(strategy_id, _display):
    r = StrategyIdentityResolver()
    assert r.is_known_id(strategy_id) is True


def test_is_known_id_false_for_display_name():
    """display 이름은 id 가 아니므로 is_known_id False."""
    r = StrategyIdentityResolver()
    assert r.is_known_id("거래량돌파") is False


def test_is_known_id_false_for_unknown():
    r = StrategyIdentityResolver()
    assert r.is_known_id("unknown_xyz") is False
    assert r.is_known_id("") is False


def test_module_level_singleton_exists():
    """consumer 가 import 할 module-level 인스턴스가 존재한다."""
    assert isinstance(STRATEGY_IDENTITY_RESOLVER, StrategyIdentityResolver)
