"""계좌 잔고 스냅샷 캐시.

PositionSizingService 가 장중 매 시그널마다 KIS API 를 직접 호출하면
rate limit 에 걸린다. 이 클래스는 잔고를 메모리에 캐싱하고,
체결/warm_up 이벤트 시에만 실제 API 를 호출한다.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from brokers.broker_api_wrapper import BrokerAPIWrapper
    from common.types import Exchange


@dataclass
class AccountSnapshot:
    total_equity: int          # 총평가금액 (KRW)
    available_cash: int        # 주문가능현금 (KRW)
    positions: Dict[str, int]  # {stock_code: 보유 평가금액(KRW)}
    fetched_at: datetime = field(default_factory=datetime.now)


class AccountSnapshotCache:
    """잔고 스냅샷 lazy fetch + TTL + invalidate + singleflight."""

    DEFAULT_TTL_SEC = 60

    def __init__(
        self,
        broker_api_wrapper: "BrokerAPIWrapper",
        logger: Optional[logging.Logger] = None,
        ttl_sec: int = DEFAULT_TTL_SEC,
    ):
        self._broker = broker_api_wrapper
        self._logger = logger or logging.getLogger(__name__)
        self._ttl_sec = ttl_sec
        self._snapshot: Optional[AccountSnapshot] = None
        self._lock = asyncio.Lock()   # singleflight: 동시 fetch 요청을 1회로 합침

    # ── 공개 API ──────────────────────────────────────────────────

    async def get(self, exchange: "Exchange | None" = None) -> AccountSnapshot:
        """스냅샷 반환. 캐시 miss 또는 TTL 만료 시 API 1회 호출."""
        if self._is_fresh():
            return self._snapshot  # type: ignore[return-value]

        async with self._lock:
            # double-checked locking: lock 획득 후 다시 확인
            if self._is_fresh():
                return self._snapshot  # type: ignore[return-value]
            return await self._fetch(exchange)

    def peek(self) -> Optional["AccountSnapshot"]:
        """캐시가 유효하면 현재 스냅샷을 반환, 미존재/만료 시 None. broker fetch 없음."""
        return self._snapshot if self._is_fresh() else None

    def invalidate(self) -> None:
        """체결 이벤트 등 잔고 변동 시 캐시 무효화."""
        self._snapshot = None
        self._logger.debug("[AccountSnapshot] 캐시 무효화")

    async def warm_up(self, exchange: "Exchange | None" = None) -> None:
        """장 시작 시 또는 원장 대사 직후 명시적 갱신."""
        async with self._lock:
            await self._fetch(exchange)

    # ── 내부 ──────────────────────────────────────────────────────

    def _is_fresh(self) -> bool:
        if self._snapshot is None:
            return False
        elapsed = (datetime.now() - self._snapshot.fetched_at).total_seconds()
        return elapsed < self._ttl_sec

    async def _fetch(self, exchange: "Exchange | None") -> AccountSnapshot:
        try:
            kwargs = {} if exchange is None else {"exchange": exchange}
            resp = await self._broker.get_account_balance(**kwargs)
            if resp is None or resp.rt_cd != "0":
                msg = resp.msg1 if resp else "응답 없음"
                self._logger.warning(f"[AccountSnapshot] 잔고 조회 실패: {msg}")
                # 실패 시 이전 스냅샷 유지(있다면), 없으면 빈 스냅샷 반환
                return self._snapshot or AccountSnapshot(
                    total_equity=0, available_cash=0, positions={}
                )

            data = resp.data or {}
            raw_output2 = data.get("output2") if isinstance(data, dict) else {}
            output2 = self._first_dict(raw_output2)
            output1 = data.get("output1") if isinstance(data, dict) else []

            total_equity = self._parse_int(output2, "tot_evlu_amt") or \
                           self._parse_int(output2, "nass_amt")
            # D+2 예수금 또는 주문가능현금 중 사용 가능한 키 우선
            available_cash = self._parse_int(output2, "ord_psbl_cash") or \
                             self._parse_int(output2, "dnca_tot_amt") or \
                             self._parse_int(output2, "prvs_rcdl_excc_amt")

            positions: Dict[str, int] = {}
            if isinstance(output1, list):
                for item in output1:
                    code = self._get_str(item, "pdno")
                    amt  = self._parse_int(item, "evlu_amt")
                    if code:
                        positions[code] = amt

            self._snapshot = AccountSnapshot(
                total_equity=total_equity,
                available_cash=available_cash,
                positions=positions,
            )
            self._logger.debug(
                f"[AccountSnapshot] 갱신 완료: 총평가={total_equity:,}원 "
                f"예수금={available_cash:,}원 종목수={len(positions)}"
            )
            return self._snapshot

        except Exception as e:
            self._logger.error(f"[AccountSnapshot] fetch 예외: {e}")
            return self._snapshot or AccountSnapshot(
                total_equity=0, available_cash=0, positions={}
            )

    @staticmethod
    def _parse_int(obj: dict | None, key: str) -> int:
        if not obj or not isinstance(obj, dict):
            return 0
        try:
            return int(str(obj.get(key) or "0").replace(",", "") or "0")
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _first_dict(value) -> dict:
        if isinstance(value, dict):
            return value
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
        return {}

    @staticmethod
    def _get_str(obj: dict | None, key: str) -> str:
        if not obj or not isinstance(obj, dict):
            return ""
        return str(obj.get(key) or "").strip()
