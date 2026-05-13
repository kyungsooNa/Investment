# strategies/strategy_executor.py
import asyncio
import logging
import time
from interfaces.strategy import Strategy
from typing import Any, Awaitable, Callable, List, Dict, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from common.types import ResStockFullInfoApiOutput
    from config.config_loader import RiskGateConfig
    from services.price_stream_service import PriceStreamService

from services.data_quality_service import DataQualityService


class StrategyExecutor:
    """Strategy 실행기.

    stage_guard=True + minervini_stage_service 주입 시,
    execute() 호출 전 stock_codes를 allowed_stages로 pre-filter 한다.
    기본값 allowed_stages=(0, 2): Stage 0(미계산)은 통과, Stage 2만 매수 허용.
    """

    def __init__(
        self,
        strategy: Strategy,
        minervini_stage_service=None,
        stage_guard: bool = False,
        allowed_stages: Tuple[int, ...] = (0, 2),
        guard_timeout: float = 3.0,
        logger: Optional[logging.Logger] = None,
        max_stage_concurrency: int = 20,
        max_liquidity_concurrency: int = 10,
        risk_gate_config: Optional["RiskGateConfig"] = None,
        get_current_price_fn: Optional[Callable[[str], Awaitable["ResStockFullInfoApiOutput"]]] = None,
        price_stream_service: Optional["PriceStreamService"] = None,
        snapshot_max_age_sec: float = 5.0,
    ):
        """
        Args:
            strategy:                래핑할 Strategy 인스턴스.
            minervini_stage_service: MinerviniStageService (optional).
            stage_guard:             True 시 Stage Guard 활성화. 기본 False.
            allowed_stages:          통과 허용 Stage 번호 튜플.
                                     기본 (0, 2) — 미계산(0)·상승(2)만 허용.
                                     Stage 0(미계산): 데이터 부족으로 판정 불가 → 전략 자체 기준에 위임.
                                     API 오류/타임아웃(-1): Fail-Close로 강제 차단.
                                     엄격하게 Stage 2만 허용하려면 (2,)로 변경할 것.
            guard_timeout:           종목별 Stage 조회 타임아웃(초). 기본 3.0.
            logger:                  Logger 인스턴스.
        """
        self.strategy = strategy
        self._minervini_svc = minervini_stage_service
        self._stage_guard = stage_guard
        self._allowed_stages = allowed_stages
        self._guard_timeout = guard_timeout
        self._logger = logger or logging.getLogger(__name__)
        self._max_stage_concurrency = max_stage_concurrency
        self._max_liquidity_concurrency = max_liquidity_concurrency
        self._risk_gate_config = risk_gate_config
        self._get_current_price_fn = get_current_price_fn
        self._price_stream_service = price_stream_service
        self._snapshot_max_age_sec = snapshot_max_age_sec

    async def execute(self, stock_codes: List[str]) -> Dict:
        filtered = await self._apply_stage_guard(stock_codes)
        filtered = await self._apply_liquidity_filter(filtered)
        return await self.strategy.run(filtered)

    # ── Liquidity Filter ───────────────────────────────────────────────────

    async def _apply_liquidity_filter(self, stock_codes: List[str]) -> List[str]:
        """거래대금/거래량 기준 미달 종목을 제거한다.

        risk_gate_config 또는 get_current_price_fn 이 없으면 그대로 반환.
        조회 오류 종목은 Fail-Close로 제거한다.
        """
        if not self._risk_gate_config or not self._get_current_price_fn:
            return stock_codes

        strategy_name = getattr(self.strategy, "name", "")
        cfg = self._risk_gate_config
        limit = cfg.strategy_limits.get(strategy_name) or cfg.default_strategy_limit
        min_value = limit.min_trading_value_won
        min_volume = limit.min_avg_volume

        if min_value is None and min_volume is None:
            return stock_codes

        sem = asyncio.Semaphore(self._max_liquidity_concurrency)

        async def _passes(code: str) -> bool:
            try:
                tr_pbmn, vol = await self._lookup_liquidity(code, sem)
                if min_value is not None and tr_pbmn < min_value:
                    self._logger.info({"event": "liquidity_blocked", "code": code,
                                       "reason": "insufficient_trading_value", "value": tr_pbmn})
                    return False
                if min_volume is not None and vol < min_volume:
                    self._logger.info({"event": "liquidity_blocked", "code": code,
                                       "reason": "insufficient_volume", "value": vol})
                    return False
                return True
            except Exception as e:
                self._logger.warning({"event": "liquidity_fetch_error", "code": code, "error": str(e)})
                return False  # Fail-Close

        results = await asyncio.gather(*[_passes(c) for c in stock_codes])
        allowed = [c for c, ok in zip(stock_codes, results) if ok]

        blocked_count = len(stock_codes) - len(allowed)
        if blocked_count:
            self._logger.info(
                f"[LiquidityFilter] {blocked_count}개 종목 필터링 "
                f"(strategy={strategy_name}, min_value={min_value}, min_vol={min_volume})"
            )
        return allowed

    async def _lookup_liquidity(
        self, code: str, sem: asyncio.Semaphore
    ) -> Tuple[int, int]:
        """(acml_tr_pbmn, acml_vol) 을 반환한다.

        조회 우선순위:
          1) PriceStreamService snapshot (received_at 이 snapshot_max_age_sec 이내)
          2) get_current_price_fn(REST). 응답을 받으면 snapshot 캐시에 보강.
        """
        if self._price_stream_service is not None:
            try:
                snap = self._price_stream_service.get_market_snapshot(code)
            except Exception:
                snap = None

            if snap is not None:
                age = time.time() - snap.received_at
                if age <= self._snapshot_max_age_sec:
                    return snap.acml_tr_pbmn, snap.acml_vol
                self._logger.debug({
                    "event": "liquidity_snapshot_stale",
                    "code": code,
                    "reason": DataQualityService.REASON_SNAPSHOT_STALE,
                    "age_sec": round(age, 2),
                })
            else:
                self._logger.debug({
                    "event": "liquidity_snapshot_missing",
                    "code": code,
                    "reason": DataQualityService.REASON_SNAPSHOT_MISSING,
                })

        async with sem:
            info = await self._get_current_price_fn(code)
        tr_pbmn = int(getattr(info, 'acml_tr_pbmn', None) or "0")
        vol = int(getattr(info, 'acml_vol', None) or "0")

        if self._price_stream_service is not None:
            try:
                self._price_stream_service.cache_price_snapshot(
                    code,
                    price=str(getattr(info, 'stck_prpr', '') or ''),
                    volume=str(vol),
                    acml_tr_pbmn=str(tr_pbmn),
                )
            except Exception as e:
                self._logger.debug(
                    {"event": "snapshot_backfill_skipped", "code": code, "error": str(e)}
                )

        return tr_pbmn, vol

    # ── Stage Guard ────────────────────────────────────────────────────────

    async def _apply_stage_guard(self, stock_codes: List[str]) -> List[str]:
        """Stage Guard가 비활성이거나 서비스 미주입 시 그대로 반환.

        활성 시: 각 종목 Stage를 병렬 조회 후 allowed_stages 외 코드 제거.
        타임아웃/오류 발생 종목은 -1(차단)으로 처리 → Fail-Close.
        """
        if not self._stage_guard or not self._minervini_svc:
            return stock_codes

        sem = asyncio.Semaphore(self._max_stage_concurrency)

        async def _safe_stage(code: str) -> int:
            try:
                async with sem:
                    result = await asyncio.wait_for(
                        self._minervini_svc.get_stage_for_code(code),
                        timeout=self._guard_timeout,
                    )
                # get_stage_for_code 반환값: tuple(int, str) 또는 int 호환
                return result[0] if isinstance(result, tuple) else int(result)
            except Exception:
                return -1  # 오류/타임아웃 → Fail-Close (차단)

        stages = await asyncio.gather(*[_safe_stage(c) for c in stock_codes])

        allowed: List[str] = []
        blocked: List[Tuple[str, int]] = []
        for code, stage in zip(stock_codes, stages):
            if stage in self._allowed_stages:
                allowed.append(code)
            else:
                blocked.append((code, stage))

        if blocked:
            self._logger.info(
                f"[StageGuard] {len(blocked)}개 종목 필터링 "
                f"(Stage {sorted({s for _, s in blocked})}): "
                f"{[c for c, _ in blocked]}"
            )
            for code, stage in blocked:
                self._logger.info({"event": "stage_blocked", "code": code, "stage": stage})
        return allowed
