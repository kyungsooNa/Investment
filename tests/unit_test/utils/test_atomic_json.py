"""P0 0-11: write_json_atomic 원자성 회귀 테스트."""
import json
import os
from unittest.mock import patch

import pytest

from utils.atomic_json import write_json_atomic


def test_writes_json_roundtrip(tmp_path):
    path = str(tmp_path / "state.json")
    write_json_atomic(path, {"a": 1, "b": [1, 2, 3]})
    with open(path, "r", encoding="utf-8") as f:
        assert json.load(f) == {"a": 1, "b": [1, 2, 3]}


def test_creates_parent_directory(tmp_path):
    path = str(tmp_path / "nested" / "deep" / "state.json")
    write_json_atomic(path, {"ok": True})
    assert os.path.exists(path)


def test_korean_preserved_with_ensure_ascii_false(tmp_path):
    path = str(tmp_path / "state.json")
    write_json_atomic(path, {"name": "삼성전자"}, ensure_ascii=False)
    raw = open(path, "r", encoding="utf-8").read()
    assert "삼성전자" in raw  # escape 되지 않고 원문 보존


def test_overwrite_replaces_existing(tmp_path):
    path = str(tmp_path / "state.json")
    write_json_atomic(path, {"v": 1})
    write_json_atomic(path, {"v": 2})
    with open(path, "r", encoding="utf-8") as f:
        assert json.load(f) == {"v": 2}


def test_existing_file_not_truncated_on_write_failure(tmp_path):
    """쓰기 도중 예외 발생 시 기존 파일이 truncate/손상되지 않는다 (atomic 보장)."""
    path = str(tmp_path / "state.json")
    write_json_atomic(path, {"good": "original"})

    # json.dump 도중 예외를 강제 → temp 파일만 영향, 원본은 os.replace 전이라 보존
    with patch("utils.atomic_json.json.dump", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            write_json_atomic(path, {"bad": "should_not_persist"})

    # 원본 그대로 유지
    with open(path, "r", encoding="utf-8") as f:
        assert json.load(f) == {"good": "original"}


def test_no_temp_files_left_after_failure(tmp_path):
    """쓰기 실패 시 temp 파일이 남지 않는다."""
    path = str(tmp_path / "state.json")
    write_json_atomic(path, {"good": "original"})

    with patch("utils.atomic_json.json.dump", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            write_json_atomic(path, {"bad": "x"})

    leftover = [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]
    assert leftover == []


def test_unserializable_data_preserves_existing(tmp_path):
    """직렬화 불가 데이터 → 예외 발생하되 기존 파일 보존."""
    path = str(tmp_path / "state.json")
    write_json_atomic(path, {"v": 1})

    class _NotSerializable:
        pass

    with pytest.raises(TypeError):
        write_json_atomic(path, {"bad": _NotSerializable()})

    with open(path, "r", encoding="utf-8") as f:
        assert json.load(f) == {"v": 1}
    leftover = [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]
    assert leftover == []
