"""미국 정규장 세션 경과·거래량 환산 서비스 테스트.

국내 전략의 `_get_market_progress_ratio` + `proj_vol = vol / progress` 를 미국장
세션(09:30~16:00 ET, 조기폐장 13:00)에 맞춰 이식한 모듈. 순수 계산이라 IO 없음.
"""
from datetime import datetime
from unittest.mock import MagicMock

import pytest
import pytz

from services.us_session_volume_service import USSessionVolumeService

NY = pytz.timezone("America/New_York")


def _svc(*, early_close=False, afternoon_boost=1.0, morning_min_vol_ratio=0.5):
    calendar = MagicMock()
    calendar.get_close_time_str.return_value = "13:00" if early_close else "16:00"
    return USSessionVolumeService(
        us_market_calendar_service=calendar,
        logger=MagicMock(),
        afternoon_boost=afternoon_boost,
        morning_min_vol_ratio=morning_min_vol_ratio,
    )


def _at(h, m, day=18):
    return NY.localize(datetime(2026, 8, day, h, m))


# ── 경과 비율 ────────────────────────────────────────────────────────────

def test_progress_is_zero_before_open():
    assert _svc().progress_ratio(_at(9, 0), "20260818") == 0.0


def test_progress_is_zero_at_open_and_one_at_close():
    s = _svc()
    assert s.progress_ratio(_at(9, 30), "20260818") == 0.0
    assert s.progress_ratio(_at(16, 0), "20260818") == 1.0


def test_progress_is_half_at_midsession():
    """09:30~16:00 = 390분. 중간(12:45)은 0.5."""
    assert _svc().progress_ratio(_at(12, 45), "20260818") == pytest.approx(0.5)


def test_progress_is_capped_at_one_after_close():
    assert _svc().progress_ratio(_at(20, 0), "20260818") == 1.0


def test_progress_uses_early_close_time():
    """조기폐장일(13:00 ET)은 총 210분 — 11:15 가 중간이다."""
    s = _svc(early_close=True)
    assert s.progress_ratio(_at(11, 15), "20261127") == pytest.approx(0.5)
    assert s.progress_ratio(_at(13, 0), "20261127") == 1.0


# ── 거래량 환산 ──────────────────────────────────────────────────────────

def test_projected_volume_scales_by_elapsed_fraction():
    """장중 절반 시점에 100만주면 종일 200만주로 환산한다."""
    s = _svc()
    assert s.project_volume(1_000_000, _at(12, 45), "20260818") == pytest.approx(2_000_000)


def test_projected_volume_uses_floor_to_avoid_blowup():
    """개장 직후 progress→0 이면 환산이 발산한다 — floor 5% 로 방어한다."""
    s = _svc()
    projected = s.project_volume(10_000, _at(9, 31), "20260818")
    assert projected == pytest.approx(10_000 / 0.05)


def test_projected_volume_is_identity_after_close():
    s = _svc()
    assert s.project_volume(500_000, _at(16, 30), "20260818") == pytest.approx(500_000)


def test_projected_volume_of_zero_is_zero():
    assert _svc().project_volume(0, _at(12, 45), "20260818") == 0.0


# ── 시간대별 허들 ────────────────────────────────────────────────────────

def test_midday_uses_base_multiplier():
    s = _svc(afternoon_boost=1.0)
    hurdle = s.volume_hurdle(avg_volume=1_000_000, base_multiplier=1.5,
                             now=_at(12, 45), trade_date="20260818")
    assert hurdle.threshold == pytest.approx(1_500_000)
    assert hurdle.min_actual_volume is None


def test_morning_adds_absolute_actual_volume_floor():
    """오전장은 환산 뻥튀기가 커서 실거래량 절대 하한을 함께 요구한다."""
    s = _svc(morning_min_vol_ratio=0.5)
    hurdle = s.volume_hurdle(avg_volume=1_000_000, base_multiplier=1.5,
                             now=_at(10, 0), trade_date="20260818")
    assert hurdle.threshold == pytest.approx(1_500_000)
    assert hurdle.min_actual_volume == pytest.approx(500_000)


def test_afternoon_raises_multiplier_against_fake_breakout():
    s = _svc(afternoon_boost=0.5)
    hurdle = s.volume_hurdle(avg_volume=1_000_000, base_multiplier=1.5,
                             now=_at(15, 30), trade_date="20260818")
    assert hurdle.threshold == pytest.approx(2_000_000)
    assert hurdle.min_actual_volume is None


def test_early_close_day_afternoon_window_shifts():
    """조기폐장일에는 오후 구간(마감 1시간 전)도 12:00 으로 당겨진다."""
    s = _svc(early_close=True, afternoon_boost=0.5)
    hurdle = s.volume_hurdle(avg_volume=1_000_000, base_multiplier=1.5,
                             now=_at(12, 30), trade_date="20261127")
    assert hurdle.threshold == pytest.approx(2_000_000)


def test_passes_combines_projection_and_floor():
    """편의 판정: 환산 거래량과 실거래량 하한을 한 번에 본다."""
    s = _svc(morning_min_vol_ratio=0.5)
    # 오전 10:00, progress = 30/390. 실거래량 400,000 → 환산은 통과하지만 하한 미달
    assert s.passes(actual_volume=400_000, avg_volume=1_000_000, base_multiplier=1.5,
                    now=_at(10, 0), trade_date="20260818") is False
    # 실거래량 600,000 → 하한 통과 + 환산도 통과
    assert s.passes(actual_volume=600_000, avg_volume=1_000_000, base_multiplier=1.5,
                    now=_at(10, 0), trade_date="20260818") is True


def test_passes_is_false_for_nonpositive_average():
    s = _svc()
    assert s.passes(actual_volume=100, avg_volume=0, base_multiplier=1.5,
                    now=_at(12, 45), trade_date="20260818") is False


def test_calendar_failure_falls_back_to_regular_close():
    """캘린더가 죽어도 판정을 멈추지 않는다 — 정규 마감(16:00)으로 계속한다."""
    calendar = MagicMock()
    calendar.get_close_time_str.side_effect = RuntimeError("calendar down")
    s = USSessionVolumeService(us_market_calendar_service=calendar, logger=MagicMock())

    assert s.progress_ratio(_at(12, 45), "20260818") == pytest.approx(0.5)
