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
        time_dispatchers=None,
        api_budget_limiter=None,
        budget_snapshot_interval_sec: float = 60.0,
    ):
        self._logger = logger or logging.getLogger(__name__)
        self._pm = performance_profiler if performance_profiler else PerformanceProfiler(enabled=False)
        self._tasks: Dict[str, SchedulableTask] = {}  # name -> task
        self._worker_pool = worker_pool
        self._api_budget_limiter = api_budget_limiter
        self._budget_snapshot_interval_sec = budget_snapshot_interval_sec
        # 시장별 TimeDispatcher 복수 지원: 단수 time_dispatcher 와 복수 time_dispatchers 를
        # 함께 받아 하나의 리스트로 정규화한다 (KR + US 동시 구동).
        self._time_dispatchers = list(time_dispatchers or [])
        if time_dispatcher is not None:
            self._time_dispatchers.insert(0, time_dispatcher)
        self._infra_tasks: List[asyncio.Task] = []  # WorkerPool/Dispatcher asyncio.Task 목록
        self._start_lock: Optional[asyncio.Lock] = None
        self._shutdown_lock: Optional[asyncio.Lock] = None
        self._starting: bool = False
        self._shutting_down: bool = False
        self._started: bool = False
        self._shutdown_completed: bool = False

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
        if self._started:
            self._logger.warning("[BackgroundScheduler] 이미 시작됨 — start_all 호출 무시")
            return
        self._starting = True
        async with lock:
            try:
                await self._start_all_locked()
                self._started = True
                self._shutdown_completed = False
            finally:
                self._starting = False

    async def _start_all_locked(self) -> None:
        t_start = self._pm.start_timer()
        self._logger.info(f"[BackgroundScheduler] 전체 시작: {len(self._tasks)}개 태스크")

        # Ticket-driven 인프라 시작
        if self._worker_pool is not None:
            await self._worker_pool.start()
            self._logger.info("[BackgroundScheduler] WorkerPool 시작 완료")
        for idx, dispatcher in enumerate(self._time_dispatchers):
            t = asyncio.create_task(dispatcher.run(), name=f"time-dispatcher-{idx}")
            self._infra_tasks.append(t)
        if self._time_dispatchers:
            self._logger.info(
                f"[BackgroundScheduler] TimeDispatcher {len(self._time_dispatchers)}개 시작 완료"
            )
        if self._api_budget_limiter is not None and self._pm.enabled:
            t = asyncio.create_task(self._budget_snapshot_loop(), name="budget-snapshot-logger")
            self._infra_tasks.append(t)
            self._logger.info("[BackgroundScheduler] BudgetSnapshot 로거 시작")

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

    async def _budget_snapshot_loop(self) -> None:
        """ApiBudgetLimiter 상태를 주기적으로 성능 로그에 남긴다.

        [Performance] 타이머는 threshold(기본 1.0s) 미만 호출을 걷어내 실제 호출량을
        가려버린다. acquired_total/rate_wait_seconds_total 같은 누적 카운터를 주기
        스냅샷으로 남겨 그 공백(실호출량, 검열 없는 총 대기시간)을 메운다.
        """
        try:
            while True:
                await asyncio.sleep(self._budget_snapshot_interval_sec)
                self._log_budget_snapshot()
        except asyncio.CancelledError:
            pass

    def _log_budget_snapshot(self) -> None:
        snapshot = self._api_budget_limiter.snapshot()
        for category, lane in snapshot.items():
            self._emit_budget_snapshot_line(category, lane)
            emergency = lane.get("emergency")
            if emergency:
                self._emit_budget_snapshot_line(f"{category}(emergency)", emergency)

    def _emit_budget_snapshot_line(self, category: str, lane: dict) -> None:
        msg = (
            f"[Performance] BudgetSnapshot.{category}: {lane['rate_wait_seconds_total']:.4f}s "
            f"(active={lane['active']}, limit={lane['limit']}, acquired_total={lane['acquired_total']}, "
            f"rate_wait_total={lane['rate_wait_total']}, max_observed_active={lane['max_observed_active']})"
        )
        self._pm.logger.info(msg)

    async def shutdown(self) -> None:
        """등록된 모든 태스크를 정상 종료한다.

        순서: 1) TimeDispatcher 중지 → 2) SchedulableTask 종료 → 3) WorkerPool shutdown
        """
        lock = self._get_shutdown_lock()
        if self._shutting_down or lock.locked():
            self._logger.warning("[BackgroundScheduler] shutdown 중복 호출 무시")
            return
        if self._shutdown_completed:
            self._logger.warning("[BackgroundScheduler] 이미 종료됨 — shutdown 호출 무시")
            return
        self._shutting_down = True
        async with lock:
            try:
                await self._shutdown_locked()
                self._started = False
                self._shutdown_completed = True
            finally:
                self._shutting_down = False

    async def _shutdown_locked(self) -> None:
        t_start = self._pm.start_timer()
        self._logger.info(f"[BackgroundScheduler] 전체 종료: {len(self._tasks)}개 태스크")

        # 1. TimeDispatcher 중지 (새 티켓 발행 차단)
        for dispatcher in self._time_dispatchers:
            dispatcher.stop()
        for t in self._infra_tasks:
            if not t.done():
                t.cancel()
        if self._infra_tasks:
            await asyncio.gather(*self._infra_tasks, return_exceptions=True)
        self._infra_tasks.clear()

        # 2. SchedulableTask 종료
        for name, task in self._tasks.items():
            if task.state in (TaskState.RUNNING, TaskState.SUSPENDED) or self._has_active_internal_tasks(task):
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

    @staticmethod
    def _has_active_internal_tasks(task: SchedulableTask) -> bool:
        internal_tasks = getattr(task, "_tasks", None)
        if not internal_tasks:
            return False
        return any(not internal_task.done() for internal_task in internal_tasks)

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
