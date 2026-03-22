# task/background/strategy_scheduler_task_adapter.py
"""
StrategyScheduler를 SchedulableTask 인터페이스로 래핑하는 어댑터.
기존 StrategyScheduler 코드를 변경하지 않고 BackgroundScheduler에 등록할 수 있게 한다.
"""
from typing import Dict

from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState
from scheduler.strategy_scheduler import StrategyScheduler


class StrategySchedulerTaskAdapter(SchedulableTask):
    """StrategyScheduler를 SchedulableTask 인터페이스로 래핑한다.

    전략 스케줄러의 scan 루프는 background로 동작하지만,
    전략에서 발생하는 매수/매도 주문은 foreground 우선순위로 실행된다.
    """

    def __init__(self, scheduler: StrategyScheduler):
        self._scheduler = scheduler
        self._state: TaskState = TaskState.IDLE

    @property
    def task_name(self) -> str:
        return "strategy_scheduler"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.NORMAL

    @property
    def state(self) -> TaskState:
        return self._state

    async def start(self) -> None:
        """전략 스케줄러의 이전 상태를 복원하고 시작한다."""
        if self._state == TaskState.RUNNING:
            return
        await self._scheduler.restore_state()
        self._state = TaskState.RUNNING

    async def stop(self) -> None:
        """전략 스케줄러를 정지하고 상태를 저장한다."""
        if self._scheduler._running:
            await self._scheduler.stop(save_state=True)
        self._state = TaskState.STOPPED

    async def suspend(self) -> None:
        """전략 스케줄러를 일시 중지한다.

        Note: 전략의 매수/매도는 critical 우선순위이므로 실제로는
        scan 루프만 일시 중지하는 것이 이상적이다.
        현재는 상태 플래그만 변경하고, 실제 루프 중지는 하지 않는다.
        """
        if self._state == TaskState.RUNNING:
            self._state = TaskState.SUSPENDED

    async def resume(self) -> None:
        """전략 스케줄러를 재개한다."""
        if self._state == TaskState.SUSPENDED:
            self._state = TaskState.RUNNING

    def get_progress(self) -> Dict:
        """태스크 진행률 반환 (SchedulableTask 인터페이스 구현).

        전략 스케줄러는 배치 진행률이 없으므로 활성 전략 수를 반환한다.
        """
        active_strategies = 0
        total_strategies = 0
        try:
            status = self._scheduler.get_status()
            strategies = status.get("strategies", [])
            total_strategies = len(strategies)
            active_strategies = sum(1 for s in strategies if s.get("enabled"))
        except Exception:
            pass

        return {
            "running": self._state == TaskState.RUNNING,
            "active_strategies": active_strategies,
            "total_strategies": total_strategies,
        }
