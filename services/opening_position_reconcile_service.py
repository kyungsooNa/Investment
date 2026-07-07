"""장 시작 직후 로컬 가상 원장과 실제 계좌 잔고를 대사한다."""
from __future__ import annotations

import inspect
import logging
from datetime import datetime
from typing import Optional

import pytz

from common.types import ErrorCode, Exchange, ResCommonResponse

_KST = pytz.timezone("Asia/Seoul")
_BROKER_RECONCILED_STRATEGY = "broker_reconciled"


class OpeningPositionReconcileService:
    """실제 계좌 잔고를 기준으로 로컬 가상 원장을 대사한다."""

    STALE_BROKER_RECONCILED_DAYS = 7

    def __init__(
        self,
        *,
        broker,
        virtual_trade_service,
        kill_switch_service=None,
        market_clock=None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._broker = broker
        self._vts = virtual_trade_service
        self._kill_switch = kill_switch_service
        self._market_clock = market_clock
        self._logger = logger or logging.getLogger(__name__)

    async def reconcile_once(self, *, exchange: Exchange = Exchange.KRX) -> dict:
        response = await self._call(self._broker.get_account_balance, exchange=exchange)
        if not self._is_success_response(response):
            msg = getattr(response, "msg1", None) or "잔고 조회 실패"
            self._logger.error(f"[OpeningPositionReconcile] broker balance failed: {msg}")
            if self._kill_switch is not None:
                await self._kill_switch.record_api_failure(f"opening_reconcile: {msg}")
            return self._empty_result(error=msg)

        actual_holdings = ((response.data or {}).get("output1", []) if isinstance(response.data, dict) else [])
        result = await self._vts.reconcile_with_broker(actual_holdings, logger=self._logger)
        result.setdefault("force_closed", [])
        result.setdefault("unknown_in_broker", [])
        result.setdefault("quantity_mismatches", [])
        result["mismatch_count"] = (
            len(result["force_closed"])
            + len(result["unknown_in_broker"])
            + len(result["quantity_mismatches"])
        )
        result["error"] = None
        result["stale_broker_reconciled"] = self._get_stale_broker_reconciled()
        return result

    def _get_stale_broker_reconciled(self) -> list:
        """오래 방치된 broker_reconciled HOLD 포지션을 찾는다.

        broker_reconciled은 실전략 없이 broker 잔고 대사로만 등록되는 전략명이라
        아무도 손절/청산을 관리하지 않는다. 등록일로부터 STALE_BROKER_RECONCILED_DAYS일
        이상 지난 건은 매 회차 재보고해 조용히 방치되지 않게 한다.
        """
        holds = self._vts.get_holds_by_strategy(_BROKER_RECONCILED_STRATEGY) or []
        now = self._current_kst_time()
        stale = []
        for hold in holds:
            code = str(hold.get("code", "")).strip()
            buy_date_raw = str(hold.get("buy_date", "")).strip()
            if not code or not buy_date_raw:
                continue
            try:
                buy_date = datetime.strptime(buy_date_raw[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            days_held = (now.date() - buy_date.date()).days
            if days_held >= self.STALE_BROKER_RECONCILED_DAYS:
                stale.append({"code": code, "days_held": days_held})
        return stale

    def _current_kst_time(self) -> datetime:
        if self._market_clock is not None:
            return self._market_clock.get_current_kst_time()
        return datetime.now(tz=_KST)

    @staticmethod
    def _is_success_response(response) -> bool:
        if isinstance(response, ResCommonResponse):
            return response.rt_cd == ErrorCode.SUCCESS.value
        return getattr(response, "rt_cd", None) == ErrorCode.SUCCESS.value

    @staticmethod
    async def _call(method, *args, **kwargs):
        try:
            response = method(*args, **kwargs)
        except TypeError:
            response = method(*args)
        if inspect.isawaitable(response):
            return await response
        return response

    @staticmethod
    def _empty_result(*, error: str | None = None) -> dict:
        return {
            "force_closed": [],
            "unknown_in_broker": [],
            "quantity_mismatches": [],
            "mismatch_count": 0,
            "error": error,
        }
