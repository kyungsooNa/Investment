"""서비스가 백그라운드 갱신 작업과 통신하기 위한 포트."""

from typing import Any, Protocol


class MinerviniRefreshTask(Protocol):
    async def get_minervini_stage2_cache(self) -> Any: ...

    def get_progress(self) -> dict: ...

    async def refresh_minervini_stage2(self) -> None: ...


class NewHighRefreshTask(Protocol):
    def get_progress(self) -> dict: ...

    async def get_newhigh_cache(self, limit: int = 200) -> Any: ...
