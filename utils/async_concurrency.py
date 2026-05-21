"""비동기 동시성 헬퍼.

`bounded_gather`는 `asyncio.gather`와 같은 결과 contract(입력 순서 보존, return_exceptions 지원)를
유지하면서 동시에 실행되는 코루틴 수를 semaphore로 제한한다.

전략 entry는 chunk 루프(`for i in range(0, len, chunk): gather(chunk)`) 패턴으로 이미 동시성을
제한하지만, exit 쪽은 holdings 전체를 unbounded `gather`로 처리한다. 보유 종목이 많아지면
순간 REST 호출량이 합산되고, 손절/청산이 entry scan과 같은 폭으로 경쟁한다.

이 헬퍼는 그 차이를 메꾸기 위한 공통 진입점이다. exit 쪽이 entry chunk_size보다 높은 limit를
받도록 호출하면 청산 경로에 우선순위를 부여할 수 있다.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Iterable, List


async def bounded_gather(
    coros: Iterable[Awaitable[Any]],
    limit: int,
    *,
    return_exceptions: bool = False,
) -> List[Any]:
    if limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")

    coro_list = list(coros)
    if not coro_list:
        return []

    sem = asyncio.Semaphore(limit)

    async def _run(coro: Awaitable[Any]) -> Any:
        async with sem:
            return await coro

    return await asyncio.gather(
        *(_run(c) for c in coro_list),
        return_exceptions=return_exceptions,
    )
