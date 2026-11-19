"""장전 코스피 상승 마감 확률 예측 (정보 표시 전용 — 매매 게이트에 연결하지 않는다).

밤사이 미국장·VIX·환율 지표로 오늘 코스피가 전일 종가보다 높게 마감(B)할 확률을
로지스틱 회귀로 추정한다. 지표 선정 근거: `scripts/run_premarket_direction_backtest.py`
(2021-10~2026-11 표본외 정확도 65.4%, 기준선 54.1%).

정렬 규칙(미래참조 방지): 한국 거래일 D 의 특징값은 미국 세션 날짜 T < D 인 종가만 쓴다.
미국 T 세션은 KST 로 T+1 새벽에 마감하므로 D 장 시작 전에 확정된다. 값은
"한국 직전 거래일 이후 새로 확정된 미국 세션들의 누적 수익률"이다(한국 휴장 자동 처리).

데이터: 국내 지수 = 네이버 fchart, 해외 = Yahoo Finance chart API.
"""
from __future__ import annotations

import json
import logging
import math
import re
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

import httpx

from services.notification_service import NotificationCategory, NotificationLevel


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    label: str
    sign: int  # +1: 상승이 호재, -1: 상승이 악재
    symbol: str = ""


US_FEATURES: tuple[FeatureSpec, ...] = (
    FeatureSpec("sox", "필라델피아 반도체(SOX)", +1, "^SOX"),
    FeatureSpec("ndx", "나스닥100", +1, "^NDX"),
    FeatureSpec("spx", "S&P500", +1, "^GSPC"),
    FeatureSpec("ewy", "EWY(미국상장 한국ETF)", +1, "EWY"),
    FeatureSpec("vix", "VIX 변화율", -1, "^VIX"),
    FeatureSpec("usdkrw", "원/달러 환율 변화율", -1, "KRW=X"),
    FeatureSpec("tnx", "미국 10년물 금리 변화율", -1, "^TNX"),
)
KR_PREV = FeatureSpec("kr_prev_ret", "전일 지수 등락(모멘텀)", +1)
GAP = FeatureSpec("gap", "시가 갭(역추세, 시가 확정 후)", -1)
# 백테스트에서 의미 없던 금리·전일 모멘텀은 운영 예측에서 뺀다.
FORECAST_FEATURES: tuple[FeatureSpec, ...] = tuple(
    f for f in US_FEATURES if f.name in ("sox", "ndx", "spx", "ewy", "vix", "usdkrw")
)


# ── 파서 ─────────────────────────────────────────────────────────────

def parse_naver_fchart(text: str) -> List[dict]:
    rows = []
    for data in re.findall(r'data="([^"]+)"', text):
        d, o, h, l, c, *_ = data.split("|")
        rows.append({
            "date": f"{d[:4]}-{d[4:6]}-{d[6:8]}",
            "open": float(o), "high": float(h), "low": float(l), "close": float(c),
        })
    rows.sort(key=lambda r: r["date"])
    return rows


