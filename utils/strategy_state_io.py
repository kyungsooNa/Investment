"""전략 state 파일 atomic load/save 헬퍼.

전략은 다수의 비동기 진입점(scan, 매수/매도 콜백, 강제청산)에서 동일 state 파일을
저장한다. 직접 `with open(path, "w")` 로 truncate 후 쓰는 기존 패턴은
- 동시 save 경합 시 partial-write 위험
- 저장 중 프로세스 종료 시 truncate 된 빈 파일이 남는 위험
을 가진다.

이 모듈은 다음을 보장한다.

- **Atomic write**: temp 파일에 기록 → `fsync` → `os.replace()` 로 원자 교체.
- **Per-file lock**: 동일 파일 경로에 대해 `asyncio.Lock` 으로 save 직렬화.
- **Pending flush**: 백그라운드 `schedule_save()` 로 시작된 save task 를
  `flush_pending()` 으로 graceful shutdown 시 await 가능.

설계 가정: 단일 event loop. 멀티 루프는 본 프로젝트 시나리오가 아니다.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, Optional, Set

from utils.atomic_json import write_json_atomic


class StrategyStateIO:
    """Atomic + lock 기반 전략 state 파일 헬퍼.

    클래스 변수로 lock dict / pending task set 을 보관한다. 단일 event loop
    가정하에 4개 전략이 공유한다.
    """

    _locks: Dict[str, asyncio.Lock] = {}
    _pending: Set[asyncio.Task] = set()

    @classmethod
    def _lock_for(cls, file_path: str) -> asyncio.Lock:
        """현재 running event loop 에 묶인 Lock 반환.

        클래스 변수 dict 는 프로세스 lifetime 동안 유지되므로, 테스트 환경의
        다중 event loop 에서 닫힌 loop 에 묶인 Lock 을 재사용하면
        `ValueError: future belongs to a different loop` 가 발생한다.
        loop 가 다르면 stale Lock 을 폐기하고 새로 생성한다.
        """
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        lock = cls._locks.get(file_path)
        if lock is not None and current is not None:
            try:
                lock_loop = lock._get_loop()
            except Exception:
                lock_loop = getattr(lock, "_loop", None)
            if lock_loop is not None and lock_loop is not current:
                lock = None
        if lock is None:
            lock = asyncio.Lock()
            cls._locks[file_path] = lock
        return lock

    @classmethod
    async def save_atomic(cls, file_path: str, data: Any) -> None:
        """data 를 file_path 에 atomic 하게 저장한다. 동일 경로 동시 호출은 직렬화된다."""
        lock = cls._lock_for(file_path)
        await lock.acquire()
        try:
            await asyncio.to_thread(cls._write_atomic, file_path, data)
        finally:
            try:
                lock.release()
            except RuntimeError as e:
                # flush 되지 않은 background save task 가 event loop 종료 후 GC 될 때,
                # release() 가 닫힌 loop 에서 waiter 를 깨우려다 'Event loop is closed'
                # 를 던진다. teardown 경로이므로 무시 — 그대로 두면
                # PytestUnraisableExceptionWarning 으로 노출된다.
                if "Event loop is closed" not in str(e):
                    raise

    @staticmethod
    def _write_atomic(file_path: str, data: Any) -> None:
        # P0 0-11: 공통 atomic util 에 위임 (tempfile → fsync → os.replace).
        write_json_atomic(file_path, data, indent=2, ensure_ascii=False)

    @classmethod
    async def load(cls, file_path: str) -> Optional[Any]:
        """file_path 의 JSON 을 로드. 파일이 없으면 None."""
        if not os.path.exists(file_path):
            return None
        return await asyncio.to_thread(cls._read_file, file_path)

    @staticmethod
    def _read_file(file_path: str) -> Any:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    @classmethod
    def schedule_save(cls, file_path: str, data: Any) -> asyncio.Task:
        """백그라운드 save task 생성 후 _pending 에 등록. flush_pending() 추적 대상."""
        task = asyncio.create_task(cls.save_atomic(file_path, data))
        cls._pending.add(task)
        task.add_done_callback(cls._pending.discard)
        return task

    @classmethod
    async def flush_pending(cls, timeout: Optional[float] = None) -> None:
        """등록된 모든 save task 완료까지 대기. timeout=None 이면 무한 대기.

        현재 running loop 에 묶인 task 만 await — 다른 loop 의 stale task 는
        skip (테스트 환경 다중 loop 안전성).
        """
        if not cls._pending:
            return
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            return
        tasks = [t for t in cls._pending if t.get_loop() is current]
        if not tasks:
            return
        if timeout is None:
            await asyncio.gather(*tasks, return_exceptions=True)
        else:
            await asyncio.wait(tasks, timeout=timeout)

    @classmethod
    def _reset_for_test(cls) -> None:
        """테스트용: lock/pending 상태 초기화."""
        cls._locks.clear()
        cls._pending.clear()
