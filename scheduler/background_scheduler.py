# scheduler/background_scheduler.py
"""
백그라운드 태스크 라이프사이클 관리 스케줄러.
SchedulableTask 인터페이스를 구현한 태스크들을 등록하고
start/stop/suspend/resume을 통합 관리한다.
"""
import logging
from typing import Dict, List, Optional

from interfaces.schedulable_task import SchedulableTask, TaskState
from core.performance_profiler import PerformanceProfiler


class BackgroundScheduler:
    """SchedulableTask 기반 백그라운드 태스크 라이프사이클 관리자.

    비즈니스 로직 없이 순수하게 태스크의 생명주기만 관리한다.
    """

    def __init__(
        self,
        logger=None,
        performance_profiler: Optional[PerformanceProfiler] = None,
    ):
        self._logger = logger or logging.getLogger(__name__)
        self._pm = performance_profiler if performance_profiler else PerformanceProfiler(enabled=False)
        self._tasks: Dict[str, SchedulableTask] = {}  # name -> task

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
        """등록된 모든 태스크를 시작한다."""
        t_start = self._pm.start_timer()
        self._logger.info(f"[BackgroundScheduler] 전체 시작: {len(self._tasks)}개 태스크")
        for name, task in self._tasks.items():
            if task.state != TaskState.RUNNING:
                try:
                    await task.start()
                    self._logger.info(f"[BackgroundScheduler] '{name}' 시작 완료")
                except Exception as e:
                    self._logger.error(f"[BackgroundScheduler] '{name}' 시작 실패: {e}", exc_info=True)
        self._pm.log_timer("BackgroundScheduler.start_all", t_start)

    async def shutdown(self) -> None:
        """등록된 모든 태스크를 정상 종료한다."""
        t_start = self._pm.start_timer()
        self._logger.info(f"[BackgroundScheduler] 전체 종료: {len(self._tasks)}개 태스크")
        for name, task in self._tasks.items():
            if task.state in (TaskState.RUNNING, TaskState.SUSPENDED):
                try:
                    await task.stop()
                    self._logger.info(f"[BackgroundScheduler] '{name}' 종료 완료")
                except Exception as e:
                    self._logger.error(f"[BackgroundScheduler] '{name}' 종료 실패: {e}", exc_info=True)
        self._pm.log_timer("BackgroundScheduler.shutdown", t_start)

    async def suspend_all(self) -> None:
        """실행 중인 모든 태스크를 일시 중지한다."""
        self._logger.info("[BackgroundScheduler] 전체 일시 중지")
        for name, task in self._tasks.items():
            if task.state == TaskState.RUNNING:
                try:
                    await task.suspend()
                except Exception as e:
                    self._logger.error(f"[BackgroundScheduler] '{name}' 일시 중지 실패: {e}")

    async def resume_all(self) -> None:
        """일시 중지된 모든 태스크를 재개한다."""
        self._logger.info("[BackgroundScheduler] 전체 재개")
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
