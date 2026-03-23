import pytest
from typing import Dict
from interfaces.schedulable_task import TaskPriority, TaskState, SchedulableTask

def test_task_priority_enum():
    """TaskPriority Enum 값 및 비교 동작 검증"""
    assert TaskPriority.CRITICAL == 0
    assert TaskPriority.HIGH == 10
    assert TaskPriority.NORMAL == 50
    assert TaskPriority.LOW == 100
    
    # IntEnum이므로 크기 비교가 가능해야 함
    assert TaskPriority.CRITICAL < TaskPriority.HIGH
    assert TaskPriority.HIGH < TaskPriority.NORMAL
    assert TaskPriority.NORMAL < TaskPriority.LOW

def test_task_state_enum():
    """TaskState Enum 값 및 문자열 동작 검증"""
    assert TaskState.IDLE == "idle"
    assert TaskState.RUNNING == "running"
    assert TaskState.SUSPENDED == "suspended"
    assert TaskState.STOPPED == "stopped"

def test_schedulable_task_instantiation_error():
    """추상 메서드 미구현 시 인스턴스화 불가 검증"""
    class IncompleteTask(SchedulableTask):
        pass

    with pytest.raises(TypeError):
        IncompleteTask()

@pytest.mark.asyncio
async def test_schedulable_task_concrete_implementation():
    """SchedulableTask 인터페이스를 구현한 구체 클래스 동작 검증"""
    
    class ConcreteTask(SchedulableTask):
        def __init__(self):
            self._state = TaskState.IDLE
            
        @property
        def task_name(self) -> str: return "test_task"

        @property
        def priority(self) -> TaskPriority: return TaskPriority.NORMAL

        async def start(self) -> None: self._state = TaskState.RUNNING
        async def stop(self) -> None: self._state = TaskState.STOPPED
        async def suspend(self) -> None: self._state = TaskState.SUSPENDED
        async def resume(self) -> None: self._state = TaskState.RUNNING

        @property
        def state(self) -> TaskState: return self._state

        def get_progress(self) -> Dict: return {"running": self._state == TaskState.RUNNING}

    task = ConcreteTask()
    
    assert task.task_name == "test_task"
    assert task.priority == TaskPriority.NORMAL
    assert task.state == TaskState.IDLE
    assert task.get_progress() == {"running": False}
    
    await task.start()
    assert task.state == TaskState.RUNNING
    assert task.get_progress() == {"running": True}
    
    await task.suspend()
    assert task.state == TaskState.SUSPENDED
    
    await task.resume()
    assert task.state == TaskState.RUNNING
    
    await task.stop()
    assert task.state == TaskState.STOPPED