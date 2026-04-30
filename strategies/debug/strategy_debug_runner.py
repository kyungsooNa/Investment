from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from common.types import TradeSignal
from interfaces.live_strategy import LiveStrategy
from strategies.debug.rejection_collector import RejectionCollector, RejectionEvent


@dataclass
class DebugReport:
    """전략 디버깅 실행 결과."""
    strategy_name: str
    requested_codes: Optional[List[str]]  # CLI --codes 그대로 (None이면 universe 전체)
    scanned_codes: List[str]              # 실제 scan에 들어간 종목 (proxy 통과 후)
    missing_codes: List[str]              # requested 중 universe 교집합에 없는 종목
    signals: List[TradeSignal]
    events: List[RejectionEvent]
    limitations: List[str] = field(default_factory=list)


class _UniverseFilterProxy:
    """OneilUniverseService를 감싸 watchlist만 필터링, 나머지 호출은 원본에 위임.

    get_watchlist()가 Dict[code, item] 형태로 반환한다는 것을 전제로 한다.
    """

    def __init__(self, inner: object, allowed: Optional[Set[str]] = None) -> None:
        self._inner = inner
        self._allowed = allowed
        self._last_full_set: Set[str] = set()
        self._last_full_codes: List[str] = []
        self._last_scanned_codes: List[str] = []

    async def get_watchlist(self, **kw) -> Dict:
        full: Dict = await self._inner.get_watchlist(**kw)
        self._last_full_codes = list(full.keys())
        self._last_full_set = set(self._last_full_codes)
        if self._allowed is None:
            self._last_scanned_codes = list(full.keys())
            return full
        filtered = {code: item for code, item in full.items() if code in self._allowed}
        self._last_scanned_codes = list(filtered.keys())
        return filtered

    def __getattr__(self, name: str):
        # is_market_timing_ok 등 다른 메서드는 원본에 그대로 위임
        return getattr(self._inner, name)


class StrategyDebugRunner:
    """전략을 한 번 실행하면서 RejectionCollector로 탈락 이유를 수집한다.

    사용 예:
        debug_logger = logging.getLogger("strategy_debug.OneilPocketPivot")
        strategy = OneilPocketPivotStrategy(..., logger=debug_logger)
        runner = StrategyDebugRunner(strategy, debug_logger)
        report = await runner.run(candidate_codes=["005930", "000660"])

    StageGuard 활성화:
        runner = StrategyDebugRunner(strategy, debug_logger, stage_service=minervini_svc)
    """

    def __init__(
        self,
        strategy: LiveStrategy,
        debug_logger: logging.Logger,
        stage_service=None,
        allowed_stages: tuple = (0, 2),
    ) -> None:
        self._strategy = strategy
        self._debug_logger = debug_logger
        self._stage_service = stage_service
        self._allowed_stages = allowed_stages

    async def _apply_stage_guard(self, codes: List[str]) -> List[str]:
        """stage_service가 주입된 경우 stage 필터를 적용하고 stage_blocked 이벤트를 emit한다."""
        allowed: List[str] = []
        for code in codes:
            try:
                result = await self._stage_service.get_stage_for_code(code)
                stage = result[0] if isinstance(result, tuple) else int(result)
            except Exception:
                stage = -1
            if stage in self._allowed_stages:
                allowed.append(code)
            else:
                self._debug_logger.info({"event": "stage_blocked", "code": code, "stage": stage})
        return allowed

    async def run(self, candidate_codes: Optional[List[str]] = None) -> DebugReport:
        """전략을 1회 실행하고 DebugReport를 반환한다.

        Args:
            candidate_codes: 스캔할 종목 코드 목록. None이면 universe 전체를 스캔.
                             universe watchlist에 없는 코드는 missing_codes에 기록된다.
        """
        original_universe = getattr(self._strategy, "_universe", None)
        proxy: Optional[_UniverseFilterProxy] = None
        scanned_codes: List[str] = []
        missing_codes: List[str] = []

        if original_universe is not None:
            allowed = set(candidate_codes) if candidate_codes else None
            proxy = _UniverseFilterProxy(original_universe, allowed=allowed)

        try:
            if proxy is not None:
                self._strategy._universe = proxy
                # candidate_codes 지정 시 missing_codes 정확히 계산, stage_service 주입 시 stage 체크를 위해 먼저 호출
                if candidate_codes or self._stage_service is not None:
                    await proxy.get_watchlist()

                if self._stage_service is not None:
                    passed = await self._apply_stage_guard(proxy._last_scanned_codes)
                    proxy._allowed = set(passed)

            with RejectionCollector(logger=self._debug_logger) as col:
                signals = await self._strategy.scan()

            if proxy is not None and candidate_codes:
                full_set = proxy._last_full_set
                scanned_codes = [c for c in candidate_codes if c in full_set]
                missing_codes = [c for c in candidate_codes if c not in full_set]
            elif proxy is not None:
                scanned_codes = list(proxy._last_scanned_codes)
            elif candidate_codes:
                scanned_codes = list(candidate_codes)

        finally:
            if proxy is not None and original_universe is not None:
                self._strategy._universe = original_universe

        return DebugReport(
            strategy_name=self._strategy.name,
            requested_codes=candidate_codes,
            scanned_codes=scanned_codes,
            missing_codes=missing_codes,
            signals=signals,
            events=col.events,
        )
