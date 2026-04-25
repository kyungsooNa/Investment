import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pytz

from config.config_loader import KillSwitchConfig
from services.notification_service import NotificationCategory, NotificationLevel, NotificationService

KST = pytz.timezone("Asia/Seoul")
_ALERT_COOLDOWN_SEC = 60


def _now_kst() -> datetime:
    return datetime.now(tz=KST)


class KillSwitchService:
    """계좌 보호용 Kill Switch.

    일손실 한도 초과, 연속 손실, 연속 API 오류, 체결 이상 시 자동으로 트립(잠금)되어
    모든 주문 및 전략 실행을 차단한다. 해제는 반드시 운영자가 수동으로 수행한다.
    """

    def __init__(
        self,
        config: KillSwitchConfig,
        notification_service: NotificationService,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._cfg = config
        self._notif = notification_service
        self._logger = logger or logging.getLogger(__name__)
        self._lock = asyncio.Lock()

        # 영속 상태
        self._is_tripped: bool = False
        self._trip_reason: Optional[str] = None
        self._trip_timestamp: Optional[datetime] = None
        self._trip_metadata: dict[str, Any] = {}
        self._consecutive_losses: int = 0
        self._consecutive_api_errors: int = 0
        self._daily_realized_loss_won: int = 0

        # in-memory only — 알림 폭주 방지
        self._last_alert_at: Optional[datetime] = None

        self._state_path = Path(self._cfg.state_file_path)
        self._load_state()

    # ── 조회 ──────────────────────────────────────────────────────────

    async def check_orders_allowed(self) -> tuple[bool, Optional[str]]:
        """주문 허용 여부 반환. (False, reason) 이면 주문 차단."""
        if not self._cfg.enabled:
            return True, None
        if self._is_tripped:
            return False, self._trip_reason
        return True, None

    async def check_strategies_allowed(self) -> tuple[bool, Optional[str]]:
        """전략 실행 허용 여부 반환. (False, reason) 이면 전략 중단."""
        if not self._cfg.enabled:
            return True, None
        if self._is_tripped:
            return False, self._trip_reason
        return True, None

    def get_status(self) -> dict[str, Any]:
        """현재 상태 스냅샷 반환 (API 응답용)."""
        return {
            "is_tripped": self._is_tripped,
            "trip_reason": self._trip_reason,
            "trip_timestamp": self._trip_timestamp.isoformat() if self._trip_timestamp else None,
            "trip_metadata": self._trip_metadata,
            "daily_realized_loss_won": self._daily_realized_loss_won,
            "consecutive_losses": self._consecutive_losses,
            "consecutive_api_errors": self._consecutive_api_errors,
            "thresholds": {
                "daily_loss_threshold_won": self._cfg.daily_loss_threshold_won,
                "daily_loss_threshold_pct": self._cfg.daily_loss_threshold_pct,
                "max_consecutive_losses": self._cfg.max_consecutive_losses,
                "max_consecutive_api_errors": self._cfg.max_consecutive_api_errors,
                "abnormal_fill_deviation_pct": self._cfg.abnormal_fill_deviation_pct,
            },
        }

    # ── 피드백 수신 ──────────────────────────────────────────────────

    async def record_trade_result(
        self,
        profit_won: int,
        code: str,
        strategy: str,
        account_balance_won: Optional[int] = None,
    ) -> None:
        """매도 체결 후 손익 기록. 임계 초과 시 트립."""
        if not self._cfg.enabled:
            return
        async with self._lock:
            if profit_won < 0:
                self._consecutive_losses += 1
                self._daily_realized_loss_won += profit_won  # 음수 누적
            else:
                self._consecutive_losses = 0

            self._save_state()

            meta = {"code": code, "strategy": strategy, "profit_won": profit_won}

            if self._consecutive_losses >= self._cfg.max_consecutive_losses:
                await self._trip(
                    f"연속 손실 {self._consecutive_losses}회 (한도: {self._cfg.max_consecutive_losses}회)",
                    meta,
                )
                return

            loss_abs = abs(self._daily_realized_loss_won)
            if loss_abs >= self._cfg.daily_loss_threshold_won:
                await self._trip(
                    f"일손실 {loss_abs:,}원 초과 (한도: {self._cfg.daily_loss_threshold_won:,}원)",
                    meta,
                )
                return

            if account_balance_won and account_balance_won > 0:
                loss_pct = loss_abs / account_balance_won * 100
                if loss_pct >= self._cfg.daily_loss_threshold_pct:
                    await self._trip(
                        f"일손실 {loss_pct:.1f}% 초과 (한도: {self._cfg.daily_loss_threshold_pct}%)",
                        meta,
                    )

    async def record_api_failure(self, reason: str) -> None:
        """API 오류 발생 기록. 연속 오류 한도 초과 시 트립."""
        if not self._cfg.enabled:
            return
        async with self._lock:
            self._consecutive_api_errors += 1
            self._save_state()
            if self._consecutive_api_errors >= self._cfg.max_consecutive_api_errors:
                await self._trip(
                    f"연속 API 오류 {self._consecutive_api_errors}회 (한도: {self._cfg.max_consecutive_api_errors}회)",
                    {"last_reason": reason},
                )

    async def record_api_success(self) -> None:
        """API 성공 시 연속 오류 카운터 초기화."""
        if not self._cfg.enabled or self._consecutive_api_errors == 0:
            return
        async with self._lock:
            self._consecutive_api_errors = 0
            self._save_state()

    async def record_fill_event(
        self,
        order_price: float,
        fill_price: float,
        code: str,
        qty: int,
    ) -> None:
        """체결가와 주문가 편차 확인. 임계 초과 시 트립."""
        if not self._cfg.enabled or order_price <= 0:
            return
        deviation_pct = abs(fill_price - order_price) / order_price * 100
        if deviation_pct >= self._cfg.abnormal_fill_deviation_pct:
            async with self._lock:
                await self._trip(
                    f"비정상 체결: 편차 {deviation_pct:.2f}% (한도: {self._cfg.abnormal_fill_deviation_pct}%)",
                    {"code": code, "qty": qty, "order_price": order_price, "fill_price": fill_price},
                )

    # ── 수동 제어 ────────────────────────────────────────────────────

    async def manual_trip(self, reason: str, operator: str) -> None:
        """운영자 수동 트립."""
        async with self._lock:
            await self._trip(f"수동 트립 by {operator}: {reason}", {"operator": operator})

    async def manual_reset(self, operator: str) -> None:
        """운영자 수동 해제. is_tripped=False로 전환."""
        async with self._lock:
            if not self._is_tripped:
                return
            prev_reason = self._trip_reason
            self._is_tripped = False
            self._trip_reason = None
            self._trip_timestamp = None
            self._trip_metadata = {}
            self._save_state()

        self._logger.warning(
            "[KillSwitch] 해제됨 (operator=%s, 이전 사유: %s)", operator, prev_reason
        )
        await self._notif.emit(
            NotificationCategory.SYSTEM,
            NotificationLevel.WARNING,
            "Kill Switch 해제",
            f"운영자 {operator}가 Kill Switch를 해제했습니다. (이전 사유: {prev_reason})",
            {"operator": operator},
        )

    async def reset_daily_counters(self) -> None:
        """거래일 시작 시 일별 카운터 초기화. is_tripped 상태는 유지."""
        async with self._lock:
            self._daily_realized_loss_won = 0
            self._consecutive_losses = 0
            self._save_state()
        self._logger.info("[KillSwitch] 일별 카운터 초기화 완료")

    # ── 내부 ─────────────────────────────────────────────────────────

    async def _trip(self, reason: str, metadata: dict[str, Any]) -> None:
        """트립 상태로 전환. 이미 트립 중이면 알림 쿨다운만 적용. Lock 보유 중에 호출."""
        now = _now_kst()

        if self._is_tripped:
            # 알림 폭주 방지 — 1분 내 중복 알림 억제
            if self._last_alert_at and (now - self._last_alert_at).total_seconds() < _ALERT_COOLDOWN_SEC:
                return
            self._last_alert_at = now
            await self._notif.emit(
                NotificationCategory.SYSTEM,
                NotificationLevel.CRITICAL,
                "Kill Switch 유지 중",
                f"[{reason}] (이전 사유: {self._trip_reason})",
                metadata,
            )
            return

        self._is_tripped = True
        self._trip_reason = reason
        self._trip_timestamp = now
        self._trip_metadata = metadata
        self._last_alert_at = now
        self._save_state()

        self._logger.critical("[KillSwitch] 트립! 사유: %s | 메타: %s", reason, metadata)
        await self._notif.emit(
            NotificationCategory.SYSTEM,
            NotificationLevel.CRITICAL,
            "Kill Switch 트립",
            f"모든 주문·전략이 차단되었습니다.\n사유: {reason}",
            metadata,
        )

    def _save_state(self) -> None:
        """현재 상태를 JSON 파일에 저장. Lock 보유 중에 호출."""
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "is_tripped": self._is_tripped,
                "trip_reason": self._trip_reason,
                "trip_timestamp": self._trip_timestamp.isoformat() if self._trip_timestamp else None,
                "trip_metadata": self._trip_metadata,
                "consecutive_losses": self._consecutive_losses,
                "consecutive_api_errors": self._consecutive_api_errors,
                "daily_realized_loss_won": self._daily_realized_loss_won,
            }
            self._state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            self._logger.error("[KillSwitch] 상태 저장 실패: %s", e)

    def _load_state(self) -> None:
        """재시작 시 JSON 파일에서 상태 복원."""
        if not self._state_path.exists():
            return
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._is_tripped = bool(state.get("is_tripped", False))
            self._trip_reason = state.get("trip_reason")
            ts = state.get("trip_timestamp")
            self._trip_timestamp = datetime.fromisoformat(ts) if ts else None
            self._trip_metadata = state.get("trip_metadata", {})
            self._consecutive_losses = int(state.get("consecutive_losses", 0))
            self._consecutive_api_errors = int(state.get("consecutive_api_errors", 0))
            self._daily_realized_loss_won = int(state.get("daily_realized_loss_won", 0))
            if self._is_tripped:
                self._logger.warning(
                    "[KillSwitch] 재시작 후 트립 상태 복원. 사유: %s (트립 시각: %s)",
                    self._trip_reason,
                    self._trip_timestamp,
                )
        except Exception as e:
            self._logger.error("[KillSwitch] 상태 복원 실패: %s", e)
