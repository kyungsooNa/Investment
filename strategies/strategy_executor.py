# strategies/strategy_executor.py
import asyncio
import logging
from interfaces.strategy import Strategy
from typing import List, Dict, Optional, Tuple


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
    ):
        """
        Args:
            strategy:                래핑할 Strategy 인스턴스.
            minervini_stage_service: MinerviniStageService (optional).
            stage_guard:             True 시 Stage Guard 활성화. 기본 False.
            allowed_stages:          통과 허용 Stage 번호 튜플.
                                     기본 (0, 2) — 미계산(0)·상승(2)만 허용.
                                     Stage 0(미계산)을 포함하는 이유: 데이터 부족/오류로
                                     Stage 판정 불가 종목을 차단하지 않고 전략 자체 기준에
                                     맡기기 위한 의도적 설계. 엄격하게 Stage 2만 허용하려면
                                     (2,)로 변경할 것.
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

    async def execute(self, stock_codes: List[str]) -> Dict:
        filtered = await self._apply_stage_guard(stock_codes)
        return await self.strategy.run(filtered)

    # ── Stage Guard ────────────────────────────────────────────────────────

    async def _apply_stage_guard(self, stock_codes: List[str]) -> List[str]:
        """Stage Guard가 비활성이거나 서비스 미주입 시 그대로 반환.

        활성 시: 각 종목 Stage를 병렬 조회 후 allowed_stages 외 코드 제거.
        타임아웃/오류 발생 종목은 Stage 0(미계산)으로 처리 → 통과.
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
                return 0  # 오류/타임아웃 → UNKNOWN → 통과

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
        return allowed
