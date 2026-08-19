"""Paper trading account expiry alert service."""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from typing import Optional

from services.notification_service import (
    NotificationCategory,
    NotificationLevel,
    NotificationService,
)


class PaperAccountExpiryAlertService:
    """Emit a daily warning near the KIS paper account expiry date."""

    def __init__(
        self,
        *,
        expires_at: Optional[str],
        notification_service: Optional[NotificationService],
        market_clock,
        warning_days: int = 7,
        state_file: str = "data/paper_account_expiry_alert_state.json",
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._expires_at_raw = expires_at
        self._expires_at = self._parse_date(expires_at)
        self._warning_days = max(0, int(warning_days))
        self._ns = notification_service
        self._market_clock = market_clock
        self._state_file = state_file
        self._logger = logger or logging.getLogger(__name__)

    async def check_and_notify(self, is_paper_trading: bool, account_number: str) -> dict:
        if not is_paper_trading:
            return {"alerted": False, "reason": "not_paper_trading"}
        if self._expires_at is None:
            return {"alerted": False, "reason": "expiry_not_configured"}
        if self._ns is None or self._market_clock is None:
            return {"alerted": False, "reason": "notification_dependencies_missing"}

        today = self._today()
        days_left = (self._expires_at - today).days
        if days_left > self._warning_days:
            return {
                "alerted": False,
                "reason": "outside_warning_window",
                "days_left": days_left,
                "expires_at": self._expires_at.isoformat(),
            }

        account = str(account_number or "").strip()
        if self._already_alerted(today, account):
            return {
                "alerted": False,
                "reason": "already_alerted_today",
                "days_left": days_left,
                "expires_at": self._expires_at.isoformat(),
            }

        title = "모의투자 계좌 만료" if days_left < 0 else "모의투자 계좌 만료 예정"
        level = NotificationLevel.ERROR if days_left < 0 else NotificationLevel.WARNING
        message = self._build_message(account, days_left)
        await self._ns.emit(
            NotificationCategory.SYSTEM,
            level,
            title,
            message,
            metadata={
                "dedup_key": f"paper_account_expiry:{account}:{self._expires_at.isoformat()}",
                "force_external": True,
                "account_number": account,
                "expires_at": self._expires_at.isoformat(),
                "days_left": days_left,
            },
        )
        self._save_state(today, account)
        return {
            "alerted": True,
            "days_left": days_left,
            "expires_at": self._expires_at.isoformat(),
        }

    def _today(self) -> date:
        now = self._market_clock.get_current_kst_time()
        return now.date() if isinstance(now, datetime) else now

    def _build_message(self, account: str, days_left: int) -> str:
        account_text = self._mask_account(account)
        expires_at = self._expires_at.isoformat()
        if days_left < 0:
            status = f"{abs(days_left)}일 전에 만료"
        elif days_left == 0:
            status = "오늘 만료"
        else:
            status = f"{days_left}일 남음"
        return (
            f"모의투자 계좌({account_text}) 유효기간이 {expires_at} 기준 {status}입니다. "
            "KIS Developers에서 모의투자 계좌를 재신청한 뒤 "
            "config.yaml의 paper_stock_account_number와 paper_account_expiry_alert.expires_at을 갱신하세요."
        )

    def _already_alerted(self, today: date, account: str) -> bool:
        state = self._load_state()
        return (
            state.get("last_alert_date") == today.isoformat()
            and state.get("account_number") == account
            and state.get("expires_at") == self._expires_at.isoformat()
        )

    def _load_state(self) -> dict:
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception as exc:
            self._logger.warning(f"[PaperAccountExpiryAlert] state load failed: {exc}")
            return {}

    def _save_state(self, today: date, account: str) -> None:
        try:
            dirname = os.path.dirname(self._state_file)
            if dirname:
                os.makedirs(dirname, exist_ok=True)
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "last_alert_date": today.isoformat(),
                        "account_number": account,
                        "expires_at": self._expires_at.isoformat(),
                    },
                    f,
                    ensure_ascii=False,
                )
        except Exception as exc:
            self._logger.warning(f"[PaperAccountExpiryAlert] state save failed: {exc}")

    @staticmethod
    def _parse_date(value: Optional[str]) -> Optional[date]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        raise ValueError(
            "paper_account_expiry_alert.expires_at은 YYYY-MM-DD, YYYY/MM/DD, YYYY.MM.DD 형식이어야 합니다."
        )

    @staticmethod
    def _mask_account(account: str) -> str:
        if len(account) <= 4:
            return "***"
        return f"{account[:2]}***{account[-2:]}"
