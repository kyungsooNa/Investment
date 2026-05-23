"""P3-4 Phase 1: strategy_id / display_name 분리 — contract 테스트.

검증 목표:
- 모든 활성 라이브 전략 클래스에 안정적인 영문 snake_case `strategy_id` 가 지정되어 있다.
- 각 전략의 `strategy_id` 는 서로 unique 하다 (consumer 가 식별자로 안전하게 사용 가능).
- `LiveStrategy` 기본 구현은 `name` 으로 fallback 한다 (오버라이드를 잊은 전략에서도 동작 유지).

본 테스트는 ID 가 한번 정해진 뒤 누군가가 무심코 변경하지 않도록 묶어 두는 잠금 장치이기도 하다.
"""
from __future__ import annotations

import re
from typing import List, Optional, Type

import pytest

from common.types import TradeSignal
from interfaces.live_strategy import LiveStrategy


# Snake case: 소문자 + 숫자 + 밑줄, 첫 글자는 소문자.
_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


# 활성 라이브 전략 클래스 + 기대 ID. 새 전략 추가 시 이 표를 업데이트해 잠금.
_ACTIVE_STRATEGIES: List[tuple] = [
    ("strategies.first_pullback_strategy", "FirstPullbackStrategy", "first_pullback"),
    ("strategies.high_tight_flag_strategy", "HighTightFlagStrategy", "high_tight_flag"),
    (
        "strategies.larry_williams_channel_breakout_strategy",
        "LarryWilliamsChannelBreakoutStrategy",
        "larry_williams_cb",
    ),
    ("strategies.larry_williams_vbo_strategy", "LarryWilliamsVBOStrategy", "larry_williams_vbo"),
    ("strategies.oneil_pocket_pivot_strategy", "OneilPocketPivotStrategy", "oneil_pocket_pivot"),
    ("strategies.oneil_squeeze_breakout_strategy", "OneilSqueezeBreakoutStrategy", "oneil_squeeze_breakout"),
    ("strategies.program_buy_follow_strategy", "ProgramBuyFollowStrategy", "program_buy_follow"),
    ("strategies.rsi2_pullback_strategy", "RSI2PullbackStrategy", "rsi2_pullback"),
    (
        "strategies.traditional_volume_breakout_strategy",
        "TraditionalVolumeBreakoutStrategy",
        "traditional_volume_breakout",
    ),
    ("strategies.volume_breakout_live_strategy", "VolumeBreakoutLiveStrategy", "volume_breakout_live"),
]


def _resolve_class(module_path: str, class_name: str) -> Type[LiveStrategy]:
    module = __import__(module_path, fromlist=[class_name])
    return getattr(module, class_name)


@pytest.mark.parametrize("module_path,class_name,expected_id", _ACTIVE_STRATEGIES)
def test_active_strategy_id_matches_expected(module_path: str, class_name: str, expected_id: str) -> None:
    """각 활성 전략이 기대 ID 를 그대로 노출하는지. ID 가 우발적으로 변경되면 실패."""
    cls = _resolve_class(module_path, class_name)
    # property 를 instance 없이 호출하기 위해 mock instance 를 만들기보다는
    # 인스턴스를 생성하지 않고도 검증 가능한 방식이 필요하다.
    # 모든 활성 전략은 `strategy_id` 를 단순 상수 반환으로 구현하므로
    # property 객체를 통해 직접 값을 얻을 수 있다.
    strategy_id_prop = cls.__dict__.get("strategy_id")
    assert strategy_id_prop is not None, f"{class_name} 에 strategy_id property 가 정의되어 있어야 한다"
    assert isinstance(strategy_id_prop, property), f"{class_name}.strategy_id 는 property 여야 한다"
    # fget 호출: self 를 None 으로 넘겨도 단순 상수 반환이라 동작한다.
    value = strategy_id_prop.fget(None)
    assert value == expected_id


def test_active_strategy_ids_are_snake_case() -> None:
    for _, class_name, sid in _ACTIVE_STRATEGIES:
        assert _SNAKE_CASE_RE.match(sid), (
            f"{class_name}.strategy_id={sid!r} 는 snake_case 가 아니다 (소문자/숫자/밑줄만 허용)"
        )


def test_active_strategy_ids_are_unique() -> None:
    ids = [sid for _, _, sid in _ACTIVE_STRATEGIES]
    duplicates = {sid for sid in ids if ids.count(sid) > 1}
    assert not duplicates, f"중복된 strategy_id: {duplicates}"


def test_live_strategy_base_class_strategy_id_falls_back_to_name() -> None:
    """기본 구현은 `name` 을 그대로 돌려준다. 신규 전략이 `strategy_id` 오버라이드를
    잊어도 식별자가 비어 있지는 않게 보장하는 안전판이다."""

    class _DummyStrategy(LiveStrategy):
        @property
        def name(self) -> str:
            return "dummy-display"

        async def scan(self) -> List[TradeSignal]:
            return []

        async def check_exits(self, holdings) -> List[TradeSignal]:
            return []

    strategy = _DummyStrategy()
    assert strategy.strategy_id == "dummy-display"
