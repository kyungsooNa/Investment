# services/subscription_policy.py
"""
실시간 현재가 구독 정책을 담당하는 서비스.

역할:
  - 여러 요청자(Portfolio, Strategy, UI)로부터 구독 요청을 받아 참조 카운팅으로 관리
  - 우선순위(HIGH > MEDIUM > LOW) 기반으로 MAX_WS_SLOTS(40) 한도 내 최적 구독 유지
  - 실제 WebSocket 구독/해지는 StreamingService에 위임
  - 구독 활성화 시 StockRepository에 mark_streaming() 알림 (TTL 우회 활성화)

StreamingService와의 역할 구분:
  - SubscriptionPolicy : 무엇을, 왜 구독할지 결정 (정책 레이어)
  - StreamingService   : 어떻게 구독하는지 처리 (프로토콜 레이어)

우선순위 카테고리:
  - HIGH   : 보유 종목 (Portfolio) — category_key: "portfolio"
  - MEDIUM : 전략 감시 종목, 프리미엄 종목, 관심종목 알림 — category_key: "strategy_*", "favorite"
  - LOW    : 웹 UI 조회 종목 — category_key: "ui_*"
"""
from __future__ import annotations

import logging
import time
import inspect
from enum import IntEnum
from typing import Dict, Set, List, Optional, TYPE_CHECKING

from repositories.streaming_stock_repo import StreamingStockRepo, StreamingType

if TYPE_CHECKING:
    from services.streaming_service import StreamingService
    from repositories.stock_repository import StockRepository
    from core.logger import StreamingEventLogger
    from services.market_calendar_service import MarketCalendarService


class SubscriptionPriority(IntEnum):
    CRITICAL = 0  # 프로그램 매매 (절대 밀어내기 불가)
    HIGH = 1      # Portfolio (보유 종목)
    MEDIUM = 2    # Strategy watchlist / premium stocks / favorite alerts
    LOW = 3       # UI page view


