"""원자적 JSON 파일 쓰기 유틸 (P0 0-11).

temp 파일에 기록 → ``fsync`` → ``os.replace`` 로 원자 교체한다. 저장 중 프로세스가
강제 종료되거나 쓰기 도중 예외가 발생해도 기존 파일이 truncate 되지 않는다.

직접 ``open(path, "w")`` 후 ``json.dump`` 하는 truncate-write 패턴(부분 쓰기/빈 파일
잔존 위험)을 대체한다. 비동기 직렬화·per-file lock 이 필요하면
``utils.strategy_state_io.StrategyStateIO`` 를 사용한다.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any


def write_json_atomic(
    file_path: str,
    data: Any,
    *,
    indent: int = 2,
    ensure_ascii: bool = False,
) -> None:
    """``data`` 를 ``file_path`` 에 원자적으로 JSON 저장한다.

    부모 디렉터리는 없으면 생성한다. temp 파일 쓰기 실패 시 temp 를 정리하고
    예외를 그대로 전파한다(기존 파일은 보존).
    """
    directory = os.path.dirname(file_path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(file_path) + ".",
        suffix=".tmp",
        dir=directory,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, file_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
