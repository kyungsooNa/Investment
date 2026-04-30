"""Pre-market operational health check task."""
from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import timedelta
from typing import Dict, List, Optional

from common.types import ErrorCode, ResCommonResponse
from interfaces.schedulable_task import SchedulableTask, TaskPriority, TaskState
from services.notification_service import NotificationCategory, NotificationLevel


class PreMarketHealthCheckTask(SchedulableTask):
    """Runs lightweight health checks before market open."""

    CHECK_INTERVAL_SEC = 60
    PRE_OPEN_WINDOW_MIN = 30

    def __init__(
        self,
        *,
        broker=None,
        env=None,
        market_calendar_service=None,
        market_clock=None,
        streaming_stock_repo=None,
        data_quality_service=None,
        notification_service=None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._broker = broker
        self._env = env
        self._mcs = market_calendar_service
        self._market_clock = market_clock
        self._streaming_stock_repo = streaming_stock_repo
        self._data_quality_service = data_quality_service
        self._ns = notification_service
        self._logger = logger or logging.getLogger(__name__)
        self._state = TaskState.IDLE
        self._tasks: List[asyncio.Task] = []
        self._last_checked_date: Optional[str] = None
        self._last_result: Dict = {"ok": True, "issues": []}

    @property
    def task_name(self) -> str:
        return "pre_market_health_check"

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.NORMAL

    @property
    def state(self) -> TaskState:
        return self._state

    async def start(self) -> None:
        if self._state == TaskState.RUNNING:
            return
        self._state = TaskState.RUNNING
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
            self._state = TaskState.RUNNING

    def get_progress(self) -> Dict:
        return {
            "running": self._state == TaskState.RUNNING,
            "last_checked_date": self._last_checked_date,
            "last_result": self._last_result,
        }

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.CHECK_INTERVAL_SEC)
                if self._state == TaskState.SUSPENDED:
                    continue
                if await self._should_run_now():
                    await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._logger.error(f"[PreMarketHealthCheck] loop error: {exc}", exc_info=True)

    async def _should_run_now(self) -> bool:
        if not self._market_clock or not self._mcs:
            return False
        now = self._market_clock.get_current_kst_time()
        date_key = now.strftime("%Y%m%d")
        if self._last_checked_date == date_key:
            return False
        try:
            if not await self._mcs.is_business_day(date_key):
                return False
        except Exception:
            return False
        open_time = self._market_clock.get_market_open_time()
        return open_time - timedelta(minutes=self.PRE_OPEN_WINDOW_MIN) <= now < open_time

    async def run_once(self) -> Dict:
        issues: List[str] = []
        if self._env is None:
            issues.append("env_missing")
        else:
            issues.extend(self._check_env_config())
            if not getattr(self._env, "access_token", None) and not getattr(self._env, "_access_token", None):
                token_ok = await self._check_token_access()
                if not token_ok:
                    issues.append("token_check_failed")
        if self._broker is None:
            issues.append("broker_missing")
        else:
            await self._check_broker_api("get_current_price", issues, "quotation_api_failed", "005930")
            await self._check_broker_api("get_account_balance", issues, "account_api_failed")
        if self._data_quality_service is None:
            issues.append("data_quality_missing")
        elif not self._data_quality_service.config.enabled:
            issues.append("data_quality_disabled")
        if self._streaming_stock_repo is None:
            issues.append("streaming_repo_missing")
        else:
            self._check_streaming_desired_state(issues)

        ok = not issues
        self._last_checked_date = (
            self._market_clock.get_current_kst_time().strftime("%Y%m%d")
            if self._market_clock else None
        )
        self._last_result = {"ok": ok, "issues": issues}
        if issues:
            self._logger.warning(f"[PreMarketHealthCheck] issues={issues}")
            if self._ns:
                await self._ns.emit(
                    NotificationCategory.SYSTEM,
                    NotificationLevel.ERROR,
                    "장 시작 전 상태 점검 실패",
                    ", ".join(issues),
                    metadata={"issues": issues},
                )
        else:
            self._logger.info("[PreMarketHealthCheck] OK")
        return self._last_result

    def _check_env_config(self) -> List[str]:
        active_config = getattr(self._env, "active_config", None) or {}
        required_keys = ("api_key", "api_secret_key", "stock_account_number", "base_url", "websocket_url")
        missing = [key for key in required_keys if not active_config.get(key)]
        return [f"config_missing:{key}" for key in missing]

    async def _check_token_access(self) -> bool:
        getter = getattr(self._env, "get_access_token", None)
        if not callable(getter):
            return False
        try:
            token = getter()
            if inspect.isawaitable(token):
                token = await token
            return bool(token)
        except Exception as exc:
            self._logger.warning(f"[PreMarketHealthCheck] token check failed: {exc}", exc_info=True)
            return False

    async def _check_broker_api(self, method_name: str, issues: List[str], issue_code: str, *args) -> None:
        method = getattr(self._broker, method_name, None)
        if not callable(method):
            issues.append(f"{issue_code}:method_missing")
            return
        try:
            response = method(*args)
            if inspect.isawaitable(response):
                response = await response
        except Exception as exc:
            self._logger.warning(f"[PreMarketHealthCheck] {method_name} failed: {exc}", exc_info=True)
            issues.append(f"{issue_code}:exception")
            return
        if not self._is_success_response(response):
            issues.append(issue_code)

    def _check_streaming_desired_state(self, issues: List[str]) -> None:
        getter = getattr(self._streaming_stock_repo, "get_desired", None)
        if not callable(getter):
            return
        try:
            getter()
        except TypeError:
            return
        except Exception as exc:
            self._logger.warning(f"[PreMarketHealthCheck] streaming desired check failed: {exc}", exc_info=True)
            issues.append("streaming_desired_check_failed")

    @staticmethod
    def _is_success_response(response) -> bool:
        if isinstance(response, ResCommonResponse):
            return response.rt_cd == ErrorCode.SUCCESS.value
        return getattr(response, "rt_cd", None) == ErrorCode.SUCCESS.value
