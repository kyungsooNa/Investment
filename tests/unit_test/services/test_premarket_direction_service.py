"""장전 시가 갭상승(A) 확률 예측 서비스 단위 테스트."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import json

import pytest

from services.notification_service import NotificationCategory
from services.premarket_direction_service import (
    FORECAST_FEATURES,
    TARGETS,
    PremarketDirectionService,
    build_forecast_features,
    summarize_track,
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

    def __init__(self, n=400, today="2026-11-19", today_us_move=0.02, missing=None, today_open=1.0):
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
        if today_open is not None:
            self.kr.append({"date": today, "open": today_open, "high": today_open,
                            "low": today_open, "close": today_open})
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


def _service(fetcher, clock=None, notifier=None, history_path=None):
    return PremarketDirectionService(
        fetcher=fetcher,
        market_clock=clock or _clock(),
        notification_service=notifier,
        logger=MagicMock(),
        min_train_days=250,
        history_path=history_path,
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


# ── 적중률 추적 ───────────────────────────────────────────────────────

def test_summarize_track_counts_recent_hits_and_confident_hits():
    history = {
        "2026-11-13": {"KOSPI": {"probability": 0.70, "actual_gap": 0.004}},   # 확신 적중
        "2026-11-16": {"KOSPI": {"probability": 0.55, "actual_gap": -0.002}},  # 비확신 실패
        "2026-11-17": {"KOSPI": {"probability": 0.30, "actual_gap": 0.001}},   # 확신 실패
        "2026-11-18": {"KOSPI": {"probability": 0.35, "actual_gap": -0.006}},  # 확신 적중
        "2026-11-19": {"KOSPI": {"probability": 0.80, "actual_gap": None}},    # 미확정 → 제외
        "2026-11-12": {"000660": {"probability": 0.9, "actual_gap": 0.01}},    # 다른 대상
    }
    assert summarize_track(history, "KOSPI", window=20) == {
        "n": 4, "hits": 2, "confident_n": 3, "confident_hits": 2,
    }
    assert summarize_track(history, "KOSPI", window=2) == {
        "n": 2, "hits": 1, "confident_n": 2, "confident_hits": 1,
    }
    assert summarize_track({}, "KOSPI") == {"n": 0, "hits": 0, "confident_n": 0, "confident_hits": 0}


@pytest.mark.asyncio
async def test_refresh_records_prediction_and_fills_past_actual_gap(tmp_path):
    path = tmp_path / "history.json"
    fetcher = FakeFetcher(today_open=None)          # 장 시작 전: 오늘 시가 없음
    prev = fetcher.kr[-2]["close"]
    gap_1118 = fetcher.kr[-1]["open"] / prev - 1     # 11-18 실제 갭
    path.write_text(json.dumps({
        "2026-11-18": {"KOSPI": {"probability": 0.8, "actual_gap": None},
                       "000660": {"probability": 0.2, "actual_gap": None}},
    }), encoding="utf-8")

    result = await _service(fetcher, history_path=path).refresh(notify=False)

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["2026-11-18"]["KOSPI"]["actual_gap"] == pytest.approx(gap_1118)
    today = saved["2026-11-19"]
    assert today["KOSPI"]["probability"] == pytest.approx(result["targets"][0]["probability"])
    assert today["KOSPI"]["actual_gap"] is None
    kospi = result["targets"][0]
    assert kospi["track"]["n"] == 1
    assert kospi["previous"] == {"date": "2026-11-18", "probability": 0.8, "actual_gap": pytest.approx(gap_1118)}


@pytest.mark.asyncio
async def test_refresh_after_open_fills_today_actual_and_keeps_it(tmp_path):
    path = tmp_path / "history.json"
    fetcher = FakeFetcher(today_open=2000.0)
    await _service(fetcher, history_path=path).refresh(notify=False)
    saved = json.loads(path.read_text(encoding="utf-8"))
    expected = 2000.0 / fetcher.kr[-2]["close"] - 1
    assert saved["2026-11-19"]["KOSPI"]["actual_gap"] == pytest.approx(expected)


@pytest.mark.asyncio
async def test_notification_goes_external_with_track_and_previous_result(tmp_path):
    path = tmp_path / "history.json"
    path.write_text(json.dumps({
        "2026-11-18": {"KOSPI": {"probability": 0.8, "actual_gap": 0.0045},
                       "000660": {"probability": 0.2, "actual_gap": 0.003}},
    }), encoding="utf-8")
    notifier = MagicMock()
    notifier.emit = AsyncMock()

    await _service(FakeFetcher(today_open=None), notifier=notifier, history_path=path).refresh(notify=True)

    kwargs = notifier.emit.await_args.kwargs
    assert kwargs["metadata"]["force_external"] is True        # 텔레그램(외부) 전송
    message = kwargs["message"]
    assert "전 거래일(2026-11-18)" in message
    assert "코스피 80% → 실제 +0.45% 적중" in message
    assert "SK하이닉스 20% → 실제 +0.30% 실패" in message
    assert "최근 20일 적중" in message


@pytest.mark.asyncio
async def test_refresh_without_history_path_does_not_persist(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = await _service(FakeFetcher(today_open=None)).refresh(notify=False)
    assert result["ok"] is True
    assert result["targets"][0]["track"]["n"] == 0   # 기록 파일이 없으니 확정된 과거 예측도 없다
    assert not (tmp_path / "data").exists()
