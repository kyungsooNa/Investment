"""4개 전략의 `load_state()` 명시적 await 회귀 테스트.

각 전략의 `__init__` 은 이벤트 루프가 있으면 fire-and-forget 으로
`_load_state_async()` 를 스케줄한다. 그 자체로는 scan 이전에 state 가
반드시 로드되었음을 보장하지 못한다. `load_state()` 는 호출자가 명시적으로
await 할 수 있게 만든 public API 다.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from strategies.first_pullback_strategy import FirstPullbackStrategy
from strategies.high_tight_flag_strategy import HighTightFlagStrategy
from strategies.oneil_pocket_pivot_strategy import OneilPocketPivotStrategy
from strategies.oneil_squeeze_breakout_strategy import OneilSqueezeBreakoutStrategy
from utils.strategy_state_io import StrategyStateIO


@pytest.fixture(autouse=True)
def _reset_state_io():
    StrategyStateIO._reset_for_test()
    yield
    StrategyStateIO._reset_for_test()


def _common_kwargs():
    return dict(
        stock_query_service=MagicMock(),
        universe_service=MagicMock(),
        market_clock=MagicMock(),
        logger=MagicMock(),
    )


@pytest.mark.asyncio
async def test_pp_load_state_reads_positions(tmp_path: Path):
    """OneilPocketPivotStrategy.load_state() 가 STATE_FILE 의 positions 를 반영."""
    state_file = tmp_path / "pp_state.json"
    payload = {
        "positions": {
            "005930": {
                "entry_type": "PP",
                "entry_price": 70000,
                "entry_date": "20260520",
                "peak_price": 71000,
                "supporting_ma": "20",
                "gap_day_low": 0,
            }
        },
        "cooldown": {"000660": "20260519"},
    }
    state_file.write_text(json.dumps(payload), encoding="utf-8")

    strat = OneilPocketPivotStrategy(**_common_kwargs(), state_file=str(state_file))
    await strat.load_state()

    assert "005930" in strat._position_state
    assert strat._position_state["005930"].entry_price == 70000
    assert strat._cooldown == {"000660": "20260519"}


@pytest.mark.asyncio
async def test_pp_load_state_idempotent(tmp_path: Path):
    """load_state() 를 2회 호출해도 안전 (idempotency)."""
    state_file = tmp_path / "pp_state.json"
    payload = {
        "positions": {
            "005930": {
                "entry_type": "BGU",
                "entry_price": 70000,
                "entry_date": "20260520",
                "peak_price": 70000,
                "supporting_ma": "",
                "gap_day_low": 69000,
            }
        },
        "cooldown": {},
    }
    state_file.write_text(json.dumps(payload), encoding="utf-8")

    strat = OneilPocketPivotStrategy(**_common_kwargs(), state_file=str(state_file))
    await strat.load_state()
    await strat.load_state()  # 두 번 호출

    assert len(strat._position_state) == 1
    assert strat._position_state["005930"].entry_price == 70000


@pytest.mark.asyncio
async def test_fp_load_state_reads_positions(tmp_path: Path):
    state_file = tmp_path / "fp_state.json"
    payload = {
        "positions": {
            "005930": {
                "entry_price": 70000,
                "entry_date": "20260520",
                "peak_price": 70500,
                "surge_day_high": 72000,
            }
        },
        "cooldown": {},
    }
    state_file.write_text(json.dumps(payload), encoding="utf-8")

    strat = FirstPullbackStrategy(**_common_kwargs(), state_file=str(state_file))
    await strat.load_state()

    assert "005930" in strat._position_state
    assert strat._position_state["005930"].entry_price == 70000


@pytest.mark.asyncio
async def test_htf_load_state_reads_positions(tmp_path: Path):
    state_file = tmp_path / "htf_state.json"
    payload = {
        "positions": {
            "005930": {
                "entry_price": 70000,
                "entry_date": "20260520",
                "peak_price": 71000,
                "pole_high": 75000,
            }
        },
        "cooldown": {},
    }
    state_file.write_text(json.dumps(payload), encoding="utf-8")

    strat = HighTightFlagStrategy(**_common_kwargs(), state_file=str(state_file))
    await strat.load_state()

    assert "005930" in strat._position_state
    assert strat._position_state["005930"].entry_price == 70000


@pytest.mark.asyncio
async def test_osb_load_state_reads_positions(tmp_path: Path):
    state_file = tmp_path / "osb_state.json"
    payload = {
        "positions": {
            "005930": {
                "entry_price": 70000,
                "entry_date": "20260520",
                "peak_price": 71000,
                "breakout_level": 70000,
            }
        },
        "cooldown": {"000660": "20260519"},
    }
    state_file.write_text(json.dumps(payload), encoding="utf-8")

    strat = OneilSqueezeBreakoutStrategy(**_common_kwargs(), state_file=str(state_file))
    await strat.load_state()

    assert "005930" in strat._position_state
    assert strat._position_state["005930"].entry_price == 70000
    assert strat._cooldown == {"000660": "20260519"}


@pytest.mark.asyncio
async def test_load_state_safe_when_file_missing(tmp_path: Path):
    """STATE_FILE 이 없으면 load_state() 가 조용히 통과한다."""
    state_file = tmp_path / "missing.json"
    strat = OneilPocketPivotStrategy(**_common_kwargs(), state_file=str(state_file))
    await strat.load_state()  # 예외 없이 종료
    assert strat._position_state == {}
