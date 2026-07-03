"""장중 캡처 후보 종목 프로그램매매 WS 구독 태스크."""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState
from repositories.streaming_stock_repo import StreamingType
from services.subscription_policy import SubscriptionPriority
from task.background.capture_candidates import resolve_capture_codes


class ProgramCaptureSubscriptionTask(SchedulableTask):
    """장중에 캡처 후보(보유+워치리스트)를 프로그램매매 WS로 구독해 pt_history에 장중 시계열을 축적한다.

    todo 1-5: 캡처 코퍼스의 program overlay가 daily_rest(일 단위 aggregate)에
    의존하는 한계 보완 — 장중 프로그램 순매수 시계열은 pt_history(WS 수신 DB)에만
    쌓이는데 구독이 수동 UI 종목뿐이라 후보 종목 데이터가 축적되지 않았다.

    안전 설계:
      - SubscriptionPriority.LOW → 트레이딩용 price 구독(HIGH/MEDIUM)을 밀어내지
        않으며(PT=2슬롯/종목), 슬롯 압박 시 rebalance가 이 카테고리를 먼저 해지한다.
      - 수동 UI로 이미 PT desired인 종목은 대상에서 제외해 해지/영속 상태 간섭을 막는다.
      - 구독 목록을 scheduler_store에 영속화 — 크래시 잔재를 재시작 시 카테고리로
        재편입한 뒤 해지해 pt_subscriptions 영구 오염을 방지한다.
    """

    CATEGORY_KEY = "microstructure_capture"
    CHECK_INTERVAL_SEC = 60
    MAX_CODES = 10  # PT=2슬롯/종목 — 캡처용 상한 (트레이딩 슬롯 여유 보존)

    def __init__(
        self,
        *,
        subscription_policy,
        streaming_stock_repo=None,
        universe_service=None,
        virtual_trade_service=None,
        market_calendar_service=None,
        market_clock=None,
        scheduler_store=None,
        max_codes: Optional[int] = None,
        check_interval_sec: Optional[int] = None,
        logger=None,
    ) -> None:
        self._policy = subscription_policy
        self._streaming_stock_repo = streaming_stock_repo
        self._universe_service = universe_service
        self._virtual_trade_service = virtual_trade_service
        self._mcs = market_calendar_service
        self._market_clock = market_clock
        self._scheduler_store = scheduler_store
        self._max_codes = max_codes if max_codes is not None else self.MAX_CODES
        self._check_interval_sec = check_interval_sec or self.CHECK_INTERVAL_SEC
        self._logger = logger or logging.getLogger(__name__)
        self._state = TaskState.IDLE
        self._tasks: List[asyncio.Task] = []
        self._state_key = "program_capture_subscribed_codes"
        self._synced_date: Optional[str] = None
        self._synced_codes: List[str] = []
        self._adopted = False  # 프로세스 시작 후 store 잔재 재편입 1회 수행 여부

    @property
    def task_name(self) -> str:
        return "program_capture_subscription"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    @property
    def state(self) -> TaskState:
        return self._state

    async def start(self) -> None:
        if any(not task.done() for task in self._tasks):
            return
        if self._state == TaskState.STOPPED:
            self._state = TaskState.IDLE
        self._tasks.append(asyncio.create_task(self._loop()))

    async def stop(self) -> None:
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._state = TaskState.STOPPED

    async def suspend(self) -> None:
        if self._state == TaskState.RUNNING:
            self._state = TaskState.SUSPENDED

    async def resume(self) -> None:
        if self._state == TaskState.SUSPENDED:
            self._state = TaskState.IDLE

    def get_progress(self) -> Dict:
        return {
            "running": self._state == TaskState.RUNNING,
            "synced_date": self._synced_date,
            "synced_codes": list(self._synced_codes),
        }

    async def _loop(self) -> None:
        while True:
            try:
                if self._state != TaskState.SUSPENDED:
                    self._state = TaskState.RUNNING
                    try:
                        await self._tick()
                    finally:
                        if self._state == TaskState.RUNNING:
                            self._state = TaskState.IDLE
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._logger.error(f"{self.task_name}: loop error — {exc}", exc_info=True)
            await asyncio.sleep(self._check_interval_sec)

    async def _tick(self) -> None:
        if self._policy is None or self._market_clock is None:
            return
        if not self._adopted:
            stored = self._load_stored_codes()
            if stored:
                # 크래시/재시작 잔재 재편입 — 이후 sync가 카테고리를 교체/해지하며 정리한다.
                await self._policy.sync_subscriptions(
                    stored, self.CATEGORY_KEY,
                    SubscriptionPriority.LOW, StreamingType.PROGRAM_TRADING,
                )
                self._synced_codes = stored
            self._adopted = True

        if await self._is_market_open_now():
            today = self._market_clock.get_current_kst_date_str()
            if self._synced_date == today:
                return
            codes = await self._resolve_target_codes()
            await self._policy.sync_subscriptions(
                codes, self.CATEGORY_KEY,
                SubscriptionPriority.LOW, StreamingType.PROGRAM_TRADING,
            )
            self._synced_date = today
            self._synced_codes = codes
            self._save_codes(codes)
            self._logger.info(
                f"{self.task_name}: {today} 캡처 후보 PT 구독 동기화 — {len(codes)}종목"
            )
        elif self._synced_codes:
            await self._policy.sync_subscriptions(
                [], self.CATEGORY_KEY,
                SubscriptionPriority.LOW, StreamingType.PROGRAM_TRADING,
            )
            self._synced_date = None
            self._synced_codes = []
            self._save_codes([])
            self._logger.info(f"{self.task_name}: 장외 — 캡처 후보 PT 구독 해지")

    async def _is_market_open_now(self) -> bool:
        now = self._market_clock.get_current_kst_time()
        if not self._market_clock.is_market_operating_hours(now):
            return False
        if self._mcs is not None:
            try:
                if not await self._mcs.is_business_day(now.strftime("%Y%m%d")):
                    return False
            except Exception:
                return False
        return True

    async def _resolve_target_codes(self) -> List[str]:
        codes = await resolve_capture_codes(
            virtual_trade_service=self._virtual_trade_service,
            universe_service=self._universe_service,
            max_codes=None,
            logger=self._logger,
            task_name=self.task_name,
        )
        already_desired: set = set()
        if self._streaming_stock_repo is not None:
            try:
                already_desired = set(
                    self._streaming_stock_repo.get_desired(StreamingType.PROGRAM_TRADING)
                )
            except Exception as exc:
                self._logger.warning(f"{self.task_name}: PT desired 조회 실패 — {exc}")
        # 우리 카테고리가 이미 올린 desired는 제외 대상이 아니다 (재시작 재편입 케이스)
        manual_desired = already_desired - set(self._synced_codes)
        filtered = [code for code in codes if code not in manual_desired]
        return filtered[: self._max_codes]

    def _load_stored_codes(self) -> List[str]:
        if self._scheduler_store is None:
            return []
        try:
            raw = self._scheduler_store.load_keyed(self._state_key)
        except Exception as exc:
            self._logger.warning(f"{self.task_name}: 구독 목록 로드 실패 — {exc}")
            return []
        if not raw:
            return []
        return [code for code in str(raw).split(",") if code]

    def _save_codes(self, codes: List[str]) -> None:
        if self._scheduler_store is None:
            return
        try:
            self._scheduler_store.save_keyed(self._state_key, ",".join(codes))
        except Exception as exc:
            self._logger.warning(f"{self.task_name}: 구독 목록 저장 실패 — {exc}")
