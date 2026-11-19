"""장전 시가 갭상승(A) 확률 예측 서비스 단위 테스트."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.notification_service import NotificationCategory
from services.premarket_direction_service import (
    FORECAST_FEATURES,
    TARGETS,
    PremarketDirectionService,
    build_forecast_features,
)


def _days(n, end=date(2026, 11, 18)):
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return list(reversed(out))


class FakeFetcher:
    """미국 지표가 오르면 다음 한국 거래일 시가가 갭상승하도록 만든 합성 데이터."""

    def __init__(self, n=400, today="2026-11-19", today_us_move=0.02, missing=None):
        days = _days(n)
        self.kr, self.us = [], {}
        moves = [((i * 37) % 21 - 10) / 500.0 for i in range(n)]  # -2% ~ +2%
        moves[-1] = today_us_move  # 11-18 미국 세션 → 11-19 한국장 예측 입력
        price = 100.0
        us_rows = []
        for d, m in zip(days, moves):
            price *= 1 + m
            us_rows.append({"date": d, "close": price})
        k = 1000.0
        for i, d in enumerate(days):
            if i > 0:
                k *= 1 + (0.01 if moves[i - 1] > 0 else -0.01)
            self.kr.append({"date": d, "open": k, "high": k, "low": k, "close": k})
        # 오늘(장 시작 전) 행이 섞여 들어와도 학습/기준일에서 제외돼야 한다
        self.kr.append({"date": today, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0})
        for spec in {f for t in TARGETS for f in t.features}:
            sign = spec.sign
            self.us[spec.symbol] = [
                {"date": r["date"], "close": r["close"] if sign > 0 else 10000.0 / r["close"]}
                for r in us_rows
            ]
        if missing:
            self.us[missing] = []
        self.fetch_kr_index = AsyncMock(side_effect=lambda symbol, count: list(self.kr))
        self.fetch_us = AsyncMock(side_effect=lambda symbol: list(self.us[symbol]))


def _clock(ts=datetime(2026, 11, 19, 8, 35)):
    clock = MagicMock()
    clock.get_current_kst_time.return_value = ts
    return clock


def _service(fetcher, clock=None, notifier=None):
    return PremarketDirectionService(
        fetcher=fetcher,
        market_clock=clock or _clock(),
        notification_service=notifier,
        logger=MagicMock(),
        min_train_days=250,
    )


def test_build_forecast_features_uses_sessions_after_previous_korean_close():
    us = {"spx": [{"date": "2026-11-17", "close": 100.0}, {"date": "2026-11-18", "close": 103.0},
                  {"date": "2026-11-19", "close": 999.0}]}
    feats = build_forecast_features(us, prev_date="2026-11-18", date="2026-11-19")
    assert feats == {"spx": pytest.approx(0.03)}


@pytest.mark.asyncio
async def test_refresh_predicts_up_after_strong_us_session_and_notifies():
    notifier = MagicMock()
    notifier.emit = AsyncMock()
    svc = _service(FakeFetcher(today_us_move=0.02), notifier=notifier)

    result = await svc.refresh(notify=True)

    assert result["ok"] is True
    assert result["date"] == "2026-11-19"
    assert result["base_date"] == "2026-11-18"          # 오늘 행은 기준일이 아니다
    assert [t["code"] for t in result["targets"]] == ["KOSPI", "000660"]
    for target in result["targets"]:
        assert target["probability"] > 0.6
        assert target["outlook"] == "갭상승 우세"
        assert target["train_days"] >= 250
    assert {f["name"] for f in result["features"]} == {"sox", "ndx", "spx", "ewy", "vix", "usdkrw", "mu"}
    spx = next(f for f in result["features"] if f["name"] == "spx")
    assert spx["value"] == pytest.approx(0.02)
    assert svc.get_latest() is result

    notifier.emit.assert_awaited_once()
    kwargs = notifier.emit.await_args.kwargs
    assert kwargs["category"] == NotificationCategory.STRATEGY
    assert "갭" in kwargs["title"]
    assert "코스피" in kwargs["message"] and "SK하이닉스" in kwargs["message"]
    assert kwargs["metadata"]["event"] == "premarket_direction_forecast"


@pytest.mark.asyncio
async def test_refresh_predicts_down_after_weak_us_session():
    svc = _service(FakeFetcher(today_us_move=-0.02))
    result = await svc.refresh(notify=False)
    assert all(t["probability"] < 0.4 for t in result["targets"])
    assert all(t["outlook"] == "갭하락 우세" for t in result["targets"])


@pytest.mark.asyncio
async def test_hynix_target_uses_micron_on_top_of_base_features():
    kospi, hynix = TARGETS
    assert kospi.features == FORECAST_FEATURES
    assert [f.name for f in hynix.features] == [f.name for f in FORECAST_FEATURES] + ["mu"]


@pytest.mark.asyncio
async def test_refresh_reports_error_without_raising_when_feature_missing():
    notifier = MagicMock()
    notifier.emit = AsyncMock()
    svc = _service(FakeFetcher(missing="^VIX"), notifier=notifier)

    result = await svc.refresh(notify=True)

    assert result["ok"] is False
    assert result["date"] == "2026-11-19"
    assert "vix" in result["error"]
    notifier.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_reports_error_when_fetch_fails():
    fetcher = FakeFetcher()
    fetcher.fetch_us = AsyncMock(side_effect=RuntimeError("boom"))
    result = await _service(fetcher).refresh(notify=False)
    assert result == {"ok": False, "date": "2026-11-19", "error": "boom"}


@pytest.mark.asyncio
async def test_get_or_compute_caches_per_day_without_notifying():
    notifier = MagicMock()
    notifier.emit = AsyncMock()
    fetcher = FakeFetcher()
    clock = _clock()
    svc = _service(fetcher, clock=clock, notifier=notifier)

    first = await svc.get_or_compute()
    second = await svc.get_or_compute()
    assert first is second
    assert fetcher.fetch_kr_index.await_count == len(TARGETS)  # 대상별 1회
    notifier.emit.assert_not_awaited()

    clock.get_current_kst_time.return_value = datetime(2026, 11, 20, 8, 35)
    await svc.get_or_compute()
    assert fetcher.fetch_kr_index.await_count == 2 * len(TARGETS)


@pytest.mark.asyncio
async def test_get_or_compute_retries_after_failed_result():
    fetcher = FakeFetcher()
    svc = _service(fetcher)
    fetcher.fetch_us = AsyncMock(side_effect=RuntimeError("boom"))
    assert (await svc.get_or_compute())["ok"] is False

    fetcher.fetch_us = AsyncMock(side_effect=lambda symbol: list(fetcher.us[symbol]))
    assert (await svc.get_or_compute())["ok"] is True
