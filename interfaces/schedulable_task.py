# interfaces/schedulable_task.py
from abc import ABC, abstractmethod
from enum import IntEnum, Enum
from typing import Dict


class TaskPriority(IntEnum):
    """태스크 우선순위. 낮은 숫자 = 높은 우선순위."""
    CRITICAL = 0    # 매수/매도 주문
    HIGH = 10       # User-initiated queries
    NORMAL = 50     # Strategy scheduler
    LOW = 100       # Background batch jobs


class TaskState(str, Enum):
    """태스크 실행 상태."""
    IDLE = "idle"
    RUNNING = "running"
    SUSPENDED = "suspended"
    STOPPED = "stopped"


class SchedulableTask(ABC):
    """스케줄러가 관리하는 태스크의 추상 인터페이스.

    모든 백그라운드/포그라운드 태스크는 이 인터페이스를 구현하여
    BackgroundScheduler/ForegroundScheduler에 등록할 수 있다.
    """

    @property
    @abstractmethod
    def task_name(self) -> str:
        """태스크 고유 식별자."""
        ...

    @property
    @abstractmethod
    def priority(self) -> TaskPriority:
        """태스크 우선순위."""
        ...

    @abstractmethod
    async def start(self) -> None:
        """태스크의 비동기 루프 또는 1회 실행을 시작한다."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """태스크를 정상 종료하고, 실행 중인 코루틴을 취소한다."""
        ...

    @abstractmethod
    async def suspend(self) -> None:
        """태스크를 일시 중지한다 (예: 포그라운드 액션이 API 대역폭 필요 시)."""
        ...

    @abstractmethod
    async def resume(self) -> None:
        """일시 중지된 태스크를 재개한다."""
        ...

    @property
    @abstractmethod
    def state(self) -> TaskState:
        """현재 태스크 상태."""
        ...

    @abstractmethod
    def get_progress(self) -> Dict:
        """태스크 진행률 및 상태 정보를 반환한다.

        모든 구현체는 최소한 {"running": bool} 키를 포함해야 한다.
        진행률이 있는 태스크는 processed, total, elapsed 등을 추가로 포함한다.
        """
        ...
