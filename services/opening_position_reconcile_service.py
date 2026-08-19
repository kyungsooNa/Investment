"""장 시작 직후 로컬 가상 원장과 실제 계좌 잔고를 대사한다."""
from __future__ import annotations

import inspect
import json
import logging
from datetime import datetime
from pathlib import Path
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
        is_paper_trading: bool = False,
        paper_account_number: Optional[str] = None,
        paper_account_rollover_state_file_path: str = "data/paper_account_reconcile_state.json",
        block_force_close_on_paper_rollover: bool = True,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._broker = broker
        self._vts = virtual_trade_service
        self._kill_switch = kill_switch_service
        self._market_clock = market_clock
        self._is_paper_trading = bool(is_paper_trading)
        self._paper_account_number = str(paper_account_number or "").strip()
        self._paper_account_rollover_state_file = Path(paper_account_rollover_state_file_path)
        self._block_force_close_on_paper_rollover = bool(block_force_close_on_paper_rollover)
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
        should_block_force_close, rollover_info = self._detect_paper_account_rollover(actual_holdings)
        if should_block_force_close:
            result = await self._vts.reconcile_with_broker(
                actual_holdings,
                logger=self._logger,
                allow_force_close=False,
            )
        else:
            result = await self._vts.reconcile_with_broker(actual_holdings, logger=self._logger)
        result.setdefault("force_closed", [])
        result.setdefault("unknown_in_broker", [])
        result.setdefault("quantity_mismatches", [])
        result["mismatch_count"] = (
            len(result["force_closed"])
            + len(result.get("force_close_blocked") or [])
            + len(result["unknown_in_broker"])
            + len(result["quantity_mismatches"])
        )
        result["error"] = None
        if rollover_info is not None:
            result["paper_account_rollover"] = rollover_info
        elif self._is_paper_trading and self._paper_account_number:
            self._save_paper_rollover_state(self._paper_account_number)
        result["stale_broker_reconciled"] = self._get_stale_broker_reconciled()
        return result

    def _detect_paper_account_rollover(self, actual_holdings: list[dict]) -> tuple[bool, dict | None]:
        if (
            not self._block_force_close_on_paper_rollover
            or not self._is_paper_trading
            or not self._paper_account_number
        ):
            return False, None

        local_holds = self._vts.get_holds() or []
        if not local_holds:
            return False, None

        if self._has_positive_broker_holding(actual_holdings):
            return False, None

        previous_account_number = self._load_paper_rollover_state().get("paper_account_number")
        if previous_account_number and previous_account_number != self._paper_account_number:
            reason = "account_changed"
        elif not previous_account_number:
            reason = "empty_paper_balance_with_local_holds"
        else:
            return False, None

        self._logger.warning(
            "[OpeningPositionReconcile] paper account rollover suspected: "
            f"reason={reason}, previous={self._mask_account_number(previous_account_number)}, "
            f"current={self._mask_account_number(self._paper_account_number)}, "
            f"local_hold_count={len(local_holds)}"
        )
        return True, {
            "detected": True,
            "reason": reason,
            "previous_account_number": self._mask_account_number(previous_account_number),
            "current_account_number": self._mask_account_number(self._paper_account_number),
            "local_hold_count": len(local_holds),
        }

    @staticmethod
    def _mask_account_number(account_number: Optional[str]) -> Optional[str]:
        if not account_number:
            return None
        text = str(account_number)
        if len(text) <= 4:
            return "****"
        return f"{'*' * (len(text) - 4)}{text[-4:]}"

    @staticmethod
    def _has_positive_broker_holding(actual_holdings: list[dict]) -> bool:
        for holding in actual_holdings or []:
            if not isinstance(holding, dict):
                continue
            raw = None
            for key in ("hldg_qty", "HLDG_QTY", "qty", "quantity"):
                value = holding.get(key)
                if str(value if value is not None else "").strip():
                    raw = value
                    break
            try:
                qty = int(float(str(raw if raw is not None else "0").replace(",", "").strip() or 0))
            except (TypeError, ValueError):
                qty = 0
            if qty > 0:
                return True
        return False

    def _load_paper_rollover_state(self) -> dict:
        try:
            if not self._paper_account_rollover_state_file.exists():
                return {}
            with self._paper_account_rollover_state_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            self._logger.warning(f"[OpeningPositionReconcile] paper rollover state load failed: {exc}")
            return {}

    def _save_paper_rollover_state(self, account_number: str) -> None:
        try:
            self._paper_account_rollover_state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "paper_account_number": account_number,
                "updated_at": self._current_kst_time().isoformat(),
            }
            with self._paper_account_rollover_state_file.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            self._logger.warning(f"[OpeningPositionReconcile] paper rollover state save failed: {exc}")

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
