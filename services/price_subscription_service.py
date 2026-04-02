# services/price_subscription_service.py
"""
실시간 현재가 구독 정책을 담당하는 서비스.

역할:
  - 여러 요청자(Portfolio, Strategy, UI)로부터 구독 요청을 받아 참조 카운팅으로 관리
  - 우선순위(HIGH > MEDIUM > LOW) 기반으로 MAX_SUBSCRIPTIONS(35) 한도 내 최적 구독 유지
  - 실제 WebSocket 구독/해지는 StreamingService에 위임
  - 구독 활성화 시 StockRepository에 mark_streaming() 알림 (TTL 우회 활성화)

StreamingService와의 역할 구분:
  - PriceSubscriptionService : 무엇을, 왜 구독할지 결정 (정책 레이어)
  - StreamingService          : 어떻게 구독하는지 처리 (프로토콜 레이어)

우선순위 카테고리:
  - HIGH   : 보유 종목 (Portfolio) — category_key: "portfolio"
  - MEDIUM : 전략 감시 종목 (Strategy watchlist, premium stocks) — category_key: "strategy_*"
  - LOW    : 웹 UI 조회 종목 — category_key: "ui_*"
"""
from __future__ import annotations

import logging
import time
from enum import IntEnum
from typing import Dict, Set, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from services.streaming_service import StreamingService
    from repositories.stock_repository import StockRepository
    from core.logger import StreamingEventLogger


class SubscriptionPriority(IntEnum):
    HIGH = 1      # Portfolio (보유 종목)
    MEDIUM = 2    # Strategy watchlist / premium stocks
    LOW = 3       # UI page view / watchlist page


