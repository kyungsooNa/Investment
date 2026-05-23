"""compute_config_hash — P3-4 설정 변경 통제.

전략 config 를 deterministic 하게 hash 화하여 TradeSignal / journal record 에
stamp 한다. 같은 config 는 같은 hash, 다른 config 는 다른 hash. 운영 중
config 변경을 사후 추적 가능하도록 만든다.

지원 입력:
- pydantic BaseModel (model_dump 로 정규화)
- dataclass
- dict
- None / empty → 빈 string (passthrough)
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from common.config_hashing import compute_config_hash


@dataclass
class _ExampleConfig:
    a: int = 1
    b: str = "hello"
    c: list = None

    def __post_init__(self):
        if self.c is None:
            self.c = []


def test_returns_empty_for_none():
    assert compute_config_hash(None) == ""


def test_returns_empty_for_empty_dict():
    assert compute_config_hash({}) == ""


def test_dict_deterministic_regardless_of_key_order():
    h1 = compute_config_hash({"a": 1, "b": 2, "c": 3})
    h2 = compute_config_hash({"c": 3, "a": 1, "b": 2})
    assert h1 == h2
    assert len(h1) > 0


def test_dict_different_values_different_hash():
    h1 = compute_config_hash({"a": 1})
    h2 = compute_config_hash({"a": 2})
    assert h1 != h2


def test_dataclass_supported():
    h1 = compute_config_hash(_ExampleConfig(a=1, b="x"))
    h2 = compute_config_hash(_ExampleConfig(a=1, b="x"))
    h3 = compute_config_hash(_ExampleConfig(a=2, b="x"))
    assert h1 == h2
    assert h1 != h3
    assert len(h1) > 0


def test_pydantic_basemodel_supported():
    pyd_module = pytest.importorskip("pydantic")
    BaseModel = pyd_module.BaseModel

    class _M(BaseModel):
        a: int = 1
        b: str = "x"

    h1 = compute_config_hash(_M(a=1, b="x"))
    h2 = compute_config_hash(_M(a=1, b="x"))
    h3 = compute_config_hash(_M(a=2, b="x"))
    assert h1 == h2
    assert h1 != h3


def test_unsupported_type_returns_empty():
    """변환 불가 객체는 (예외 대신) 빈 string 으로 안전 처리."""
    class _NoSerialize:
        pass
    assert compute_config_hash(_NoSerialize()) == ""


def test_hash_is_short_hex():
    """길이 12 hex digest — log 가독성 위해 짧게."""
    h = compute_config_hash({"a": 1})
    assert len(h) == 12
    assert all(c in "0123456789abcdef" for c in h)


def test_nested_dict_supported():
    h1 = compute_config_hash({"a": {"x": 1, "y": [1, 2]}})
    h2 = compute_config_hash({"a": {"y": [1, 2], "x": 1}})
    assert h1 == h2


def test_dataclass_and_dict_with_same_data_produce_same_hash():
    """동일 정규화 결과면 동일 hash (dataclass vs dict 차이 없어야 함)."""
    dc_hash = compute_config_hash(_ExampleConfig(a=1, b="x", c=[1, 2]))
    dict_hash = compute_config_hash({"a": 1, "b": "x", "c": [1, 2]})
    assert dc_hash == dict_hash
