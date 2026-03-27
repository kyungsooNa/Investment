# services/realtime_subscription_service.py
"""
WebSocket 실시간 구독 정책을 담당하는 서비스.

역할:
  - 체결가(H0UNCNT0)와 프로그램매매(H0STPGM0) 구독을 통합 카운트로 관리
  - 우선순위(CRITICAL > HIGH > MEDIUM > LOW) 기반으로 MAX_SUBSCRIPTIONS(40) 한도 내 최적 구독 유지
  - 실제 WebSocket 구독/해지는 StreamingService에 위임
  - 체결가 구독 활성화 시 StockRepository에 mark_streaming() 알림 (TTL 우회 활성화)

PT(프로그램매매) 구독 구조:
  - H0STPGM0 = 프로그램매매 데이터 (1슬롯, 별도 카운트)
  - 체결가(H0UNCNT0) = CRITICAL 우선순위로 통합 풀에 등록 (ref-count: "program_trading_price")
    → PT 종목이 이미 portfolio/strategy로 구독 중이면 ref-count만 증가 (추가 구독 없음)

우선순위 카테고리:
  - CRITICAL : 프로그램매매 대상 종목 체결가 — category_key: "program_trading_price"
  - HIGH     : 보유 종목 (Portfolio) — category_key: "portfolio"
  - MEDIUM   : 전략 감시 종목 (Strategy watchlist, premium stocks) — category_key: "strategy_*"
  - LOW      : 웹 UI 조회 종목 — category_key: "ui_*"
"""
from __future__ import annotations

import logging
import time
from enum import IntEnum
from typing import Dict, Set, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from services.streaming_service import StreamingService
    from repositories.stock_repository import StockRepository


class SubscriptionPriority(IntEnum):
    CRITICAL = 0  # 프로그램매매 체결가 (절대 퇴출 안 됨)
    HIGH = 1      # Portfolio (보유 종목)
    MEDIUM = 2    # Strategy watchlist / premium stocks
    LOW = 3       # UI page view / watchlist page


