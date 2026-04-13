"""
services/minervini_stage_service.py

Mark Minervini의 4단계(Stage Analysis) + 트렌드 템플릿(Trend Template) 구현.

Stage 정의:
    1 (무관심): 바닥 다지기, 이평선 수렴
    2 (상승):   트렌드 템플릿 8조건 충족 — 유일한 매수 구간
    3 (고점):   변동성 급증 + MA50 이탈 — 익절/손절 강화
    4 (하락):   MA200 하회 or MA200 하향 — 매수 금지
"""

from __future__ import annotations

import logging
from statistics import mean
from typing import List, Optional, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from services.stock_query_service import StockQueryService
    from services.rs_rating_service import RSRatingService


class MinerviniStageService:
    """미너비니 4단계 분류 서비스."""

    # Stage 상수
    STAGE_UNKNOWN = 0
    STAGE_1_NEGLECT = 1
    STAGE_2_ADVANCING = 2
    STAGE_3_TOPPING = 3
    STAGE_4_DECLINING = 4

    def __init__(
        self,
        stock_query_service: "StockQueryService",
        rs_rating_service: Optional["RSRatingService"] = None,
        slope_lookback: int = 20,
        volatility_threshold: float = 0.02,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """
        Args:
            stock_query_service: OHLCV 조회용 서비스.
            rs_rating_service:   RS Rating 조회용 서비스 (선택적).
            slope_lookback:      MA200 기울기 계산 기간(거래일). 기본 20 = 미너비니 "최소 1개월".
            volatility_threshold: Stage 3 고변동성 임계값 (ATR/평균가). 기본 0.02 = 2%.
            logger:              Logger 인스턴스.
        """
        self._stock_query_svc = stock_query_service
        self._rs_rating_svc = rs_rating_service
        self._slope_lookback = slope_lookback
        self._vol_threshold = volatility_threshold
        self._logger = logger or logging.getLogger(__name__)

    # ── 공개 비동기 메서드 ─────────────────────────────────────────────────

    async def get_stage_for_code(self, code: str) -> int:
        """단일 종목의 현재 Stage를 실시간으로 계산한다.

        OHLCV 260일치 조회 → RS Rating 조회 → classify_stage() 호출.

        Returns:
            1~4 (Stage), 또는 0 (데이터 부족으로 미계산).
        """
        try:
            ohlcv_resp = await self._stock_query_svc.get_recent_daily_ohlcv(
                code, limit=260
            )
            if not ohlcv_resp or ohlcv_resp.rt_cd != "0" or not ohlcv_resp.data:
                self._logger.debug(f"[MinerviniStage] {code} OHLCV 데이터 없음")
                return self.STAGE_UNKNOWN

            rows = ohlcv_resp.data
            closes, lows = self._extract_price_series(rows)

            if len(closes) < 200:
                self._logger.debug(
                    f"[MinerviniStage] {code} 종가 데이터 부족 ({len(closes)}일) — STAGE_UNKNOWN"
                )
                return self.STAGE_UNKNOWN

            rs_rating = await self._fetch_rs_rating(code)
            return self.classify_stage(closes, lows, rs_rating)

        except Exception as e:
            self._logger.warning(f"[MinerviniStage] {code} Stage 계산 오류: {e}")
            return self.STAGE_UNKNOWN

    # ── 핵심 계산 메서드 (동기, 순수 함수) ────────────────────────────────

    def classify_stage(
        self,
        closes: List[float],
        lows: List[float],
        rs_rating: int = 0,
    ) -> int:
        """미너비니 트렌드 템플릿으로 Stage 1~4를 분류한다.

        Args:
            closes:    종가 리스트 (오래된 순). 최소 200개 필요.
            lows:      장중 저가 리스트 (stck_lwpr 기준). 52주 신저가 산출에 사용.
            rs_rating: IBD RS Rating 1~99. 0이면 해당 조건 skip.

        Returns:
            STAGE_4 (4), STAGE_2 (2), STAGE_3 (3), STAGE_1 (1), STAGE_UNKNOWN (0).
        """
        if len(closes) < 200:
            return self.STAGE_UNKNOWN

        price = closes[-1]
        ma50  = mean(closes[-50:])
        ma150 = mean(closes[-150:])
        ma200 = mean(closes[-200:])

        # MA200 기울기: numpy 선형회귀 (20일 = 미너비니 "최소 1개월 우상향" 기준)
        slope_window = closes[-self._slope_lookback:]
        ma200_slope = self._calculate_slope(slope_window)

        # 52주 고가: 종가 기준
        w52_closes = closes[-252:] if len(closes) >= 252 else closes
        w52_high = max(w52_closes)

        # 52주 저가: 장중 저가 기준 (미너비니 원칙)
        low_window = lows[-252:] if len(lows) >= 252 else lows
        w52_low = min(low_window) if low_window else min(closes)

        # ── Stage 4 (최우선 필터) ──────────────────────────────────────────
        if price < ma200 or ma200_slope <= 0:
            return self.STAGE_4_DECLINING

        # ── Stage 2 (트렌드 템플릿 8조건) ────────────────────────────────
        # 조건 8: RS Rating — 데이터 없으면(0) skip하고 경고 로그
        if rs_rating == 0:
            self._logger.debug(
                f"[MinerviniStage] RS Rating 데이터 부족 — RS 조건 skip (stage 판정 계속)"
            )
            rs_ok = True
        else:
            rs_ok = rs_rating >= 70

        is_stage2 = (
            ma200_slope > 0              # ①  MA200 우상향
            and ma50 > ma150 > ma200     # ②③ 정배열 (50 > 150 > 200)
            and price > ma150            # ④  가격 > MA150
            and price > ma50             # ⑤  가격 > MA50
            and price >= w52_low * 1.25  # ⑥  52주 저가(장중 저가) 대비 +25%
            and price >= w52_high * 0.75 # ⑦  52주 고가 대비 -25% 이내
            and rs_ok                    # ⑧  RS Rating >= 70
        )
        if is_stage2:
            return self.STAGE_2_ADVANCING

        # ── Stage 3 (고점/배분) ───────────────────────────────────────────
        if price < ma50 and self._is_high_volatility(closes):
            return self.STAGE_3_TOPPING

        # ── Stage 1 (무관심/횡보) ─────────────────────────────────────────
        return self.STAGE_1_NEGLECT

    # ── 내부 헬퍼 ──────────────────────────────────────────────────────────

    def _calculate_slope(self, values: List[float]) -> float:
        """numpy 선형회귀로 기울기 산출.

        단순 증감률보다 노이즈에 강건하며, 미너비니 "지속적 우상향" 조건에 부합.
        데이터가 2개 미만이면 0.0 반환.
        """
        if len(values) < 2:
            return 0.0
        x = np.arange(len(values), dtype=float)
        y = np.array(values, dtype=float)
        try:
            slope = float(np.polyfit(x, y, 1)[0])
        except (np.linalg.LinAlgError, ValueError):
            slope = 0.0
        return slope

    def _is_high_volatility(self, closes: List[float], period: int = 20) -> bool:
        """ATR-proxy: 일간 절대 변화량 / 평균가 > vol_threshold (기본 2%).

        향후 Phase 2에서 VCP(Volatility Contraction Pattern) 판정으로 확장 예정.
        check_vcp_pattern(): 최근 4~5주 고점 대비 하락폭 수축 여부 체크.
        """
        if len(closes) < period + 1:
            return False
        window = closes[-(period + 1):]
        changes = [abs(window[i] - window[i - 1]) for i in range(1, len(window))]
        avg_price = mean(window[1:]) or 1.0
        return (mean(changes) / avg_price) > self._vol_threshold

    def _extract_price_series(
        self, rows: list
    ) -> tuple[List[float], List[float]]:
        """OHLCV 행 목록에서 종가/저가 리스트 추출.

        KIS API 필드명(stck_clpr, stck_lwpr) 우선, 없으면 'close'/'low' fallback.
        rows는 오래된 순 또는 최신 순 모두 처리 — DB에서 날짜 오름차순 반환 가정.
        """
        closes: List[float] = []
        lows: List[float] = []
        for r in rows:
            close_val = r.get("stck_clpr") or r.get("close") or 0
            low_val   = r.get("stck_lwpr") or r.get("low") or close_val
            try:
                closes.append(float(close_val))
                lows.append(float(low_val))
            except (TypeError, ValueError):
                continue
        return closes, lows

    async def _fetch_rs_rating(self, code: str) -> int:
        """RS Rating 서비스에서 최신 RS Rating 조회. 없으면 0 반환."""
        if not self._rs_rating_svc:
            return 0
        try:
            resp = await self._rs_rating_svc.get_rating(code)
            if resp and resp.rt_cd == "0" and resp.data:
                return int(resp.data.rs_rating)
        except Exception as e:
            self._logger.debug(f"[MinerviniStage] {code} RS Rating 조회 실패: {e}")
        return 0

    # ── 디버그 헬퍼 ────────────────────────────────────────────────────────

    def describe_stage(self, stage: int) -> str:
        """Stage 번호를 사람이 읽기 좋은 문자열로 변환."""
        return {
            self.STAGE_UNKNOWN:   "미계산",
            self.STAGE_1_NEGLECT: "Stage 1 (무관심)",
            self.STAGE_2_ADVANCING: "Stage 2 (상승)",
            self.STAGE_3_TOPPING: "Stage 3 (고점)",
            self.STAGE_4_DECLINING: "Stage 4 (하락)",
        }.get(stage, f"Stage {stage}")
