"""STRATEGY_STATUS_MAP — P3-4 활성/실험/레거시 registry (1차 대안).

Phase 1 매핑 잠금된 10개 전략을 status 별로 분류한다:
- StrategyFactory.build() 가 자동 register 하는 7개 = ACTIVE
- 수동/백테스트 용도로 코드는 있으나 자동 register 안 되는 3개 = EXPERIMENTAL
- 1차에서는 LEGACY 없음 (디렉터리 이동 없이 metadata 만 추가)

미지 strategy_id 는 UNKNOWN.
"""
from __future__ import annotations

import pytest

from common.strategy_identity import (
    STRATEGY_DISPLAY_MAP,
    STRATEGY_IDENTITY_RESOLVER,
    STRATEGY_STATUS_MAP,
    StrategyStatus,
)


ACTIVE_IDS = {
    "first_pullback",
    "high_tight_flag",
    "larry_williams_cb",
    "larry_williams_vbo",
    "oneil_pocket_pivot",
    "oneil_squeeze_breakout",
    "rsi2_pullback",
}
EXPERIMENTAL_IDS = {
    "program_buy_follow",
    "traditional_volume_breakout",
    "volume_breakout_live",
}


def test_status_enum_has_expected_members():
    assert StrategyStatus.ACTIVE
    assert StrategyStatus.EXPERIMENTAL
    assert StrategyStatus.LEGACY
    assert StrategyStatus.UNKNOWN


def test_status_map_covers_all_phase1_strategies():
    """Phase 1 매핑 잠금된 10개 모두 status 가 지정되어야 한다."""
    assert set(STRATEGY_STATUS_MAP.keys()) == set(STRATEGY_DISPLAY_MAP.keys())


def test_status_map_active_seven_strategies():
    actual_active = {
        sid for sid, status in STRATEGY_STATUS_MAP.items()
        if status == StrategyStatus.ACTIVE
    }
    assert actual_active == ACTIVE_IDS


def test_status_map_experimental_three_strategies():
    actual_exp = {
        sid for sid, status in STRATEGY_STATUS_MAP.items()
        if status == StrategyStatus.EXPERIMENTAL
    }
    assert actual_exp == EXPERIMENTAL_IDS


def test_no_overlap_between_active_and_experimental():
    assert ACTIVE_IDS.isdisjoint(EXPERIMENTAL_IDS)


@pytest.mark.parametrize("sid", sorted(ACTIVE_IDS))
def test_resolver_get_status_active(sid):
    assert STRATEGY_IDENTITY_RESOLVER.get_status(sid) == StrategyStatus.ACTIVE


@pytest.mark.parametrize("sid", sorted(EXPERIMENTAL_IDS))
def test_resolver_get_status_experimental(sid):
    assert STRATEGY_IDENTITY_RESOLVER.get_status(sid) == StrategyStatus.EXPERIMENTAL


def test_resolver_get_status_unknown_returns_unknown():
    assert STRATEGY_IDENTITY_RESOLVER.get_status("custom_research_001") == StrategyStatus.UNKNOWN
    assert STRATEGY_IDENTITY_RESOLVER.get_status("") == StrategyStatus.UNKNOWN
    assert STRATEGY_IDENTITY_RESOLVER.get_status(None) == StrategyStatus.UNKNOWN


def test_resolver_get_status_accepts_display_name():
    """display 입력도 받아서 내부적으로 strategy_id 로 정규화 후 status 반환."""
    assert STRATEGY_IDENTITY_RESOLVER.get_status("거래량돌파") == StrategyStatus.EXPERIMENTAL
    assert STRATEGY_IDENTITY_RESOLVER.get_status("첫눌림목") == StrategyStatus.ACTIVE
