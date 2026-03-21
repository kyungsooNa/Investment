# core/retry_queue/api_request_queue.py
import asyncio
import dataclasses
import random
from typing import Callable, Coroutine, Any

from core.retry_queue.retry_classifier import classify, RequestOutcome


@dataclasses.dataclass
class QueuedRequest:
    fn: Callable[..., Coroutine]
    args: tuple
    kwargs: dict
    future: asyncio.Future     # 호출자에게 최종 결과를 전달하는 Future
    attempt: int = 0
    request_id: str = ""


class ApiRequestQueue:
    """
    조회 API 요청의 비동기 재시도 큐.

    - 성공(DONE)  : future 완료 + _done_q 에 적재
    - 재시도(RETRY): 지수 백오프 후 재실행 (asyncio.Task 생성, 큐 블로킹 없음)
    - 최종 실패(FAIL): future 완료(실패 결과) + _fail_q 에 적재

    주의: 주문(trading) API 는 멱등성 문제로 이 큐를 사용하지 않습니다.
    """

    MAX_RETRIES = 5
    BASE_DELAY  = 1.0   # 초 (지수 백오프: BASE_DELAY * 2^(attempt-1))
    MAX_DELAY   = 30.0  # 최대 지연

    def __init__(self, logger):
        self._logger = logger
        self._done_q: asyncio.Queue[tuple[QueuedRequest, Any]] = asyncio.Queue()
        self._fail_q: asyncio.Queue[tuple[QueuedRequest, Any]] = asyncio.Queue()
        self._pending_tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    # 공개 인터페이스
    # ------------------------------------------------------------------

    async def submit(self, fn: Callable, *args, request_id: str = "", **kwargs) -> asyncio.Future:
        """
        요청을 즉시 실행합니다.
        실패 시 백그라운드에서 자동 재시도하며, 최종 결과를 Future로 반환합니다.
        """
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        req = QueuedRequest(fn, args, kwargs, future, attempt=0, request_id=request_id)
        self._spawn(self._execute(req))
        return future

    async def stop(self):
        """대기 중인 모든 재시도 태스크를 취소합니다."""
        tasks = list(self._pending_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._logger.info(f"[RetryQueue] 종료 완료 (취소된 태스크: {len(tasks)}개)")

    # ------------------------------------------------------------------
    # 내부 구현
    # ------------------------------------------------------------------

    def _spawn(self, coro: Coroutine) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
        return task

    async def _execute(self, req: QueuedRequest):
        try:
            result = await req.fn(*req.args, **req.kwargs)
        except Exception as e:
            self._logger.warning(
                f"[RetryQueue] 예외 발생 (id={req.request_id}, attempt={req.attempt}): {e}"
            )
            result = None

        outcome = classify(result)

        if outcome == RequestOutcome.DONE:
            self._resolve(req, result)
            await self._done_q.put((req, result))

        elif outcome == RequestOutcome.RETRY:
            req.attempt += 1
            if req.attempt >= self.MAX_RETRIES:
                msg = getattr(result, "msg1", "응답 없음")
                self._logger.error(
                    f"[RetryQueue] 최종 실패 (id={req.request_id}, "
                    f"시도={req.attempt}/{self.MAX_RETRIES}): {msg}"
                )
                self._resolve(req, result)
                await self._fail_q.put((req, result))
            else:
                base = min(self.BASE_DELAY * (2 ** (req.attempt - 1)), self.MAX_DELAY)
                delay = base * (0.5 + random.random() * 0.5)  # [50%, 100%] jitter
                msg = getattr(result, "msg1", "응답 없음")
                self._logger.warning(
                    f"[RetryQueue] {delay:.1f}초 후 재시도 "
                    f"(id={req.request_id}, {req.attempt}/{self.MAX_RETRIES}): {msg}"
                )
                self._spawn(self._delay_and_execute(req, delay))

        else:  # FAIL
            msg = getattr(result, "msg1", "응답 없음")
            self._logger.error(
                f"[RetryQueue] 재시도 불가 실패 (id={req.request_id}): {msg}"
            )
            self._resolve(req, result)
            await self._fail_q.put((req, result))

    async def _delay_and_execute(self, req: QueuedRequest, delay: float):
        await asyncio.sleep(delay)
        await self._execute(req)

    def _resolve(self, req: QueuedRequest, result: Any):
        """Future 가 아직 완료되지 않은 경우에만 결과를 설정합니다."""
        if not req.future.done():
            req.future.set_result(result)

    # ------------------------------------------------------------------
    # 외부 소비자용 (알림 등에 활용 가능)
    # ------------------------------------------------------------------

    @property
    def done_queue(self) -> asyncio.Queue:
        return self._done_q

    @property
    def fail_queue(self) -> asyncio.Queue:
        return self._fail_q

    @property
    def pending_count(self) -> int:
        return len(self._pending_tasks)
