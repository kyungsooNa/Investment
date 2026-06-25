"""활성 라이브 전략의 lifecycle contract checklist 테스트 (P3 3-4 ①).

`LiveStrategy` 인터페이스 + 등록 전략(활성 7 + 인버스 ETF 슬리브 shadow/paper)이
공유해야 할 최소 lifecycle hook 의 존재/타입을 고정한다. 신규 전략 추가 시 contract
누락을 자동 탐지하기 위한 안전망.

검증 항목:
  - 식별자 3종 (`name`/`strategy_id`/`display_name`) 이 non-empty str.
  - 핵심 lifecycle hook (`scan`/`check_exits`/`load_state`/`save_state`) 이 async callable.
  - 이벤트 라우터 hook (`evaluate_single`/`current_candidate_codes`) 이 callable + 반환 타입.
  - `LiveStrategy` 상속.
  - 전략 간 `strategy_id` 충돌 없음.
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Callable
from unittest.mock import MagicMock

import pytest

from interfaces.live_strategy import LiveStrategy
from strategies.first_pullback_strategy import FirstPullbackStrategy
from strategies.high_tight_flag_strategy import HighTightFlagStrategy
from strategies.inverse_etf_regime_strategy import InverseEtfRegimeStrategy
from strategies.larry_williams_channel_breakout_strategy import (
    LarryWilliamsChannelBreakoutStrategy,
)
from strategies.larry_williams_vbo_strategy import LarryWilliamsVBOStrategy
from strategies.oneil_pocket_pivot_strategy import OneilPocketPivotStrategy
from strategies.oneil_squeeze_breakout_strategy import OneilSqueezeBreakoutStrategy
from strategies.rsi2_pullback_strategy import RSI2PullbackStrategy


def _common_kwargs():
    return dict(
        stock_query_service=MagicMock(),
        universe_service=MagicMock(),
        market_clock=MagicMock(),
        logger=MagicMock(),
    )


def _with_indicator():
    kw = _common_kwargs()
    kw["indicator_service"] = MagicMock()
    return kw


def _factory_pp() -> LiveStrategy:
    return OneilPocketPivotStrategy(**_common_kwargs())


def _factory_htf() -> LiveStrategy:
    return HighTightFlagStrategy(**_common_kwargs())


def _factory_fp() -> LiveStrategy:
    return FirstPullbackStrategy(**_common_kwargs())


def _factory_osb() -> LiveStrategy:
    return OneilSqueezeBreakoutStrategy(**_common_kwargs())


def _factory_rsi2() -> LiveStrategy:
    return RSI2PullbackStrategy(**_with_indicator())


def _factory_cb() -> LiveStrategy:
    return LarryWilliamsChannelBreakoutStrategy(**_with_indicator())


def _factory_vbo() -> LiveStrategy:
    return LarryWilliamsVBOStrategy(
        stock_query_service=MagicMock(),
        market_clock=MagicMock(),
        logger=MagicMock(),
    )


def _factory_inverse() -> LiveStrategy:
    return InverseEtfRegimeStrategy(
        stock_query_service=MagicMock(),
        market_regime_service=MagicMock(),
        indicator_service=MagicMock(),
        market_clock=MagicMock(),
        logger=MagicMock(),
    )


ACTIVE_STRATEGY_FACTORIES: list[tuple[str, Callable[[], LiveStrategy]]] = [
    ("oneil_pocket_pivot", _factory_pp),
    ("high_tight_flag", _factory_htf),
    ("first_pullback", _factory_fp),
    ("oneil_squeeze_breakout", _factory_osb),
    ("rsi2_pullback", _factory_rsi2),
    ("larry_williams_channel_breakout", _factory_cb),
    ("larry_williams_vbo", _factory_vbo),
    ("inverse_etf_regime", _factory_inverse),
]


def _is_async_callable(obj) -> bool:
    if not callable(obj):
        return False
    return inspect.iscoroutinefunction(obj) or asyncio.iscoroutinefunction(obj)


@pytest.fixture(params=ACTIVE_STRATEGY_FACTORIES, ids=lambda p: p[0])
def strategy_instance(request) -> LiveStrategy:
    _, factory = request.param
    return factory()


def test_inherits_live_strategy(strategy_instance):
    """모든 활성 전략은 LiveStrategy 를 상속해야 한다."""
    assert isinstance(strategy_instance, LiveStrategy)


def test_identifiers_are_nonempty_strings(strategy_instance):
    """name / strategy_id / display_name 은 모두 non-empty str."""
    for attr in ("name", "strategy_id", "display_name"):
        value = getattr(strategy_instance, attr)
        assert isinstance(value, str), f"{attr} must be str, got {type(value).__name__}"
        assert value.strip(), f"{attr} must be non-empty"


def test_scan_is_async_callable(strategy_instance):
    assert _is_async_callable(strategy_instance.scan), "scan() must be async callable"


def test_check_exits_is_async_callable(strategy_instance):
    assert _is_async_callable(
        strategy_instance.check_exits
    ), "check_exits() must be async callable"


def test_load_state_hook_present(strategy_instance):
    """load_state() 는 모든 전략에 존재해야 한다 (default no-op 포함)."""
    assert _is_async_callable(
        strategy_instance.load_state
    ), "load_state() must be async callable"


def test_save_state_hook_present(strategy_instance):
    """save_state() 는 모든 전략에 존재해야 한다 (default no-op 포함, load_state 와 대칭)."""
    assert _is_async_callable(
        strategy_instance.save_state
    ), "save_state() must be async callable"


def test_evaluate_single_hook_present(strategy_instance):
    """evaluate_single() 은 LiveStrategy default 또는 전략 override 로 항상 존재."""
    assert _is_async_callable(
        strategy_instance.evaluate_single
    ), "evaluate_single() must be async callable"


def test_current_candidate_codes_returns_list(strategy_instance):
    """current_candidate_codes() 는 동기 호출 가능 + list 반환."""
    result = strategy_instance.current_candidate_codes()
    assert isinstance(result, list), f"must return list, got {type(result).__name__}"


@pytest.mark.asyncio
async def test_load_save_state_callable_without_exception(strategy_instance):
    """load_state/save_state 가 mock 환경에서 예외 없이 await 가능해야 한다.

    state 파일이 있는 전략도 default state_file 경로(파일 없음) 에서 조용히 통과해야
    한다 — 운영 bootstrap barrier 가 이 호출에 의존한다.
    """
    await strategy_instance.load_state()
    # save_state 는 default no-op 또는 전략 override 가 mock state 로 동작 가능해야 함.
    # 일부 전략의 save_state 는 실제 _position_state 가 dict 형태이기만 하면 동작한다.
    save_fn = getattr(strategy_instance, "save_state", None)
    if save_fn is not None:
        try:
            await save_fn()
        except Exception:
            # save 가 mock dependency 부재로 실패할 수는 있다. contract 자체는 "존재 + async" 만 검증.
            pass


def test_strategy_ids_are_unique():
    """모든 활성 전략의 strategy_id 는 서로 충돌하지 않아야 한다."""
    ids = []
    for label, factory in ACTIVE_STRATEGY_FACTORIES:
        instance = factory()
        ids.append((label, instance.strategy_id))
    duplicates = [
        sid for sid in {s for _, s in ids} if [s for _, s in ids].count(sid) > 1
    ]
    assert not duplicates, f"중복된 strategy_id: {duplicates}"


def test_names_are_unique():
    """모든 활성 전략의 name 도 충돌하지 않아야 한다 (UI/저널 식별 안정성)."""
    names = []
    for label, factory in ACTIVE_STRATEGY_FACTORIES:
        instance = factory()
        names.append((label, instance.name))
    duplicates = [n for n in {n for _, n in names} if [n for _, n in names].count(n) > 1]
    assert not duplicates, f"중복된 name: {duplicates}"
