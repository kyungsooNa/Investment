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
import asyncio
from typing import List, Optional, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from services.stock_query_service import StockQueryService
    from services.rs_rating_service import RSRatingService
    from repositories.stock_repository import StockRepository
    from task.background.after_market.minervini_update_task import MinerviniUpdateTask


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
        stock_repository: Optional["StockRepository"] = None,
        slope_lookback: int = 20,
        volatility_threshold: float = 0.02,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """
        Args:
            stock_query_service: OHLCV 조회용 서비스.
            rs_rating_service:   RS Rating 조회용 서비스 (선택적).
            stock_repository:    Stage2 DB 조회용 레포지터리 (선택적).
            slope_lookback:      MA200 기울기 계산 기간(거래일). 기본 20 = 미너비니 "최소 1개월".
            volatility_threshold: Stage 3 고변동성 임계값 (ATR/평균가). 기본 0.02 = 2%.
            logger:              Logger 인스턴스.
        """
        self._stock_query_svc = stock_query_service
        self._rs_rating_svc = rs_rating_service
        self._stock_repository = stock_repository
        self._minervini_update_task: Optional["MinerviniUpdateTask"] = None
        self._refresh_task: Optional[asyncio.Task] = None
        self._slope_lookback = slope_lookback
        self._vol_threshold = volatility_threshold
        self._logger = logger or logging.getLogger(__name__)

    def set_minervini_update_task(self, minervini_update_task) -> None:
        """MinerviniUpdateTask 후주입 — 순환 의존 해소용 (WiringPhase 에서 호출)."""
        self._minervini_update_task = minervini_update_task

    # ── 공개 비동기 메서드 ─────────────────────────────────────────────────

    async def get_stage2_list(self):
        """Minervini Stage 2 종목 목록을 조회한다.

        1차: DB(stock_repository)에서 최신 거래일의 Stage2 종목 조회.
        2차: DB에 데이터가 없으면 _minervini_update_task in-memory 캐시 사용.
        3차: 캐시도 없고 갱신 중이 아니면 백그라운드 갱신 트리거 후 수집 대기 응답.

        Returns:
            ResCommonResponse: rt_cd="0" 성공(data=list), rt_cd="0" 수집 중(data=[]),
                               rt_cd="1" 태스크 미설정(data=None).
        """
        from common.types import ResCommonResponse

        # 1차: DB 조회
        if self._stock_repository:
            try:
                latest_date = await self._stock_repository.get_latest_trade_date()
                if latest_date:
                    db_items = await self._stock_repository.get_minervini_stage2_stocks(latest_date)
                    if db_items:
                        data = [
                            {
                                "code": it.get("code", ""),
                                "name": it.get("name", ""),
                                "stck_prpr": str(it.get("current_price") or 0),
                                "prdy_ctrt": str(it.get("change_rate") or 0),
                                "stage": it.get("minervini_stage", 2),
                                "rs_rating": it.get("rs_rating") or 0,
                                "market_cap": it.get("market_cap") or 0,
                            }
                            for it in db_items
                        ]
                        return ResCommonResponse(rt_cd="0", msg1="성공", data=data)
            except Exception as e:
                self._logger.debug(f"[MinerviniStage] Stage2 DB 조회 실패 — 캐시로 폴백: {e}")

        # 2차: in-memory 캐시
        task = self._minervini_update_task
        if not task:
            return ResCommonResponse(rt_cd="1", msg1="MinerviniUpdateTask 미설정", data=None)

        cache = await task.get_minervini_stage2_cache()
        if cache:
            return ResCommonResponse(rt_cd="0", msg1="성공", data=cache)

        # 3차: 갱신 트리거 후 수집 대기
        progress = task.get_progress()
        if not progress.get("running"):
            # 참조를 보관해 실행 중 GC 수거를 방지 (fire-and-forget 안티패턴 회피).
            self._refresh_task = asyncio.create_task(task.refresh_minervini_stage2())
        return ResCommonResponse(rt_cd="0", msg1="수집 중", data=[])

    async def get_stage_for_code(self, code: str) -> tuple[int, str]:
        """단일 종목의 현재 Stage를 실시간으로 계산한다.

        OHLCV 260일치 조회 → RS Rating 조회 → classify_stage() 호출.

        Returns:
            1~4 (Stage), 또는 0 (데이터 부족으로 미계산).
        """
        try:
            # 1) 우선 DB에 저장된 장마감 기준 Minervini 결과가 있는지 확인
            try:
                stock_repo = None
                if hasattr(self._stock_query_svc, 'market_data_service'):
                    stock_repo = getattr(self._stock_query_svc.market_data_service, '_stock_repo', None)
                if stock_repo:
                    try:
                        snap = await stock_repo.get_latest_daily_snapshot(code)
                        if snap and snap.get('minervini_stage') is not None:
                            # 확인된 경우, 최신 거래일 스냅샷인지 검증
                            latest_td = None
                            if hasattr(self._stock_query_svc.market_data_service, '_mcs') and self._stock_query_svc.market_data_service._mcs:
                                latest_td = await self._stock_query_svc.market_data_service._mcs.get_latest_trading_date()
                            # snap['trade_date'] 비교 — 없으면 그대로 반환
                            if not latest_td or snap.get('trade_date') == latest_td:
                                try:
                                    stage_val = int(snap.get('minervini_stage') or 0)
                                except Exception:
                                    stage_val = self.STAGE_UNKNOWN
                                reason_val = snap.get('minervini_reason') or "(DB)"
                                self._logger.info(f"[MinerviniStage] {code} DB snapshot used: stage={stage_val}")
                                return stage_val, reason_val
                    except Exception:
                        # DB access 실패시 무시하고 실시간 계산 실행
                        pass
            except Exception:
                pass
            try:
                ohlcv_resp = await self._stock_query_svc.get_recent_daily_ohlcv(
                    code, limit=260
                )
            except asyncio.CancelledError:
                self._logger.debug(f"[MinerviniStage] {code} OHLCV 호출 취소됨")
                return self.STAGE_UNKNOWN, "작업 취소"

            if not ohlcv_resp or ohlcv_resp.rt_cd != "0" or not ohlcv_resp.data:
                self._logger.debug(f"[MinerviniStage] {code} OHLCV 데이터 없음")
                return self.STAGE_UNKNOWN, "OHLCV 데이터 없음"

            rows = ohlcv_resp.data
            closes, lows = self._extract_price_series(rows)

            if len(closes) < 200:
                self._logger.debug(
                    f"[MinerviniStage] {code} 종가 데이터 부족 ({len(closes)}일) — STAGE_UNKNOWN"
                )
                return self.STAGE_UNKNOWN, f"데이터 부족 ({len(closes)}일)"

            rs_rating = await self._fetch_rs_rating(code)
            stage, reason = self.classify_stage(closes, lows, rs_rating, return_reason=True)
            # 로그에 판정 이유를 남기고 호출자에게도 반환
            self._logger.info(f"[MinerviniStage] {code} Stage={stage} reason={reason}")
            return stage, reason

        except Exception as e:
            self._logger.warning(f"[MinerviniStage] {code} Stage 계산 오류: {e}")
            return self.STAGE_UNKNOWN, f"오류: {e}"

    # ── 핵심 계산 메서드 (동기, 순수 함수) ────────────────────────────────

    def classify_stage(
        self,
        closes: List[float],
        lows: List[float],
        rs_rating: int = 0,
        return_reason: bool = False,
    ) -> tuple[int, str] | int:
        """미너비니 트렌드 템플릿으로 Stage 1~4를 분류한다.

        Args:
            closes:    종가 리스트 (오래된 순). 최소 200개 필요.
            lows:      장중 저가 리스트 (stck_lwpr 기준). 52주 신저가 산출에 사용.
            rs_rating: IBD RS Rating 1~99. 0이면 해당 조건 skip.

        Returns:
            STAGE_4 (4), STAGE_2 (2), STAGE_3 (3), STAGE_1 (1), STAGE_UNKNOWN (0).
        """
        if len(closes) < 200:
            reason = f"데이터 부족 ({len(closes)}일, 200일 필요)"
            return (self.STAGE_UNKNOWN, reason) if return_reason else self.STAGE_UNKNOWN

        price = closes[-1]
        ma50  = mean(closes[-50:])
        ma150 = mean(closes[-150:])
        ma200 = mean(closes[-200:])

        # MA200 기울기: MA200 시리즈 자체에 numpy 선형회귀
        # (20일 = 미너비니 "최소 1개월 우상향" 기준). 원시 종가가 아니라
        # 이동평균선의 추세를 봐야 단기 눌림을 하락 전환으로 오판하지 않는다.
        ma200_series = self._ma_series(closes, 200, self._slope_lookback)
        ma200_slope = self._calculate_slope(ma200_series)

        # 52주 고가: 종가 기준
        w52_closes = closes[-252:] if len(closes) >= 252 else closes
        w52_high = max(w52_closes)

        # 52주 저가: 장중 저가 기준 (미너비니 원칙)
        low_window = lows[-252:] if len(lows) >= 252 else lows
        w52_low = min(low_window) if low_window else min(closes)

        # ── Stage 4 (최우선 필터) ──────────────────────────────────────────
        if price < ma200:
            reason = f"가격이 MA200 아래 (가격={price:.2f} < MA200={ma200:.2f})"
            return (self.STAGE_4_DECLINING, reason) if return_reason else self.STAGE_4_DECLINING
        if ma200_slope <= 0:
            reason = f"MA200 기울기 비양수 (기울기={ma200_slope:.6f})"
            return (self.STAGE_4_DECLINING, reason) if return_reason else self.STAGE_4_DECLINING

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
            reason = (
                f"트렌드 템플릿 충족: ma200_slope={ma200_slope:.6f}, ma50={ma50:.2f}, "
                f"ma150={ma150:.2f}, ma200={ma200:.2f}, 가격={price:.2f}, RS={rs_rating}"
            )
            return (self.STAGE_2_ADVANCING, reason) if return_reason else self.STAGE_2_ADVANCING

        # ── Stage 3 (고점/배분) ───────────────────────────────────────────
        if price < ma50 and self._is_high_volatility(closes):
            reason = f"MA50 아래이면서 고변동성 (가격={price:.2f} < MA50={ma50:.2f})"
            return (self.STAGE_3_TOPPING, reason) if return_reason else self.STAGE_3_TOPPING

        # ── Stage 1 (무관심/횡보) ─────────────────────────────────────────
        reason = "기본: 무관심/횡보"
        return (self.STAGE_1_NEGLECT, reason) if return_reason else self.STAGE_1_NEGLECT

    # ── 내부 헬퍼 ──────────────────────────────────────────────────────────

    def _ma_series(self, closes: List[float], window: int, count: int) -> List[float]:
        """최근 'count'개 거래일에 대한 'window'기간 단순이동평균 시리즈(오래된 순).

        MA200 기울기 산출에 사용. 데이터가 window+1 미만이면 점이 1개 이하라
        기울기를 낼 수 없어 빈/단일 리스트를 반환하며, 이 경우 _calculate_slope가
        0.0을 돌려주어 보수적으로 Stage 4(하락)로 분류된다.
        """
        n = len(closes)
        max_points = n - window + 1
        if max_points < 1:
            return []
        points = min(count, max_points)
        return [
            mean(closes[i - window + 1: i + 1])
            for i in range(n - points, n)
        ]

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

        Stage 3(고점/배분) 판정에 사용.
        VCP 설정 중인 종목은 check_vcp_pattern()으로 별도 판별.
        """
        if len(closes) < period + 1:
            return False
        window = closes[-(period + 1):]
        changes = [abs(window[i] - window[i - 1]) for i in range(1, len(window))]
        avg_price = mean(window[1:]) or 1.0
        return (mean(changes) / avg_price) > self._vol_threshold

    def check_vcp_pattern(
        self,
        closes: List[float],
        highs: Optional[List[float]] = None,
        weeks: int = 5,
    ) -> bool:
        """VCP(Volatility Contraction Pattern) 감지: 연속 주간 변동폭이 수축하는지 확인.

        미너비니의 VCP는 매 사이클마다 고점-저점 폭이 줄어드는 패턴으로,
        Stage 2 진입 직전 저변동성 눌림목 구간임을 나타낸다.
        (Stage 3의 고변동성과 반대 — Stage 1→2 전환 신호)

        Args:
            closes: 종가 리스트 (오래된 순). 최소 weeks × 5개 필요.
            highs:  장중 고가 리스트 (optional). 없으면 closes로 대체.
            weeks:  분석할 주 수 (기본 5주 = ~25거래일).

        Returns:
            True if VCP 패턴 감지
            (마지막 weeks−1개 주간 중 절반 이상이 이전 주보다 변동폭 수축).
        """
        period = weeks * 5
        if len(closes) < period:
            return False

        window_c = closes[-period:]
        window_h = (highs[-period:] if highs and len(highs) >= period else window_c)

        weekly_ranges: List[float] = []
        for i in range(0, period, 5):
            wh = window_h[i: i + 5]
            wl = window_c[i: i + 5]
            if wh and wl:
                weekly_ranges.append(max(wh) - min(wl))

        if len(weekly_ranges) < 2:
            return False

        contractions = sum(
            1
            for i in range(1, len(weekly_ranges))
            if weekly_ranges[i] < weekly_ranges[i - 1]
        )
        return contractions >= max(1, len(weekly_ranges) // 2)

    def _extract_price_series(
        self, rows: list
    ) -> tuple[List[float], List[float]]:
        """OHLCV 행 목록에서 종가/저가 리스트 추출.

        KIS API 필드명(stck_clpr, stck_lwpr) 우선, 없으면 'close'/'low' fallback.
        모든 행에 날짜 키가 있으면 오래된 순으로 정렬해 closes[-1]이 최신가가 되도록
        보장한다(없으면 입력 순서 유지). 종가 0 이하 행은 거래정지 등 비정상으로
        보고 제외한다 — 52주 저가 등 계산 오염 방지.
        """
        rows = self._ensure_ascending(rows)
        closes: List[float] = []
        lows: List[float] = []
        for r in rows:
            close_val = r.get("stck_clpr") or r.get("close") or 0
            low_val   = r.get("stck_lwpr") or r.get("low") or close_val
            try:
                c = float(close_val)
                l = float(low_val)
            except (TypeError, ValueError):
                self._logger.warning(
                    f"[MinerviniStage] 가격 데이터 변환 실패 — "
                    f"close={close_val!r}, low={low_val!r}"
                )
                continue
            if c <= 0:
                continue
            if l <= 0:
                l = c
            closes.append(c)
            lows.append(l)
        return closes, lows

    def _ensure_ascending(self, rows: list) -> list:
        """모든 행에 동일한 날짜 키가 있으면 오래된 순으로 정렬, 아니면 원본 유지."""
        for key in ("date", "stck_bsop_date"):
            if rows and all(isinstance(r, dict) and r.get(key) for r in rows):
                return sorted(rows, key=lambda r: r[key])
        return rows

    async def _fetch_rs_rating(self, code: str) -> int:
        """RS Rating 서비스에서 최신 RS Rating 조회. 없으면 0 반환."""
        if not self._rs_rating_svc:
            return 0
        try:
            try:
                resp = await self._rs_rating_svc.get_rating(code)
            except asyncio.CancelledError:
                self._logger.debug(f"[MinerviniStage] {code} RS Rating 조회 취소됨")
                return 0
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
