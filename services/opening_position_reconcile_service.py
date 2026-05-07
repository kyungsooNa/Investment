"""장 시작 직후 로컬 가상 원장과 실제 계좌 잔고를 대사한다."""
from __future__ import annotations

import inspect
import logging
from typing import Any, Optional

from common.types import ErrorCode, Exchange, ResCommonResponse


class OpeningPositionReconcileService:
    """VirtualTradeRepository HOLD 수량을 목표 포지션으로 보고 실제 계좌 수량과 맞춘다."""

    SOURCE = "reconcile:opening"

    def __init__(
        self,
        *,
        broker,
        order_execution_service,
        virtual_trade_service,
        detect_only: bool = True,
        auto_buy_missing_local: bool = False,
        auto_sell_extra_broker: bool = False,
        allow_sell_unknown_broker: bool = False,
        managed_codes: Optional[set[str]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._broker = broker
        self._oes = order_execution_service
        self._vts = virtual_trade_service
        self._detect_only = detect_only
        self._auto_buy_missing_local = auto_buy_missing_local
        self._auto_sell_extra_broker = auto_sell_extra_broker
        self._allow_sell_unknown_broker = allow_sell_unknown_broker
        self._managed_codes = {str(code).strip() for code in managed_codes or set() if str(code).strip()}
        self._logger = logger or logging.getLogger(__name__)

    async def reconcile_once(self, *, exchange: Exchange = Exchange.KRX) -> dict:
        response = await self._call(self._broker.get_account_balance, exchange=exchange)
        if not self._is_success_response(response):
            msg = getattr(response, "msg1", None) or "잔고 조회 실패"
            self._logger.error(f"[OpeningPositionReconcile] broker balance failed: {msg}")
            return self._empty_result(error=msg)

        actual_holdings = ((response.data or {}).get("output1", []) if isinstance(response.data, dict) else [])
        local_positions = self._normalize_local_positions(self._vts.get_holds())
        broker_positions = self._normalize_broker_positions(actual_holdings)
        plan = self._build_plan(local_positions, broker_positions)

        result = {
            "detect_only": self._detect_only,
            **plan,
            "executed": [],
            "error": None,
        }
        result["mismatch_count"] = (
            len(result["planned_buys"]) + len(result["planned_sells"]) + len(result["skipped"])
        )

        if self._detect_only:
            self._logger.info(f"[OpeningPositionReconcile] detect_only plan={result}")
            return result

        await self._execute_plan(result, exchange)
        return result

    async def _execute_plan(self, result: dict, exchange: Exchange) -> None:
        for item in result["planned_buys"]:
            if not self._auto_buy_missing_local:
                result["skipped"].append({**item, "reason": "auto_buy_disabled"})
                continue
            response = await self._oes.handle_place_buy_order(
                item["code"],
                0,
                item["qty"],
                source=self.SOURCE,
                finalize_immediately=False,
            )
            result["executed"].append(self._order_result("BUY", item, response))

        for item in result["planned_sells"]:
            if not self._auto_sell_extra_broker:
                result["skipped"].append({**item, "reason": "auto_sell_disabled"})
                continue
            response = await self._oes.handle_place_sell_order(
                item["code"],
                0,
                item["qty"],
                source=self.SOURCE,
                finalize_immediately=False,
            )
            result["executed"].append(self._order_result("SELL", item, response))

    def _build_plan(self, local_positions: dict[str, dict], broker_positions: dict[str, int]) -> dict:
        planned_buys: list[dict] = []
        planned_sells: list[dict] = []
        skipped: list[dict] = []
        matched: list[dict] = []

        all_codes = sorted(set(local_positions) | set(broker_positions))
        managed_codes = set(local_positions) | self._managed_codes
        for code in all_codes:
            local_qty = int(local_positions.get(code, {}).get("qty", 0))
            broker_qty = int(broker_positions.get(code, 0))
            diff = local_qty - broker_qty
            if diff == 0:
                if local_qty > 0:
                    matched.append({"code": code, "qty": local_qty})
                continue

            strategy = local_positions.get(code, {}).get("strategy", "")
            if diff > 0:
                planned_buys.append({
                    "code": code,
                    "qty": diff,
                    "local_qty": local_qty,
                    "broker_qty": broker_qty,
                    "strategy": strategy,
                })
                continue

            sell_qty = abs(diff)
            if code not in managed_codes and not self._allow_sell_unknown_broker:
                skipped.append({
                    "code": code,
                    "qty": sell_qty,
                    "local_qty": local_qty,
                    "broker_qty": broker_qty,
                    "reason": "unknown_broker_holding",
                })
                continue

            reason = "broker_extra_qty" if local_qty > 0 else "unknown_broker_holding"
            planned_sells.append({
                "code": code,
                "qty": sell_qty,
                "local_qty": local_qty,
                "broker_qty": broker_qty,
                "reason": reason,
            })

        return {
            "planned_buys": planned_buys,
            "planned_sells": planned_sells,
            "skipped": skipped,
            "matched": matched,
        }

    @staticmethod
    def _normalize_local_positions(holds: list[dict]) -> dict[str, dict]:
        positions: dict[str, dict] = {}
        for hold in holds or []:
            code = str(hold.get("code", "")).strip()
            if not code:
                continue
            qty = OpeningPositionReconcileService._to_int(hold.get("qty"), default=1)
            if qty <= 0:
                continue
            current = positions.setdefault(code, {"qty": 0, "strategies": []})
            current["qty"] += qty
            strategy = str(hold.get("strategy", "")).strip()
            if strategy:
                current["strategies"].append(strategy)
                current.setdefault("strategy", strategy)
        return positions

    @staticmethod
    def _normalize_broker_positions(holdings: list[dict]) -> dict[str, int]:
        positions: dict[str, int] = {}
        for holding in holdings or []:
            code = str(
                holding.get("pdno")
                or holding.get("PDNO")
                or holding.get("code")
                or ""
            ).strip()
            if not code:
                continue
            qty = OpeningPositionReconcileService._to_int(
                holding.get("hldg_qty")
                or holding.get("HLDG_QTY")
                or holding.get("qty")
                or holding.get("quantity")
            )
            if qty > 0:
                positions[code] = positions.get(code, 0) + qty
        return positions

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            text = str(value if value is not None else "").replace(",", "").strip()
            return int(float(text)) if text else default
        except (TypeError, ValueError):
            return default

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
    def _order_result(action: str, item: dict, response) -> dict:
        return {
            "action": action,
            "code": item["code"],
            "qty": item["qty"],
            "success": OpeningPositionReconcileService._is_success_response(response),
            "message": getattr(response, "msg1", "") if response is not None else "응답 없음",
        }

    @staticmethod
    def _empty_result(*, error: str | None = None) -> dict:
        return {
            "detect_only": True,
            "planned_buys": [],
            "planned_sells": [],
            "skipped": [],
            "matched": [],
            "executed": [],
            "mismatch_count": 0,
            "error": error,
        }