class RealtimeSubscriptionService:
    """
    우선순위 기반 실시간 WebSocket 구독 관리 서비스.

    체결가(H0UNCNT0) + 프로그램매매(H0STPGM0) 구독을 통합 카운트로 관리.

    참조 카운팅 (체결가):
      - add_subscription(code, priority, category_key): 카운트 0→1이면 실제 구독
      - remove_subscription(code, category_key): 카운트 1→0이면 실제 구독 해지
      - sync_subscriptions(codes, category_key, priority): 카테고리 전체 원자적 교체

    프로그램매매 구독:
      - add_program_trading(code): H0STPGM0 구독 + CRITICAL 체결가 구독
      - remove_program_trading(code): H0STPGM0 해지 + CRITICAL 체결가 ref-count 감소

    MAX 한도 초과 시 (체결가):
      - 전체 요청 종목을 우선순위로 정렬 후 상위 (MAX_SUBSCRIPTIONS - PT슬롯수)개만 구독
      - 우선순위가 동일하면 종목코드 오름차순으로 결정적(deterministic) 선택
    """

    MAX_SUBSCRIPTIONS = 40  # KIS WebSocket 전체 한도

    def __init__(
        self,
        streaming_service: "StreamingService",
        stock_repo: "StockRepository",
        logger=None,
    ):
        self._streaming = streaming_service
        self._stock_repo = stock_repo
        self._logger = logger or logging.getLogger(__name__)

        # code -> {category_key -> SubscriptionPriority}  (체결가 구독용)
        self._refs: Dict[str, Dict[str, SubscriptionPriority]] = {}

        # 현재 실제로 WebSocket 구독 중인 체결가 종목 집합 (H0UNCNT0)
        self._active_codes: Set[str] = set()

        # 현재 구독 중인 프로그램매매 종목 집합 (H0STPGM0)
        self._pt_codes: Set[str] = set()

        # 체결가 구독 시작 시각 (code -> epoch seconds)
        self._subscribed_at: Dict[str, float] = {}

    # ── 체결가 구독 Public API ──────────────────────────────────────

    async def add_subscription(
        self, code: str, priority: SubscriptionPriority, category_key: str
    ) -> None:
        """특정 카테고리에서 체결가 구독을 요청합니다."""
        self._refs.setdefault(code, {})[category_key] = priority
        await self._rebalance()

    async def remove_subscription(self, code: str, category_key: str) -> None:
        """특정 카테고리에서 체결가 구독을 해제합니다."""
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

    # ── 프로그램매매 구독 Public API ────────────────────────────────

    async def add_program_trading(self, code: str) -> bool:
        """
        프로그램매매 구독을 추가합니다.
        - H0STPGM0 구독 (프로그램매매 데이터)
        - 해당 종목 체결가를 CRITICAL 우선순위로 통합 풀에 등록
        """
        if code in self._pt_codes:
            self._logger.debug(f"RealtimeSubscriptionService: PT 구독 이미 존재 {code}")
            return True
        try:
            success = await self._streaming.subscribe_program_trading(code)
            if not success:
                self._logger.warning(f"RealtimeSubscriptionService: PT 구독 실패 {code}")
                return False
            self._pt_codes.add(code)
            # 체결가는 CRITICAL로 통합 풀에 등록 (이미 다른 카테고리로 구독 중이면 ref-count만 증가)
            await self.add_subscription(code, SubscriptionPriority.CRITICAL, "program_trading_price")
            self._logger.info(f"RealtimeSubscriptionService: PT 구독 등록 {code}")
            return True
        except Exception as e:
            self._logger.error(f"RealtimeSubscriptionService: PT 구독 중 오류 {code}: {e}")
            return False

    async def remove_program_trading(self, code: str) -> None:
        """프로그램매매 구독을 해지합니다."""
        if code not in self._pt_codes:
            return
        try:
            await self._streaming.unsubscribe_program_trading(code)
            self._pt_codes.discard(code)
            # 체결가 ref-count 감소 (0이 되면 _rebalance에서 실제 해지)
            await self.remove_subscription(code, "program_trading_price")
            self._logger.info(f"RealtimeSubscriptionService: PT 구독 해지 {code}")
        except Exception as e:
            self._logger.error(f"RealtimeSubscriptionService: PT 구독 해지 중 오류 {code}: {e}")

    def get_program_trading_codes(self) -> List[str]:
        """현재 구독 중인 프로그램매매 종목 목록을 반환합니다."""
        return sorted(self._pt_codes)

    def has_program_trading_subscriptions(self) -> bool:
        """프로그램매매 구독 종목이 있는지 확인합니다."""
        return bool(self._pt_codes)

    # ── 재연결 복원 ─────────────────────────────────────────────────

    async def restore_all_subscriptions(self, callback=None) -> None:
        """
        WebSocket 재연결 후 모든 구독을 복원합니다 (watchdog이 호출).

        Args:
            callback: connect_websocket에 전달할 콜백 (없으면 재연결 스킵)
        """
        # 1. PT 구독 복원 (H0STPGM0)
        pt_codes = list(self._pt_codes)
        pt_success = 0
        for code in pt_codes:
            try:
                success = await self._streaming.subscribe_program_trading(code)
                if success:
                    pt_success += 1
                else:
                    self._logger.warning(f"[복원] PT 구독 실패: {code}")
                    self._pt_codes.discard(code)
                    self._refs.get(code, {}).pop("program_trading_price", None)
                    if code in self._refs and not self._refs[code]:
                        del self._refs[code]
            except Exception as e:
                self._logger.error(f"[복원] PT 구독 중 오류 ({code}): {e}")
                self._pt_codes.discard(code)

        # 2. 체결가 구독 복원 (H0UNCNT0) — _active_codes 기준
        price_codes = list(self._active_codes)
        self._active_codes.clear()  # 재구독 전 초기화 (실제 연결이 끊겼으므로)
        price_success = 0
        for code in price_codes:
            try:
                success = await self._streaming.subscribe_unified_price(code)
                if success:
                    self._active_codes.add(code)
                    self._subscribed_at[code] = time.time()
                    self._stock_repo.mark_streaming(code)
                    price_success += 1
                else:
                    self._logger.warning(f"[복원] 체결가 구독 실패: {code}")
            except Exception as e:
                self._logger.error(f"[복원] 체결가 구독 중 오류 ({code}): {e}")

        self._logger.info(
            f"[복원] 구독 복원 완료 — PT: {pt_success}/{len(pt_codes)}, "
            f"체결가: {price_success}/{len(price_codes)}"
        )

    # ── 상태 조회 ─────────────────────────────────────────────────

    def is_streaming(self, code: str) -> bool:
        """해당 종목이 현재 체결가 실시간 구독 중인지 여부."""
        return code in self._active_codes

    def get_status(self) -> dict:
        """현재 구독 현황을 반환합니다 (모니터링/API 용)."""
        _priority_names = {
            SubscriptionPriority.CRITICAL: "CRITICAL",
            SubscriptionPriority.HIGH: "HIGH",
            SubscriptionPriority.MEDIUM: "MEDIUM",
            SubscriptionPriority.LOW: "LOW",
        }
        by_priority: Dict[str, list] = {"CRITICAL": [], "HIGH": [], "MEDIUM": [], "LOW": []}

        for code, cats in self._refs.items():
            best = min(cats.values(), key=lambda p: int(p))
            by_priority[_priority_names[best]].append({
                "code": code,
                "active": code in self._active_codes,
                "subscribed_at": self._subscribed_at.get(code),
            })

        for key in by_priority:
            by_priority[key].sort(key=lambda x: x["code"])

        return {
            "total_count": len(self._active_codes) + len(self._pt_codes),
            "pt_count": len(self._pt_codes),
            "price_count": len(self._active_codes),
            "max_subscriptions": self.MAX_SUBSCRIPTIONS,
            "pt_codes": sorted(self._pt_codes),
            "price_codes": sorted(self._active_codes),
            "by_priority": by_priority,
        }

    # ── Internal rebalance logic ────────────────────────────────────

    async def _rebalance(self) -> None:
        """
        요청된 체결가 구독 목록을 우선순위로 정렬하여 한도 내로 유지.
        PT 슬롯(H0STPGM0)을 먼저 확보한 뒤 남은 슬롯으로 체결가 구독 관리.
        """
        def _best_priority(code: str) -> int:
            return min(int(p) for p in self._refs[code].values())

        ranked = sorted(self._refs.keys(), key=lambda c: (_best_priority(c), c))
        # PT 슬롯 먼저 확보 후 남은 슬롯으로 체결가 구독
        available_price_slots = self.MAX_SUBSCRIPTIONS - len(self._pt_codes)
        desired: Set[str] = set(ranked[:available_price_slots])

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
                f"RealtimeSubscriptionService: 구독 한도 초과 — {dropped}개 종목이 대기 상태 "
                f"(active_price={len(self._active_codes)}, active_pt={len(self._pt_codes)}, "
                f"requested={len(self._refs)}, max={self.MAX_SUBSCRIPTIONS})"
            )

    async def _do_subscribe(self, code: str) -> None:
        try:
            success = await self._streaming.subscribe_unified_price(code)
            if success:
                self._active_codes.add(code)
                self._subscribed_at[code] = time.time()
                self._stock_repo.mark_streaming(code)
                self._logger.debug(f"RealtimeSubscriptionService: 체결가 구독 등록 {code}")
        except Exception as e:
            self._logger.error(f"RealtimeSubscriptionService: 체결가 구독 실패 {code}: {e}")

    async def _do_unsubscribe(self, code: str) -> None:
        try:
            await self._streaming.unsubscribe_unified_price(code)
            self._active_codes.discard(code)
            self._subscribed_at.pop(code, None)
            self._stock_repo.unmark_streaming(code)
            self._logger.debug(f"RealtimeSubscriptionService: 체결가 구독 해지 {code}")
        except Exception as e:
            self._logger.error(f"RealtimeSubscriptionService: 체결가 구독 해지 실패 {code}: {e}")
