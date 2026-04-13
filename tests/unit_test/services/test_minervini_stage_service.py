"""
tests/unit_test/services/test_minervini_stage_service.py

MinerviniStageService 단위 테스트.
- classify_stage(): Stage 1~4 분류 로직
- check_vcp_pattern(): VCP 수축 감지
- _is_high_volatility(): ATR-proxy
- get_stage_for_code(): 비동기 통합 흐름
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from services.minervini_stage_service import MinerviniStageService


# ── 픽스처 ──────────────────────────────────────────────────────────────────

def _make_svc(**kwargs) -> MinerviniStageService:
    svc = MinerviniStageService(
        stock_query_service=AsyncMock(),
        rs_rating_service=None,
        **kwargs,
    )
    return svc


def _flat_closes(price: float, n: int) -> list[float]:
    """단순 수평 종가 시계열."""
    return [price] * n


def _trending_closes(start: float, end: float, n: int) -> list[float]:
    """선형 우상향 종가 시계열."""
    step = (end - start) / (n - 1)
    return [start + step * i for i in range(n)]


# ── classify_stage ───────────────────────────────────────────────────────────

class TestClassifyStage:

    def test_insufficient_data_returns_unknown(self):
        svc = _make_svc()
        closes = _flat_closes(10000, 150)   # 200일 미만
        result = svc.classify_stage(closes, closes)
        assert result == MinerviniStageService.STAGE_UNKNOWN

    def test_stage4_when_price_below_ma200(self):
        """가격이 MA200 아래이면 Stage 4."""
        svc = _make_svc()
        # 200일 기간 동안 서서히 하락 → 마지막 가격이 MA200 아래
        closes = _trending_closes(10000, 5000, 260)
        lows = closes[:]
        result = svc.classify_stage(closes, lows)
        assert result == MinerviniStageService.STAGE_4_DECLINING

    def test_stage4_when_ma200_slope_negative(self):
        """MA200 기울기가 음수이면 Stage 4."""
        svc = _make_svc()
        # MA200은 마지막 20일만 급락 → slope <= 0
        closes = [10000] * 240 + _trending_closes(10000, 7000, 20)
        lows = closes[:]
        result = svc.classify_stage(closes, lows)
        assert result == MinerviniStageService.STAGE_4_DECLINING

    def test_stage2_trend_template_all_conditions(self):
        """트렌드 템플릿 8조건 충족 → Stage 2."""
        svc = _make_svc()
        # 우상향 260일: 가격 > MA50 > MA150 > MA200, slope > 0
        closes = _trending_closes(5000, 15000, 260)
        lows = [c * 0.95 for c in closes]   # 저가는 종가의 95%
        result = svc.classify_stage(closes, lows, rs_rating=80)
        assert result == MinerviniStageService.STAGE_2_ADVANCING

    def test_stage2_rs_zero_skips_rs_condition(self):
        """RS Rating = 0 이면 RS 조건 skip 후 나머지 조건으로 판정."""
        svc = _make_svc()
        closes = _trending_closes(5000, 15000, 260)
        lows = [c * 0.95 for c in closes]
        # rs_rating=0 → rs 조건 skip → 나머지 조건 충족이면 Stage 2
        result = svc.classify_stage(closes, lows, rs_rating=0)
        assert result == MinerviniStageService.STAGE_2_ADVANCING

    def test_stage1_flat_above_ma200(self):
        """MA200 위이지만 Stage 2 조건 미달, 저변동성 → Stage 1."""
        svc = _make_svc()
        # 완전 수평 → slope ≒ 0 이면 Stage 4지만 미세 우상향으로 stage1 유도
        closes = _trending_closes(9900, 10000, 260)
        lows = closes[:]
        result = svc.classify_stage(closes, lows, rs_rating=50)
        # 정배열 미달(ma50≈ma150≈ma200), 저변동성 → Stage 1
        assert result == MinerviniStageService.STAGE_1_NEGLECT

    def test_stage3_price_below_ma50_high_volatility(self):
        """MA50 이탈 + 고변동성 → Stage 3.

        설계:
        - 240일 강한 우상향(5000→12000) → MA200 확립, slope 양수
        - 마지막 20일: 완만한 우상향 drift(+73/일) + ±300 교번 → slope 양수 유지
          - price ≈ 9587, MA200 ≈ 9360 (price > MA200 ✓)
          - MA50 ≈ 10620 (price < MA50 ✓)
          - ATR ≈ 6% >> 0.001 임계값 ✓
        """
        svc = _make_svc(volatility_threshold=0.001)
        base = _trending_closes(5000, 12000, 240)
        # 완만 우상향 drift + 교번 변동 → slope 양수 & 변동성 크고 MA50 아래
        volatile_20 = [
            8500 + i * 73 + (300 if i % 2 == 0 else -300)
            for i in range(20)
        ]
        closes = base + volatile_20
        lows = closes[:]
        result = svc.classify_stage(closes, lows)
        assert result == MinerviniStageService.STAGE_3_TOPPING

    def test_return_reason_tuple(self):
        """return_reason=True 시 (int, str) 튜플 반환."""
        svc = _make_svc()
        closes = _trending_closes(5000, 15000, 260)
        lows = [c * 0.95 for c in closes]
        stage, reason = svc.classify_stage(closes, lows, rs_rating=80, return_reason=True)
        assert isinstance(stage, int)
        assert isinstance(reason, str)
        assert len(reason) > 0


# ── check_vcp_pattern ────────────────────────────────────────────────────────

class TestCheckVcpPattern:

    def test_contracting_weekly_ranges_returns_true(self):
        """주간 변동폭이 꾸준히 수축하면 VCP 감지."""
        svc = _make_svc()
        # 5주(25일) — 매주 range 점차 감소: 500 → 400 → 300 → 200 → 100
        closes = []
        for week_range in [500, 400, 300, 200, 100]:
            mid = 10000.0
            for i in range(5):
                closes.append(mid + (week_range / 2 if i < 3 else -week_range / 2))
        assert svc.check_vcp_pattern(closes) is True

    def test_expanding_weekly_ranges_returns_false(self):
        """주간 변동폭이 증가(팽창)하면 VCP 아님."""
        svc = _make_svc()
        # 매주 range 점차 증가: 100 → 200 → 300 → 400 → 500
        closes = []
        for week_range in [100, 200, 300, 400, 500]:
            mid = 10000.0
            for i in range(5):
                closes.append(mid + (week_range / 2 if i < 3 else -week_range / 2))
        assert svc.check_vcp_pattern(closes) is False

    def test_insufficient_data_returns_false(self):
        """데이터가 부족하면 False."""
        svc = _make_svc()
        closes = [10000.0] * 10   # 5주(25일) 미만
        assert svc.check_vcp_pattern(closes, weeks=5) is False

    def test_with_highs_parameter(self):
        """highs 파라미터가 제공되면 highs로 주간 고가 계산."""
        svc = _make_svc()
        closes = [10000.0] * 25
        highs = []
        for week_range in [500, 400, 300, 200, 100]:
            for _ in range(5):
                highs.append(10000.0 + week_range)
        result = svc.check_vcp_pattern(closes, highs=highs, weeks=5)
        # highs가 수축하므로 VCP 감지
        assert result is True


# ── _is_high_volatility ──────────────────────────────────────────────────────

class TestIsHighVolatility:

    def test_low_volatility_returns_false(self):
        svc = _make_svc(volatility_threshold=0.02)
        closes = [10000.0] * 25   # 변화 없음
        assert svc._is_high_volatility(closes) is False

    def test_high_volatility_returns_true(self):
        svc = _make_svc(volatility_threshold=0.001)   # 낮은 임계값
        closes = [10000 + (500 if i % 2 == 0 else -500) for i in range(25)]
        assert svc._is_high_volatility(closes) is True

    def test_insufficient_data_returns_false(self):
        svc = _make_svc()
        closes = [10000.0] * 5   # period+1 미만
        assert svc._is_high_volatility(closes, period=20) is False


# ── get_stage_for_code (비동기 통합) ─────────────────────────────────────────

class TestGetStageForCode:

    @pytest.mark.asyncio
    async def test_returns_unknown_when_ohlcv_missing(self):
        svc = _make_svc()
        svc._stock_query_svc.get_recent_daily_ohlcv = AsyncMock(
            return_value=None
        )
        stage, reason = await svc.get_stage_for_code("000000")
        assert stage == MinerviniStageService.STAGE_UNKNOWN

    @pytest.mark.asyncio
    async def test_returns_unknown_when_data_insufficient(self):
        from common.types import ResCommonResponse
        svc = _make_svc()
        # 150일치만 반환
        rows = [{"close": 10000.0, "low": 9500.0}] * 150
        svc._stock_query_svc.get_recent_daily_ohlcv = AsyncMock(
            return_value=ResCommonResponse(rt_cd="0", msg1="ok", data=rows)
        )
        stage, _ = await svc.get_stage_for_code("000000")
        assert stage == MinerviniStageService.STAGE_UNKNOWN

    @pytest.mark.asyncio
    async def test_returns_stage2_for_uptrending_stock(self):
        from common.types import ResCommonResponse
        svc = _make_svc()
        closes = _trending_closes(5000, 15000, 260)
        rows = [
            {"close": c, "low": c * 0.95}
            for c in closes
        ]
        svc._stock_query_svc.get_recent_daily_ohlcv = AsyncMock(
            return_value=ResCommonResponse(rt_cd="0", msg1="ok", data=rows)
        )
        stage, _ = await svc.get_stage_for_code("005930")
        assert stage == MinerviniStageService.STAGE_2_ADVANCING

    @pytest.mark.asyncio
    async def test_error_in_ohlcv_returns_unknown(self):
        svc = _make_svc()
        svc._stock_query_svc.get_recent_daily_ohlcv = AsyncMock(
            side_effect=RuntimeError("API 오류")
        )
        stage, _ = await svc.get_stage_for_code("000000")
        assert stage == MinerviniStageService.STAGE_UNKNOWN