def parse_yahoo_chart(text: str) -> List[dict]:
    result = json.loads(text)["chart"]["result"][0]
    tz = timezone(timedelta(seconds=result["meta"].get("gmtoffset", 0)))
    closes = result["indicators"]["quote"][0]["close"]
    rows = {}
    for ts, c in zip(result.get("timestamp") or [], closes):
        if c is None:
            continue
        rows[datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d")] = float(c)
    return [{"date": d, "close": rows[d]} for d in sorted(rows)]


# ── 데이터셋 ─────────────────────────────────────────────────────────

def asof_return(series: Sequence[dict], prev_date: str, date: str) -> Optional[float]:
    """date 미만 마지막 종가 / prev_date 미만 마지막 종가 - 1."""
    dates = [r["date"] for r in series]
    base_idx = bisect_left(dates, prev_date) - 1
    now_idx = bisect_left(dates, date) - 1
    if base_idx < 0 or now_idx < 0:
        return None
    return series[now_idx]["close"] / series[base_idx]["close"] - 1.0


def build_dataset(kr_rows: Sequence[dict], us: Dict[str, Sequence[dict]]) -> List[dict]:
    rows = []
    for i in range(1, len(kr_rows)):
        prev, cur = kr_rows[i - 1], kr_rows[i]
        row = {"date": cur["date"]}
        for name, series in us.items():
            row[name] = asof_return(series, prev["date"], cur["date"])
        row[KR_PREV.name] = prev["close"] / kr_rows[i - 2]["close"] - 1.0 if i >= 2 else None
        row[GAP.name] = cur["open"] / prev["close"] - 1.0
        row["up_close"] = int(cur["close"] > prev["close"])
        row["up_body"] = int(cur["close"] > cur["open"])
        row["up_gap"] = int(cur["open"] > prev["close"])
        rows.append(row)
    return rows


def _fit_logit(X: List[List[float]], y: List[int], iters: int = 400, lr: float = 0.5, l2: float = 1e-2):
    import numpy as np

    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd[sd == 0] = 1.0
    Z = (X - mu) / sd
    w = np.zeros(Z.shape[1])
    b = 0.0
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-(Z @ w + b)))
        g = p - y
        w -= lr * (Z.T @ g / len(y) + l2 * w)
        b -= lr * g.mean()
    return mu, sd, w, b




# ── 운영 예측 ─────────────────────────────────────────────────────────

UP_THRESHOLD = 0.6
DOWN_THRESHOLD = 0.4


def build_forecast_features(us: Dict[str, Sequence[dict]], prev_date: str, date: str) -> Dict[str, Optional[float]]:
    """오늘(date) 장 시작 전에 확정된 미국 세션의 직전 한국 거래일(prev_date) 대비 변화."""
    return {name: asof_return(series, prev_date, date) for name, series in us.items()}


def _outlook(probability: float) -> str:
    if probability >= UP_THRESHOLD:
        return "상승 우세"
    if probability <= DOWN_THRESHOLD:
        return "하락 우세"
    return "중립"


class DailyHistoryFetcher:
    """국내 지수(네이버 fchart)·해외 지표(Yahoo chart) 일봉 수집기."""

    _NAVER_URL = "https://fchart.stock.naver.com/sise.nhn"
    _YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    _HEADERS = {"User-Agent": "Mozilla/5.0"}

    def __init__(self, timeout_sec: float = 15.0, yahoo_range: str = "7y"):
        self._timeout_sec = timeout_sec
        self._yahoo_range = yahoo_range

    async def fetch_kr_index(self, symbol: str, count: int) -> List[dict]:
        async with httpx.AsyncClient(timeout=self._timeout_sec, headers=self._HEADERS) as client:
            resp = await client.get(
                self._NAVER_URL,
                params={"symbol": symbol, "timeframe": "day", "count": count, "requestType": 0},
            )
            resp.raise_for_status()
            return parse_naver_fchart(resp.content.decode("euc-kr", errors="replace"))

    async def fetch_us(self, symbol: str) -> List[dict]:
        async with httpx.AsyncClient(timeout=self._timeout_sec, headers=self._HEADERS) as client:
            resp = await client.get(
                self._YAHOO_URL.format(symbol=symbol),
                params={"range": self._yahoo_range, "interval": "1d"},
            )
            resp.raise_for_status()
            return parse_yahoo_chart(resp.text)


