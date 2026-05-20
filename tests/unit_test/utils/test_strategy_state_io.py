"""StrategyStateIO atomic load/save + per-file lock + flush_pending 회귀 테스트."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.strategy_state_io import StrategyStateIO


@pytest.fixture(autouse=True)
def _reset_state():
    StrategyStateIO._reset_for_test()
    yield
    StrategyStateIO._reset_for_test()


@pytest.mark.asyncio
async def test_save_atomic_creates_file(tmp_path: Path):
    """save_atomic 은 지정 경로에 JSON 파일을 생성한다."""
    target = tmp_path / "state.json"
    payload = {"positions": {"005930": {"qty": 1}}, "cooldown": {}}

    await StrategyStateIO.save_atomic(str(target), payload)

    assert target.exists()
    with open(target, "r", encoding="utf-8") as f:
        assert json.load(f) == payload


@pytest.mark.asyncio
async def test_save_atomic_creates_parent_directory(tmp_path: Path):
    """save_atomic 은 부모 디렉토리가 없으면 생성한다."""
    target = tmp_path / "nested" / "deeper" / "state.json"
    await StrategyStateIO.save_atomic(str(target), {"k": 1})
    assert target.exists()


@pytest.mark.asyncio
async def test_save_atomic_preserves_original_on_write_failure(tmp_path: Path):
    """os.replace 가 실패해도 기존 파일이 보존되고 temp 파일이 청소된다.

    Atomic 성격의 핵심: replace 가 실패하면 commit 전이므로 원본은 절대 손상되지 않는다.
    """
    target = tmp_path / "state.json"
    original = {"positions": {"005930": {"qty": 1}}, "cooldown": {}}
    target.write_text(json.dumps(original), encoding="utf-8")

    new_payload = {"positions": {"000660": {"qty": 99}}, "cooldown": {"a": "b"}}

    with patch("utils.strategy_state_io.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            await StrategyStateIO.save_atomic(str(target), new_payload)

    # 기존 파일은 그대로
    with open(target, "r", encoding="utf-8") as f:
        assert json.load(f) == original

    # mkstemp 가 만든 temp 파일이 정리됨 (prefix=state.json. suffix=.tmp)
    tmp_leftovers = list(tmp_path.glob("state.json.*.tmp"))
    assert tmp_leftovers == [], f"temp files leaked: {tmp_leftovers}"


@pytest.mark.asyncio
async def test_save_atomic_serializes_concurrent_writes(tmp_path: Path):
    """동일 파일 동시 save 는 per-file lock 으로 직렬화되어 부분 기록이 없다."""
    target = tmp_path / "state.json"

    payload_a = {"v": "A" * 1000}
    payload_b = {"v": "B" * 1000}

    # 두 save 를 동시에 시작
    await asyncio.gather(
        StrategyStateIO.save_atomic(str(target), payload_a),
        StrategyStateIO.save_atomic(str(target), payload_b),
    )

    # 최종 파일은 둘 중 하나의 완전한 내용 (interleaved 가 아닌)
    with open(target, "r", encoding="utf-8") as f:
        final = json.load(f)
    assert final in (payload_a, payload_b)


@pytest.mark.asyncio
async def test_load_returns_none_when_missing(tmp_path: Path):
    """파일이 없으면 None 반환."""
    missing = tmp_path / "no_such_state.json"
    assert await StrategyStateIO.load(str(missing)) is None


@pytest.mark.asyncio
async def test_load_reads_json_content(tmp_path: Path):
    """기존 파일은 dict 로 반환."""
    target = tmp_path / "state.json"
    payload = {"positions": {"005930": {"qty": 5}}, "cooldown": {"x": "y"}}
    target.write_text(json.dumps(payload), encoding="utf-8")

    assert await StrategyStateIO.load(str(target)) == payload


@pytest.mark.asyncio
async def test_save_then_load_roundtrip(tmp_path: Path):
    """save → load 가 동일 페이로드를 반환."""
    target = tmp_path / "state.json"
    payload = {"positions": {"a": 1}, "cooldown": {"b": "c"}}
    await StrategyStateIO.save_atomic(str(target), payload)
    assert await StrategyStateIO.load(str(target)) == payload


@pytest.mark.asyncio
async def test_flush_pending_awaits_scheduled_saves(tmp_path: Path):
    """schedule_save 로 시작된 background task 가 flush_pending 으로 완료 대기된다."""
    target = tmp_path / "state.json"

    task = StrategyStateIO.schedule_save(str(target), {"v": 42})
    assert task in StrategyStateIO._pending

    await StrategyStateIO.flush_pending()

    # 파일이 완성되어 있음
    with open(target, "r", encoding="utf-8") as f:
        assert json.load(f) == {"v": 42}

    # pending set 비워짐
    assert len(StrategyStateIO._pending) == 0


@pytest.mark.asyncio
async def test_flush_pending_noop_when_empty():
    """pending 이 비어 있으면 즉시 반환."""
    await StrategyStateIO.flush_pending()  # 예외 없이 종료