class SubscriptionPolicy:
    """
    우선순위 기반 실시간 현재가 구독 관리 서비스.

    참조 카운팅:
      - add_subscription(code, priority, category_key, stream_type): 카운트 0→1이면 실제 구독
      - remove_subscription(code, category_key): 카운트 1→0이면 실제 구독 해지
      - sync_subscriptions(codes, category_key, priority): 카테고리 전체 원자적 교체

    MAX 한도 초과 시:
      - 전체 요청 종목을 우선순위로 정렬 후 상위 MAX_SUBSCRIPTIONS개만 구독
      - 우선순위가 동일하면 종목코드 오름차순으로 결정적(deterministic) 선택
    """

    MAX_WS_SLOTS = 40  # KIS 웹소켓 최대 구독 한도 (PT=1슬롯, Price=1슬롯)
    _PERSISTENT_PRICE_CATEGORIES = frozenset({"favorite"})

    def __init__(
        self,
        streaming_service: "StreamingService",
        stock_repo: "StockRepository",
        logger=None,
        streaming_logger: Optional["StreamingEventLogger"] = None,
        streaming_stock_repo: Optional["StreamingStockRepo"] = None,
        market_calendar: Optional["MarketCalendarService"] = None,
    ):
        self._streaming = streaming_service
        self._stock_repo = stock_repo
        self._logger = logger or logging.getLogger(__name__)
        self._streaming_logger = streaming_logger
        self._streaming_stock_repo = streaming_stock_repo
        self._market_calendar = market_calendar

        # code -> { category_key -> { 'priority': Priority, 'type': StreamingType } }
        self._refs: Dict[str, Dict[str, dict]] = {}

        # 현재 실제로 WebSocket 구독 중인 종목 집합
        self._active_codes_price: Set[str] = set()
        self._active_codes_pt: Set[str] = set()
        self._external_reserved_slots = 0

        # summary 로그 스로틀 (동시 다발적 rebalance 호출로 인한 중복 발화 방지)
        self._last_summary_time: float = 0.0
        self._SUMMARY_THROTTLE_SEC: float = 2.0

    # ── Public API ─────────────────────────────────────────────────

    def clear_active_state(self) -> None:
        """
        재연결 시 워치독이 호출 — 브로커 연결이 리셋되었으므로 내부 활성 집합을 비운다.
        이후 _rebalance()를 호출하면 _refs(desired)에 있는 모든 종목이 신규 구독된다.
        streaming_stock_repo의 active 초기화는 워치독(_restore_all_subscriptions)에서 별도 처리.
        """
        count_price = len(self._active_codes_price)
        count_pt = len(self._active_codes_pt)
        self._active_codes_price.clear()
        self._active_codes_pt.clear()
        self._streaming_logger.log_clear_active_state(f"SubscriptionPolicy: active 상태 초기화 {count_price}개 클리어")
        self._streaming_logger.log_clear_active_state(f"SubscriptionPolicy: active 상태 초기화 {count_pt}개 클리어")

    def set_external_reserved_slots(self, slots: int) -> None:
        """정책 외부에서 복원한 PT/주문통보 슬롯을 일반 구독 배정에서 제외한다."""
        self._external_reserved_slots = max(0, min(int(slots), self.MAX_WS_SLOTS))

    async def add_subscription(
        self, code: str, priority: SubscriptionPriority, 
        category_key: str, stream_type: StreamingType = StreamingType.UNIFIED_PRICE,
        *, rebalance: bool = True,
    ) -> bool:
        """
        구독을 요청합니다. 
        단, CRITICAL(프로그램 매매) 요청 시 슬롯이 부족하면 밀어내지 않고 False(거절)를 반환합니다.
        """
        # 1. 시뮬레이션: 이 요청을 수용했을 때 총 슬롯 계산 (PT = 1, Price = 1)
        required_slots = 1
        current_used_slots = self._calculate_used_slots()
        
        if priority == SubscriptionPriority.CRITICAL and (current_used_slots + required_slots > self.MAX_WS_SLOTS):
            self._streaming_logger.log_add_subscription_rejection(code=code, message=f"웹소켓 한도 초과: 프로그램 매매 구독 거절")
            return False # 거절 (Rejection)

        # 2. 등록
        self._refs.setdefault(code, {})[category_key] = {
            "priority": priority,
            "type": stream_type
        }
        
        # 3. 재조정 (Rebalance)
        if rebalance:
            await self._rebalance()
        return True

    async def remove_subscription(self, code: str, category_key: str) -> None:
        """특정 카테고리에서 종목 구독을 해제합니다."""
        if code in self._refs:
            self._refs[code].pop(category_key, None)
            if not self._refs[code]:
                del self._refs[code]
                if self._streaming_stock_repo:
                    await self._streaming_stock_repo.unmark_desired(code, StreamingType.UNIFIED_PRICE)
        await self._rebalance()

    async def remove_category(self, category_key: str) -> None:
        """카테고리 전체의 구독을 한 번에 해제합니다 (전략 종료 시 사용)."""
        codes_in_category = [c for c, cats in self._refs.items() if category_key in cats]
        for code in codes_in_category:
            self._refs[code].pop(category_key, None)
            if not self._refs[code]:
                del self._refs[code]
                if self._streaming_stock_repo:
                    await self._streaming_stock_repo.unmark_desired(code, StreamingType.UNIFIED_PRICE)
        await self._rebalance()

    async def drop_unhealthy_price_subscription(self, code: str, reason: str = "unhealthy_stream") -> bool:
        """비정상 체결가 스트리밍 종목을 가격 구독 목록에서 제거한다.

        REST fallback이 필요한 수준이면 해당 가격 구독은 신뢰하지 않고,
        다음 조회부터 StockRepository의 streaming TTL 우회를 타지 않도록 정리한다.
        다만 관심종목 알림은 지속 구독 의도를 보존하고 연결만 새로 맺는다.
        """
        if not code:
            return False

        removed = False
        preserved_price_ref = False
        if code in self._refs:
            price_categories = [
                category_key
                for category_key, request in self._refs[code].items()
                if request.get("type") == StreamingType.UNIFIED_PRICE
            ]
            for category_key in price_categories:
                if category_key in self._PERSISTENT_PRICE_CATEGORIES:
                    preserved_price_ref = True
                    continue
                self._refs[code].pop(category_key, None)
                removed = True
            if not self._refs.get(code):
                self._refs.pop(code, None)

        if self._streaming_stock_repo and code not in self._refs:
            await self._streaming_stock_repo.unmark_desired(code, StreamingType.UNIFIED_PRICE)

        unsubscribed = False
        if code in self._active_codes_price:
            await self._do_unsubscribe(code, StreamingType.UNIFIED_PRICE)
            unsubscribed = True
            removed = True
        else:
            self._stock_repo.unmark_streaming(code)
            if self._streaming_stock_repo:
                await self._streaming_stock_repo.mark_inactive(code, StreamingType.UNIFIED_PRICE)

        if preserved_price_ref:
            await self._rebalance()

        if self._streaming_logger and not unsubscribed and not preserved_price_ref:
            self._streaming_logger.log_unsubscribe(
                code=code,
                active_count=len(self._active_codes_price) + len(self._active_codes_pt),
            )
        self._logger.warning(
            f"SubscriptionPolicy: unhealthy price subscription dropped "
            f"code={code} reason={reason}"
        )
        return removed

    async def sync_subscriptions(
        self,
        codes: List[str],
        category_key: str,
        priority: SubscriptionPriority,
        stream_type: StreamingType = StreamingType.UNIFIED_PRICE,
        *,
        rebalance: bool = True,
    ) -> None:
        """
        카테고리 전체를 새 코드 목록으로 원자적으로 교체합니다.
        전략 워치리스트 갱신 시 사용 — rebalance 1회만 호출.
        """
        old_codes = {c for c, cats in self._refs.items() if category_key in cats}
        new_codes = set(codes)

        removed_codes = old_codes - new_codes
        added_codes = new_codes - old_codes

        for code in removed_codes:
            self._refs[code].pop(category_key, None)
            if not self._refs[code]:
                del self._refs[code]

        for code in new_codes:
            self._refs.setdefault(code, {})[category_key] = {
                "priority": priority,
                "type": stream_type,
            }

        if self._streaming_stock_repo:
            for code in removed_codes:
                if code not in self._refs:
                    await self._streaming_stock_repo.unmark_desired(code, stream_type)
            for code in added_codes:
                await self._streaming_stock_repo.mark_desired(
                    code,
                    stream_type,
                    source="program",
                )

        if rebalance:
            await self._rebalance()

    def is_streaming(self, code: str) -> bool:
        """해당 종목이 현재 실시간 구독 중인지 여부."""
        return code in self._active_codes_price or code in self._active_codes_pt

    def get_status(self) -> dict:
        """현재 구독 현황을 반환합니다 (모니터링/API 용)."""
        pending_by_priority: Dict[int, List[str]] = {}
        for code, cats in self._refs.items():
            # 수정: p가 딕셔너리이므로 "priority" 키를 가져와서 계산
            best = min(int(p["priority"]) for p in cats.values())
            pending_by_priority.setdefault(best, []).append(code)

        return {
            "active_count": len(self._active_codes_price) + len(self._active_codes_pt),
            "max_subscriptions": self.MAX_WS_SLOTS,
            "active_codes_price": sorted(self._active_codes_price),
            "active_codes_pt": sorted(self._active_codes_pt),
            "pending_count": len(self._refs),
            "pending_by_priority": {
                "CRITICAL": sorted(pending_by_priority.get(int(SubscriptionPriority.CRITICAL), [])),
                "HIGH": sorted(pending_by_priority.get(int(SubscriptionPriority.HIGH), [])),
                "MEDIUM": sorted(pending_by_priority.get(int(SubscriptionPriority.MEDIUM), [])),
                "LOW": sorted(pending_by_priority.get(int(SubscriptionPriority.LOW), [])),
            },
        }

    # ── Internal rebalance logic ────────────────────────────────────

    async def _rebalance(self) -> None:
        """
        요청된 구독 목록을 우선순위로 정렬하여 MAX_WS_SLOTS 한도 내에서 최적 분배.
        변경이 필요한 종목만 구독/해지 처리하며, 변동 사항을 로깅함.
        """
        def _best_priority(code: str) -> int:
            return min(int(req["priority"]) for req in self._refs[code].values())

        # 1. 우선순위 높은 순으로 정렬
        ranked_codes = sorted(self._refs.keys(), key=lambda c: (_best_priority(c), c))
        
        desired_price: Set[str] = set()
        desired_pt: Set[str] = set()

        ledger = self._get_broker_ledger()
        if ledger is None:
            reserved = self._external_reserved_slots
        else:
            # 정책이 소유하지 않는 등록(장운영정보/체결통보 등)도 KIS 한도를 소비한다.
            reserved = max(
                0,
                ledger["total"] - len(ledger["price_codes"]) - len(ledger["program_trading_codes"]),
            )
        available_slots = max(0, self.MAX_WS_SLOTS - reserved)

        # 2. 슬롯 할당 (Greedy)
        for code in ranked_codes:
            requests = self._refs[code].values()
            is_pt = any(req["type"] == StreamingType.PROGRAM_TRADING for req in requests)
            is_price = any(req["type"] == StreamingType.UNIFIED_PRICE for req in requests)

            allocated = False
            if is_pt and available_slots >= 1:
                desired_pt.add(code)
                available_slots -= 1
                allocated = True
            if is_price and available_slots >= 1:
                desired_price.add(code)
                available_slots -= 1
                allocated = True
            if not allocated:
                break

        # 3. 변경 대상 추출 (타입별 분리)
        # 브로커에 등록돼 있으나 정책이 원하지 않는 종목(고아)도 해지 대상에 포함한다.
        # 정책 장부만 초기화되고 KIS 등록은 남는 경로가 있어, 회수하지 않으면 슬롯이 영구 소모된다.
        registered_price = ledger["price_codes"] if ledger else set()
        registered_pt = ledger["program_trading_codes"] if ledger else set()
        registered_pt_policy_owned = registered_pt
        if registered_pt and self._streaming_stock_repo:
            source_getter = getattr(self._streaming_stock_repo, "get_pt_subscription_sources", None)
            if callable(source_getter):
                sources = source_getter()
                if isinstance(sources, dict):
                    registered_pt_policy_owned = {
                        code for code in registered_pt
                        if sources.get(code) != StreamingStockRepo.SOURCE_MANUAL
                    }

        to_unsubscribe_price = (self._active_codes_price | registered_price) - desired_price
        to_subscribe_price = desired_price - self._active_codes_price

        to_unsubscribe_pt = (self._active_codes_pt | registered_pt_policy_owned) - desired_pt
        to_subscribe_pt = desired_pt - self._active_codes_pt

        # 4. 실제 구독/해지 수행
        for code in sorted(to_unsubscribe_price):
            await self._do_unsubscribe(code, StreamingType.UNIFIED_PRICE)
        for code in sorted(to_unsubscribe_pt):
            await self._do_unsubscribe(code, StreamingType.PROGRAM_TRADING)

        if not await self._ensure_websocket_connected_for_subscribe(to_subscribe_price | to_subscribe_pt):
            return

        # 슬롯이 모자라 브로커가 일부를 거절하더라도 우선순위 높은 종목이 먼저 자리를 잡도록
        # ranked_codes 순서를 그대로 따른다 (set 순회는 비결정적이라 LOW 가 먼저 채갈 수 있다).
        subscribe_rank = {code: i for i, code in enumerate(ranked_codes)}
        for code in sorted(to_subscribe_price, key=lambda c: subscribe_rank.get(c, len(ranked_codes))):
            await self._do_subscribe(code, StreamingType.UNIFIED_PRICE)
        for code in sorted(to_subscribe_pt, key=lambda c: subscribe_rank.get(c, len(ranked_codes))):
            await self._do_subscribe(code, StreamingType.PROGRAM_TRADING)

        # 5. [기존 로직 복원] 한도 초과(Dropped) 경고 로그
        total_requested = sum(
            int(any(req["type"] == StreamingType.UNIFIED_PRICE for req in cats.values()))
            + int(any(req["type"] == StreamingType.PROGRAM_TRADING for req in cats.values()))
            for cats in self._refs.values()
        )
        total_fulfilled = len(desired_price) + len(desired_pt)
        dropped = total_requested - total_fulfilled

        if dropped > 0:
            self._streaming_logger.log_dropped_subscriptions(
                message=f"SubscriptionPolicy: 웹소켓 구독 한도 초과 — {dropped}개 종목이 대기 상태 "
                         f"(active_pt={len(self._active_codes_pt)}, active_price={len(self._active_codes_price)}, "
                         f"requested={total_requested}, max_slots={self.MAX_WS_SLOTS})"
            )

        # 6. [기존 로직 복원] 2초 스로틀 기반 상태 요약 기록
        # 6. 2초 스로틀 기반 상태 요약 기록
        changed = bool(to_subscribe_price or to_unsubscribe_price or to_subscribe_pt or to_unsubscribe_pt)
        
        if changed and self._streaming_logger:
            now = time.monotonic()
            if now - self._last_summary_time >= self._SUMMARY_THROTTLE_SEC:
                self._last_summary_time = now
                status = self.get_status() 
                
                # ✅ 수정 포인트: Price와 PT의 활성화된 종목들을 하나의 리스트로 병합 (중복 제거 후 정렬)
                combined_active_codes = sorted(
                    set(status.get("active_codes_price", [])) | set(status.get("active_codes_pt", []))
                )
                
                self._streaming_logger.log_summary(
                    active_count=status.get("active_count", 0),
                    active_codes=combined_active_codes,  # 병합된 리스트 전달
                    pending_by_priority=status.get("pending_by_priority", {}),
                )

    async def _ensure_websocket_connected_for_subscribe(self, codes: Set[str]) -> bool:
        if not codes:
            return True
        if self._market_calendar and not await self._is_realtime_market_open_now():
            return True

        connector = getattr(self._streaming, "connect_websocket", None)
        if not callable(connector):
            return True

        try:
            result = connector()
            if inspect.isawaitable(result):
                result = await result
            if result is False:
                if self._streaming_logger:
                    self._streaming_logger.log_subscribe_failure(
                        ",".join(sorted(codes)),
                        "SubscriptionPolicy: WebSocket 연결 실패 — 구독 보류",
                    )
                return False
            return True
        except Exception as e:
            if self._streaming_logger:
                self._streaming_logger.log_subscribe_failure(
                    ",".join(sorted(codes)),
                    f"SubscriptionPolicy: WebSocket 연결 실패 — {e}",
                )
            return False

    async def _do_subscribe(self, code: str, stream_type: StreamingType) -> None:
        if self._market_calendar and not await self._is_realtime_market_open_now():
            self._streaming_logger.log_subscribe_pending(code=code, message="SubscriptionPolicy: 장 외 시간 — 구독 보류")
            return
        try:
            if stream_type == StreamingType.UNIFIED_PRICE:
                success = await self._streaming.subscribe_unified_price(code)
                # subscribe_unified_price 의 성공은 "프레임 전송"일 뿐이다. KIS 등록 ACK 까지
                # 확정돼야 active 로 마킹한다. ACK 미확정이면 active 로 두지 않아(유령 구독 방지)
                # 다음 rebalance 에서 재구독된다.
                if success and not await self._streaming.wait_unified_price_ack(code):
                    if self._streaming_logger:
                        self._streaming_logger.log_subscribe_failure(
                            code, "SubscriptionPolicy: 구독 ACK 미확정 — 다음 rebalance 재시도")
                    return
            elif stream_type == StreamingType.PROGRAM_TRADING:
                success = await self._streaming.subscribe_program_trading(code)
                if success:
                    success = await self._streaming.wait_program_trading_ack(code)
            if success:
                if stream_type == StreamingType.UNIFIED_PRICE:
                    self._active_codes_price.add(code)
                elif stream_type == StreamingType.PROGRAM_TRADING:
                    self._active_codes_pt.add(code)                
                self._stock_repo.mark_streaming(code)

                if self._streaming_stock_repo:
                    await self._streaming_stock_repo.mark_active(code, stream_type)
                if self._streaming_logger:
                    categories = self._refs.get(code, {})
                    # 로거 호환성을 위해 과거 구조(value가 int인 형태)로 파싱하여 전달
                    logger_categories = {k: int(v["priority"]) for k, v in categories.items()}
                    total_active_count = len(self._active_codes_price) + len(self._active_codes_pt)
                    self._streaming_logger.log_subscribe(
                        code=code,
                        categories=logger_categories,
                        active_count=total_active_count,
                    )
            else:
                self._streaming_logger.log_add_subscription_rejection(
                    code=code,
                    message=
                    f"SubscriptionPolicy: 구독 실패(False 반환)"
                    f"— WebSocket 미연결 또는 브로커 거부 가능성"
                )
        except Exception as e:
            self._streaming_logger.log_subscribe_failure(code, f"SubscriptionPolicy: 구독 실패 : {e}")

    async def _do_unsubscribe(self, code: str, stream_type: StreamingType) -> None:
        try:
            # 1. 스트리밍 해지 및 변수 집합 업데이트
            if stream_type == StreamingType.UNIFIED_PRICE:
                await self._streaming.unsubscribe_unified_price(code)
                self._active_codes_price.discard(code)
            elif stream_type == StreamingType.PROGRAM_TRADING:
                await self._streaming.unsubscribe_program_trading(code)
                self._active_codes_pt.discard(code)
                
            self._stock_repo.unmark_streaming(code)
            
            # 2. Repo 상태 업데이트 (하드코딩 제거, stream_type 매개변수 사용)
            if self._streaming_stock_repo:
                await self._streaming_stock_repo.mark_inactive(code, stream_type)
                
            # 3. 해지 로깅
            if self._streaming_logger:
                # ✅ 수정: 옛날 _active_codes 변수 대신, 두 집합의 합으로 카운트 계산
                total_active_count = len(self._active_codes_price) + len(self._active_codes_pt)
                self._streaming_logger.log_unsubscribe(
                    code=code,
                    active_count=total_active_count,
                )
        except Exception as e:
            # 에러 로깅도 kwargs 방식으로 통일
            if self._streaming_logger:
                self._streaming_logger.log_unsubscribe_failure(code=code, reason=str(e))

    def _get_broker_ledger(self) -> Optional[dict]:
        """브로커가 보고하는 실제 KIS 등록 원장. 제공하지 않으면 None.

        정책 내부 장부(_active_codes_*)는 재연결·직접 구독 경로에서 실제 등록과
        어긋날 수 있으므로, 가능하면 브로커 원장을 슬롯 회계의 진실 소스로 쓴다.
        """
        getter = getattr(self._streaming, "get_subscription_ledger", None)
        if not callable(getter):
            return None
        try:
            ledger = getter()
        except Exception as e:
            self._logger.warning(f"SubscriptionPolicy: 구독 원장 조회 실패 — 내부 장부 사용 ({e})")
            return None
        if not isinstance(ledger, dict):
            return None
        try:
            return {
                "total": int(ledger["total"]),
                "price_codes": set(ledger["price_codes"]),
                "program_trading_codes": set(ledger["program_trading_codes"]),
            }
        except (KeyError, TypeError, ValueError):
            return None

    def _calculate_used_slots(self) -> int:
        """
        현재 사용 중인 웹소켓 슬롯 개수를 계산합니다.
        (일반 호가/체결(Price) = 1슬롯, 프로그램 매매(PT) = 1슬롯)
        """
        ledger = self._get_broker_ledger()
        if ledger is not None:
            return ledger["total"]
        return (
            self._external_reserved_slots
            +
            len(self._active_codes_price)
            + len(self._active_codes_pt)
        )

    async def _is_realtime_market_open_now(self) -> bool:
        """실시간 스트리밍 구독은 NXT 확장 거래시간을 포함해 장중 여부를 판단한다."""
        try:
            return await self._market_calendar.is_market_open_now(include_nxt=True)
        except TypeError:
            return await self._market_calendar.is_market_open_now()

# Backward-compatibility alias
PriceSubscriptionService = SubscriptionPolicy