class PremarketDirectionService:
    """장전 1회 코스피 상승 마감(종가>전일종가) 확률을 계산·보관·알림한다."""

    INDEX_SYMBOL = "KOSPI"

    def __init__(
        self,
        *,
        fetcher,
        market_clock,
        notification_service=None,
        logger: Optional[logging.Logger] = None,
        history_count: int = 1500,
        min_train_days: int = 250,
    ):
        self._fetcher = fetcher
        self._clock = market_clock
        self._notification_service = notification_service
        self._logger = logger or logging.getLogger(__name__)
        self._history_count = history_count
        self._min_train_days = min_train_days
        self._latest: Optional[dict] = None

    def get_latest(self) -> Optional[dict]:
        return self._latest

    async def get_or_compute(self) -> dict:
        """오늘 성공 결과가 없으면 알림 없이 계산한다 (장전 태스크를 놓친 날의 웹 조회용)."""
        today = self._clock.get_current_kst_time().strftime("%Y-%m-%d")
        latest = self._latest
        if latest is not None and latest.get("date") == today and latest.get("ok"):
            return self._latest
        return await self.refresh(notify=False)

    async def refresh(self, notify: bool = True) -> dict:
        today = self._clock.get_current_kst_time().strftime("%Y-%m-%d")
        try:
            result = await self._compute(today)
        except Exception as exc:
            self._logger.warning("장전 코스피 방향 예측 실패: %s", exc)
            result = {"ok": False, "date": today, "error": str(exc)}
        self._latest = result
        if notify and result["ok"] and self._notification_service is not None:
            await self._notify(result)
        return result

    async def _compute(self, today: str) -> dict:
        kr = [r for r in await self._fetcher.fetch_kr_index(self.INDEX_SYMBOL, self._history_count)
              if r["date"] < today]
        us = {f.name: await self._fetcher.fetch_us(f.symbol) for f in FORECAST_FEATURES}
        names = [f.name for f in FORECAST_FEATURES]
        empty = [n for n in names if not us[n]]
        if not kr or empty:
            raise ValueError(f"시세 수신 실패: {', '.join(empty) or self.INDEX_SYMBOL}")

        train = [r for r in build_dataset(kr, us) if all(r.get(n) is not None for n in names)]
        if len(train) < self._min_train_days:
            raise ValueError(f"학습 데이터 부족: {len(train)}일 < {self._min_train_days}일")
        mu, sd, w, b = _fit_logit([[r[n] for n in names] for r in train], [r["up_close"] for r in train])

        base_date = kr[-1]["date"]
        feats = build_forecast_features(us, prev_date=base_date, date=today)
        missing = [n for n in names if feats.get(n) is None]
        if missing:
            raise ValueError(f"오늘 지표 누락: {', '.join(missing)}")

        z = [(feats[n] - mu[i]) / sd[i] for i, n in enumerate(names)]
        logit = sum(w[i] * z[i] for i in range(len(names))) + b
        probability = 1.0 / (1.0 + math.exp(-logit))
        return {
            "ok": True,
            "date": today,
            "base_date": base_date,
            "probability": probability,
            "outlook": _outlook(probability),
            "train_days": len(train),
            "features": [
                {"name": f.name, "label": f.label, "value": feats[f.name],
                 "contribution": float(w[i] * z[i])}
                for i, f in enumerate(FORECAST_FEATURES)
            ],
        }

    async def _notify(self, result: dict) -> None:
        moves = " · ".join(f"{f['label']} {f['value'] * 100:+.2f}%" for f in result["features"])
        message = (
            f"오늘 코스피 상승 마감 확률 {result['probability'] * 100:.0f}% ({result['outlook']})\n"
            f"• 기준: {result['base_date']} 종가 대비 · 학습 {result['train_days']:,}일\n"
            f"• 밤사이: {moves}\n"
            "※ 참고 정보 — 백테스트 표본외 정확도 약 65%, 매매 신호 아님"
        )
        await self._notification_service.emit(
            category=NotificationCategory.STRATEGY,
            level=NotificationLevel.INFO,
            title="장전 코스피 방향 예측",
            message=message,
            metadata={
                "event": "premarket_direction_forecast",
                "probability": result["probability"],
                "outlook": result["outlook"],
            },
        )
