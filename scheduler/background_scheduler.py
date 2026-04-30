# scheduler/background_scheduler.py
"""
백그라운드 태스크 라이프사이클 관리 스케줄러.
SchedulableTask 인터페이스를 구현한 태스크들을 등록하고
start/stop/suspend/resume을 통합 관리한다.

Ticket-driven 모드: WorkerPool + TimeDispatcher를 주입하면
장 마감 시 자동으로 티켓이 발행되어 WorkerPool 워커가 처리한다.
기존 after_market_loop 방식 태스크와 공존 가능.
"""
import asyncio
import logging
from typing import Dict, List, Optional

from interfaces.schedulable_task import SchedulableTask, TaskState
from core.performance_profiler import PerformanceProfiler


class BackgroundScheduler:
    """SchedulableTask 기반 백그라운드 태스크 라이프사이클 관리자.

    비즈니스 로직 없이 순수하게 태스크의 생명주기만 관리한다.
    worker_pool / time_dispatcher가 주입되면 Ticket-driven 인프라도 함께 시작/종료한다.
    """

    def __init__(
        self,
        logger=None,
        performance_profiler: Optional[PerformanceProfiler] = None,
        worker_pool=None,
        time_dispatcher=None,
    ):
        self._logger = logger or logging.getLogger(__name__)
        self._pm = performance_profiler if performance_profiler else PerformanceProfiler(enabled=False)
        self._tasks: Dict[str, SchedulableTask] = {}  # name -> task
        self._worker_pool = worker_pool
        self._time_dispatcher = time_dispatcher
        self._infra_tasks: List[asyncio.Task] = []  # WorkerPool/Dispatcher asyncio.Task 목록
        self._start_lock: Optional[asyncio.Lock] = None
        self._shutdown_lock: Optional[asyncio.Lock] = None
        self._starting: bool = False
        self._shutting_down: bool = False

    def _get_start_lock(self) -> asyncio.Lock:
        if self._start_lock is None:
            self._start_lock = asyncio.Lock()
        return self._start_lock

    def _get_shutdown_lock(self) -> asyncio.Lock:
        if self._shutdown_lock is None:
            self._shutdown_lock = asyncio.Lock()
        return self._shutdown_lock

    def register(self, task: SchedulableTask) -> None:
        """SchedulableTask를 등록한다."""
        if task.task_name in self._tasks:
            self._logger.warning(f"[BackgroundScheduler] 태스크 '{task.task_name}' 이미 등록됨 — 덮어씁니다.")
        self._tasks[task.task_name] = task
        self._logger.info(
            f"[BackgroundScheduler] 태스크 등록: {task.task_name} (priority={task.priority})"
        )

    def unregister(self, task_name: str) -> None:
        """등록된 태스크를 제거한다."""
        if task_name in self._tasks:
            del self._tasks[task_name]
            self._logger.info(f"[BackgroundScheduler] 태스크 제거: {task_name}")

    async def start_all(self) -> None:
        """등록된 모든 태스크를 시작한다.

        Ticket-driven 인프라(WorkerPool, TimeDispatcher)가 주입된 경우 먼저 시작한다.
        """
        lock = self._get_start_lock()
        if self._starting or lock.locked():
            self._logger.warning("[BackgroundScheduler] start_all 중복 호출 무시")
            return
        self._starting = True
        async with lock:
            try:
                await self._start_all_locked()
            finally:
                self._starting = False

    async def _start_all_locked(self) -> None:
        t_start = self._pm.start_timer()
        self._logger.info(f"[BackgroundScheduler] 전체 시작: {len(self._tasks)}개 태스크")

        # Ticket-driven 인프라 시작
        if self._worker_pool is not None:
            await self._worker_pool.start()
            self._logger.info("[BackgroundScheduler] WorkerPool 시작 완료")
        if self._time_dispatcher is not None:
            t = asyncio.create_task(self._time_dispatcher.run(), name="time-dispatcher")
            self._infra_tasks.append(t)
            self._logger.info("[BackgroundScheduler] TimeDispatcher 시작 완료")

        for name, task in self._tasks.items():
            if task.state == TaskState.SUSPENDED:
                try:
                    await task.resume()
                    self._logger.info(f"[BackgroundScheduler] '{name}' 재개 완료")
                except Exception as e:
                    self._logger.error(f"[BackgroundScheduler] '{name}' 재개 실패: {e}", exc_info=True)
            elif task.state != TaskState.RUNNING:
                try:
                    await task.start()
                    self._logger.info(f"[BackgroundScheduler] '{name}' 시작 완료")
                except Exception as e:
                    self._logger.error(f"[BackgroundScheduler] '{name}' 시작 실패: {e}", exc_info=True)
        self._pm.log_timer("BackgroundScheduler.start_all", t_start)

    async def shutdown(self) -> None:
        """등록된 모든 태스크를 정상 종료한다.

        순서: 1) TimeDispatcher 중지 → 2) SchedulableTask 종료 → 3) WorkerPool shutdown
        """
        lock = self._get_shutdown_lock()
        if self._shutting_down or lock.locked():
            self._logger.warning("[BackgroundScheduler] shutdown 중복 호출 무시")
            return
        self._shutting_down = True
        async with lock:
            try:
                await self._shutdown_locked()
            finally:
                self._shutting_down = False

    async def _shutdown_locked(self) -> None:
        t_start = self._pm.start_timer()
        self._logger.info(f"[BackgroundScheduler] 전체 종료: {len(self._tasks)}개 태스크")

        # 1. TimeDispatcher 중지 (새 티켓 발행 차단)
        if self._time_dispatcher is not None:
            self._time_dispatcher.stop()
        for t in self._infra_tasks:
            if not t.done():
                t.cancel()
        if self._infra_tasks:
            await asyncio.gather(*self._infra_tasks, return_exceptions=True)
        self._infra_tasks.clear()

        # 2. SchedulableTask 종료
        for name, task in self._tasks.items():
            if task.state in (TaskState.RUNNING, TaskState.SUSPENDED):
                try:
                    await task.stop()
                    self._logger.info(f"[BackgroundScheduler] '{name}' 종료 완료")
                except Exception as e:
                    self._logger.error(f"[BackgroundScheduler] '{name}' 종료 실패: {e}", exc_info=True)

        # 3. WorkerPool Graceful Shutdown (잔여 티켓 처리 후 종료)
        if self._worker_pool is not None:
            await self._worker_pool.shutdown()
            self._logger.info("[BackgroundScheduler] WorkerPool 종료 완료")

        self._pm.log_timer("BackgroundScheduler.shutdown", t_start)

    async def suspend_all(self) -> None:
        """실행 중인 모든 태스크를 일시 중지한다 (WorkerPool 포함)."""
        self._logger.info("[BackgroundScheduler] 전체 일시 중지")
        if self._worker_pool is not None:
            self._worker_pool.suspend()
        for name, task in self._tasks.items():
            if task.state == TaskState.RUNNING:
                try:
                    await task.suspend()
                except Exception as e:
                    self._logger.error(f"[BackgroundScheduler] '{name}' 일시 중지 실패: {e}")

    async def resume_all(self) -> None:
        """일시 중지된 모든 태스크를 재개한다 (WorkerPool 포함)."""
        self._logger.info("[BackgroundScheduler] 전체 재개")
        if self._worker_pool is not None:
            self._worker_pool.resume()
        for name, task in self._tasks.items():
            if task.state == TaskState.SUSPENDED:
                try:
                    await task.resume()
                except Exception as e:
                    self._logger.error(f"[BackgroundScheduler] '{name}' 재개 실패: {e}")

    def get_task(self, name: str) -> Optional[SchedulableTask]:
        """이름으로 태스크를 조회한다."""
        return self._tasks.get(name)

    def get_all_status(self) -> List[dict]:
        """모든 등록된 태스크의 상태를 반환한다."""
        return [
            {
                "name": task.task_name,
                "state": task.state.value,
                "priority": int(task.priority),
            }
            for task in self._tasks.values()
        ]
