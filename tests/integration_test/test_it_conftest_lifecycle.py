import asyncio

import pytest
from httpx import AsyncClient

from tests.integration_test.conftest import _drain_strategy_state_load_tasks


@pytest.mark.asyncio
async def test_drain_strategy_state_load_tasks_cancels_stuck_task():
    started = asyncio.Event()

    async def _load_state_async():
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(_load_state_async())
    await started.wait()

    await _drain_strategy_state_load_tasks(timeout=0.01)

    assert task.done()
    assert task.cancelled()


@pytest.mark.asyncio
async def test_deep_paper_ctx_uses_same_loop_async_client(deep_paper_ctx):
    assert isinstance(deep_paper_ctx._test_client, AsyncClient)