class PriceSubscriptionService:
    """
    우선순위 기반 실시간 현재가 구독 관리 서비스.

    참조 카운팅:
      - add_subscription(code, priority, category_key): 카운트 0→1이면 실제 구독
      - remove_subscription(code, category_key): 카운트 1→0이면 실제 구독 해지
      - sync_subscriptions(codes, category_key, priority): 카테고리 전체 원자적 교체

    MAX 한도 초과 시:
      - 전체 요청 종목을 우선순위로 정렬 후 상위 MAX_SUBSCRIPTIONS개만 구독
      - 우선순위가 동일하면 종목코드 오름차순으로 결정적(deterministic) 선택
    """

    MAX_SUBSCRIPTIONS = 35

    def __init__(
        self,
        streaming_service: "StreamingService",
        stock_repo: "StockRepository",
        logger=None,
        streaming_logger: Optional["StreamingEventLogger"] = None,
    ):
        self._streaming = streaming_service
        self._stock_repo = stock_repo
        self._logger = logger or logging.getLogger(__name__)
        self._streaming_logger = streaming_logger

        # code -> {category_key -> SubscriptionPriority}
        self._refs: Dict[str, Dict[str, SubscriptionPriority]] = {}

        # 현재 실제로 WebSocket 구독 중인 종목 집합
        self._active_codes: Set[str] = set()

        # summary 로그 스로틀 (동시 다발적 rebalance 호출로 인한 중복 발화 방지)
        self._last_summary_time: float = 0.0
        self._SUMMARY_THROTTLE_SEC: float = 2.0

    # ── Public API ─────────────────────────────────────────────────

    async def add_subscription(
        self, code: str, priority: SubscriptionPriority, category_key: str
    ) -> None:
        """특정 카테고리에서 종목 구독을 요청합니다."""
        self._refs.setdefault(code, {})[category_key] = priority
        await self._rebalance()

    async def remove_subscription(self, code: str, category_key: str) -> None:
        """특정 카테고리에서 종목 구독을 해제합니다."""
        if code in self._refs:
            self._refs[code].pop(category_key, None)
            if not self._refs[code]:
                del self._refs[code]
        await self._rebalance()

    async def remove_category(self, category_key: str) -> None:
        """카테고리 전체의 구독을 한 번에 해제합니다 (전략 종료 시 사용)."""
        codes_in_category = [c for c, cats in self._refs.items() if category_key in cats]
        for code in codes_in_category:
            self._refs[code].pop(category_key, None)
            if not self._refs[code]:
                del self._refs[code]
        await self._rebalance()

    async def sync_subscriptions(
        self,
        codes: List[str],
        category_key: str,
        priority: SubscriptionPriority,
    ) -> None:
        """
        카테고리 전체를 새 코드 목록으로 원자적으로 교체합니다.
        전략 워치리스트 갱신 시 사용 — rebalance 1회만 호출.
        """
        old_codes = {c for c, cats in self._refs.items() if category_key in cats}
        new_codes = set(codes)

        for code in old_codes - new_codes:
            self._refs[code].pop(category_key, None)
            if not self._refs[code]:
                del self._refs[code]

        for code in new_codes:
            self._refs.setdefault(code, {})[category_key] = priority

        await self._rebalance()

    def is_streaming(self, code: str) -> bool:
        """해당 종목이 현재 실시간 구독 중인지 여부."""
        return code in self._active_codes

    def get_status(self) -> dict:
        """현재 구독 현황을 반환합니다 (모니터링/API 용)."""
        pending_by_priority: Dict[int, List[str]] = {}
        for code, cats in self._refs.items():
            best = min(int(p) for p in cats.values())
            pending_by_priority.setdefault(best, []).append(code)

        return {
            "active_count": len(self._active_codes),
            "max_subscriptions": self.MAX_SUBSCRIPTIONS,
            "active_codes": sorted(self._active_codes),
            "pending_count": len(self._refs),
            "pending_by_priority": {
                "HIGH": sorted(pending_by_priority.get(int(SubscriptionPriority.HIGH), [])),
                "MEDIUM": sorted(pending_by_priority.get(int(SubscriptionPriority.MEDIUM), [])),
                "LOW": sorted(pending_by_priority.get(int(SubscriptionPriority.LOW), [])),
            },
        }

    # ── Internal rebalance logic ────────────────────────────────────

    async def _rebalance(self) -> None:
        """
        요청된 구독 목록을 우선순위로 정렬하여 MAX_SUBSCRIPTIONS개 이내로 유지.
        변경이 필요한 종목만 구독/해지 처리.
        """
        def _best_priority(code: str) -> int:
            return min(int(p) for p in self._refs[code].values())

        ranked = sorted(self._refs.keys(), key=lambda c: (_best_priority(c), c))
        desired: Set[str] = set(ranked[: self.MAX_SUBSCRIPTIONS])

        to_unsubscribe = self._active_codes - desired
        to_subscribe = desired - self._active_codes

        for code in to_unsubscribe:
            await self._do_unsubscribe(code)

        for code in to_subscribe:
            await self._do_subscribe(code)

        # MAX 초과로 탈락된 종목이 있으면 경고 로그
        dropped = len(self._refs) - len(desired)
        if dropped > 0:
            self._logger.warning(
                f"PriceSubscriptionService: 구독 한도 초과 — {dropped}개 종목이 대기 상태 "
                f"(active={len(self._active_codes)}, requested={len(self._refs)}, max={self.MAX_SUBSCRIPTIONS})"
            )

        # 변경이 있었을 때 현재 구독 상태 요약 기록 (2초 스로틀로 중복 발화 방지)
        if (to_subscribe or to_unsubscribe) and self._streaming_logger:
            now = time.monotonic()
            if now - self._last_summary_time >= self._SUMMARY_THROTTLE_SEC:
                self._last_summary_time = now
                status = self.get_status()
                self._streaming_logger.log_summary(
                    active_count=status["active_count"],
                    active_codes=status["active_codes"],
                    pending_by_priority=status["pending_by_priority"],
                )

    async def _do_subscribe(self, code: str) -> None:
        try:
            success = await self._streaming.subscribe_unified_price(code)
            if success:
                self._active_codes.add(code)
                self._stock_repo.mark_streaming(code)
                self._logger.debug(f"PriceSubscriptionService: 구독 등록 {code}")
                if self._streaming_logger:
                    categories = self._refs.get(code, {})
                    self._streaming_logger.log_subscribe(
                        code=code,
                        categories=categories,
                        active_count=len(self._active_codes),
                    )
            else:
                self._logger.warning(
                    f"PriceSubscriptionService: 구독 실패(False 반환) {code} "
                    f"— WebSocket 미연결 또는 브로커 거부 가능성"
                )
        except Exception as e:
            self._logger.error(f"PriceSubscriptionService: 구독 실패 {code}: {e}")

    async def _do_unsubscribe(self, code: str) -> None:
        try:
            await self._streaming.unsubscribe_unified_price(code)
            self._active_codes.discard(code)
            self._stock_repo.unmark_streaming(code)
            self._logger.debug(f"PriceSubscriptionService: 구독 해지 {code}")
            if self._streaming_logger:
                self._streaming_logger.log_unsubscribe(
                    code=code,
                    active_count=len(self._active_codes),
                )
        except Exception as e:
            self._logger.error(f"PriceSubscriptionService: 구독 해지 실패 {code}: {e}")
