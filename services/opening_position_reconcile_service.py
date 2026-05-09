"""장 시작 직후 로컬 가상 원장과 실제 계좌 잔고를 대사한다."""
from __future__ import annotations

import inspect
import logging
from typing import Optional

from common.types import ErrorCode, Exchange, ResCommonResponse


class OpeningPositionReconcileService:
    """실제 계좌 잔고를 기준으로 로컬 가상 원장을 대사한다."""

    def __init__(
        self,
        *,
        broker,
        virtual_trade_service,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._broker = broker
        self._vts = virtual_trade_service
        self._logger = logger or logging.getLogger(__name__)

    async def reconcile_once(self, *, exchange: Exchange = Exchange.KRX) -> dict:
        response = await self._call(self._broker.get_account_balance, exchange=exchange)
        if not self._is_success_response(response):
            msg = getattr(response, "msg1", None) or "잔고 조회 실패"
            self._logger.error(f"[OpeningPositionReconcile] broker balance failed: {msg}")
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
        return result

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
