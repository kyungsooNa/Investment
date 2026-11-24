"""장전 양·음봉과 전일 대비 종가 방향 확률 예측 서비스 단위 테스트."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import json

import httpx
import pytest
import respx

from services.notification_service import NotificationCategory
from services.premarket_direction_service import (
    FORECAST_FEATURES,
    TARGETS,
    DailyHistoryFetcher,
    PremarketDirectionService,
    build_forecast_features,
    summarize_body_track,
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
    """미국 지표가 오르면 다음 한국 거래일이 상승 마감하도록 만든 합성 데이터."""

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
            open_price = k
            if i > 0:
                k *= 1 + (0.01 if moves[i - 1] > 0 else -0.01)
            self.kr.append({"date": d, "open": open_price, "high": max(open_price, k),
                            "low": min(open_price, k), "close": k})
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


@pytest.mark.asyncio
async def test_hynix_daily_prices_use_yahoo_regular_session_ohlc():
    payload = {"chart": {"result": [{
        "meta": {"gmtoffset": 32400},
        "timestamp": [1795392000, 1795478400],
        "indicators": {"quote": [{
            "open": [1079000, 1212000], "close": [1085000, 1158000],
        }]},
    }]}}
    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://query1.finance.yahoo.com/v8/finance/chart/000660.KS")
        route.mock(return_value=httpx.Response(200, json=payload))
        rows = await DailyHistoryFetcher().fetch_kr_index("000660", 2)

    assert route.called
    assert rows == [
        {"date": "2026-11-23", "open": 1079000, "close": 1085000},
        {"date": "2026-11-24", "open": 1212000, "close": 1158000},
    ]


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
    assert [t["code"] for t in result["targets"]] == ["KOSPI", "KOSDAQ", "000660"]
    for target in result["targets"]:
        assert target["probability"] > 0.6
        assert target["outlook"] == "상승 우세"
        assert 0.0 <= target["body_probability"] <= 1.0
        assert target["body_outlook"] in {"양봉 우세", "음봉 우세", "중립"}
        assert target["train_days"] >= 250
    assert {f["name"] for f in result["features"]} == {"sox", "ndx", "spx", "ewy", "vix", "usdkrw", "mu"}
    spx = next(f for f in result["features"] if f["name"] == "spx")
    assert spx["value"] == pytest.approx(0.02)
    assert svc.get_latest() is result

    notifier.emit.assert_awaited_once()
    kwargs = notifier.emit.await_args.kwargs
    assert kwargs["category"] == NotificationCategory.STRATEGY
    assert "종가" in kwargs["title"]
    assert kwargs["message"].index("  양음봉:") < kwargs["message"].index("  전일 대비:")
    assert "• 코스피\n  양음봉: 양봉" in kwargs["message"]
    assert "전일 대비: ▲ 상승 마감" in kwargs["message"]
    assert "코스닥" in kwargs["message"] and "SK하이닉스" in kwargs["message"]
    assert kwargs["metadata"]["event"] == "premarket_direction_forecast"


@pytest.mark.asyncio
async def test_refresh_predicts_down_after_weak_us_session():
    svc = _service(FakeFetcher(today_us_move=-0.02))
    result = await svc.refresh(notify=False)
    assert all(t["probability"] < 0.4 for t in result["targets"])
    assert all(t["outlook"] == "하락 우세" for t in result["targets"])


@pytest.mark.asyncio
async def test_hynix_target_uses_micron_on_top_of_base_features():
    kospi, kosdaq, hynix = TARGETS
    assert kospi.features == FORECAST_FEATURES
    assert kosdaq.features == FORECAST_FEATURES
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
        "2026-11-13": {"KOSPI": {"probability": 0.70, "actual_change": 0.004}},   # 확신 적중
        "2026-11-16": {"KOSPI": {"probability": 0.55, "actual_change": -0.002}},  # 비확신 실패
        "2026-11-17": {"KOSPI": {"probability": 0.30, "actual_change": 0.001}},   # 확신 실패
        "2026-11-18": {"KOSPI": {"probability": 0.35, "actual_change": -0.006}},  # 확신 적중
        "2026-11-19": {"KOSPI": {"probability": 0.80, "actual_change": None}},    # 미확정 → 제외
        "2026-11-12": {"000660": {"probability": 0.9, "actual_change": 0.01}},    # 다른 대상
    }
    assert summarize_track(history, "KOSPI", window=20) == {
        "n": 4, "hits": 2, "confident_n": 3, "confident_hits": 2,
    }
    assert summarize_track(history, "KOSPI", window=2) == {
        "n": 2, "hits": 1, "confident_n": 2, "confident_hits": 1,
    }
    assert summarize_track({}, "KOSPI") == {"n": 0, "hits": 0, "confident_n": 0, "confident_hits": 0}


def test_summarize_body_track_counts_candle_direction_independently():
    history = {
        "2026-11-17": {"KOSPI": {"body_probability": 0.7, "actual_body_change": 0.01}},
        "2026-11-18": {"KOSPI": {"body_probability": 0.3, "actual_body_change": 0.02}},
        "2026-11-19": {"KOSPI": {"body_probability": 0.8, "actual_body_change": None}},
    }
    assert summarize_body_track(history, "KOSPI") == {
        "n": 2, "hits": 1, "confident_n": 2, "confident_hits": 1,
    }


@pytest.mark.asyncio
async def test_refresh_records_prediction_and_fills_past_actual_change(tmp_path):
    path = tmp_path / "history.json"
    fetcher = FakeFetcher(today_open=None)          # 장 시작 전: 오늘 일봉 없음
    prev = fetcher.kr[-2]["close"]
    chg_1118 = fetcher.kr[-1]["close"] / prev - 1    # 11-18 실제 종가 등락
    path.write_text(json.dumps({
        "2026-11-18": {"KOSPI": {"probability": 0.8, "actual_change": None},
                       "000660": {"probability": 0.2, "actual_change": None}},
    }), encoding="utf-8")

    result = await _service(fetcher, history_path=path).refresh(notify=False)

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["2026-11-18"]["KOSPI"]["actual_change"] == pytest.approx(chg_1118)
    today = saved["2026-11-19"]
    assert today["KOSPI"]["probability"] == pytest.approx(result["targets"][0]["probability"])
    assert today["KOSPI"]["body_probability"] == pytest.approx(result["targets"][0]["body_probability"])
    assert today["KOSPI"]["actual_change"] is None
    assert today["KOSPI"]["actual_body_change"] is None
    kospi = result["targets"][0]
    assert kospi["track"]["n"] == 1
    assert kospi["previous"] == {"date": "2026-11-18", "probability": 0.8, "actual_change": pytest.approx(chg_1118)}


@pytest.mark.asyncio
async def test_intraday_today_bar_is_not_taken_as_actual_close(tmp_path):
    """장중 조회 시 오늘 일봉의 '종가'는 현재가라 확정값이 아니다 — 다음 날 채운다."""
    path = tmp_path / "history.json"
    fetcher = FakeFetcher(today_open=2000.0)
    await _service(fetcher, history_path=path).refresh(notify=False)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["2026-11-19"]["KOSPI"]["actual_change"] is None


@pytest.mark.asyncio
async def test_notification_goes_external_with_track_and_previous_result(tmp_path):
    path = tmp_path / "history.json"
    path.write_text(json.dumps({
        "2026-11-18": {"KOSPI": {"probability": 0.8, "actual_change": 0.0045},
                       "000660": {"probability": 0.2, "actual_change": 0.003}},
    }), encoding="utf-8")
    notifier = MagicMock()
    notifier.emit = AsyncMock()

    fetcher = FakeFetcher(today_open=None)
    await _service(fetcher, notifier=notifier, history_path=path).refresh(notify=True)

    kwargs = notifier.emit.await_args.kwargs
    assert kwargs["metadata"]["force_external"] is True        # 텔레그램(외부) 전송
    message = kwargs["message"]
    assert "오늘 예측\n기준: 양음봉은 당일 시가, 전일 대비는 2026-11-18 종가" in message
    assert "전 거래일 결과 (2026-11-18)" in message
    assert "• 코스피: 전일 대비 ▲ 상승 마감 80% → 실제 +0.45% 적중" in message
    hynix_change = fetcher.kr[-1]["close"] / fetcher.kr[-2]["close"] - 1
    hynix_result = "적중" if hynix_change < 0 else "실패"
    assert f"• SK하이닉스: 전일 대비 ▼ 하락 마감 80% → 실제 {hynix_change * 100:+.2f}% {hynix_result}" in message
    assert "최근 최대 20회 예측 적중 (기록된 날 기준)" in message
    assert "• 코스피: 양음봉 0/0일 · 전일 대비 1/1일" in message


@pytest.mark.asyncio
async def test_notification_separates_forecast_previous_results_and_sample_sizes():
    notifier = MagicMock(emit=AsyncMock())
    svc = _service(FakeFetcher(today_open=None), notifier=notifier)
    result = {
        "base_date": "2026-11-23",
        "features": [{"label": "필라델피아 반도체(SOX)", "value": 0.1403}],
        "targets": [{
            "code": "KOSPI", "name": "코스피", "body_probability": 0.48,
            "body_outlook": "중립", "probability": 0.99, "outlook": "상승 우세",
            "previous": {"date": "2026-11-23", "body_probability": 0.49,
                         "actual_body_change": -0.01, "probability": 0.03,
                         "actual_change": -0.0331},
            "body_track": {"hits": 1, "n": 1}, "track": {"hits": 1, "n": 1},
        }],
    }

    await svc._notify(result)

    message = notifier.emit.await_args.kwargs["message"]
    assert "오늘 예측\n기준: 양음봉은 당일 시가, 전일 대비는 2026-11-23 종가" in message
    assert "코스피\n  양음봉: 음봉 52% (중립)\n  전일 대비: ▲ 상승 마감 99% (상승 우세)" in message
    assert "전 거래일 결과 (2026-11-23)\n• 코스피\n  양음봉: 음봉 51% → 실제 음봉 적중\n  전일 대비: ▼ 하락 마감 97% → 실제 -3.31% 적중" in message
    assert "최근 최대 20회 예측 적중 (기록된 날 기준)\n• 코스피: 양음봉 1/1일 · 전일 대비 1/1일" in message
    assert "밤사이 입력 지표\n• 필라델피아 반도체(SOX) +14.03%" in message


@pytest.mark.asyncio
async def test_refresh_without_history_path_does_not_persist(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = await _service(FakeFetcher(today_open=None)).refresh(notify=False)
    assert result["ok"] is True
    assert result["targets"][0]["track"]["n"] == 0   # 기록 파일이 없으니 확정된 과거 예측도 없다
    assert not (tmp_path / "data").exists()


# ── 장 마감 후 결과 보고 ───────────────────────────────────────────────

def test_summarize_track_without_window_counts_all_history():
    history = {f"2026-10-{d:02d}": {"KOSPI": {"probability": 0.7, "actual_change": 0.01 if d % 3 else -0.01}}
               for d in range(1, 31)}
    total = summarize_track(history, "KOSPI", window=None)
    assert total["n"] == 30
    assert total["hits"] == 20          # d % 3 == 0 인 10일만 실패


@pytest.mark.asyncio
async def test_report_close_fills_today_actual_and_notifies_hit_and_totals(tmp_path):
    path = tmp_path / "history.json"
    fetcher = FakeFetcher(today_open=1.0)
    prev_close = fetcher.kr[-2]["close"]
    fetcher.kr[-1].update(open=prev_close * 0.97, close=prev_close * 0.98)  # 전일 대비 -2%, 양봉
    path.write_text(json.dumps({
        "2026-11-18": {"KOSPI": {"probability": 0.6, "actual_change": 0.01}},
        "2026-11-19": {
            "KOSPI": {"probability": 0.23, "body_probability": 0.8,
                      "actual_change": None, "actual_body_change": None},
            "000660": {"probability": 0.7, "body_probability": 0.8,
                       "actual_change": None, "actual_body_change": None},
        },
    }), encoding="utf-8")
    notifier = MagicMock()
    notifier.emit = AsyncMock()
    svc = _service(fetcher, notifier=notifier, history_path=path)

    result = await svc.report_close("2026-11-19", notify=True)

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["2026-11-19"]["KOSPI"]["actual_change"] == pytest.approx(-0.02)
    assert saved["2026-11-19"]["KOSPI"]["actual_body_change"] == pytest.approx(0.98 / 0.97 - 1)
    kospi = next(t for t in result["targets"] if t["code"] == "KOSPI")
    assert kospi["direction_hit"] is True                            # 하락 77% 예측 → -2% 마감
    assert kospi["body_hit"] is True                                 # 양봉 80% 예측 → 실제 양봉
    assert kospi["score"] == {"hits": 2, "total": 2}
    assert kospi["total"] == {"n": 2, "hits": 2, "confident_n": 2, "confident_hits": 2}
    hynix = next(t for t in result["targets"] if t["code"] == "000660")
    assert hynix["direction_hit"] is False                           # 상승 70% 예측 → -2% 마감
    assert hynix["body_hit"] is True
    assert hynix["score"] == {"hits": 1, "total": 2}

    kwargs = notifier.emit.await_args.kwargs
    assert kwargs["metadata"]["force_external"] is True
    assert kwargs["metadata"]["event"] == "premarket_direction_result"
    assert kwargs["title"].endswith("전일 대비 1/2 · 양음봉 2/2")
    message = kwargs["message"]
    assert "오늘 적중: 전일 대비 1/2 · 양음봉 2/2 (합계 3/4)" in message
    assert "• 코스피 (2/2)\n  양음봉: 양봉 80% → 실제 양봉(+1.03%) 적중\n  전일 대비: ▼ 하락 마감 77% → 실제 -2.00% 적중" in message
    assert "• SK하이닉스 (1/2)\n  양음봉: 양봉 80% → 실제 양봉(+1.03%) 적중\n  전일 대비: ▲ 상승 마감 70% → 실제 -2.00% 실패" in message
    assert "누적 (기록된 예측 기준)\n• 코스피: 양음봉 1/1일 · 전일 대비 2/2일" in message
    assert "※ 양음봉 등락률은 당일 시가, 전일 대비 등락률은 전 거래일 종가 기준" in message


@pytest.mark.asyncio
async def test_report_close_replaces_stale_hynix_actuals_from_price_feed(tmp_path):
    path = tmp_path / "history.json"
    fetcher = FakeFetcher(today_open=1.0)
    fetcher.kr[-3]["close"] = 1113000
    fetcher.kr[-2].update(open=1079000, close=1085000)
    fetcher.kr[-1].update(open=1212000, close=1158000)
    path.write_text(json.dumps({
        "2026-11-18": {"000660": {"probability": 0.2, "body_probability": 0.7,
                                    "actual_change": -0.0136, "actual_body_change": 0.02}},
        "2026-11-19": {"000660": {"probability": 0.98, "body_probability": 0.24,
                                    "actual_change": 0.0518, "actual_body_change": -0.0446}},
    }), encoding="utf-8")

    result = await _service(fetcher, history_path=path).report_close("2026-11-19", notify=False)

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["2026-11-18"]["000660"]["actual_change"] == pytest.approx(1085000 / 1113000 - 1)
    assert saved["2026-11-18"]["000660"]["actual_body_change"] == pytest.approx(1085000 / 1079000 - 1)
    assert saved["2026-11-19"]["000660"]["actual_change"] == pytest.approx(1158000 / 1085000 - 1)
    assert saved["2026-11-19"]["000660"]["actual_body_change"] == pytest.approx(1158000 / 1212000 - 1)
    assert result["targets"][0]["actual_change"] == pytest.approx(1158000 / 1085000 - 1)


@pytest.mark.asyncio
async def test_report_close_skips_when_no_prediction_recorded_today(tmp_path):
    path = tmp_path / "history.json"
    notifier = MagicMock()
    notifier.emit = AsyncMock()
    result = await _service(FakeFetcher(), notifier=notifier, history_path=path).report_close("2026-11-19")
    assert result == {"ok": True, "date": "2026-11-19", "skipped": "no_prediction"}
    notifier.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_close_reports_error_when_close_not_yet_available(tmp_path):
    path = tmp_path / "history.json"
    path.write_text(json.dumps({"2026-11-19": {"KOSPI": {"probability": 0.3, "actual_change": None}}}),
                    encoding="utf-8")
    notifier = MagicMock()
    notifier.emit = AsyncMock()
    result = await _service(FakeFetcher(today_open=None), notifier=notifier,
                            history_path=path).report_close("2026-11-19")
    assert result["ok"] is False
    assert "KOSPI" in result["error"]
    notifier.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_error_message_names_exception_type_when_message_is_empty(tmp_path):
    """httpx.ReadTimeout 처럼 메시지가 빈 예외도 원인을 알 수 있어야 한다."""
    class ReadTimeout(Exception):
        pass

    fetcher = FakeFetcher()
    fetcher.fetch_us = AsyncMock(side_effect=ReadTimeout())
    assert (await _service(fetcher).refresh(notify=False))["error"] == "ReadTimeout"

    path = tmp_path / "history.json"
    path.write_text(json.dumps({"2026-11-19": {"KOSPI": {"probability": 0.3, "actual_change": None}}}),
                    encoding="utf-8")
    fetcher.fetch_kr_index = AsyncMock(side_effect=ReadTimeout())
    result = await _service(fetcher, history_path=path).report_close("2026-11-19")
    assert result["error"] == "ReadTimeout"
